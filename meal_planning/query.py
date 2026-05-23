"""Parameterized SQL for meal planning. Prefer these helpers over ad hoc ORM query builders."""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from models import (
    ALLOWED_MEAL_SLOTS,
    Meal,
    MealPrep,
    NO_COOK_MEAL_IDS,
    assert_valid_meal_slot_values,
)


def fetch_meal_ids_for_slot(session: Session, slot: str) -> list[int]:
    """Return ``meals.id`` for rows whose ``slot`` JSONB array contains ``slot``."""
    slot_json = json.dumps([slot])
    rows = session.execute(
        text(
            """
            SELECT m.id
            FROM meals AS m
            WHERE m.slot @> CAST(:slot_json AS jsonb)
              AND m.id > 0
            ORDER BY m.id
            """
        ),
        {"slot_json": slot_json},
    ).fetchall()
    return [int(r[0]) for r in rows]


def _meal_ids_cell_to_ints(raw: object) -> list[int]:
    """Normalize a ``meal_ids`` JSONB cell to a list of ints (empty if unusable)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[int] = []
        for x in raw:
            if isinstance(x, bool):
                continue
            try:
                out.append(int(x))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return [int(x) for x in json.loads(raw)]
    return []


def fetch_daily_option_meal_ids(
    session: Session, calendar_date: date, slot: str
) -> list[int]:
    """``meal_ids`` from the ``daily_options`` row for this date and slot (``ORDER BY id LIMIT 1``)."""
    row = session.execute(
        text(
            """
            SELECT meal_ids
            FROM daily_options
            WHERE date = :calendar_date AND slot = :slot
            ORDER BY id
            LIMIT 1
            """
        ),
        {"calendar_date": calendar_date, "slot": slot},
    ).fetchone()
    if row is None:
        return []
    return _meal_ids_cell_to_ints(row[0])


def fetch_meal_slot_array(session: Session, meal_id: int) -> list[str]:
    """``meals.slot`` JSONB array for one meal id (empty if missing)."""
    row = session.execute(
        text(
            """
            SELECT slot
            FROM meals
            WHERE id = :meal_id
            """
        ),
        {"meal_id": meal_id},
    ).fetchone()
    if row is None or row[0] is None:
        return []
    raw = row[0]
    if isinstance(raw, list):
        return [str(x).strip().lower() for x in raw if isinstance(x, str) and x.strip()]
    if isinstance(raw, str):
        return [x.strip().lower() for x in json.loads(raw) if str(x).strip()]
    return []


def fetch_meals_by_ids(session: Session, meal_ids: list[int]) -> list[tuple[int, str]]:
    """Return ``(id, name)`` for each meal id (order not guaranteed)."""
    if not meal_ids:
        return []
    stmt = text(
        """
        SELECT m.id, m.name
        FROM meals AS m
        WHERE m.id IN :ids
        """
    ).bindparams(bindparam("ids", expanding=True))
    rows = session.execute(stmt, {"ids": meal_ids}).fetchall()
    return [(int(r[0]), r[1]) for r in rows]


def fetch_first_meal_prep_by_meal_id(
    session: Session, meal_ids: list[int]
) -> dict[int, tuple[str | None, str | None]]:
    """Map ``meal_id`` -> ``(pre_prep, ingredients)`` using the lowest ``meal_prep.id`` per meal."""
    if not meal_ids:
        return {}
    stmt = text(
        """
        SELECT DISTINCT ON (mp.meal_id)
            mp.meal_id,
            mp.pre_prep,
            mp.ingredients
        FROM meal_prep AS mp
        WHERE mp.meal_id IN :ids
        ORDER BY mp.meal_id, mp.id
        """
    ).bindparams(bindparam("ids", expanding=True))
    rows = session.execute(stmt, {"ids": meal_ids}).fetchall()
    return {int(r[0]): (r[1], r[2]) for r in rows}


def fetch_daily_option_first_id(
    session: Session, calendar_date: date, slot: str
) -> int | None:
    """Primary key of the first ``daily_options`` row for date + slot, if any."""
    row = session.execute(
        text(
            """
            SELECT id
            FROM daily_options
            WHERE date = :calendar_date AND slot = :slot
            ORDER BY id
            LIMIT 1
            """
        ),
        {"calendar_date": calendar_date, "slot": slot},
    ).fetchone()
    return int(row[0]) if row else None


def update_daily_options_meal_ids(session: Session, row_id: int, meal_ids: list[int]) -> None:
    """Set ``meal_ids`` JSONB for a single ``daily_options`` row."""
    session.execute(
        text(
            """
            UPDATE daily_options
            SET meal_ids = CAST(:meal_ids AS jsonb)
            WHERE id = :row_id
            """
        ),
        {"row_id": row_id, "meal_ids": json.dumps(meal_ids)},
    )


def insert_daily_options_row(
    session: Session, calendar_date: date, slot: str, meal_ids: list[int]
) -> None:
    """Insert one ``daily_options`` row."""
    session.execute(
        text(
            """
            INSERT INTO daily_options (date, slot, meal_ids)
            VALUES (:calendar_date, :slot, CAST(:meal_ids AS jsonb))
            """
        ),
        {
            "calendar_date": calendar_date,
            "slot": slot,
            "meal_ids": json.dumps(meal_ids),
        },
    )


def fetch_all_meal_names(session: Session) -> list[tuple[int, str]]:
    """All ``(id, name)`` from ``meals``, ordered by id."""
    rows = session.execute(
        text(
            """
            SELECT m.id, m.name
            FROM meals AS m
            WHERE m.id > 0
            ORDER BY m.id
            """
        ),
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def fetch_daily_choices_by_date(
    session: Session, calendar_date: date
) -> dict[str, int]:
    """Map ``slot`` -> ``meal_id`` for ``daily_choices`` on ``calendar_date`` (allowed slots only)."""
    rows = session.execute(
        text(
            """
            SELECT slot, meal_id
            FROM daily_choices
            WHERE date = :calendar_date
            """
        ),
        {"calendar_date": calendar_date},
    ).fetchall()
    out: dict[str, int] = {}
    for slot_raw, meal_id_raw in rows:
        slot = str(slot_raw).strip().lower()
        if slot in ALLOWED_MEAL_SLOTS:
            out[slot] = int(meal_id_raw)
    return out


def fetch_random_unsent_trivia_id(session: Session) -> int:
    """Random unsent trivia id, or oldest row if all have been sent. Raises if table empty."""
    row = session.execute(
        text(
            """
            SELECT id
            FROM trivia
            WHERE last_sent_on IS NULL
            ORDER BY random()
            LIMIT 1
            """
        ),
    ).fetchone()
    if row is not None:
        return int(row[0])

    row = session.execute(
        text(
            """
            SELECT id
            FROM trivia
            ORDER BY created_at ASC NULLS LAST, id ASC
            LIMIT 1
            """
        ),
    ).fetchone()
    if row is None:
        raise LookupError("No trivia rows in database")
    return int(row[0])


def upsert_daily_choice(
    session: Session, calendar_date: date, slot: str, meal_id: int
) -> None:
    """Insert or replace the chosen meal for ``date`` + ``slot`` (``uq_daily_choices_date_slot``)."""
    session.execute(
        text(
            """
            INSERT INTO daily_choices (date, slot, meal_id)
            VALUES (:calendar_date, :slot, :meal_id)
            ON CONFLICT (date, slot)
            DO UPDATE SET meal_id = EXCLUDED.meal_id
            """
        ),
        {"calendar_date": calendar_date, "slot": slot, "meal_id": meal_id},
    )


def clear_daily_choice(session: Session, calendar_date: date, slot: str) -> bool:
    """Delete the ``daily_choices`` row for ``date`` + ``slot``. Returns True if a row was removed."""
    result = session.execute(
        text(
            """
            DELETE FROM daily_choices
            WHERE date = :calendar_date AND slot = :slot
            """
        ),
        {"calendar_date": calendar_date, "slot": slot},
    )
    return bool(result.rowcount)


def clear_cook_absent_choice(session: Session, calendar_date: date, slot: str) -> bool:
    """Delete only when the row is cook-absent or cook-holiday (``meal_id`` in ``{-1, -2}``)."""
    no_cook_ids = list(NO_COOK_MEAL_IDS)
    result = session.execute(
        text(
            """
            DELETE FROM daily_choices
            WHERE date = :calendar_date
              AND slot = :slot
              AND meal_id IN :no_cook_ids
            """
        ).bindparams(bindparam("no_cook_ids", expanding=True)),
        {
            "calendar_date": calendar_date,
            "slot": slot,
            "no_cook_ids": no_cook_ids,
        },
    )
    return bool(result.rowcount)


def fetch_daily_choices_for_dates(
    session: Session, calendar_dates: list[date]
) -> dict[date, dict[str, int]]:
    """Map ``calendar_date`` -> ``slot`` -> ``meal_id`` for the given dates."""
    if not calendar_dates:
        return {}
    stmt = text(
        """
        SELECT date, slot, meal_id
        FROM daily_choices
        WHERE date IN :dates
        """
    ).bindparams(bindparam("dates", expanding=True))
    rows = session.execute(stmt, {"dates": calendar_dates}).fetchall()
    out: dict[date, dict[str, int]] = {d: {} for d in calendar_dates}
    for date_raw, slot_raw, meal_id_raw in rows:
        d = date_raw if isinstance(date_raw, date) else date.fromisoformat(str(date_raw))
        slot = str(slot_raw).strip().lower()
        if slot in ALLOWED_MEAL_SLOTS and d in out:
            out[d][slot] = int(meal_id_raw)
    return out


def format_daily_choices_context(
    session: Session, choices_by_date: dict[date, dict[str, int]]
) -> str:
    """Plain-text context for cook-absence LLM: date -> slot -> meal name."""
    if not choices_by_date:
        return "(no daily choices for referenced dates)"
    all_ids: list[int] = []
    for slot_map in choices_by_date.values():
        all_ids.extend(slot_map.values())
    id_to_name = {mid: name for mid, name in fetch_meals_by_ids(session, list(set(all_ids)))}
    lines: list[str] = []
    for d in sorted(choices_by_date.keys()):
        slot_map = choices_by_date[d]
        if not slot_map:
            continue
        lines.append(f"{d.isoformat()}:")
        for slot in ("breakfast", "lunch", "dinner"):
            if slot in slot_map:
                mid = slot_map[slot]
                name = id_to_name.get(mid, f"meal #{mid}")
                lines.append(f"  {slot}: {name} (meal_id={mid})")
    return "\n".join(lines) if lines else "(no daily choices for referenced dates)"


def fetch_slots_for_meal_id_in_daily_options(
    session: Session, calendar_date: date, meal_id: int
) -> list[str]:
    """Distinct ``daily_options.slot`` where ``meal_ids`` JSONB array contains ``meal_id``."""
    meal_json = json.dumps([meal_id])
    rows = session.execute(
        text(
            """
            SELECT DISTINCT slot
            FROM daily_options
            WHERE date = :calendar_date
              AND meal_ids IS NOT NULL
              AND meal_ids @> CAST(:meal_json AS jsonb)
            ORDER BY slot
            """
        ),
        {"calendar_date": calendar_date, "meal_json": meal_json},
    ).fetchall()
    return [str(r[0]) for r in rows]


def insert_meal_with_prep(
    session: Session,
    name: str,
    slots: list[str],
    ingredients_csv: str | None,
    pre_prep_csv: str | None,
) -> int:
    """Insert one ``meals`` row and one ``meal_prep`` row; return new ``meals.id``."""
    assert_valid_meal_slot_values(slots)
    meal = Meal(name=name.strip(), slot=list(slots))
    session.add(meal)
    session.flush()
    prep = MealPrep(
        meal_id=meal.id,
        pre_prep=pre_prep_csv,
        ingredients=ingredients_csv,
    )
    session.add(prep)
    session.flush()
    return int(meal.id)
