"""Per-chat spend limits over UTC calendar periods."""

import datetime as dt
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
    return {
        Period.DAILY: int(settings.cost_limit_daily_usd * 1_000_000),
        Period.WEEKLY: int(settings.cost_limit_weekly_usd * 1_000_000),
        Period.MONTHLY: int(settings.cost_limit_monthly_usd * 1_000_000),
    }


async def exceeded_period(session: AsyncSession, chat_id: int, settings: Settings, now: dt.datetime) -> Period | None:
    """The first period whose limit the chat has reached, if any."""
    limits = limits_microusd(settings)
    for period, start in period_starts(now).items():
        if await repo.spent_microusd(session, chat_id, start) >= limits[period]:
            return period
    return None
