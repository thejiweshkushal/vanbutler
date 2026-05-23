"""Fuzzy meal-name matching: tolerant spelling (e.g. Chhole vs Chole) but distinct dishes (Chhole Rice vs Rajma Rice)."""

from __future__ import annotations

import re
from typing import Literal

from rapidfuzz import fuzz

MealMatchTier = Literal["HIGH", "DECENT", "NONE"]


def _collapse_doubled_consonants(token: str) -> str:
    out: list[str] = []
    prev: str | None = None
    for ch in token:
        if prev and ch.isalpha() and ch == prev and ch.isascii() and ch.islower():
            continue
        out.append(ch)
        prev = ch
    return "".join(out)


def _normalize_token(token: str) -> str:
    t = token.lower()
    if t.endswith("h") and len(t) > 2 and t[-2].isalpha():
        t = t[:-1]
    if t.startswith("chh"):
        t = "ch" + t[3:]
    if t.endswith("kh"):
        t = t[:-1] + "k"
    t = t.replace("w", "v")
    return _collapse_doubled_consonants(t)


def normalize_meal_name(name: str) -> str:
    """Lowercase, strip punctuation, per-token transliteration heuristics, single spaces."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    tokens = [_normalize_token(tok) for tok in s.split() if tok]
    return " ".join(tokens)


def score_meal_name(
    query: str, candidates: list[tuple[int, str]]
) -> list[tuple[int, str, float]]:
    """Return ``(id, name, score)`` sorted by score descending (RapidFuzz token_set_ratio on normalized strings)."""
    qn = normalize_meal_name(query)
    if not qn:
        return []
    scored: list[tuple[int, str, float]] = []
    for mid, name in candidates:
        cn = normalize_meal_name(name)
        if not cn:
            continue
        score = float(fuzz.token_set_ratio(qn, cn))
        scored.append((mid, name, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored


def classify_match(
    scored: list[tuple[int, str, float]],
    *,
    high_min: float = 90.0,
    high_margin: float = 10.0,
    decent_min: float = 70.0,
    decent_max: float = 89.0,
) -> tuple[MealMatchTier, int | None, list[tuple[int, str, float]]]:
    """
    HIGH: top score >= high_min and (top - second) >= high_margin -> unique meal id.
    DECENT: top in [decent_min, decent_max] inclusive, or high scores with ambiguous margin.
    NONE: top < decent_min.
    """
    if not scored:
        return ("NONE", None, [])
    top_id, _top_name, top_s = scored[0]
    second_s = scored[1][2] if len(scored) > 1 else 0.0
    margin = top_s - second_s

    if top_s >= high_min and margin >= high_margin:
        return ("HIGH", top_id, scored[:3])

    if top_s >= high_min and margin < high_margin:
        decent_pool = [x for x in scored[:5] if x[2] >= high_min - 5]
        return ("DECENT", None, decent_pool[:3] if decent_pool else scored[:3])

    if decent_min <= top_s <= decent_max:
        return ("DECENT", None, scored[:3])

    if top_s < decent_min:
        return ("NONE", None, scored[:3])

    return ("DECENT", None, scored[:3])
