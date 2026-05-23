"""Insert seed rows into meals and meal_prep.

For each seed row, if a meal with the same name (exact match, stripped) already
exists, that row is skipped; otherwise one meal and one meal_prep row are inserted.

Run from repo root with venv active and DATABASE_URL set:

  python -m scripts.seed_meals
  python scripts/seed_meals.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# `python scripts/seed_meals.py` puts scripts/ on sys.path, not the project root.
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError

load_dotenv()

from database import SessionLocal, engine, get_session  # noqa: E402
from models import Meal, MealPrep, assert_valid_meal_slot_values  # noqa: E402

_SEED_ROWS: list[dict[str, Any]] = [
    {
        "name": "Salad",
        "slots": ["breakfast"],
        "pre_prep": "",
        "ingredients": "lettuce, red cabbage, mayo, sesame dressing, cucumbers, carrots",
    },
    {
        "name": "Palak Dal + Roti/Rice",
        "slots": ["lunch", "dinner"],
        "pre_prep": "soak chhole",
        "ingredients": "spinach",
    },
    {
        "name": "Moong Chhila",
        "slots": ["breakfast"],
        "pre_prep": "soak moong + rice",
        "ingredients": "",
    },
]


def _fail(msg: str, *, code: int = 1) -> None:
    print(f"[seed_meals] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _require_database_url() -> None:
    raw = os.environ.get("DATABASE_URL")
    if raw is None or not str(raw).strip():
        _fail(
            "DATABASE_URL is missing or empty. Set it in .env or the environment "
            "so the script can connect to Neon/Postgres."
        )


def _require_engine() -> None:
    if engine is None or SessionLocal is None:
        _fail(
            "SQLAlchemy engine was not created (DATABASE_URL was unset when "
            "database.py was imported). Fix DATABASE_URL and re-run this script "
            "in a fresh process."
        )


def _optional_text_field(
    val: object, *, field: str, row_index: int
) -> str | None:
    """Return stripped text, or None if missing/blank. Non-strings raise."""
    if val is None:
        return None
    if not isinstance(val, str):
        raise ValueError(
            f"Seed row {row_index}: {field!r} must be a string or null/omitted, "
            f"got {type(val).__name__}"
        )
    s = val.strip()
    return s if s else None


def _normalize_slots(slots: object, row_index: int) -> list[str]:
    if not isinstance(slots, list) or len(slots) == 0:
        raise ValueError(
            f"Seed row {row_index}: 'slots' must be a non-empty list of strings; "
            f"got {slots!r}"
        )
    out: list[str] = []
    for j, raw in enumerate(slots):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(
                f"Seed row {row_index}, slot #{j}: each slot must be a non-empty string; "
                f"got {raw!r}"
            )
        out.append(raw.strip().lower())
    assert_valid_meal_slot_values(out)
    return out


def _validate_seed_row(row: dict[str, Any], index: int) -> None:
    name = row.get("name")
    if name is None or not str(name).strip():
        raise ValueError(
            f"Seed row {index}: field 'name' must be a non-empty string; got {name!r}"
        )
    for key in ("pre_prep", "ingredients"):
        if key in row and row[key] is not None and not isinstance(row[key], str):
            raise ValueError(
                f"Seed row {index}: field {key!r} must be a string or null/omitted, "
                f"got {type(row[key]).__name__}"
            )
    _normalize_slots(row.get("slots"), index)


def main() -> None:
    print("[seed_meals] Starting seed script.")
    _require_database_url()
    _require_engine()

    try:
        for i, row in enumerate(_SEED_ROWS):
            _validate_seed_row(row, i)
    except ValueError as e:
        _fail(str(e))

    inserted = 0
    skipped = 0
    try:
        with get_session() as session:
            print(
                f"[seed_meals] Processing {len(_SEED_ROWS)} seed row(s) "
                "(skip when meal name already exists)."
            )
            for i, row in enumerate(_SEED_ROWS):
                name = str(row["name"]).strip()
                existing = session.scalar(
                    select(Meal).where(Meal.name == name).limit(1)
                )
                if existing is not None:
                    skipped += 1
                    print(
                        f"[seed_meals]   Skip #{i + 1}: name={name!r} "
                        f"(meal id={existing.id} already in database)."
                    )
                    continue

                slots = _normalize_slots(row["slots"], i)
                meal = Meal(name=name, slot=slots)
                session.add(meal)
                session.flush()
                prep = MealPrep(
                    meal_id=meal.id,
                    pre_prep=_optional_text_field(
                        row.get("pre_prep"), field="pre_prep", row_index=i
                    ),
                    ingredients=_optional_text_field(
                        row.get("ingredients"), field="ingredients", row_index=i
                    ),
                )
                session.add(prep)
                session.flush()
                inserted += 1
                pp = prep.pre_prep
                pp_show = (pp[:57] + "...") if pp and len(pp) > 60 else pp
                print(
                    f"[seed_meals]   Inserted #{i + 1}: meal id={meal.id} name={name!r} "
                    f"slots={meal.slot!r}; prep pre_prep={pp_show!r} "
                    f"ingredients={prep.ingredients!r}"
                )

            if len(_SEED_ROWS) == 0:
                pass
            elif inserted == 0:
                print(
                    "[seed_meals] No new rows inserted "
                    "(each seed name already matched a meal)."
                )
            else:
                print(
                    f"[seed_meals] Inserted {inserted} meal(s) with prep; "
                    f"skipped {skipped} (name already existed)."
                )

    except (OperationalError, ProgrammingError) as e:
        raise RuntimeError(
            "Database error while seeding. If tables are missing, run "
            "`alembic upgrade head` from the repo root first (includes migration "
            "20260429_0002 for JSONB columns). "
            f"Original error: {e}"
        ) from e

    print("[seed_meals] Transaction committed.")

    if inserted > 0:
        print("[seed_meals] Done (seed applied).")
    else:
        print("[seed_meals] Done (no inserts).")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"[seed_meals] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
