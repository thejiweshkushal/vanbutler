import asyncio
import os

import requests
from dotenv import load_dotenv
from sqlalchemy.exc import IntegrityError

load_dotenv()

from database import get_session
from .message_service import (
    delete_message_row_by_pk,
    parse_whapi_message_id_from_send_response,
    parse_status_from_send_response,
)
from models import Message

_STATUS_METADATA_MAX = 8000

NO_VIABLE_MEAL_OPTIONS_MESSAGE = (
    "I'm really sorry, but there are no more options I have in mind. "
    "Might you suggest what you want to have tomorrow? I'll work on adding more options to my inventory."
)

INTENT_PARSE_FAILURE_MESSAGE = (
    "I didn't quite catch that—might you rephrase, ever so briefly, what you'd like for tomorrow?"
)


def send_intent_parse_failure_notice() -> dict:
    """When the LLM returns unusable JSON after all parse retries."""
    return send_food_group_message(INTENT_PARSE_FAILURE_MESSAGE)


def _weekday_name(calendar_date) -> str:
    if hasattr(calendar_date, "strftime"):
        return calendar_date.strftime("%A")
    return str(calendar_date)


def send_skip_confirmation(slots: list[str], calendar_date) -> dict:
    """Confirm a slot is set to None (not eating that meal)."""
    when = _weekday_name(calendar_date)
    slots_s = ", ".join(s.strip().title() for s in slots if isinstance(s, str) and s.strip())
    text = (
        f"I've set {slots_s} for {when} to None — I shan't offer options for "
        f"{'that slot' if len(slots) == 1 else 'those slots'}."
    )
    return send_food_group_message(text)


def send_freeze_confirmation(meal_display_name: str, slot: str, calendar_date) -> dict:
    """Confirm a locked choice for tomorrow (slot title-cased in prose)."""
    slot_pretty = slot.strip().title() if isinstance(slot, str) else str(slot)
    when = _weekday_name(calendar_date)
    text = (
        f"I've set {slot_pretty} for {when} to {meal_display_name}. "
        "Unless you tell me otherwise, I shall assume it stands."
    )
    return send_food_group_message(text)


def send_ambiguous_match(query: str, candidate_names: list[str]) -> dict:
    """Ask which meal they meant when several DB names score similarly."""
    lines = [f"I found a few possibilities for {query!r}:"]
    for i, name in enumerate(candidate_names[:3], start=1):
        lines.append(f"{i}. {name}")
    lines.append("Which did you have in mind, if any?")
    return send_food_group_message("\n".join(lines))


def send_meal_not_found(meal_name: str) -> dict:
    """Offer to add a meal missing from the database."""
    text = (
        f"I'm afraid {meal_name!r} is not in my menu yet. "
        "Would you like me to add it? Kindly specify which slots you would like it for "
        "(breakfast, lunch, dinner), and whether any ingredients or preprocessing checks apply."
    )
    return send_food_group_message(text)


