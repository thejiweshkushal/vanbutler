"""Import meals from meal_planning/vanbutler - meals_.csv (direct insert, no duplicate checks).

Meal Time maps to meals.slot: Breakfast -> ["breakfast"]; Lunch or Dinner -> ["lunch", "dinner"].
Status is ignored. Dish Name -> meals.name; pre_processing / ingredients_check -> meal_prep.

Run from repo root with venv active and DATABASE_URL set:

  python -m scripts.import_vanbutler_meals
  python scripts/import_vanbutler_meals.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from sqlalchemy.exc import OperationalError, ProgrammingError

load_dotenv()

from database import SessionLocal, engine, get_session  # noqa: E402
from models import Meal, MealPrep, assert_valid_meal_slot_values  # noqa: E402

_CSV_PATH = _repo_root / "meal_planning" / "vanbutler - meals_.csv"


def _fail(msg: str, *, code: int = 1) -> None:
    print(f"[import_vanbutler_meals] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _require_database_url() -> None:
    if not os.environ.get("DATABASE_URL", "").strip():
        _fail("DATABASE_URL is missing or empty.")
    if engine is None or SessionLocal is None:
        _fail("SQLAlchemy engine was not created. Fix DATABASE_URL and re-run.")


def _optional_text(val: object) -> str | None:
    if val is None:
        return None
    if not isinstance(val, str):
        raise TypeError(f"Expected str or None, got {type(val).__name__}")
    s = val.strip()
    return s if s else None


def _slots_for_meal_time(meal_time: str) -> list[str]:
    key = meal_time.strip().lower()
    if key == "breakfast":
        slots = ["breakfast"]
    elif key in ("lunch", "dinner"):
        slots = ["lunch", "dinner"]
    else:
        raise ValueError(f"Unknown Meal Time {meal_time!r}")
    assert_valid_meal_slot_values(slots)
    return slots


def _load_csv_rows() -> list[dict[str, Any]]:
    if not _CSV_PATH.is_file():
        _fail(f"CSV not found: {_CSV_PATH}")
    rows: list[dict[str, Any]] = []
    with _CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            meal_time = (row.get("Meal Time") or "").strip()
            name = (row.get("Dish Name") or "").strip()
            if not meal_time or not name:
                _fail(f"Row {i} is missing Meal Time or Dish Name.")
            rows.append(
                {
                    "name": name,
                    "slots": _slots_for_meal_time(meal_time),
                    "pre_prep": _optional_text(row.get("pre_processing")),
                    "ingredients": _optional_text(row.get("ingredients_check")),
                }
            )
    return rows


def main() -> None:
    print("[import_vanbutler_meals] Starting import (no duplicate checks).")
    _require_database_url()

    csv_rows = _load_csv_rows()
    inserted = 0

    try:
        with get_session() as session:
            print(f"[import_vanbutler_meals] Inserting {len(csv_rows)} row(s).")
            for i, row in enumerate(csv_rows, start=1):
                name = row["name"]
                meal = Meal(name=name, slot=list(row["slots"]))
                session.add(meal)
                session.flush()
                prep = MealPrep(
                    meal_id=meal.id,
                    pre_prep=row["pre_prep"],
                    ingredients=row["ingredients"],
                )
                session.add(prep)
                session.flush()
                inserted += 1
                print(
                    f"[import_vanbutler_meals]   Inserted #{i}: meal id={meal.id} "
                    f"name={name!r} slots={meal.slot!r} "
                    f"pre_prep={prep.pre_prep!r} ingredients={prep.ingredients!r}"
                )
    except (OperationalError, ProgrammingError) as e:
        raise RuntimeError(
            "Database error while importing meals. If tables are missing, run "
            "`alembic upgrade head` first. "
            f"Original error: {e}"
        ) from e

    print(f"[import_vanbutler_meals] Done — inserted={inserted}.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"[import_vanbutler_meals] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
