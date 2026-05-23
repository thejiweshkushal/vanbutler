"""Parse and apply secondary LLM output for cook absence / corrections."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Any

from llm.llm_service import analyze_cook_absence_raw
from meal_planning.dates import today_calendar_date_in_ist
from meal_planning.query import (
    clear_cook_absent_choice,
    fetch_daily_choices_for_dates,
    format_daily_choices_context,
    upsert_daily_choice,
)
from models import ALLOWED_MEAL_SLOTS, COOK_ABSENT_MEAL_ID
from sqlalchemy.orm import Session

from .conversation import build_cook_absence_snippet

log = logging.getLogger(__name__)

COOK_PARSE_MAX_ATTEMPTS = int(os.environ.get("COOK_PARSE_MAX_ATTEMPTS", "3"))

_ALLOWED_SLOTS = frozenset(ALLOWED_MEAL_SLOTS)
_ALLOWED_ACTIONS = frozenset({"set_cook_absent", "clear"})


def _strip_json_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, count=1, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()


def _expand_slot(slot: str) -> list[str]:
    s = slot.strip().lower()
    if s == "all":
        return ["breakfast", "lunch", "dinner"]
    if s in _ALLOWED_SLOTS:
        return [s]
    return []


def _parse_and_validate_cook_absence_json(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_json_fences(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    updates = data.get("updates")
    if updates is None:
        updates = []
    if not isinstance(updates, list):
        return None
    for ob in updates:
        if not isinstance(ob, dict):
            return None
        action = ob.get("action")
        if action not in _ALLOWED_ACTIONS:
            return None
        slot_raw = ob.get("slot")
        if not isinstance(slot_raw, str) or not _expand_slot(slot_raw):
            return None
        cal = ob.get("calendar_date")
        if not isinstance(cal, str) or not cal.strip():
            return None
        try:
            date.fromisoformat(cal.strip())
        except ValueError:
            return None
    conf = data.get("confirmation_text")
    if conf is not None and not isinstance(conf, str):
        return None
    return data


def _cook_context_dates(today_ist: date, *, days_ahead: int = 13) -> list[date]:
    return [today_ist + timedelta(days=i) for i in range(0, days_ahead + 1)]


def _apply_cook_updates(session: Session, updates: list[dict[str, Any]]) -> set[date]:
    touched: set[date] = set()
    for ob in updates:
        if not isinstance(ob, dict):
            continue
        action = ob.get("action")
        cal_raw = ob.get("calendar_date")
        slot_raw = ob.get("slot")
        if not isinstance(cal_raw, str) or not isinstance(slot_raw, str):
            continue
        try:
            cal = date.fromisoformat(cal_raw.strip())
        except ValueError:
            continue
        for slot in _expand_slot(slot_raw):
            touched.add(cal)
            if action == "set_cook_absent":
                upsert_daily_choice(
                    session, cal, slot, COOK_ABSENT_MEAL_ID
                )
            elif action == "clear":
                clear_cook_absent_choice(session, cal, slot)
    return touched


async def resolve_cook_absence(
    session: Session,
    *,
    group_id: str,
) -> tuple[set[date], str | None]:
    """
    Run secondary LLM and apply updates.

    Returns ``(touched_dates, confirmation_text)``.
    """
    snippet = build_cook_absence_snippet(session, group_id=group_id)
    if not snippet.strip():
        log.info("resolve_cook_absence: empty snippet")
        return (set(), None)

    today_ist = today_calendar_date_in_ist()
    context_dates = _cook_context_dates(today_ist)
    choices_by_date = fetch_daily_choices_for_dates(session, context_dates)
    choices_ctx = format_daily_choices_context(session, choices_by_date)

    parsed: dict[str, Any] | None = None
    for attempt in range(1, COOK_PARSE_MAX_ATTEMPTS + 1):
        try:
            raw = await analyze_cook_absence_raw(
                snippet,
                today_ist_iso=today_ist.isoformat(),
                daily_choices_context=choices_ctx,
                attempt=attempt,
            )
        except Exception:
            log.exception("analyze_cook_absence_raw failed on attempt %d", attempt)
            raise
        parsed = _parse_and_validate_cook_absence_json(raw)
        if parsed is not None:
            break
        log.warning(
            "Cook absence JSON parse failed (attempt %d/%d)",
            attempt,
            COOK_PARSE_MAX_ATTEMPTS,
        )

    if parsed is None:
        return (set(), None)

    updates = parsed.get("updates") or []
    if not isinstance(updates, list):
        updates = []
    touched = _apply_cook_updates(session, updates)
    conf = parsed.get("confirmation_text")
    confirmation = conf.strip() if isinstance(conf, str) and conf.strip() else None
    return (touched, confirmation)
