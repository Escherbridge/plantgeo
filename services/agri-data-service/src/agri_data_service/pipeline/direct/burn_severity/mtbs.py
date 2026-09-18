"""The MTBS implementation of the burn-severity source protocol: United States, governed fire years.

Fetches one governed MTBS release day straight from the EDW feature service. Never touches Postgres.
See `AGENTS.md` in this directory, section "The source protocol".

Reuses `ingest.mtbs.fetch_release_features` / `build_mtbs_record` / `release_observation_window` /
`validate_release_window` -- the exact paged-capture-and-parse step the Postgres ingestion path
fetches and normalises through -- because the fetch-and-parse step never wrote to PostgreSQL in the
first place; only the shared `ingest.writer.FeatureWriter` did, through `run_mtbs_ingestion_job`.
Importing the pure fetch/parse functions is not importing the write path.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.mtbs import (
    MTBS_BOUNDS,
    build_mtbs_record,
    fetch_release_features,
    release_observation_window,
    resolve_data_available_at,
    validate_release_window,
)
from agri_data_service.pipeline.direct.burn_severity.products import (
    governed_release_days,
    release_days_by_ignition_year,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from datetime import date

    from agri_data_service.ingest.mtbs import BoundingBox, MtbsBurnSeverityRecord
    from agri_data_service.pipeline.direct.burn_severity.source_protocol import BurnSeveritySource

#: The region manifest's `source_slug` for this implementation (`foundation/region/pnw.json`).
MTBS_SOURCE_SLUG: Final = "mtbs"

#: Monitoring Trends in Burn Severity maps United States fires only; a source-system fact, not this
#: deployment's footprint (`foundation/region/AGENTS.md`, "Source coverage claims").
MTBS_COVERAGE: Final = SourceCoverageClaim(coverage="regional", iso_country_codes=("US",))


class BurnSeverityFetchError(RuntimeError):
    """Raised when a governed release day's cohorts cannot be read as complete from MTBS's own service."""


@dataclass(frozen=True, slots=True)
class BurnSeverityDaySource:
    """One release day's UNION of every ignition-year cohort MTBS resolves to that day.

    `ignition_years` is always non-empty -- a release day only exists in this lane's candidate set
    because `products.py::release_days_by_ignition_year` derived it from at least one governed
    cohort. `records` may still be an empty tuple: a real fire year whose entire cohort falls
    outside this deployment's bounding box is an honest zero, not a fetch failure -- there is no
    "not published yet" state for this lane the way USDM's weekly Tuesday has one
    (`docs/lanes/burn-severity.md` section 3: every entry in `MTBS_ANNUAL_RELEASE_DATES` is already
    a real, past release).
    """

    day: date
    ignition_years: tuple[int, ...]
    records: tuple[MtbsBurnSeverityRecord, ...]
    fetched_at: datetime


async def fetch_burn_severity_release_day(  # noqa: PLR0913 - drought's fetch plus the two coordinates only this lane has
    day: date,
    ignition_years: Sequence[int],
    *,
    bounding_box: BoundingBox,
    retry_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
) -> BurnSeverityDaySource:
    """Page every ignition-year cohort mapped to `day` to completion, as one bounded retried unit.

    A single `httpx.AsyncClient` is shared across every cohort in the union, matching
    `ingest_mtbs`'s own per-run client reuse (`ingest/mtbs.py:909-941`). The whole union is one
    retry unit rather than one retry per cohort: a release day with two cohorts must publish both
    or neither, so a transient failure on the second cohort must not leave the first cohort's
    already-fetched rows unaccounted for by the caller's own retry accounting.
    """
    if not ignition_years:
        raise BurnSeverityFetchError(f"burn-severity release day {day.isoformat()} maps to no ignition-year cohort")

    async def fetch_once() -> BurnSeverityDaySource:
        records: list[MtbsBurnSeverityRecord] = []
        async with upstream_client(MTBS_BOUNDS) as client:
            for ignition_year in ignition_years:
                features, _authoritative_count = await fetch_release_features(
                    ignition_year, bounding_box, client=client
                )
                cohort = [build_mtbs_record(feature, ignition_year) for feature in features]
                if cohort:
                    # The same two tripwires `capture_release`/`fetch_mtbs_records` apply
                    # (`ingest/mtbs.py:1079-1084`): a release date that does not lead its cohort's
                    # last ignition by the governed floor is ignition-shaped, and one landing on
                    # `now()` is a clock reading wearing a release date.
                    _observed_from, observed_to = release_observation_window(cohort)
                    validate_release_window(resolve_data_available_at(ignition_year), observed_to)
                    resolved_day = resolve_data_available_at(ignition_year).date()
                    if resolved_day != day:
                        raise BurnSeverityFetchError(
                            f"ignition year {ignition_year}'s cohort resolves to release day "
                            f"{resolved_day.isoformat()}, not the requested {day.isoformat()}; "
                            "MTBS_ANNUAL_RELEASE_DATES may have changed between the caller's mapping "
                            "and this fetch"
                        )
                records.extend(cohort)
        return BurnSeverityDaySource(
            day=day,
            ignition_years=tuple(ignition_years),
            records=tuple(records),
            fetched_at=datetime.now(UTC),
        )

    return await _retry_async(
        f"MTBS release day {day.isoformat()} fetch",
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
    raise BurnSeverityFetchError(f"{label} failed after {attempts} attempts") from last_error


def _retry_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    ceiling = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return float(ceiling + random.uniform(0.0, min(1.0, ceiling / 4)))


class MtbsBurnSeveritySource:
    """The pilot's burn-severity binding: MTBS's Burned Area Boundaries on the USDA EDW service.

    A class rather than the bare module functions so the binding is a VALUE the region manifest can
    resolve to and `pipeline/source_bindings.py` can put in a table, and so `isinstance` against the
    `runtime_checkable` `BurnSeveritySource` protocol is a real conformance check rather than a
    comment. Every method delegates to existing functions; it adds no behaviour of its own.
    """

    source_slug = MTBS_SOURCE_SLUG
    coverage = MTBS_COVERAGE

    def release_days(self) -> tuple[date, ...]:
        """Every governed MTBS release day, through the lane's one governed release-day table."""
        return governed_release_days()

    def ignition_years_by_release_day(self) -> dict[date, tuple[int, ...]]:
        """Each governed release day mapped to its ignition-year cohort(s), read from the same table."""
        return release_days_by_ignition_year()

    async def fetch_release_day(  # noqa: PLR0913 - the protocol's own signature; see source_protocol.py
        self,
        day: date,
        ignition_years: Sequence[int],
        *,
        bounding_box: BoundingBox,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> BurnSeverityDaySource:
        """Fetch one governed release day; see `fetch_burn_severity_release_day`, which this forwards to."""
        return await fetch_burn_severity_release_day(
            day,
            ignition_years,
            bounding_box=bounding_box,
            retry_attempts=retry_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )


#: The single instance the manifest's `mtbs` binding resolves to; stateless, so one is enough.
MTBS_BURN_SEVERITY_SOURCE: BurnSeveritySource = MtbsBurnSeveritySource()


__all__ = [
    "MTBS_BURN_SEVERITY_SOURCE",
    "MTBS_COVERAGE",
    "MTBS_SOURCE_SLUG",
    "BurnSeverityDaySource",
    "BurnSeverityFetchError",
    "MtbsBurnSeveritySource",
    "fetch_burn_severity_release_day",
]
