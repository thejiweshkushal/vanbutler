"""Persona and shared configuration from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

_DEFAULT_BACKSTORY = (
    "You served them on their honeymoon aboard the Tulip Cruise in Ha Long Bay. "
    "You now work for Viking Cruises but maintain a WhatsApp connection to help "
    "plan their daily meals."
)


def get_butler_name() -> str:
    return os.environ.get("BUTLER_NAME", "Van")


def get_household_names() -> str:
    return os.environ.get("HOUSEHOLD_NAMES", "Jiwesh and Mansi")


def get_butler_backstory() -> str:
    return os.environ.get("BUTLER_BACKSTORY", _DEFAULT_BACKSTORY)


def get_household_names_or() -> str:
    """Household phrasing for 'A or B' in prompts (e.g. 'Jiwesh or Mansi')."""
    names = get_household_names()
    if " and " in names:
        return names.replace(" and ", " or ", 1)
    return names


def get_cook_nickname() -> str:
    return os.environ.get("COOK_NICKNAME", "didi")
