"""Trivia ingest with semantic dedup (Cohere embed + rerank) and greeting send helpers."""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from sqlalchemy import select

load_dotenv()

from database import get_session
from models import Trivia, TriviaEmbedding

EMBED_MODEL = "embed-v4.0"
RERANK_MODEL = "rerank-v3.5"
CANDIDATE_SIM_CUTOFF = 0.5
CANDIDATE_MAX = 30
RERANK_TOP_N = 5
RERANK_DUPE_THRESHOLD = 0.8

_COHERE_EMBED_URL = "https://api.cohere.com/v2/embed"
_COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
_SEMANTICALS_PATH = Path(__file__).resolve().parent / "semanticals.csv"
_SEMANTICALS_COLUMNS = (
    "input_trivia",
    "db_trivia",
    "relevance_score",
    "model",
    "created_at",
)


@dataclass(frozen=True)
class _Candidate:
    trivia_id: int
    trivia: str
    similarity: float


def _cohere_headers() -> dict[str, str]:
    key = os.environ.get("COHERE_KEY")
    if not key or not str(key).strip():
        raise RuntimeError("COHERE_KEY is not set in the environment")
    return {
        "Authorization": f"Bearer {key.strip()}",
        "Content-Type": "application/json",
    }


def embed_texts(texts: list[str], *, input_type: str) -> list[list[float]]:
    """Call Cohere v2 embed; return float vectors for each text."""
    if not texts:
        return []
    r = requests.post(
        _COHERE_EMBED_URL,
        headers=_cohere_headers(),
        json={
            "model": EMBED_MODEL,
            "input_type": input_type,
            "texts": texts,
            "embedding_types": ["float"],
        },
        timeout=(30, 90),
    )
    r.raise_for_status()
    data = r.json()
    embeddings = data.get("embeddings") or {}
    floats = embeddings.get("float")
    if not floats or len(floats) != len(texts):
        raise RuntimeError(
            f"Cohere embed returned unexpected float embeddings: {data!r}"
        )
    return floats


def rerank(query: str, documents: list[str], *, top_n: int) -> list[dict]:
    """Call Cohere v2 rerank; return results with index and relevance_score."""
    if not documents:
        return []
    r = requests.post(
        _COHERE_RERANK_URL,
        headers=_cohere_headers(),
        json={
            "model": RERANK_MODEL,
            "query": query,
            "documents": documents,
            "top_n": top_n,
        },
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    return list(data.get("results") or [])


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _append_semanticals_row(
    *,
    input_trivia: str,
    db_trivia: str,
    relevance_score: float,
    model: str,
    created_at: str,
) -> None:
    write_header = not _SEMANTICALS_PATH.exists()
    with _SEMANTICALS_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_SEMANTICALS_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "input_trivia": input_trivia,
                "db_trivia": db_trivia,
                "relevance_score": relevance_score,
                "model": model,
                "created_at": created_at,
            }
        )


def _load_category_candidates(category: str) -> list[tuple[int, str, list[float]]]:
    with get_session() as session:
        rows = session.execute(
            select(Trivia.id, Trivia.trivia, TriviaEmbedding.embedding)
            .join(TriviaEmbedding, TriviaEmbedding.trivia_id == Trivia.id)
            .where(Trivia.category == category)
        ).all()
    out: list[tuple[int, str, list[float]]] = []
    for trivia_id, trivia_text, embedding in rows:
        if not isinstance(embedding, list):
            continue
        out.append((trivia_id, trivia_text, embedding))
    return out


def insert_trivia_direct(category: str, trivia: str) -> int:
    """Insert a trivia row without semantic dedup. Returns the new trivia id."""
    cat = category.strip()
    text = trivia.strip()
    if not cat:
        raise ValueError("category must be non-empty")
    if not text:
        raise ValueError("trivia must be non-empty")

    with get_session() as session:
        row = Trivia(category=cat, trivia=text, last_sent_on=None)
        session.add(row)
        session.flush()
        return row.id


