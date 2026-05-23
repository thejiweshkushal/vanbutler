"""CLI: slot meal options (plain-text formatter, no LLM), send WhatsApp message, upsert daily_options.

Run from repo root with venv active (DATABASE_URL, WHAPI_*; GROQ_KEY only required for the intent LLM, not this CLI):

  python -m scripts.send_slot_options --slot breakfast
  python scripts/send_slot_options.py --slot lunch

For production scheduling at 18:42 IST (greeting + slot orchestration), use
``python -m scripts.run_daily_evening_trigger`` or GitHub Actions
(see README "Daily evening trigger").
"""

from __future__ import annotations

import argparse
import asyncio
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
        "--slot",
        required=True,
        choices=("breakfast", "lunch", "dinner"),
        help="Meal slot to propose options for",
    )
    parser.add_argument(
        "--option-count",
        type=int,
        default=3,
        help="Number of random meals to pick (default 3)",
    )
    parser.add_argument(
        "--storage-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="daily_options date (default: tomorrow local)",
    )
    args = parser.parse_args()
    storage_date = _parse_storage_date(args.storage_date)

    from meal_planning.meal_options import run_slot_options

    async def _run() -> dict:
        return await run_slot_options(
            args.slot,
            storage_date=storage_date,
            option_count=args.option_count,
        )

    try:
        out = asyncio.run(_run())
    except Exception as exc:
        print(f"[send_slot_options] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(out)


if __name__ == "__main__":
    main()
