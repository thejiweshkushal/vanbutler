"""Seed initial cricket trivia rows via ingest_trivia (embed + dedup pipeline).

Run from repo root with venv active (DATABASE_URL, COHERE_KEY):

  python -m scripts.seed_trivia
  python scripts/seed_trivia.py

Skips rows whose exact trivia text already exists in the trivia table.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from database import SessionLocal, engine, get_session  # noqa: E402
from models import Trivia  # noqa: E402

_SEED_ROWS: list[dict[str, Any]] = [
    {
        "category": "Cricket",
        "trivia": (
            "The Women's Cricket World Cup was inaugurated in 1973, which is two years "
            "before the first Men's Cricket World Cup was held in 1975."
        ),
    },
    {
        "category": "Cricket",
        "trivia": (
            "Sir Don Bradman needed just four runs in his final Test innings to finish "
            "with a career average of 100.00. He was bowled for a duck, leaving him with "
            "the most famous average in sports: 99.94"
        ),
    },
    {
        "category": "Cricket",
        "trivia": (
            "Before making his debut for India, a 14-year-old Sachin Tendulkar once took "
            "the field as a substitute fielder for Pakistan during a practice match against "
            "India in 1987."
        ),
    },
]


def _fail(msg: str, *, code: int = 1) -> None:
    print(f"[seed_trivia] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _require_database_url() -> None:
    raw = os.environ.get("DATABASE_URL")
    if raw is None or not str(raw).strip():
        _fail(
            "DATABASE_URL is missing or empty. Set it in .env or the environment."
        )


def _require_engine() -> None:
    if engine is None or SessionLocal is None:
        _fail(
            "SQLAlchemy engine was not created. Fix DATABASE_URL and re-run in a fresh process."
        )


def main() -> None:
    print("[seed_trivia] Starting seed script.")
    _require_database_url()
    _require_engine()

    from trivia.matching import ingest_trivia

    added = 0
    skipped = 0
    rejected = 0

    for i, row in enumerate(_SEED_ROWS):
        category = str(row["category"]).strip()
        trivia_text = str(row["trivia"]).strip()

        with get_session() as session:
            existing = session.scalar(
                select(Trivia).where(Trivia.trivia == trivia_text).limit(1)
            )
        if existing is not None:
            skipped += 1
            print(
                f"[seed_trivia]   Skip #{i + 1}: trivia already in database "
                f"(id={existing.id})."
            )
            continue

        try:
            result = ingest_trivia(category, trivia_text)
        except Exception as exc:
            _fail(f"Row #{i + 1} failed: {exc}")

        status = result.get("status")
        if status == "added":
            added += 1
            print(
                f"[seed_trivia]   Added #{i + 1}: trivia_id={result.get('trivia_id')} "
                f"category={category!r}"
            )
        elif status == "rejected":
            rejected += 1
            print(
                f"[seed_trivia]   Rejected #{i + 1} (semantic duplicate): "
                f"{result.get('matches')}"
            )
        else:
            print(f"[seed_trivia]   Row #{i + 1}: {result}")

    print(
        f"[seed_trivia] Done — added={added}, skipped={skipped}, rejected={rejected}."
    )


if __name__ == "__main__":
    main()
