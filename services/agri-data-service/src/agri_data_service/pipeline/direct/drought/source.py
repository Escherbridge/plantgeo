"""Fetch one settled USDM release straight from the archive, bounded and retried. Never touches Postgres.

Reuses `ingest.usdm.fetch_drought_release` -- the exact dated-release adapter the Postgres ingestion
path fetches and parses through -- because the fetch-and-parse step never wrote to PostgreSQL in the
first place; only `PostgresDroughtStore` did. Importing the pure fetch/parse function is not
importing the write path.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.usdm import USDM_BOUNDS, fetch_drought_release

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import date

    from agri_data_service.ingest.usdm import DroughtRelease


class DroughtSourceError(RuntimeError):
    """Raised when USDM's own archive cannot be read as a complete, well-formed release."""


@dataclass(frozen=True, slots=True)
class DroughtDaySource:
    """One settled USDM Tuesday, fetched straight from the archive.

    `release` is `None` for USDM's documented "not published yet" 404
    (`ingest.usdm.fetch_drought_release`'s own contract) -- a real answer, not a fetch failure, and
    the adapter is what decides whether that is a refusal or a governed absence.
    """

    day: date
    release: DroughtRelease | None
    fetched_at: datetime


async def fetch_drought_day(
    day: date,
    *,
    retry_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> DroughtDaySource:
    """Fetch exactly one dated USDM release, retrying transport failures as one bounded unit."""

    async def fetch_once() -> DroughtDaySource:
        async with upstream_client(USDM_BOUNDS) as client:
            release = await fetch_drought_release(client, day.isoformat())
        return DroughtDaySource(day=day, release=release, fetched_at=datetime.now(UTC))

    return await _retry_async(
        f"USDM {day.isoformat()} source fetch",
        fetch_once,
        attempts=retry_attempts,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
    )


async def _retry_async[T](
    label: str,
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_seconds: float,
    max_seconds: float,
) -> T:
    """Retry a transport-bound coroutine with jittered exponential backoff, raising the last error."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as error:  # every transport/parse failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            await asyncio.sleep(_retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds))
    assert last_error is not None  # attempts >= 1 is enforced by the caller's config validation
    raise DroughtSourceError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


__all__ = [
    "DroughtDaySource",
    "DroughtSourceError",
    "fetch_drought_day",
]
