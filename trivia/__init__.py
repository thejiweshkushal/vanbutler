"""Trivia ingest, semantic dedup, and greeting helpers."""

from trivia.matching import (
    ingest_trivia,
    send_random_unsent_trivia_greeting,
    send_trivia_by_id,
)

__all__ = [
    "ingest_trivia",
    "send_random_unsent_trivia_greeting",
    "send_trivia_by_id",
]
