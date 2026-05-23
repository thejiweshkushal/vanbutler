"""CLI: daily 18:42 IST evening trigger — trivia greeting + first unfrozen slot options.

Run from repo root with venv active (DATABASE_URL, WHAPI_*, FOOD_GROUP_ID, GEMINI_API_KEY):

  python -m scripts.run_daily_evening_trigger
  python scripts/run_daily_evening_trigger.py --storage-date 2026-05-20

Scheduled via GitHub Actions (see .github/workflows/daily_evening_trigger.yml).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv

load_dotenv()


def _parse_storage_date(s: str | None) -> date | None:
    if not s or not str(s).strip():
        return None
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Target calendar date for daily_choices/daily_options (default: tomorrow IST)",
    )
    args = parser.parse_args()
    storage_date = _parse_storage_date(args.storage_date)

    from meal_planning.orchestration import run_daily_evening_trigger

    async def _run() -> dict:
        return await run_daily_evening_trigger(storage_date=storage_date)

    try:
        out = asyncio.run(_run())
    except Exception as exc:
        print(f"[run_daily_evening_trigger] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
