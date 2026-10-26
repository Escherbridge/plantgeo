"""The USDM implementation of the drought layer's source protocol: United States, weekly Tuesdays.

Fetches one settled USDM release straight from the archive, bounded and retried. Never touches
Postgres. See `AGENTS.md` in this directory, section "The source protocol".

Reuses `ingest.usdm.fetch_drought_release` -- the exact dated-release adapter the Postgres ingestion
path fetches and parses through -- because the fetch-and-parse step never wrote to PostgreSQL in the
first place; only `PostgresDroughtStore` did. Importing the pure fetch/parse function is not
importing the write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.usdm import USDM_BOUNDS, fetch_drought_release
from agri_data_service.pipeline.lanes._shared.retry import retry_with_backoff
from agri_data_service.pipeline.lanes.drought.products import release_weeks

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.ingest.usdm import DroughtRelease
    from agri_data_service.pipeline.lanes.drought.source_protocol import DroughtSource

#: The region manifest's `source_slug` for this implementation (`foundation/region/pnw.json`).
USDM_SOURCE_SLUG: Final = "usdm"

#: The U.S. Drought Monitor maps the United States and its territories only; a source-system fact,
#: not this deployment's footprint (`foundation/region/AGENTS.md`, "Source coverage claims").
USDM_COVERAGE: Final = SourceCoverageClaim(coverage="regional", iso_country_codes=("US",))


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

    return await retry_with_backoff(
        fetch_once,
        attempts=retry_attempts,
        base_seconds=retry_base_seconds,
        max_seconds=retry_max_seconds,
        exhausted=DroughtSourceError,
        label=f"USDM {day.isoformat()} source fetch",
    )


class UsdmDroughtSource:
    """The pilot's drought binding: the U.S. Drought Monitor's weekly Tuesday archive.

    A class rather than the bare module functions so the binding is a VALUE the region manifest can
    resolve to and `pipeline/source_bindings.py` can put in a table, and so `isinstance` against the
    `runtime_checkable` `DroughtSource` protocol is a real conformance check rather than a comment.
    Every method delegates to this module's existing functions; it adds no behaviour of its own.
    """

    source_slug = USDM_SOURCE_SLUG
    coverage = USDM_COVERAGE

    def release_days(self, first_day: date, last_day: date) -> tuple[date, ...]:
        """Every USDM release Tuesday in the window, through the lane's one canonical Tuesday walk."""
        return release_weeks(first_day, last_day)

    async def fetch_release_day(
        self,
        day: date,
        *,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> DroughtDaySource:
        """Fetch one dated USDM release; see `fetch_drought_day`, which this only forwards to."""
        return await fetch_drought_day(
            day,
            retry_attempts=retry_attempts,
            retry_base_seconds=retry_base_seconds,
            retry_max_seconds=retry_max_seconds,
        )


#: The single instance the manifest's `usdm` binding resolves to; stateless, so one is enough.
USDM_DROUGHT_SOURCE: DroughtSource = UsdmDroughtSource()


__all__ = [
    "USDM_COVERAGE",
    "USDM_DROUGHT_SOURCE",
    "USDM_SOURCE_SLUG",
    "DroughtDaySource",
    "DroughtSourceError",
    "UsdmDroughtSource",
    "fetch_drought_day",
]
