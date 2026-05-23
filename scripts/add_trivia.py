"""CLI: add trivia to the database (with semantic dedup).

Run from repo root with venv active (DATABASE_URL, COHERE_KEY):

  python -m scripts.add_trivia --category Cricket --trivia "Some fact..."
  python scripts/add_trivia.py --category Cricket --trivia "Some fact..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", required=True, help="Trivia category (e.g. Cricket)")
    parser.add_argument("--trivia", required=True, help="Trivia text to ingest")
    args = parser.parse_args()

    from trivia.matching import ingest_trivia

    try:
        result = ingest_trivia(args.category, args.trivia)
    except ValueError as exc:
        print(f"[add_trivia] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:
        print(f"[add_trivia] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(result)


if __name__ == "__main__":
    main()
