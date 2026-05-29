"""LLM wrapper with DB-backed call logging across two providers (Gemini + Groq).

Trivia greeting calls route to Gemini (``google-genai``) — its prior produces
warmer, wittier WhatsApp prose for the daily greeting. Intent classification
routes to Groq (``groq``), where Llama 3.3 70B with JSON mode is fast and
reliable for schema-bound structured output.

Every model call goes through ``_call_and_log``, which dispatches by
``provider``, times the request, and persists one row to ``llm_logs`` (see
``models.LLMLog``). Logging is best-effort — a failure to write the row never
breaks the main flow.

Transient errors (provider-appropriate 5xx, 429 rate-limit, and connection or
timeout errors) are retried with the delays in ``_TRANSIENT_RETRY_DELAYS``.
Each transient attempt — success or failure — gets its own ``llm_logs`` row,
tagged via ``request_metadata.transient_retry`` (1 for the first retry, 2 for
the second). ``request_metadata.provider`` records which backend served each
row, so you can filter logs by provider.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Literal

import groq
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from groq import AsyncGroq

from llm.prompts import (
    get_conversation_intent_prompt,
    get_cook_absence_prompt,
    get_trivia_greeting_prompt,
)

load_dotenv()

log = logging.getLogger(__name__)

Provider = Literal["gemini", "groq"]

_gemini_client: genai.Client | None = None
_groq_client: AsyncGroq | None = None

# Sleep before retry 1 and retry 2 respectively.
# Total attempts per call = 1 + len(_TRANSIENT_RETRY_DELAYS) = 3.
_TRANSIENT_RETRY_DELAYS: tuple[int, ...] = (30, 60)
_GEMINI_OVERLOAD_BATCH_RETRY_DELAY_SECONDS = 300
_GEMINI_OVERLOAD_BATCH_RETRIES = 3


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini_client


def _get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=os.environ["GROQ_KEY"])
    return _groq_client


def _is_transient(exc: BaseException | None, provider: Provider) -> bool:
    """Provider-appropriate transient-error check for retry decisions."""
    if provider == "gemini":
        if isinstance(exc, genai_errors.ServerError):
            return True
        if isinstance(exc, genai_errors.ClientError):
            return getattr(exc, "code", None) == 429
        return False
    if provider == "groq":
        return isinstance(
            exc,
            (
                groq.RateLimitError,
                groq.InternalServerError,
                groq.APITimeoutError,
                groq.APIConnectionError,
            ),
        )
    return False


def _is_gemini_overload_503(exc: BaseException | None) -> bool:
    """True when Gemini returns server-side temporary overload (HTTP 503)."""
    if not isinstance(exc, genai_errors.ServerError):
        return False
    code = getattr(exc, "code", None)
    if code == 503:
        return True
    message = str(exc).lower()
    return "503" in message and "unavailable" in message


def _extract_response_metadata(
    response: Any, provider: Provider
) -> dict[str, Any] | None:
    """Pull token counts, finish reason, and served model off the SDK response (best-effort).

    Returns provider-specific keys: Gemini uses ``prompt_token_count`` /
    ``candidates_token_count`` / ``total_token_count``; Groq uses
    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` plus the
    server-side timing breakdown (``queue_time`` / ``prompt_time`` /
    ``completion_time`` / ``total_time``).
    """
    if response is None:
        return None
    out: dict[str, Any] = {}

    if provider == "gemini":
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            for key in (
                "prompt_token_count",
                "candidates_token_count",
                "total_token_count",
                "cached_content_token_count",
                "thoughts_token_count",
            ):
                v = getattr(usage, key, None)
                if v is not None:
                    try:
                        out[key] = int(v)
                    except (TypeError, ValueError):
                        out[key] = str(v)
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish_reason = getattr(candidates[0], "finish_reason", None)
            if finish_reason is not None:
                out["finish_reason"] = str(finish_reason)
        model_version = getattr(response, "model_version", None)
        if model_version:
            out["model_version"] = str(model_version)
        return out or None

    if provider == "groq":
        usage = getattr(response, "usage", None)
        if usage is not None:
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "queue_time",
                "prompt_time",
                "completion_time",
                "total_time",
            ):
                v = getattr(usage, key, None)
                if v is not None:
                    try:
                        out[key] = int(v) if key.endswith("_tokens") else float(v)
                    except (TypeError, ValueError):
                        out[key] = str(v)
            # Cached-prompt tokens (Groq prefix cache) — not counted against rate limits.
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details is not None else None
            if cached is not None:
                try:
                    out["cached_tokens"] = int(cached)
                except (TypeError, ValueError):
                    out["cached_tokens"] = str(cached)
        choices = getattr(response, "choices", None) or []
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
            if finish_reason is not None:
                out["finish_reason"] = str(finish_reason)
        served_model = getattr(response, "model", None)
        if served_model:
            out["model_version"] = str(served_model)
        system_fingerprint = getattr(response, "system_fingerprint", None)
        if system_fingerprint:
            out["system_fingerprint"] = str(system_fingerprint)
        return out or None

    return None


def _persist_llm_log(
    *,
    kind: str,
    model: str,
    prompt: str,
    response_text: str | None,
    response: Any,
    error: str | None,
    attempt: int,
    latency_ms: int,
    request_metadata: dict[str, Any] | None,
    provider: Provider,
) -> None:
    """Best-effort: never raises. Writes one ``llm_logs`` row."""
    try:
        from database import SessionLocal
        from models import LLMLog

        if SessionLocal is None:
            return
        meta = _extract_response_metadata(response, provider)
        with SessionLocal() as session:
            row = LLMLog(
                kind=kind,
                model=model,
                prompt=prompt,
                response_text=response_text,
                response_metadata=meta,
                request_metadata=request_metadata,
                latency_ms=latency_ms,
                attempt=attempt,
                error=error,
            )
            session.add(row)
            session.commit()
    except Exception:
        log.exception("llm_logs persist failed (kind=%s)", kind)


async def _call_and_log(
    *,
    kind: str,
    model: str,
    prompt: str,
    provider: Provider,
    attempt: int = 1,
    request_metadata: dict[str, Any] | None = None,
    extra_call_kwargs: dict[str, Any] | None = None,
) -> str:
    """Invoke the chosen provider with transient-error retry; persist one ``llm_logs`` row per attempt.

    Dispatches by ``provider``:
      - ``"gemini"`` → ``client.aio.models.generate_content(model, contents=prompt, **extras)``
      - ``"groq"``   → ``client.chat.completions.create(model, messages=[...], **extras)``

    Retries on provider-appropriate transient errors with the delays in
    ``_TRANSIENT_RETRY_DELAYS``. Non-transient exceptions (auth, validation,
    bad request, etc.) are re-raised immediately. The final raised exception,
    if any, comes from the last transient attempt.

    ``extra_call_kwargs`` is merged into the underlying SDK call — use it for
    Groq's ``response_format={"type": "json_object"}`` or any provider-specific
    parameters (e.g. ``temperature``).
    """
    delays = (0,) + _TRANSIENT_RETRY_DELAYS  # length = total attempts
    last_exc: BaseException | None = None
    call_kwargs = extra_call_kwargs or {}

    for transient_idx, delay_before in enumerate(delays):
        if delay_before:
            log.warning(
                "LLM transient retry %d/%d for kind=%s provider=%s in %ds (last error: %r)",
                transient_idx,
                len(_TRANSIENT_RETRY_DELAYS),
                kind,
                provider,
                delay_before,
                last_exc,
            )
            await asyncio.sleep(delay_before)

        started = time.perf_counter()
        response: Any = None
        error_text: str | None = None
        text_out: str = ""
        try:
            if provider == "gemini":
                response = await _get_gemini_client().aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    **call_kwargs,
                )
                text_out = (response.text or "").strip()
            elif provider == "groq":
                response = await _get_groq_client().chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    **call_kwargs,
                )
                choices = getattr(response, "choices", None) or []
                if choices:
                    content = getattr(choices[0].message, "content", None)
                    text_out = (content or "").strip()
            else:
                raise ValueError(f"unknown provider: {provider!r}")
        except Exception as exc:
            error_text = repr(exc)
            last_exc = exc
        finally:
            latency_ms = int((time.perf_counter() - started) * 1000)
            req_meta: dict[str, Any] = {**(request_metadata or {}), "provider": provider}
            if transient_idx > 0:
                req_meta["transient_retry"] = transient_idx
            _persist_llm_log(
                kind=kind,
                model=model,
                prompt=prompt,
                response_text=text_out if response is not None else None,
                response=response,
                error=error_text,
                attempt=attempt,
                latency_ms=latency_ms,
                request_metadata=req_meta,
                provider=provider,
            )

        if error_text is None:
            return text_out  # success

        # Decide whether to retry the next attempt.
        if not _is_transient(last_exc, provider) or transient_idx + 1 >= len(delays):
            assert last_exc is not None
            raise last_exc

    raise RuntimeError("LLM call exhausted retries without producing a result")


async def generate_trivia_greeting(trivia: str, *, attempt: int = 1) -> str:
    """Generate trivia greeting with Gemini-first strategy and Groq fallback.

    Strategy:
    1) Gemini call with in-call transient retry (30s, 60s).
    2) If Gemini still fails due to overload 503, run up to 3 more full Gemini
       batches, sleeping 5 minutes between batches.
    3) If all Gemini batches fail, fallback to Groq.
    """
    prompt = get_trivia_greeting_prompt(trivia)
    gemini_model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    req_meta = {"trivia_chars": len(trivia)}

    # 1 + _GEMINI_OVERLOAD_BATCH_RETRIES total Gemini batches.
    total_batches = 1 + _GEMINI_OVERLOAD_BATCH_RETRIES
    last_exc: BaseException | None = None
    for batch_idx in range(total_batches):
        if batch_idx > 0:
            log.warning(
                "Gemini overload batch retry %d/%d for trivia greeting in %ds",
                batch_idx,
                _GEMINI_OVERLOAD_BATCH_RETRIES,
                _GEMINI_OVERLOAD_BATCH_RETRY_DELAY_SECONDS,
            )
            await asyncio.sleep(_GEMINI_OVERLOAD_BATCH_RETRY_DELAY_SECONDS)
        try:
            return await _call_and_log(
                kind="trivia_greeting",
                model=gemini_model,
                prompt=prompt,
                provider="gemini",
                attempt=attempt + batch_idx,
                request_metadata={
                    **req_meta,
                    "greeting_provider": "gemini",
                    "gemini_batch_retry": batch_idx,
                },
            )
        except Exception as exc:
            last_exc = exc
            if not _is_gemini_overload_503(exc):
                raise

    assert last_exc is not None
    groq_model = os.environ.get("GROQ_GREETING_MODEL", os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"))
    log.warning(
        "Gemini greeting unavailable after %d batch(es); falling back to Groq model=%s. Last error=%r",
        total_batches,
        groq_model,
        last_exc,
    )
    return await _call_and_log(
        kind="trivia_greeting",
        model=groq_model,
        prompt=prompt,
        provider="groq",
        attempt=attempt + total_batches,
        request_metadata={
            **req_meta,
            "greeting_provider": "groq_fallback",
            "gemini_batches_exhausted": total_batches,
        },
    )


async def analyze_conversation_intent_raw(
    conversation_snippet: str, *, attempt: int = 1
) -> str:
    """Call Groq (Llama 3.3 70B) with the intent-classification prompt; return raw text (JSON expected).

    Uses Groq's JSON mode so the model is constrained to emit a valid JSON object,
    matching the schema documented in ``get_conversation_intent_prompt``.
    """
    prompt = get_conversation_intent_prompt(conversation_snippet)
    model_name = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    return await _call_and_log(
        kind="intent_classify",
        model=model_name,
        prompt=prompt,
        provider="groq",
        attempt=attempt,
        request_metadata={
            "snippet_chars": len(conversation_snippet),
            "snippet_lines": (conversation_snippet.count("\n") + 1)
            if conversation_snippet
            else 0,
        },
        extra_call_kwargs={"response_format": {"type": "json_object"}},
    )


async def analyze_cook_absence_raw(
    conversation_snippet: str,
    *,
    today_ist_iso: str,
    daily_choices_context: str,
    attempt: int = 1,
) -> str:
    """Resolve cook absence / corrections via Gemini JSON mode; return raw JSON text."""
    prompt = get_cook_absence_prompt(
        conversation_snippet,
        today_ist_iso=today_ist_iso,
        daily_choices_context=daily_choices_context,
    )
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    return await _call_and_log(
        kind="cook_absence_resolve",
        model=model_name,
        prompt=prompt,
        provider="gemini",
        attempt=attempt,
        request_metadata={
            "snippet_chars": len(conversation_snippet),
            "today_ist": today_ist_iso,
        },
        extra_call_kwargs={
            "config": genai_types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        },
    )
