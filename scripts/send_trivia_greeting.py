"""CLI: send a test trivia greeting to the food WhatsApp group.

Run from repo root with venv active (WHAPI_*, FOOD_GROUP_ID, GEMINI_API_KEY):

  python -m scripts.send_trivia_greeting
  python scripts/send_trivia_greeting.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv

load_dotenv()

TEST_TRIVIA = (
    "A Pair is when a batsman gets a duck in both innings of a Test. A King Pair is when they are dismissed for a Golden Duck in both innings."
)


def main() -> None:
    from messages_service.helpers import send_trivia_greeting

    try:
        result = send_trivia_greeting(TEST_TRIVIA)
    except Exception as exc:
        print(f"[send_trivia_greeting] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(result)


if __name__ == "__main__":
    main()
