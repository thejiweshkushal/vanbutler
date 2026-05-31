"""Dispatch intent statuses to DB writes and WhatsApp replies."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from meal_planning.match import classify_match, normalize_meal_name, score_meal_name
from meal_planning.meal_options import run_slot_options
from meal_planning.query import (
    fetch_all_meal_names,
    fetch_meal_slot_array,
    fetch_meals_by_ids,
    fetch_slots_for_meal_id_in_daily_options,
    insert_meal_with_prep,
    upsert_daily_choice,
)
from ..helpers import (
    send_ambiguous_match,
    send_food_group_message,
    send_freeze_confirmation,
    send_meal_added_confirmation,
    send_meal_added_freeze_prompt,
    send_meal_already_exists,
    send_meal_not_found,
    send_skip_confirmation,
    send_slot_clarification,
)
from models import SKIP_MEAL_ID, assert_valid_meal_slot_values
from sqlalchemy.orm import Session

from config import get_cook_nickname
from .cook_absence import resolve_cook_absence

log = logging.getLogger(__name__)

ALLOWED_SLOTS = frozenset({"breakfast", "lunch", "dinner"})


def _parse_slots_field(raw: Any) -> list[str] | None:
    """Return normalized slot strings, or None if unknown / null."""
    if raw is None:
        return None
    if raw == "UNKNOWN":
        return None
    if isinstance(raw, str) and raw.strip().upper() == "UNKNOWN":
        return None
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for x in raw:
        if not isinstance(x, str) or not x.strip():
            continue
        s = x.strip().lower()
        if s in ALLOWED_SLOTS:
            out.append(s)
    return out if out else None


def _resolve_freeze_slots(
    session: Session,
    meal_id: int,
    storage_date: date,
    intent_slots: list[str] | None,
) -> tuple[list[str] | None, bool]:
    """
    Return ``(slots, ok)``. ``slots`` is None if ambiguous (caller should ask).
    ``ok`` is False only on unexpected errors (currently always True when slots is None).
    """
    if intent_slots:
        try:
            assert_valid_meal_slot_values(intent_slots)
        except ValueError:
            return (None, True)
        return (intent_slots, True)

    from_opts = fetch_slots_for_meal_id_in_daily_options(session, storage_date, meal_id)
    if len(from_opts) == 1:
        return ([from_opts[0]], True)

    meal_slots = fetch_meal_slot_array(session, meal_id)
    if len(meal_slots) == 1:
        return ([meal_slots[0]], True)

    return (None, True)


async def handle_freeze(
    session: Session,
    intent: dict[str, Any],
    *,
    storage_date: date,
) -> bool:
    meal_raw = intent.get("meal_name")
    if not isinstance(meal_raw, str) or not meal_raw.strip():
        log.warning("FREEZE_MEAL_OPTION missing meal_name: %r", intent)
        return False

    meal_query = meal_raw.strip()
    intent_slots = _parse_slots_field(intent.get("slots"))

    candidates = fetch_all_meal_names(session)
    scored = score_meal_name(meal_query, candidates)
    tier, meal_id, top_for_reply = classify_match(scored)

    if tier == "NONE":
        send_meal_not_found(meal_query)
        return False

    if tier == "DECENT" or meal_id is None:
        names = [t[1] for t in top_for_reply if t[2] >= 70][:3]
        if not names and top_for_reply:
            names = [top_for_reply[0][1]]
        send_ambiguous_match(meal_query, names)
        return False

    resolved_slots, _ok = _resolve_freeze_slots(session, meal_id, storage_date, intent_slots)
    if not resolved_slots:
        rows = fetch_meals_by_ids(session, [meal_id])
        display = rows[0][1] if rows else meal_query
        send_slot_clarification(display)
        return False

    for slot in resolved_slots:
        upsert_daily_choice(session, storage_date, slot, meal_id)

    rows = fetch_meals_by_ids(session, [meal_id])
    display = rows[0][1] if rows else meal_query
    if len(resolved_slots) == 1:
        send_freeze_confirmation(display, resolved_slots[0], storage_date)
    else:
        slots_s = ", ".join(s.title() for s in resolved_slots)
        send_food_group_message(
            f"I've set {slots_s} for {storage_date.strftime('%A')} to {display}. "
            "Unless you tell me otherwise, I shall assume it stands."
        )

    return True


async def handle_suggest_more(intent: dict[str, Any], *, storage_date: date) -> None:
    slots = _parse_slots_field(intent.get("slots"))
    if not slots:
        log.warning("SUGGEST_MORE_OPTIONS missing resolvable slots: %r", intent)
        return
    for slot in slots:
        await run_slot_options(slot, storage_date=storage_date)


def handle_add_new(session: Session, intent: dict[str, Any]) -> None:
    meal_raw = intent.get("meal_name")
    if not isinstance(meal_raw, str) or not meal_raw.strip():
        log.warning("ADD_NEW_OPTION_TO_DB missing meal_name: %r", intent)
        return

    name = meal_raw.strip()
    slots = _parse_slots_field(intent.get("slots"))
    if not slots:
        log.warning("ADD_NEW_OPTION_TO_DB missing slots: %r", intent)
        return

    ing = intent.get("ingredients")
    prep = intent.get("preprocessing")
    ingredients_csv = (
        ", ".join(str(x).strip() for x in ing if str(x).strip()) or None
    ) if isinstance(ing, list) else None
    pre_prep_csv = (
        ", ".join(str(x).strip() for x in prep if str(x).strip()) or None
    ) if isinstance(prep, list) else None

    qn = normalize_meal_name(name)
    for _mid, existing in fetch_all_meal_names(session):
        if normalize_meal_name(existing) == qn:
            send_meal_already_exists(existing)
            return

    try:
        assert_valid_meal_slot_values(slots)
    except ValueError as e:
        log.warning("ADD_NEW_OPTION_TO_DB invalid slots: %s", e)
        return

    reserved = {
        normalize_meal_name("None"),
        normalize_meal_name("Cook Not Coming"),
        normalize_meal_name("Cook Holiday"),
    }
    if qn in reserved:
        send_food_group_message(
            "That name is reserved for system use. Please choose a different name for the dish."
        )
        return

    insert_meal_with_prep(session, name, slots, ingredients_csv, pre_prep_csv)
    send_meal_added_confirmation(name, slots)
    send_meal_added_freeze_prompt(name, slots)


async def handle_skip_meal(
    session: Session,
    intent: dict[str, Any],
    *,
    storage_date: date,
) -> bool:
    slots = _parse_slots_field(intent.get("slots"))
    if not slots:
        log.warning("SKIP_MEAL missing resolvable slots: %r", intent)
        return False
    for slot in slots:
        upsert_daily_choice(session, storage_date, slot, SKIP_MEAL_ID)
    send_skip_confirmation(slots, storage_date)
    return True


async def handle_cook_not_coming(session: Session, *, group_id: str) -> set[date]:
    """Secondary LLM resolves dates; returns calendar dates touched."""
    touched, confirmation = await resolve_cook_absence(session, group_id=group_id)
    if confirmation:
        send_food_group_message(confirmation)
    elif not touched:
        cook = get_cook_nickname()
        send_food_group_message(
            "I wasn't quite able to sort out the cook schedule from that — "
            f"might you say which day or days {cook} isn't coming?"
        )
    return touched
