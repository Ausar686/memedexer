"""Spend limits over UTC calendar periods: per chat, and across all chats."""

import dataclasses
import datetime as dt
import decimal
import enum

from sqlalchemy.ext.asyncio import AsyncSession

from memedexer.config import Settings
from memedexer.storage import repo


class Period(enum.StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def period_starts(now: dt.datetime) -> dict[Period, dt.datetime]:
    """Start of the current day, ISO week (Monday) and month, in UTC."""
    day = now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        Period.DAILY: day,
        Period.WEEKLY: day - dt.timedelta(days=day.weekday()),
        Period.MONTHLY: day.replace(day=1),
    }


def limits_microusd(settings: Settings) -> dict[Period, int]:
    """Each chat's own limits."""
    return {
        Period.DAILY: _microusd(settings.cost_limit_daily_usd),
        Period.WEEKLY: _microusd(settings.cost_limit_weekly_usd),
        Period.MONTHLY: _microusd(settings.cost_limit_monthly_usd),
    }


@dataclasses.dataclass(frozen=True)
class Limit:
    period: Period
    microusd: int
    # Across all chats rather than for one chat.
    total: bool = False

    def __str__(self) -> str:
        return f"total {self.period}" if self.total else str(self.period)


def limits(settings: Settings) -> list[Limit]:
    """The chat's own limits, then the total across all chats, in the order they're checked."""
    chat = [Limit(period, microusd) for period, microusd in limits_microusd(settings).items()]
    return [*chat, Limit(Period.MONTHLY, _microusd(settings.cost_limit_total_monthly_usd), total=True)]


async def exceeded_limit(session: AsyncSession, chat_id: int, settings: Settings, now: dt.datetime) -> Limit | None:
    """The first limit the chat (or all chats together) has reached, if any."""
    starts = period_starts(now)
    for limit in limits(settings):
        spent = await repo.spent_microusd(session, None if limit.total else chat_id, starts[limit.period])
        if spent >= limit.microusd:
            return limit
    return None


def _microusd(usd: decimal.Decimal) -> int:
    return int(usd * 1_000_000)
