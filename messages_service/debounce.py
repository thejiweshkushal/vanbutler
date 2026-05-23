"""Global coalescing debounce: one pending asyncio task; reset timer on each arm."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

_pending_task: asyncio.Task | None = None
_lock = asyncio.Lock()


def debounce_seconds() -> float:
    """Seconds to wait after the last ``arm_or_reset_debounce`` before firing."""
    return float(
        os.environ.get(
            "MESSAGE_DEBOUNCE_SECONDS",
            os.environ.get("INTENT_DEBOUNCE_SECONDS", "10"),
        )
    )


async def _debounced_run(target: Callable[[], Awaitable[None]]) -> None:
    global _pending_task
    me = asyncio.current_task()
    try:
        await asyncio.sleep(debounce_seconds())
        await target()
    except asyncio.CancelledError:
        return
    except Exception:
        log.exception("debounced callback failed")
    finally:
        async with _lock:
            if _pending_task is me:
                _pending_task = None


async def arm_or_reset_debounce(target: Callable[[], Awaitable[None]]) -> None:
    """
    Cancel any in-flight debounce task, then start a fresh wait.

    After ``debounce_seconds()`` of quiet, ``target()`` runs once on the event loop.
    """
    global _pending_task
    async with _lock:
        if _pending_task is not None and not _pending_task.done():
            _pending_task.cancel()
        _pending_task = asyncio.create_task(_debounced_run(target))
