"""Calendar dates in Asia/Kolkata for meal storage (tomorrow = next local day)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def today_calendar_date_in_ist(*, now_ist: datetime | None = None) -> date:
    """Return today's date in Asia/Kolkata."""
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)
    return now_ist.date()


def tomorrow_calendar_date_in_ist(*, now_ist: datetime | None = None) -> date:
    """Return tomorrow's date in Asia/Kolkata (used for ``daily_options`` / ``daily_choices``)."""
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)
    return (now_ist.date() + timedelta(days=1))
