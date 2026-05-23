"""Meal intent classification (LLM) and dispatch for the food group."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from typing import Any

from database import engine, get_session
from llm.llm_service import analyze_conversation_intent_raw
from meal_planning.dates import tomorrow_calendar_date_in_ist
from ..helpers import send_food_group_message, send_intent_parse_failure_notice
from .conversation import build_conversation_snippet
from .handlers import (
    handle_add_new,
    handle_cook_not_coming,
    handle_freeze,
    handle_skip_meal,
    handle_suggest_more,
)

log = logging.getLogger(__name__)

INTENT_PARSE_MAX_ATTEMPTS = int(os.environ.get("INTENT_PARSE_MAX_ATTEMPTS", "3"))


def _strip_json_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, count=1, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


_ALLOWED_SLOTS = {"breakfast", "lunch", "dinner"}


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _is_slot_list(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(
        isinstance(s, str) and s.strip().lower() in _ALLOWED_SLOTS for s in v
    )


def _is_freeze_slots(v: Any) -> bool:
    """FREEZE accepts: list of valid slot strings, the literal "UNKNOWN", or null."""
    if v is None or v == "UNKNOWN":
        return True
    if isinstance(v, list):
        return all(
            isinstance(s, str) and s.strip().lower() in _ALLOWED_SLOTS for s in v
        )
    return False


def _parse_and_validate_intent_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_json_fences(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    intents = data.get("intents")
    if not isinstance(intents, list):
        return None
    allowed = {
        "FREEZE_MEAL_OPTION",
        "SUGGEST_MORE_OPTIONS",
        "ADD_NEW_OPTION_TO_DB",
        "CLARIFY",
        "CASUAL_REPLY",
        "SKIP_MEAL",
        "COOK_NOT_COMING",
    }
    for ob in intents:
        if not isinstance(ob, dict):
            return None
        st = ob.get("status")
        if st not in allowed:
            return None
        if st == "FREEZE_MEAL_OPTION":
            if not _is_nonempty_str(ob.get("meal_name")):
                return None
            if not _is_freeze_slots(ob.get("slots")):
                return None
        elif st == "SUGGEST_MORE_OPTIONS":
            if not _is_slot_list(ob.get("slots")):
                return None
        elif st == "SKIP_MEAL":
            if not _is_slot_list(ob.get("slots")):
                return None
        elif st == "COOK_NOT_COMING":
            pass
        elif st == "ADD_NEW_OPTION_TO_DB":
            meal_name = ob.get("meal_name")
            slots = ob.get("slots")
            ingredients = ob.get("ingredients")
            preprocessing = ob.get("preprocessing")
            if any(v == "UNKNOWN" for v in (meal_name, slots, ingredients, preprocessing)):
                return None
            if not _is_nonempty_str(meal_name):
                return None
            if not _is_slot_list(slots):
                return None
            if ingredients is not None and not isinstance(ingredients, list):
                return None
            if preprocessing is not None and not isinstance(preprocessing, list):
                return None
    rt = data.get("reply_text")
    if rt is not None and not isinstance(rt, str):
        return None
    return data


async def analyze_conversation_intent(snippet: str) -> dict[str, Any] | None:
    """Call the LLM up to ``INTENT_PARSE_MAX_ATTEMPTS`` times until JSON validates."""
    for attempt in range(1, INTENT_PARSE_MAX_ATTEMPTS + 1):
        try:
            raw = await analyze_conversation_intent_raw(snippet, attempt=attempt)
        except Exception:
            log.exception("LLM analyze_conversation_intent_raw failed on attempt %d", attempt)
            raise
        parsed = _parse_and_validate_intent_json(raw)
        if parsed is not None:
            return parsed
        preview = (raw[:500] + "…") if len(raw) > 500 else raw
        log.warning(
            "Intent JSON parse/validate failed (attempt %d/%d): body_preview=%r",
            attempt,
            INTENT_PARSE_MAX_ATTEMPTS,
            preview,
        )
    return None


async def process_food_group() -> None:
    """Build snippet, classify intents, run handlers, send ``reply_text`` when set."""
    if engine is None:
        log.warning("process_food_group: DATABASE_URL unset; skipping")
        return
    group_id = os.environ.get("FOOD_GROUP_ID")
    if not group_id or not str(group_id).strip():
        log.warning("process_food_group: FOOD_GROUP_ID unset; skipping")
        return

    storage_date = tomorrow_calendar_date_in_ist()

    with get_session() as session:
        snippet = build_conversation_snippet(session, group_id=group_id)

    if not snippet.strip():
        log.info("process_food_group: empty snippet; skipping LLM")
        return

    try:
        parsed = await analyze_conversation_intent(snippet)
    except Exception:
        log.exception("process_food_group: LLM failure")
        send_intent_parse_failure_notice()
        return

    if parsed is None:
        send_intent_parse_failure_notice()
        return

    intents_raw = parsed.get("intents") or []
    reply_text = parsed.get("reply_text")

    affected_dates: set[date] = set()
    with get_session() as session:
        for ob in intents_raw:
            if not isinstance(ob, dict):
                continue
            st = ob.get("status")
            if st == "FREEZE_MEAL_OPTION":
                if await handle_freeze(session, ob, storage_date=storage_date):
                    affected_dates.add(storage_date)
            elif st == "SUGGEST_MORE_OPTIONS":
                await handle_suggest_more(ob, storage_date=storage_date)
            elif st == "ADD_NEW_OPTION_TO_DB":
                handle_add_new(session, ob)
            elif st == "SKIP_MEAL":
                if await handle_skip_meal(session, ob, storage_date=storage_date):
                    affected_dates.add(storage_date)
            elif st == "COOK_NOT_COMING":
                touched = await handle_cook_not_coming(session, group_id=group_id)
                affected_dates.update(touched)

    if affected_dates:
        from meal_planning.orchestration import on_daily_choices_updated

        for cal_date in sorted(affected_dates):
            await on_daily_choices_updated(storage_date=cal_date)

    if isinstance(reply_text, str) and reply_text.strip():
        send_food_group_message(reply_text.strip())