def _format_slots_choice_prose(slots: list[str]) -> str:
    """Pick-one slot phrasing, e.g. ``['lunch', 'dinner']`` → ``'Lunch or Dinner'``."""
    labels = [s.strip().title() for s in slots if isinstance(s, str) and s.strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


def send_meal_added_confirmation(meal_name: str, slots: list[str]) -> dict:
    """Confirm a new meal was written to the database."""
    slots_s = ", ".join(s.title() for s in slots)
    text = (
        f"Done — {meal_name} is now on file for {slots_s}. "
        "I shall remember the ingredients and prep notes you gave me."
    )
    return send_food_group_message(text)


def send_meal_added_freeze_prompt(meal_name: str, slots: list[str]) -> dict:
    """After add confirmation, ask whether to lock the meal for those slots tomorrow."""
    slots_s = _format_slots_choice_prose(slots)
    text = f"Do you want to have {meal_name} for {slots_s}?"
    return send_food_group_message(text)


def send_meal_already_exists(meal_name: str) -> dict:
    text = (
        f"{meal_name} is already in my book under that name. "
        "If you meant a different dish, do tell me the exact wording you prefer."
    )
    return send_food_group_message(text)


def send_slot_clarification(meal_display_name: str) -> dict:
    """When the meal matches but breakfast/lunch/dinner is ambiguous."""
    text = (
        f"I have {meal_display_name} in mind, but I could not tell which slot you meant "
        "for tomorrow. Might you say breakfast, lunch, or dinner?"
    )
    return send_food_group_message(text)


def send_no_viable_meal_options_notice() -> dict:
    """Send the fixed apology when there are no meals left for this slot (after exclusions)."""
    return send_food_group_message(NO_VIABLE_MEAL_OPTIONS_MESSAGE)


def send_menu_already_frozen_notice() -> dict:
    """Evening trigger when tomorrow's menu is already fully locked."""
    return send_food_group_message(
        "It seems the menu for tomorrow has already been frozen."
    )


def send_slot_decision_intro(slot: str) -> dict:
    """Evening transition before options for lunch or dinner."""
    s = slot.strip().lower()
    if s == "lunch":
        text = "We'll decide the lunch menu now."
    elif s == "dinner":
        text = "We'll decide the dinner menu now."
    else:
        raise ValueError(f"No evening intro for slot {slot!r}")
    return send_food_group_message(text)


def send_will_share_slot_options(slot: str) -> dict:
    """Post-freeze transition before sending options for the next unfrozen slot."""
    slot_pretty = slot.strip().title() if isinstance(slot, str) else str(slot)
    return send_food_group_message(f"I'll share {slot_pretty} options now.")


def send_tomorrow_menu_summary(text: str) -> dict:
    """Send the formatted tomorrow menu summary (meals + optional prep/ingredients)."""
    return send_food_group_message(text)


async def send_trivia_greeting_async(trivia: str) -> dict:
    """Take trivia text, generate a Van greeting via LLM, and post to the food group."""
    from llm.llm_service import generate_trivia_greeting

    text = (await generate_trivia_greeting(trivia)).strip()
    if not text:
        raise RuntimeError("LLM returned empty trivia greeting")
    return send_food_group_message(text)


async def send_sunday_cook_holiday_message_async(calendar_date) -> dict:
    """Generate and send Van's Sunday cook-holiday assumption message."""
    from llm.llm_service import generate_sunday_cook_holiday_message

    weekday_str = _weekday_name(calendar_date)
    text = (await generate_sunday_cook_holiday_message(weekday_str)).strip()
    if not text:
        raise RuntimeError("LLM returned empty Sunday cook-holiday message")
    return send_food_group_message(text)


def send_trivia_greeting(trivia: str) -> dict:
    """Sync wrapper for ``send_trivia_greeting_async`` (CLI / non-async callers)."""
    return asyncio.run(send_trivia_greeting_async(trivia))


def _mark_queued_failed(pk: int, reason: str) -> None:
    with get_session() as session:
        row = session.get(Message, pk)
        if row:
            row.status = "failed"
            row.status_metadata = reason[:_STATUS_METADATA_MAX]


def send_food_group_message(text: str) -> dict:
    """Send plain text to FOOD_GROUP_ID via Whapi; persist queued/sent/failed in ``messages``."""
    base = os.environ["WHAPI_URL"].rstrip("/")
    token = os.environ["WHAPI_TOKEN"]
    group_id = os.environ["FOOD_GROUP_ID"]

    with get_session() as session:
        row = Message(
            whapi_message_id=None,
            from_wa=None,
            from_name=None,
            message_at=None,
            direction="outbound",
            message=text,
            status="queued",
            chat_id=group_id,
            wa_type="text",
            status_metadata=None,
        )
        session.add(row)
        session.flush()
        pk = row.id

    try:
        r = requests.post(
            f"{base}/messages/text",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
            },
            json={"to": group_id, "body": text},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        _mark_queued_failed(pk, str(exc))
        raise

    whapi_id = parse_whapi_message_id_from_send_response(data)
    status = parse_status_from_send_response(data)
    if not whapi_id:
        _mark_queued_failed(pk, "Whapi send response missing message id")
        raise RuntimeError("Whapi send response missing message id")

    try:
        with get_session() as session:
            row = session.get(Message, pk)
            if row:
                row.whapi_message_id = whapi_id
                row.status = status
    except IntegrityError:
        with get_session() as session:
            delete_message_row_by_pk(session, pk)

    return data
