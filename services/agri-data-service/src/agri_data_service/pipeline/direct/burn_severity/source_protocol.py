"""The burn-severity layer's source contract: coverage, governed release days, and one bounded pull.

See `AGENTS.md` in this directory, section "The source protocol", for what the layer owns, what a
source owns, and why this layer's pull signature carries an ignition-year cohort the others do not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim

#: A WGS84 west/south/east/north tuple. Declared here rather than imported from `ingest/mtbs.py`
#: (which also defines it) so the layer's contract does not name one source's module; the two
#: aliases collapse when the shared alias moves down into `foundation`.
BoundingBox = tuple[float, float, float, float]


@runtime_checkable
class BurnSeverityReleaseDay(Protocol):
    """One governed release day as the layer consumes it: the day, its cohorts, rows, and read clock.

    `records` may be an empty tuple and that is an honest zero, not a fetch failure: a real fire
    year whose whole cohort falls outside the deployment's envelope publishes nothing here. Unlike
    the drought layer there is no "not published yet" state -- every governed release day is already
    a past release. Timestamps are UTC (`federation.md` §2).
    """

    day: date
    ignition_years: tuple[int, ...]
    records: tuple[object, ...]
    fetched_at: datetime


@runtime_checkable
class BurnSeveritySource(Protocol):
    """One region's burn-severity source: coverage, the governed release calendar, and a dated pull.

    Implemented by `mtbs.py` for the pilot. The pull takes the ignition-year cohorts the release day
    maps to and the envelope to pull inside, because a burn-severity release is published per fire
    year rather than per calendar day; the envelope is the region manifest's, passed in
    (`layer-lanes.md` §1b), never a module constant.
    """

    #: The manifest's `source_slug` for this implementation, e.g. `"mtbs"`.
    source_slug: str
    #: Where this source can fill the burn-severity layer at all (`federation.md` §2).
    coverage: SourceCoverageClaim

    def release_days(self) -> tuple[date, ...]:
        """Every release day this source has governed evidence for, oldest first.

        No window argument, unlike the drought layer's calendar walk: this set grows only through a
        governance action dating a fire year's completion, never through the passage of time, so the
        whole candidate set is always small enough to return entire.
        """
        ...

    def ignition_years_by_release_day(self) -> dict[date, tuple[int, ...]]:
        """Each governed release day mapped to the ignition-year cohort(s) it publishes."""
        ...

    async def fetch_release_day(
        self,
        day: date,
        ignition_years: Sequence[int],
        *,
        bounding_box: BoundingBox,
        retry_attempts: int,
        retry_base_seconds: float,
        retry_max_seconds: float,
    ) -> BurnSeverityReleaseDay:
        """Page every cohort mapped to `day` to completion inside `bounding_box`, as one retry unit."""
        ...


__all__ = [
    "BoundingBox",
    "BurnSeverityReleaseDay",
    "BurnSeveritySource",
]
