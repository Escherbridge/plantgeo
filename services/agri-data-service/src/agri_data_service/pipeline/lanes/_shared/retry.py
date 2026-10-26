"""Jittered exponential backoff for a transport-bound coroutine, extracted from three byte-identical
copies (`burn_severity/mtbs.py`, `drought/usdm.py`, `evacuation_zones/source.py`); see `AGENTS.md` §Retry.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def jittered_backoff_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    """Exponential backoff capped at `max_seconds`, plus up to a quarter of the ceiling in jitter."""
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


async def retry_with_backoff[T](  # noqa: PLR0913 - every lane's own exhausted type, label and never_retry list, named rather than left implicit
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
    exhausted: Callable[[str], Exception],
    label: str,
    never_retry: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Retry `operation` with jittered exponential backoff, raising `exhausted(...)` from the last error.

    `never_retry` lets a caller's own refusal (evacuation_zones' truncation) propagate on first sight
    instead of being retried and then re-raised as `exhausted`; every other exception is retried the
    same way, matching every one of the three copies this replaces. `sleep` defaults to
    `asyncio.sleep` and exists so a test can inject a no-op and skip real wall-clock delay.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except never_retry:
            raise
        except Exception as error:  # every transport/parse failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            await sleep(jittered_backoff_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds))
    assert last_error is not None  # attempts >= 1 is enforced by the caller's config validation
    raise exhausted(f"{label} failed after {attempts} attempts") from last_error


__all__ = ["jittered_backoff_delay", "retry_with_backoff"]
