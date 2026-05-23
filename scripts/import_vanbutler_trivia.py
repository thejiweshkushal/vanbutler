"""Import trivia from trivia/vanbutler_trivia.csv (direct insert, batched embeddings).

Skips rows whose exact trivia text already exists. Embeddings are created via
Cohere in batches of 5 with a 10-second pause between batches.

Run from repo root with venv active (DATABASE_URL, COHERE_KEY):

  python -m scripts.import_vanbutler_trivia
  python scripts/import_vanbutler_trivia.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from database import SessionLocal, engine, get_session  # noqa: E402
from models import Trivia, TriviaEmbedding  # noqa: E402

_CSV_PATH = _repo_root / "trivia" / "vanbutler_trivia.csv"
_EMBED_BATCH_SIZE = 5
_EMBED_SLEEP_SECONDS = 10.0


def _fail(msg: str, *, code: int = 1) -> None:
    print(f"[import_vanbutler_trivia] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def _require_env() -> None:
    if not os.environ.get("DATABASE_URL", "").strip():
        _fail("DATABASE_URL is missing or empty.")
    if not os.environ.get("COHERE_KEY", "").strip():
        _fail("COHERE_KEY is missing or empty.")
    if engine is None or SessionLocal is None:
        _fail("SQLAlchemy engine was not created. Fix DATABASE_URL and re-run.")


def _load_csv_rows() -> list[tuple[str, str]]:
    if not _CSV_PATH.is_file():
        _fail(f"CSV not found: {_CSV_PATH}")
    rows: list[tuple[str, str]] = []
    with _CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            fact = (row.get("fact") or "").strip()
            category = (row.get("category") or "").strip()
            if not fact or not category:
                _fail(f"Row {i} is missing fact or category.")
            rows.append((category, fact))
    return rows


def main() -> None:
    print("[import_vanbutler_trivia] Starting import.")
    _require_env()

    from trivia.matching import create_trivia_embeddings_batched, insert_trivia_direct

    csv_rows = _load_csv_rows()
    inserted: list[tuple[int, str]] = []
    skipped = 0

    for i, (category, fact) in enumerate(csv_rows, start=1):
        with get_session() as session:
            existing = session.scalar(
                select(Trivia).where(Trivia.trivia == fact).limit(1)
            )
            existing_id = existing.id if existing is not None else None
        if existing_id is not None:
            skipped += 1
            print(
                f"[import_vanbutler_trivia]   Skip #{i}: already in database "
                f"(id={existing_id})."
            )
            continue

        trivia_id = insert_trivia_direct(category, fact)
        inserted.append((trivia_id, fact))
        print(
            f"[import_vanbutler_trivia]   Added #{i}: trivia_id={trivia_id} "
            f"category={category!r}"
        )

    to_embed: list[tuple[int, str]] = list(inserted)
    with get_session() as session:
        missing = session.execute(
            select(Trivia.id, Trivia.trivia)
            .outerjoin(TriviaEmbedding, TriviaEmbedding.trivia_id == Trivia.id)
            .where(TriviaEmbedding.id.is_(None))
            .order_by(Trivia.id)
        ).all()
    for trivia_id, trivia_text in missing:
        if (trivia_id, trivia_text) not in to_embed:
            to_embed.append((trivia_id, trivia_text))

    embedded = 0
    if to_embed:
        print(
            f"[import_vanbutler_trivia] Creating embeddings for {len(to_embed)} "
            f"row(s) in batches of {_EMBED_BATCH_SIZE} "
            f"(sleep {_EMBED_SLEEP_SECONDS}s between batches)."
        )
        embedded = create_trivia_embeddings_batched(
            to_embed,
            batch_size=_EMBED_BATCH_SIZE,
            sleep_seconds=_EMBED_SLEEP_SECONDS,
        )

    print(
        "[import_vanbutler_trivia] Done — "
        f"added={len(inserted)}, skipped={skipped}, embedded={embedded}."
    )


if __name__ == "__main__":
    main()
