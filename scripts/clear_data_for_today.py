"""CLI: reset test data for a fresh daily flow.

Clears:
  - ``daily_options`` and ``daily_choices`` for tomorrow (IST) by default
  - ``trivia.last_sent_on`` for rows whose last-sent calendar day is today (IST)
  - ``messages`` rows whose ``timestamp`` is today (IST), for ``FOOD_GROUP_ID`` when set

Run from repo root with venv active and DATABASE_URL set:

  python -m scripts.clear_data_for_today
  python -m scripts.clear_data_for_today --slot breakfast
  python -m scripts.clear_data_for_today --dry-run
  python -m scripts.clear_data_for_today --date 2026-05-20

Does not touch WhatsApp, the LLM, ``meals``, or ``meal_prep``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from database import engine, get_session  # noqa: E402
from meal_planning.dates import (  # noqa: E402
    today_calendar_date_in_ist,
    tomorrow_calendar_date_in_ist,
)
from models import ALLOWED_MEAL_SLOTS  # noqa: E402

_SLOT_CHOICES = ("all",) + tuple(sorted(ALLOWED_MEAL_SLOTS))
_CLI_NAME = "clear_data_for_today"


def _fail(msg: str, *, code: int = 1) -> None:
    print(f"[{_CLI_NAME}] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _parse_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def _summarize_daily(table: str, target_date: date, slot: str | None) -> None:
    if slot is None:
        sql = text(
            f"SELECT id, slot FROM {table} WHERE date = :d ORDER BY slot, id"
        )
        params = {"d": target_date}
    else:
        sql = text(
            f"SELECT id, slot FROM {table} WHERE date = :d AND slot = :s ORDER BY id"
        )
        params = {"d": target_date, "s": slot}

    with get_session() as session:
        rows = session.execute(sql, params).all()

    if not rows:
        print(f"  {table}: no matching rows.")
        return
    print(f"  {table}: {len(rows)} row(s)")
    for r in rows:
        print(f"    id={r[0]} slot={r[1]}")


def _delete_daily(table: str, target_date: date, slot: str | None) -> int:
    if slot is None:
        sql = text(f"DELETE FROM {table} WHERE date = :d")
        params = {"d": target_date}
    else:
        sql = text(f"DELETE FROM {table} WHERE date = :d AND slot = :s")
        params = {"d": target_date, "s": slot}

    with get_session() as session:
        result = session.execute(sql, params)
    return int(result.rowcount or 0)


def _summarize_trivia_sent_today(today_ist: date) -> None:
    sql = text(
        """
        SELECT id, category, last_sent_on
        FROM trivia
        WHERE last_sent_on IS NOT NULL
          AND (last_sent_on AT TIME ZONE 'Asia/Kolkata')::date = :today
        ORDER BY id
        """
    )
    with get_session() as session:
        rows = session.execute(sql, {"today": today_ist}).all()

    if not rows:
        print("  trivia.last_sent_on: no rows sent today (IST).")
        return
    print(f"  trivia.last_sent_on: {len(rows)} row(s) to clear (sent on {today_ist})")
    for r in rows:
        print(f"    id={r[0]} category={r[1]!r} last_sent_on={r[2]}")


def _messages_today_params() -> tuple[str, dict]:
    """Optional ``FOOD_GROUP_ID`` clause fragment and bind params (without ``today``)."""
    group_id = os.environ.get("FOOD_GROUP_ID")
    if group_id and str(group_id).strip():
        return " AND chat_id = :chat_id", {"chat_id": str(group_id).strip()}
    return "", {}


def _summarize_messages_today(today_ist: date) -> None:
    chat_clause, chat_params = _messages_today_params()
    sql = text(
        f"""
        SELECT id, direction, timestamp
        FROM messages
        WHERE timestamp IS NOT NULL
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
          {chat_clause}
        ORDER BY timestamp, id
        """
    )
    params = {"today": today_ist, **chat_params}
    with get_session() as session:
        rows = session.execute(sql, params).all()

    group_id = os.environ.get("FOOD_GROUP_ID")
    scope = f"chat_id={group_id!r}" if group_id and str(group_id).strip() else "all chats"
    if not rows:
        print(f"  messages ({scope}): no rows on {today_ist} (IST).")
        return
    print(f"  messages ({scope}): {len(rows)} row(s) on {today_ist} (IST)")
    for r in rows[:20]:
        print(f"    id={r[0]} direction={r[1]} timestamp={r[2]}")
    if len(rows) > 20:
        print(f"    ... and {len(rows) - 20} more")


def _delete_messages_today(today_ist: date) -> int:
    chat_clause, chat_params = _messages_today_params()
    sql = text(
        f"""
        DELETE FROM messages
        WHERE timestamp IS NOT NULL
          AND (timestamp AT TIME ZONE 'Asia/Kolkata')::date = :today
          {chat_clause}
        """
    )
    params = {"today": today_ist, **chat_params}
    with get_session() as session:
        result = session.execute(sql, params)
    return int(result.rowcount or 0)


def _clear_trivia_sent_today(today_ist: date) -> int:
    sql = text(
        """
        UPDATE trivia
        SET last_sent_on = NULL
        WHERE last_sent_on IS NOT NULL
          AND (last_sent_on AT TIME ZONE 'Asia/Kolkata')::date = :today
        """
    )
    with get_session() as session:
        result = session.execute(sql, {"today": today_ist})
    return int(result.rowcount or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slot",
        choices=_SLOT_CHOICES,
        default="all",
        help="Meal slot to clear from daily tables (default: all)",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Calendar date for daily_options/daily_choices (default: tomorrow IST)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without modifying the database",
    )
    args = parser.parse_args()

    if engine is None:
        _fail("DATABASE_URL is not set; cannot connect to the database.")

    target_date = _parse_date(args.date) or tomorrow_calendar_date_in_ist()
    today_ist = today_calendar_date_in_ist()
    slot_filter: str | None = None if args.slot == "all" else args.slot

    print(f"daily tables date: {target_date.isoformat()} (IST tomorrow if --date omitted)")
    print(f"trivia reset filter: last_sent_on on {today_ist.isoformat()} (IST today)")
    _gid = os.environ.get("FOOD_GROUP_ID")
    print(
        f"messages filter: timestamp on {today_ist.isoformat()} (IST today)"
        + (f", chat_id={_gid!r}" if _gid and str(_gid).strip() else ", all chats")
    )
    print(f"slot filter: {args.slot}")
    print(f"mode: {'DRY-RUN (no changes)' if args.dry_run else 'UPDATE/DELETE'}\n")

    print("Before (daily tables):")
    _summarize_daily("daily_options", target_date, slot_filter)
    _summarize_daily("daily_choices", target_date, slot_filter)
    print("\nBefore (trivia):")
    _summarize_trivia_sent_today(today_ist)
    print("\nBefore (messages):")
    _summarize_messages_today(today_ist)

    if args.dry_run:
        print("\nDry run: no rows were changed.")
        return

    deleted_options = _delete_daily("daily_options", target_date, slot_filter)
    deleted_choices = _delete_daily("daily_choices", target_date, slot_filter)
    cleared_trivia = _clear_trivia_sent_today(today_ist)
    deleted_messages = _delete_messages_today(today_ist)

    print(
        f"\nDeleted: daily_options={deleted_options}, daily_choices={deleted_choices}; "
        f"trivia last_sent_on cleared={cleared_trivia}; messages={deleted_messages}"
    )

    print("\nAfter (daily tables):")
    _summarize_daily("daily_options", target_date, slot_filter)
    _summarize_daily("daily_choices", target_date, slot_filter)
    print("\nAfter (trivia):")
    _summarize_trivia_sent_today(today_ist)
    print("\nAfter (messages):")
    _summarize_messages_today(today_ist)


if __name__ == "__main__":
    main()
