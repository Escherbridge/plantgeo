"""Fetch ONE complete WFIGS `_Current` population straight from ArcGIS, bounded and retried.

Never touches PostgreSQL. Reuses `ingest.wfigs.fetch_fire_perimeters_walk` -- the exact adaptive,
byte-bounded, oversized-record-skipping page walk the PostgreSQL ingestion path fetches and parses
through -- because the fetch-and-parse step never wrote to PostgreSQL in the first place; only
`ingest/writer.py`'s `FeatureWriter` did. Importing the pure walk is not importing the write path.
This is the same relationship `drought/source.py` has with `ingest.usdm.fetch_drought_release`.

ONE FETCH PER TURN, AND THE WHOLE PACKAGE DEPENDS ON THAT. `watermark.py` derives this lane's
version stamp from the fetched population, `rows.py` conforms the same population, and
`gap_fill._fill_static_day` re-reads the watermark on both sides of the export as its race bracket.
All four look at ONE `FirePerimetersSource`, captured once and passed by reference, so a turn makes
exactly one ~11 MB walk rather than three. See `forward.py`'s `_MemoizedDirectWatermark`.

A TRUNCATED WALK IS REFUSED RATHER THAN PUBLISHED, and that is a deliberate divergence from
`ingest.wfigs.run_fire_perimeters_ingestion_job`, which reports `truncated=True` and writes anyway.
It can: it refreshes one row per incident IN PLACE, so a perimeter missing from a clipped fetch
keeps its previous row and stays on the map. This lane writes a FULL RE-SNAPSHOT under a version
stamp, so the same clipped fetch would publish a version with those perimeters DELETED -- a silent
loss no later census could distinguish from the fire actually being out. There is no half-version to
write, so a clipped walk fails the turn by name and the previous version keeps serving.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, resolve_bounded_bbox, resolve_max_source_records
from agri_data_service.ingest.wfigs import (
    WFIGS_BOUNDS,
    fetch_fire_perimeters_walk,
    oversized_refusal_reason,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from agri_data_service.ingest.arcgis import AdaptiveWalkOutcome


class FirePerimetersSourceError(RuntimeError):
    """Raised when WFIGS cannot be read as a complete, well-formed current-incident population."""


class FirePerimetersTruncatedError(FirePerimetersSourceError):
    """Raised when the walk returned a population WFIGS itself said was clipped.

    Separate from the base error because it is not a transport failure and retrying the same request
    cannot clear it: the record ceiling, the total byte budget and an undeliverable record are all
    properties of the request and the feed, not of one attempt. An operator raising
    `INGEST_MAX_SOURCE_RECORDS`, widening `INGEST_BBOX` or accepting the named loss is the only way
    past it, so the turn says which of the three happened rather than spinning.
    """


@dataclass(frozen=True, slots=True)
class FirePerimetersSource:
    """One complete WFIGS `_Current` capture: the population, the bbox it covers, and WHEN.

    `fetched_at` is the whole lane's clock. It is the instant this writer observed the population,
    and `watermark.py` promotes it to a version stamp ONLY when the population differs from the
    version already published -- exactly as `sql/ingest/refresh_features.sql`'s `IS DISTINCT FROM`
    gate is what makes `geo.features.updated_at` a change clock rather than a poll clock.
    """

    perimeters: tuple[Mapping[str, object], ...]
    bbox: str
    fetched_at: datetime
    bytes_read: int


async def fetch_fire_perimeters_source(
    *,
    bbox: str | None = None,
    retry_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> FirePerimetersSource:
    """Walk every bounded page of WFIGS `_Current` once, refusing a clipped or unbounded answer.

    An unconfigured `INGEST_BBOX` is a REFUSAL here, where `run_fire_perimeters_ingestion_job`
    returns `skipped_result`. That job's skip is safe because skipping leaves every existing
    `geo.features` row untouched; this writer's only two options at a version boundary are "publish
    this population" and "publish nothing", and publishing a population fetched over an unstated
    extent would stamp a version whose coverage nobody can cite.
    """
    area = resolve_bounded_bbox(bbox)
    if area is None:
        raise FirePerimetersSourceError(
            f"{UNCONFIGURED_BBOX_REASON}, so there is no bounded extent this version could claim to cover"
        )

    async def walk_once() -> FirePerimetersSource:
        async with upstream_client(WFIGS_BOUNDS) as client:
            perimeters, outcome = await fetch_fire_perimeters_walk(client, area)
        _refuse_incomplete_walk(perimeters, outcome, bbox=area)
        return FirePerimetersSource(
            perimeters=tuple(perimeters),
            bbox=area,
            fetched_at=datetime.now(UTC),
            bytes_read=outcome.bytes_read,
        )

    return await _retry_async(
        f"WFIGS _Current walk over {area}",
        walk_once,
        attempts=retry_attempts,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
    )


def _refuse_incomplete_walk(
    perimeters: Sequence[Mapping[str, object]],
    outcome: AdaptiveWalkOutcome,
    *,
    bbox: str,
) -> None:
    """Raise when the walk reports any of the three ways a population can come back clipped.

    All three are named separately because the operator action differs: a record ceiling wants
    `INGEST_MAX_SOURCE_RECORDS` raised, an `exceededTransferLimit`/byte-budget stop wants the extent
    or the budget revisited, and an oversized record is a specific named perimeter WFIGS could not
    deliver at all inside `WFIGS_BOUNDS.max_bytes`.
    """
    seen = len(perimeters)
    ceiling = resolve_max_source_records()
    truncated = outcome.truncated
    oversized = outcome.oversized
    if seen > ceiling:
        raise FirePerimetersTruncatedError(
            f"WFIGS returned {seen} perimeters over {bbox}, past the {ceiling}-record INGEST_MAX_SOURCE_RECORDS "
            "ceiling; a full re-snapshot may not be published from a population this writer would have to "
            "slice, because the slice would publish the dropped perimeters as extinguished"
        )
    if truncated:
        raise FirePerimetersTruncatedError(
            f"the WFIGS walk over {bbox} stopped short of the whole feed (record ceiling, page ceiling or the "
            "128 MiB total byte budget); a version stamped on a clipped population would publish every "
            "perimeter it missed as gone"
        )
    if oversized:
        raise FirePerimetersTruncatedError(
            f"the WFIGS walk over {bbox} could not deliver every record: {oversized_refusal_reason(oversized)}. "
            "The PostgreSQL job survives this by leaving those incidents' existing rows in place; a full "
            "re-snapshot has no such fallback and refuses instead"
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

    `FirePerimetersTruncatedError` is deliberately NOT retried: it is a property of the request and
    the feed, not of one attempt, so re-walking would spend the whole budget reproducing it.
    """
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except FirePerimetersTruncatedError:
            raise
        except Exception as error:  # every transport/parse failure is retried the same way
            last_error = error
            if attempt >= attempts:
                break
            await asyncio.sleep(_retry_delay(attempt, base_seconds=base_seconds, max_seconds=max_seconds))
    assert last_error is not None  # attempts >= 1 is enforced by the caller's config validation
    raise FirePerimetersSourceError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


__all__ = [
    "FirePerimetersSource",
    "FirePerimetersSourceError",
    "FirePerimetersTruncatedError",
    "fetch_fire_perimeters_source",
]
