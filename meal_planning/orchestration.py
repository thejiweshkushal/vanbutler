"""Daily evening trigger and post-freeze meal-planning orchestration."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from database import get_session
from meal_planning.dates import tomorrow_calendar_date_in_ist
from meal_planning.meal_options import run_slot_options
from meal_planning.query import (
    fetch_daily_choices_by_date,
    fetch_first_meal_prep_by_meal_id,
    fetch_meals_by_ids,
    upsert_daily_choice,
)
from models import (
    COOK_ABSENT_MEAL_ID,
    COOK_HOLIDAY_MEAL_ID,
    is_no_cook_meal_id,
    is_system_meal_id,
)
from config import get_cook_nickname
from messages_service.helpers import (
    send_menu_already_frozen_notice,
    send_slot_decision_intro,
    send_sunday_cook_holiday_message_async,
    send_tomorrow_menu_summary,
    send_will_share_slot_options,
)
SLOT_ORDER: tuple[str, ...] = ("breakfast", "lunch", "dinner")


def _weekday_name(calendar_date: date) -> str:
    return calendar_date.strftime("%A")


def _is_nonempty_prep_field(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _frozen_slots(choices: dict[str, int]) -> set[str]:
    return {s for s in SLOT_ORDER if s in choices}


def _first_unfrozen_slot(frozen: set[str]) -> str | None:
    for slot in SLOT_ORDER:
        if slot not in frozen:
            return slot
    return None


def _all_slots_frozen(frozen: set[str]) -> bool:
    return frozen >= set(SLOT_ORDER)


def _all_slots_no_cook(meal_ids: list[int]) -> bool:
    return len(meal_ids) == 3 and all(is_no_cook_meal_id(mid) for mid in meal_ids)


def _no_cook_summary_line(calendar_date: date, meal_ids: list[int]) -> str:
    cook = get_cook_nickname()
    weekday = _weekday_name(calendar_date)
    if all(mid == COOK_HOLIDAY_MEAL_ID for mid in meal_ids):
        return f"Tomorrow is {weekday} — {cook} is off, so I shan't plan meals."
    if all(mid == COOK_ABSENT_MEAL_ID for mid in meal_ids):
        return (
            f"Tomorrow ({weekday}) is cook-absent for all three meals — no prep needed."
        )
    return f"Tomorrow ({weekday}) has no cook for any meal — no prep needed."


def format_tomorrow_menu_summary(session: Session, calendar_date: date) -> str:
    """Plain-text menu + optional Preparations / Ingredients sections."""
    choices = fetch_daily_choices_by_date(session, calendar_date)
    if not _all_slots_frozen(set(choices.keys())):
        raise ValueError(
            f"Cannot format menu summary: not all slots frozen for {calendar_date}"
        )

    meal_ids = [choices[slot] for slot in SLOT_ORDER]
    if _all_slots_no_cook(meal_ids):
        return _no_cook_summary_line(calendar_date, meal_ids)

    id_to_name = {mid: name for mid, name in fetch_meals_by_ids(session, meal_ids)}
    prep_meal_ids = [mid for mid in meal_ids if not is_system_meal_id(mid)]
    prep_by_meal = fetch_first_meal_prep_by_meal_id(session, prep_meal_ids)

    lines: list[str] = [
        f"Tomorrow's menu ({_weekday_name(calendar_date)}):",
        "",
    ]
    for slot in SLOT_ORDER:
        meal_id = choices[slot]
        name = id_to_name.get(meal_id, f"meal #{meal_id}")
        lines.append(f"{slot.title()}: {name}")

    prep_lines: list[str] = []
    ingredient_lines: list[str] = []
    for slot in SLOT_ORDER:
        meal_id = choices[slot]
        pre_prep, ingredients = prep_by_meal.get(meal_id, (None, None))
        if _is_nonempty_prep_field(pre_prep):
            prep_lines.append(f"{slot.title()}: {pre_prep.strip()}")
        if _is_nonempty_prep_field(ingredients):
            ingredient_lines.append(f"{slot.title()}: {ingredients.strip()}")

    if prep_lines:
        lines.extend(["", "Preparations:", *prep_lines])
    if ingredient_lines:
        lines.extend(["", "Ingredients:", *ingredient_lines])

    return "\n".join(lines)


async def _orchestrate_evening_after_greeting(*, storage_date: date) -> dict:
    with get_session() as session:
        choices = fetch_daily_choices_by_date(session, storage_date)

    if storage_date.weekday() == 6 and not choices:
        with get_session() as session:
            for slot in SLOT_ORDER:
                upsert_daily_choice(
                    session, storage_date, slot, COOK_HOLIDAY_MEAL_ID
                )
        await send_sunday_cook_holiday_message_async(storage_date)
        return {
            "storage_date": storage_date.isoformat(),
            "outcome": "sunday_holiday_assumed",
        }

    frozen = _frozen_slots(choices)

    if _all_slots_frozen(frozen):
        send_menu_already_frozen_notice()
        return {
            "storage_date": storage_date.isoformat(),
            "outcome": "menu_already_frozen",
        }

    slot = _first_unfrozen_slot(frozen)
    assert slot is not None

    if slot in ("lunch", "dinner"):
        send_slot_decision_intro(slot)

    result = await run_slot_options(slot, storage_date=storage_date)
    return {
        "storage_date": storage_date.isoformat(),
        "slot": slot,
        "slot_options": result,
        "outcome": "options_sent",
    }


async def _orchestrate_after_choice(*, storage_date: date) -> dict:
    with get_session() as session:
        choices = fetch_daily_choices_by_date(session, storage_date)

    frozen = _frozen_slots(choices)
    next_slot = _first_unfrozen_slot(frozen)

    if _all_slots_frozen(frozen):
        with get_session() as session:
            summary = format_tomorrow_menu_summary(session, storage_date)
        send_tomorrow_menu_summary(summary)
        return {
            "storage_date": storage_date.isoformat(),
            "outcome": "menu_summary_sent",
        }

    slot = next_slot
    assert slot is not None

    send_will_share_slot_options(slot)
    result = await run_slot_options(slot, storage_date=storage_date)
    return {
        "storage_date": storage_date.isoformat(),
        "slot": slot,
        "slot_options": result,
        "outcome": "options_sent",
    }


async def run_daily_evening_trigger(*, storage_date: date | None = None) -> dict:
    """Send trivia greeting, then advance the first unfrozen slot for tomorrow."""
    if storage_date is None:
        storage_date = tomorrow_calendar_date_in_ist()

    from trivia.matching import send_random_unsent_trivia_greeting_async

    greeting_result = await send_random_unsent_trivia_greeting_async()
    orchestration_result = await _orchestrate_evening_after_greeting(
        storage_date=storage_date
    )
    return {
        "storage_date": storage_date.isoformat(),
        "greeting": greeting_result,
        "orchestration": orchestration_result,
    }


async def on_daily_choices_updated(*, storage_date: date) -> dict:
    """After a freeze: send menu summary or offer options for the next unfrozen slot."""
    return await _orchestrate_after_choice(storage_date=storage_date)