def create_trivia_embeddings_batched(
    items: list[tuple[int, str]],
    *,
    batch_size: int = 5,
    sleep_seconds: float = 10.0,
) -> int:
    """Create search_document embeddings for trivia rows, ``batch_size`` at a time."""
    import time

    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    created = 0
    for i in range(0, len(items), batch_size):
        if i > 0 and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        batch = items[i : i + batch_size]
        texts = [text for _, text in batch]
        vectors: list[list[float]] | None = None
        for attempt in range(5):
            try:
                vectors = embed_texts(texts, input_type="search_document")
                break
            except requests.exceptions.RequestException as exc:
                if attempt == 4:
                    raise
                wait = 10 * (attempt + 1)
                print(
                    f"[create_trivia_embeddings_batched] Batch {i // batch_size + 1} "
                    f"attempt {attempt + 1} failed ({exc}); retrying in {wait}s."
                )
                time.sleep(wait)
        assert vectors is not None
        with get_session() as session:
            for (trivia_id, _), vec in zip(batch, vectors):
                session.add(
                    TriviaEmbedding(
                        trivia_id=trivia_id,
                        model=EMBED_MODEL,
                        input_type="search_document",
                        embedding=vec,
                    )
                )
                created += 1
    return created


def ingest_trivia(category: str, trivia: str) -> dict:
    """Accept incoming trivia: dedup via embed + cosine + rerank, or insert."""
    cat = category.strip()
    text = trivia.strip()
    if not cat:
        raise ValueError("category must be non-empty")
    if not text:
        raise ValueError("trivia must be non-empty")

    query_vec = embed_texts([text], input_type="search_query")[0]

    scored: list[_Candidate] = []
    for trivia_id, db_trivia, db_vec in _load_category_candidates(cat):
        sim = _cosine(query_vec, db_vec)
        if sim >= CANDIDATE_SIM_CUTOFF:
            scored.append(_Candidate(trivia_id=trivia_id, trivia=db_trivia, similarity=sim))

    scored.sort(key=lambda c: c.similarity, reverse=True)
    candidates = scored[:CANDIDATE_MAX]

    if candidates:
        doc_texts = [c.trivia for c in candidates]
        rerank_results = rerank(text, doc_texts, top_n=RERANK_TOP_N)
        now_iso = datetime.now(timezone.utc).isoformat()
        matches: list[dict] = []
        rejected = False

        for item in rerank_results:
            score = float(item.get("relevance_score", 0.0))
            if score <= RERANK_DUPE_THRESHOLD:
                continue
            idx = int(item["index"])
            db_trivia = doc_texts[idx]
            _append_semanticals_row(
                input_trivia=text,
                db_trivia=db_trivia,
                relevance_score=score,
                model=RERANK_MODEL,
                created_at=now_iso,
            )
            matches.append(
                {
                    "db_trivia": db_trivia,
                    "relevance_score": score,
                    "trivia_id": candidates[idx].trivia_id,
                }
            )
            rejected = True

        if rejected:
            return {"status": "rejected", "matches": matches}

    doc_vec = embed_texts([text], input_type="search_document")[0]
    with get_session() as session:
        row = Trivia(category=cat, trivia=text, last_sent_on=None)
        session.add(row)
        session.flush()
        trivia_id = row.id
        emb = TriviaEmbedding(
            trivia_id=trivia_id,
            model=EMBED_MODEL,
            input_type="search_document",
            embedding=doc_vec,
        )
        session.add(emb)

    return {"status": "added", "trivia_id": trivia_id}


def mark_trivia_sent(trivia_id: int) -> None:
    """Set last_sent_on to now for the given trivia row."""
    with get_session() as session:
        row = session.get(Trivia, trivia_id)
        if row is None:
            raise LookupError(f"Trivia id={trivia_id} not found")
        row.last_sent_on = datetime.now(timezone.utc)


async def send_trivia_by_id_async(trivia_id: int) -> dict:
    """Fetch trivia by id, send greeting, then mark last_sent_on on success."""
    with get_session() as session:
        row = session.get(Trivia, trivia_id)
        if row is None:
            raise LookupError(f"Trivia id={trivia_id} not found")
        trivia_text = row.trivia

    from messages_service.helpers import send_trivia_greeting_async

    result = await send_trivia_greeting_async(trivia_text)
    mark_trivia_sent(trivia_id)
    return result


def send_trivia_by_id(trivia_id: int) -> dict:
    """Sync wrapper for ``send_trivia_by_id_async``."""
    import asyncio

    return asyncio.run(send_trivia_by_id_async(trivia_id))


async def send_random_unsent_trivia_greeting_async() -> dict:
    """Pick random unsent trivia (or oldest if all sent), send greeting, mark sent."""
    from meal_planning.query import fetch_random_unsent_trivia_id

    with get_session() as session:
        trivia_id = fetch_random_unsent_trivia_id(session)
    return await send_trivia_by_id_async(trivia_id)


def send_random_unsent_trivia_greeting() -> dict:
    """Sync wrapper for ``send_random_unsent_trivia_greeting_async``."""
    import asyncio

    return asyncio.run(send_random_unsent_trivia_greeting_async())
