"""Build LLM conversation snippets from persisted ``messages`` rows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from config import get_butler_name

IST = ZoneInfo("Asia/Kolkata")


def build_conversation_snippet(
    session: Session,
    *,
    group_id: str,
    now_ist: datetime | None = None,
    limit: int = 15,
) -> str:
    """
    Last ``limit`` messages for ``group_id`` on the current IST calendar day, oldest first.
    Each line: ``"<sender>: <text>"`` — sender ``Van`` for outbound, else ``from_name`` / ``from``.
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    elif now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST)
    else:
        now_ist = now_ist.astimezone(IST)

    day_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    start_utc = day_start.astimezone(timezone.utc)
    end_utc = day_end.astimezone(timezone.utc)

    rows = session.execute(
        text(
            """
            SELECT direction, from_name, "from", message, timestamp
            FROM messages
            WHERE chat_id = :group_id
              AND message IS NOT NULL
              AND btrim(message) <> ''
              AND timestamp >= :start_utc
              AND timestamp < :end_utc
            ORDER BY timestamp DESC
            LIMIT :lim
            """
        ),
        {"group_id": group_id, "start_utc": start_utc, "end_utc": end_utc, "lim": limit},
    ).fetchall()

    lines: list[str] = []
    for r in reversed(rows):
        direction, from_name, from_wa, message, _ts = r
        if direction == "outbound":
            sender = get_butler_name()
        else:
            sender = (from_name if isinstance(from_name, str) and from_name.strip() else None) or (
                str(from_wa) if from_wa is not None else None
            ) or "Unknown"
        body = message if isinstance(message, str) else ""
        lines.append(f"{sender}: {body.strip()}")

    return "\n".join(lines)


def build_cook_absence_snippet(
    session: Session,
    *,
    group_id: str,
    now_ist: datetime | None = None,
) -> str:
    """Last 10 messages today (IST) — same window as main intent, smaller limit."""
    return build_conversation_snippet(
        session, group_id=group_id, now_ist=now_ist, limit=10
    )
