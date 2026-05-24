"""Random meal option sampling, plain-text payload formatting, and WhatsApp notify + daily_options upsert."""

from __future__ import annotations

import logging
import random
from datetime import date, timedelta

from sqlalchemy.orm import Session

from database import get_session
from meal_planning.dates import today_calendar_date_in_ist, tomorrow_calendar_date_in_ist
from messages_service.helpers import send_food_group_message, send_no_viable_meal_options_notice
from meal_planning.query import (
    fetch_daily_option_first_id,
    fetch_daily_option_meal_ids,
    fetch_daily_choices_for_dates,
    fetch_meal_ids_for_slot,
    fetch_meals_by_ids,
    insert_daily_options_row,
    update_daily_options_meal_ids,
)
from models import assert_valid_meal_slot_values, is_system_meal_id

log = logging.getLogger(__name__)


def format_meal_options_message(payload: dict) -> str:
    """Plain-text WhatsApp message: title-cased slot, then ``"N. <name>"`` lines (no LLM)."""
    slot = payload.get("slot")
    if not isinstance(slot, str) or not slot.strip():
        raise ValueError("payload must include non-empty string 'slot'")
    options = payload.get("options")
    if not isinstance(options, list):
        raise ValueError("payload must include list 'options'")
    if not all(isinstance(x, str) for x in options):
        raise ValueError("payload 'options' must be a list of meal name strings")

    lines: list[str] = [slot.strip().title()]
    for i, name in enumerate(options, start=1):
        lines.append(f"{i}. {name}")
    return "\n".join(lines)


def load_exclude_meal_ids(session: Session, calendar_date: date, slot: str) -> list[int]:
    """Meal ids to exclude when sampling: ``daily_options`` for date+slot, plus on the first
    lunch/dinner batch recent ``daily_choices`` for the same slot (today and prior two IST days).
    """
    assert_valid_meal_slot_values([slot])
    excluded = fetch_daily_option_meal_ids(session, calendar_date, slot)
    if excluded or slot not in ("lunch", "dinner"):
        return excluded

    today = today_calendar_date_in_ist()
    lookback = [today - timedelta(days=i) for i in range(3)]
    choices_by_date = fetch_daily_choices_for_dates(session, lookback)
    seen = set(excluded)
    for d in lookback:
        meal_id = choices_by_date.get(d, {}).get(slot)
        if meal_id is None or is_system_meal_id(meal_id) or meal_id in seen:
            continue
        seen.add(meal_id)
        excluded.append(meal_id)
    return excluded


def _append_meal_ids_for_storage(existing: list[int], new_batch: list[int]) -> list[int]:
    """Preserve ``existing`` order, then append ids from ``new_batch`` not already present."""
    out = list(existing)
    seen = set(existing)
    for mid in new_batch:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return out


def generate_meal_ids(
    session: Session,
    slot: str,
    *,
    exclude_meal_ids: list[int] | None = None,
    option_count: int = 3,
) -> list[int]:
    """Random meal ids for ``slot`` from ``meals``, excluding ``exclude_meal_ids``."""
    assert_valid_meal_slot_values([slot])
    if option_count < 1:
        raise ValueError("option_count must be >= 1")
    excluded = set(exclude_meal_ids or ())
    candidates = fetch_meal_ids_for_slot(session, slot)
    pool = [mid for mid in candidates if mid not in excluded]
    if not pool:
        log.warning(
            "generate_meal_ids: no meals for slot=%r after exclusions (candidates=%d excluded=%d)",
            slot,
            len(candidates),
            len(excluded),
        )
        return []
    k = min(option_count, len(pool))
    if k < option_count:
        log.warning(
            "generate_meal_ids: only %d meal(s) for slot=%r (requested %d)",
            k,
            slot,
            option_count,
        )
    return random.sample(pool, k)


def build_meal_options(session: Session, slot: str, meal_ids: list[int]) -> dict:
    """Payload for the options LLM: ``slot`` plus ``options`` as meal names only (ids stay server-side)."""
    assert_valid_meal_slot_values([slot])
    if not meal_ids:
        return {"slot": slot, "options": []}

    id_order = {mid: i for i, mid in enumerate(meal_ids)}
    meal_rows = fetch_meals_by_ids(session, meal_ids)
    meals_sorted = sorted(meal_rows, key=lambda t: id_order[t[0]])

    names: list[str] = [name for _mid, name in meals_sorted]
    return {"slot": slot, "options": names}


def upsert_daily_options(
    session: Session,
    calendar_date: date,
    slot: str,
    meal_ids: list[int],
) -> list[int]:
    """Append ``meal_ids`` (this run) onto stored ``meal_ids`` for date+slot, or insert first row.

    Returns the full stored list after write.
    """
    assert_valid_meal_slot_values([slot])
    existing = fetch_daily_option_meal_ids(session, calendar_date, slot)
    merged = _append_meal_ids_for_storage(existing, meal_ids)
    row_id = fetch_daily_option_first_id(session, calendar_date, slot)
    if row_id is None:
        insert_daily_options_row(session, calendar_date, slot, merged)
    else:
        update_daily_options_meal_ids(session, row_id, merged)
    return merged


async def run_slot_options(
    slot: str,
    *,
    storage_date: date | None = None,
    option_count: int = 3,
) -> dict:
    """Pick meals, format options (plain text, no LLM), send WhatsApp, upsert ``daily_options``.

    If nothing is left after exclusions, sends ``send_no_viable_meal_options_notice`` and skips
    the formatter and ``daily_options`` upsert.
    """
    if storage_date is None:
        storage_date = tomorrow_calendar_date_in_ist()

    assert_valid_meal_slot_values([slot])

    with get_session() as session:
        excluded = load_exclude_meal_ids(session, storage_date, slot)
        meal_ids = generate_meal_ids(
            session,
            slot,
            exclude_meal_ids=excluded,
            option_count=option_count,
        )
        if not meal_ids:
            send_no_viable_meal_options_notice()
            return {
                "storage_date": storage_date.isoformat(),
                "slot": slot,
                "meal_ids": [],
                "outcome": "no_viable_options_notice_sent",
            }
        payload = build_meal_options(session, slot, meal_ids)

    options_text = format_meal_options_message(payload)
    send_food_group_message(options_text)

    with get_session() as session:
        stored = upsert_daily_options(session, storage_date, slot, meal_ids)

    return {
        "storage_date": storage_date.isoformat(),
        "slot": slot,
        "meal_ids": meal_ids,
        "meal_ids_stored": stored,
        "outcome": "options_sent",
    }
