import datetime as dt
import decimal

from sqlalchemy.ext.asyncio import AsyncSession

from memedexer.config import Settings
from memedexer.pipeline.budget import Limit, Period, exceeded_limit, limits, limits_microusd, period_starts
from memedexer.storage import repo
from tests.factories import CHAT_ID, seed_chat, seed_job

# A Thursday; the ISO week started on Monday 2026-10-05.
NOW = dt.datetime(2026, 10, 8, 15, 30, tzinfo=dt.UTC)


def test_period_starts() -> None:
    assert period_starts(NOW) == {
        Period.DAILY: dt.datetime(2026, 10, 8, tzinfo=dt.UTC),
        Period.WEEKLY: dt.datetime(2026, 10, 5, tzinfo=dt.UTC),
        Period.MONTHLY: dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    }


def test_period_starts_converts_to_utc() -> None:
    late_evening_utc = dt.datetime(2026, 10, 9, 1, tzinfo=dt.timezone(dt.timedelta(hours=5)))
    assert period_starts(late_evening_utc)[Period.DAILY] == dt.datetime(2026, 10, 8, tzinfo=dt.UTC)


def test_monday_week_starts_today() -> None:
    monday = dt.datetime(2026, 10, 5, 0, 0, 1, tzinfo=dt.UTC)
    assert period_starts(monday)[Period.WEEKLY] == dt.datetime(2026, 10, 5, tzinfo=dt.UTC)


def test_limits_in_microusd(settings: Settings) -> None:
    settings.cost_limit_daily_usd = decimal.Decimal("0.5")
    assert limits_microusd(settings) == {Period.DAILY: 500_000, Period.WEEKLY: 100_000_000, Period.MONTHLY: 200_000_000}


def test_limits_check_the_chat_first_then_all_chats(settings: Settings) -> None:
    settings.cost_limit_total_monthly_usd = decimal.Decimal("150")

    assert limits(settings) == [
        Limit(Period.DAILY, 20_000_000),
        Limit(Period.WEEKLY, 100_000_000),
        Limit(Period.MONTHLY, 200_000_000),
        Limit(Period.MONTHLY, 150_000_000, total=True),
    ]
    assert [str(limit) for limit in limits(settings)] == ["daily", "weekly", "monthly", "total monthly"]


async def _spend(
    session: AsyncSession, cost: int, finished_at: dt.datetime, message_id: int, chat_id: int = CHAT_ID
) -> None:
    job = await seed_job(session, message_id=message_id, chat_id=chat_id)
    job.cost_microusd, job.finished_at = cost, finished_at
    await session.commit()


async def test_exceeded_limit(session: AsyncSession, settings: Settings) -> None:
    settings.cost_limit_daily_usd = decimal.Decimal("1")
    settings.cost_limit_weekly_usd = decimal.Decimal("2")
    await seed_chat(session)

    await _spend(session, 999_999, NOW, 1)
    assert await exceeded_limit(session, CHAT_ID, settings, NOW) is None

    await _spend(session, 1, NOW, 2)
    assert await exceeded_limit(session, CHAT_ID, settings, NOW) == Limit(Period.DAILY, 1_000_000)
    assert await exceeded_limit(session, CHAT_ID, settings, NOW + dt.timedelta(days=1)) is None

    await _spend(session, 1_000_000, NOW - dt.timedelta(days=2), 3)
    tomorrow = NOW + dt.timedelta(days=1)
    assert await exceeded_limit(session, CHAT_ID, settings, tomorrow) == Limit(Period.WEEKLY, 2_000_000)
    assert await repo.spent_microusd(session, CHAT_ID, NOW - dt.timedelta(days=30)) == 2_000_000


async def test_total_limit_counts_every_chat(session: AsyncSession, settings: Settings) -> None:
    settings.cost_limit_total_monthly_usd = decimal.Decimal("3")
    other = CHAT_ID - 1
    await seed_chat(session)
    await seed_chat(session, chat_id=other)

    await _spend(session, 2_000_000, NOW, 1)
    assert await exceeded_limit(session, other, settings, NOW) is None

    await _spend(session, 1_000_000, NOW - dt.timedelta(days=1), 2, chat_id=other)
    # Neither chat is over its own limits, but together they reached the total.
    assert await exceeded_limit(session, other, settings, NOW) == Limit(Period.MONTHLY, 3_000_000, total=True)
    assert await repo.spent_microusd(session, None, NOW - dt.timedelta(days=30)) == 3_000_000
