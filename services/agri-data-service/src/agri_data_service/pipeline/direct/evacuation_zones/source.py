"""Fetch Oregon OEM's current evacuation-area state straight from ArcGIS, bounded. Never touches Postgres.

Reuses `ingest.evacuation_zones.fetch_evacuation_zones` -- the exact adaptive paged walk the Postgres
ingestion path fetched and parsed through -- because the fetch-and-parse step never wrote to
PostgreSQL in the first place; only `FeatureWriter`/`ingest_features` did. Importing the pure
fetch/parse function is not importing the write path, and the recent ingestion removal deliberately
KEPT these shared modules for exactly this reuse.

What is imported, and why each one rather than a local restatement:

- `fetch_evacuation_zones` -- the bbox-bounded, byte-budgeted, retried, oversized-record-governed
  page walk (`ingest/evacuation_zones.py:321-349`). Re-implementing it would fork the 64 MiB
  per-run transfer budget, the 20-page ceiling and the `INGEST_MAX_SOURCE_RECORDS` record ceiling.
- `EVACUATION_ZONES_BOUNDS` -- the 16 MiB / 20 s per-request bound the client is built with.
- `EVACUATION_ZONES_QUERY_URL` (via `products.DIRECT_QUERY_URL`) -- reached only through
  `fetch_evacuation_zones`; this module accepts NO caller-supplied endpoint, which is what makes
  "widening coverage" impossible to do by accident. See `products.COVERED_STATE`.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.evacuation_zones import EVACUATION_ZONES_BOUNDS, fetch_evacuation_zones
from agri_data_service.ingest.http import upstream_client

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


class EvacuationZonesSourceError(RuntimeError):
    """Raised when Oregon OEM's feed cannot be read as a complete, well-formed statewide snapshot."""


class EvacuationZonesTruncatedError(RuntimeError):
    """Raised when the upstream walk reports it did not deliver every currently-published area.

    A REFUSAL, NOT A PARTIAL PUBLICATION, and this is where the direct writer deliberately diverges
    from the Postgres path it replaces. `run_evacuation_zones_ingestion_job`
    (DELETED 2026-09-07) wrote whatever it got and set `truncated=True` on the
    result, so a bitten page/byte/record ceiling produced a warehouse row set that was quietly short
    of the statewide picture, and `evacuation_zones_day_export.sql` (deleted 2026-09-06) then
    exported that short set as a full snapshot. Nothing downstream reads `truncated`.

    For a version-stamped full-snapshot lane there is no honest way to publish a partial population:
    the snapshot IS the claim "these are all the currently-published Oregon OEM evacuation areas".
    A truncated fetch is therefore refused and retried next tick, exactly as an unsettled source is.
    """


@dataclass(frozen=True, slots=True)
class EvacuationZonesSource:
    """One statewide capture of Oregon OEM's current-state view, and when this repo took it.

    `zones` are the parsed records `parse_evacuation_zone_collection` produced, unmodified -- the
    same dicts `build_evacuation_zone_write` consumed. `fetched_at` is the instant the capture
    completed; it dates nothing about the SOURCE and is never written into `observed_at`, only into
    the two staleness columns that honestly mean "when a run last saw this" (see `rows.py`).
    """

    bbox: str
    fetched_at: datetime
    zones: tuple[Mapping[str, object], ...]

    @property
    def zone_count(self) -> int:
        """How many currently-published areas this capture holds; 0 is a real, honest answer."""
        return len(self.zones)


async def fetch_evacuation_zones_snapshot(
    bbox: str,
    *,
    retry_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> EvacuationZonesSource:
    """Capture the whole bounded statewide set once, retrying transport failures as one unit.

    ONE CAPTURE PER TURN, and the whole walk is the retry unit rather than a single page: a snapshot
    stitched from pages fetched minutes apart across a retry series would mix two states of a
    fast-moving multi-fire event into one version, which is the same "wrong-but-plausible" failure a
    partial publication would be.
    """

    async def fetch_once() -> EvacuationZonesSource:
        async with upstream_client(EVACUATION_ZONES_BOUNDS) as client:
            zones, truncated = await fetch_evacuation_zones(client, bbox)
        if truncated:
            raise EvacuationZonesTruncatedError(
                f"Oregon OEM's feed did not deliver every published area within this run's bounds "
                f"({len(zones)} returned before a page, byte or record ceiling bit); refusing to "
                "publish a partial statewide evacuation picture as a full snapshot"
            )
        return EvacuationZonesSource(bbox=bbox, fetched_at=datetime.now(UTC), zones=tuple(zones))

    return await _retry_async(
        "Oregon OEM evacuation-area snapshot fetch",
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
    """Retry a transport-bound coroutine with jittered exponential backoff, raising the last error.

    A truncation refusal is NOT retried inside the turn: re-walking the same pages against the same
    ceilings asks the same question and gets the same answer, and the ceilings are per-run budgets
    that a second walk inside one turn would not reset.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except EvacuationZonesTruncatedError:
            raise
        except Exception as error:  # every transport/parse failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            await asyncio.sleep(_retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds))
    assert last_error is not None  # attempts >= 1 is enforced by the caller's config validation
    raise EvacuationZonesSourceError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


__all__ = [
    "EvacuationZonesSource",
    "EvacuationZonesSourceError",
    "EvacuationZonesTruncatedError",
    "fetch_evacuation_zones_snapshot",
]
