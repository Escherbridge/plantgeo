"""Reconcile the drought lane's written Parquet partitions against what USDM itself holds.

Layer L3 (pipeline): may import `foundation`, `warehouse`, `pipeline`, `db`, `ingest`; may NOT
import `method`, `planes`, or `interface`. Compares WRITTEN state (this object store's listing)
against the SOURCE SYSTEM (USDM's own archive) -- never against `geo.drought_areas` or any other
local intermediate table. Two things this repo wrote agreeing with each other proves only that the
code agrees with itself; it says nothing about what USDM actually published.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

from agri_data_service.foundation.parquet.paths import partition_day_statuses, try_parse_partition_path
from agri_data_service.ingest.usdm import fetch_drought_release
from agri_data_service.ingest.usdm_history import usdm_release_weeks
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    from datetime import date

    import httpx

    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only stream this lane ever writes (docs/lanes/drought.md section 5); never `"forecast"`.
DROUGHT_OBSERVED_KIND: Final = "observed"

NOT_CHECKED_ALREADY_WRITTEN: Final = "not checked against USDM: this warehouse already wrote the release"
NOT_CHECKED_ALREADY_RECORDED: Final = "not checked against USDM: a governed-absence marker is already recorded"
NOT_CHECKED_CONFLICT: Final = (
    "not checked against USDM: this day carries BOTH a part file and an absence marker, "
    "which only a manual admin action produces"
)

DroughtWeekStatus = Literal["written", "recorded_absence", "conflict", "source_gap", "unrecorded_absence"]


@dataclass(frozen=True, slots=True)
class DroughtWeekReconciliation:
    """One release Tuesday's verdict: what this warehouse holds, and what USDM said when asked."""

    valid_date: date
    status: DroughtWeekStatus
    source_response: str


@dataclass(frozen=True, slots=True)
class DroughtReconciliationReport:
    """Every release Tuesday in one window, each named with its own verdict -- never a bare count.

    Carries `lane` explicitly (rather than leaving it implicit in "this came from the drought
    module") so a failure surfaced from this report always names the release date, the lane, and
    the source response together, per `layer-lanes.md` section 4.
    """

    first_day: date
    last_day: date
    weeks: tuple[DroughtWeekReconciliation, ...]
    lane: str = DROUGHT_STREAM

    @property
    def gaps(self) -> tuple[DroughtWeekReconciliation, ...]:
        """Weeks USDM confirms it published that this warehouse never wrote: the real defects."""
        return tuple(week for week in self.weeks if week.status == "source_gap")

    @property
    def conflicts(self) -> tuple[DroughtWeekReconciliation, ...]:
        """Weeks carrying both a part file and an absence marker; only a manual admin action makes one."""
        return tuple(week for week in self.weeks if week.status == "conflict")


class UsdmSourceCheck(Protocol):
    """The read-only seam a reconciliation asks whether USDM ever published one release Tuesday."""

    async def was_published(self, valid_date: date) -> bool:
        """Return whether USDM's own archive holds a release for this Tuesday."""
        ...


@dataclass(frozen=True, slots=True)
class HttpUsdmSourceCheck:
    """Production `UsdmSourceCheck`: ask USDM directly through the shipped dated-release adapter."""

    client: httpx.AsyncClient

    async def was_published(self, valid_date: date) -> bool:
        """Fetch the exact archive file for `valid_date`; USDM's documented 404 answers False."""
        release = await fetch_drought_release(self.client, valid_date.isoformat())
        return release is not None


async def reconcile_drought_releases(
    store: ObjectStore,
    source: UsdmSourceCheck,
    *,
    first_day: date,
    last_day: date,
) -> DroughtReconciliationReport:
    """Classify every USDM release Tuesday in `[first_day, last_day]` by what was written vs. published.

    `usdm_release_weeks` is the same canonical Tuesday walk `ingest/usdm_history.py` trusts for the
    live backfill, reused rather than restated so "what counts as a release week" cannot drift
    between ingest and validation. Only a week neither written nor recorded absent ever reaches
    `source`: the common case (already written, or already carrying a governed-absence marker) is
    settled from one listing, so a multi-year window costs one USDM request per genuinely
    unresolved week, not one per week in the span.
    """
    weeks = usdm_release_weeks(first_day, last_day)
    if not weeks:
        return DroughtReconciliationReport(first_day=first_day, last_day=last_day, weeks=())
    keys = store.list_partition_keys(DROUGHT_STREAM, DROUGHT_OBSERVED_KIND)
    statuses = partition_day_statuses(
        layer=DROUGHT_STREAM,
        kind=DROUGHT_OBSERVED_KIND,
        first_day=weeks[0].release_date,
        last_day=weeks[-1].release_date,
        keys=keys,
    )
    verdicts = tuple([await _reconcile_week(week.release_date, statuses[week.release_date], source) for week in weeks])
    return DroughtReconciliationReport(first_day=first_day, last_day=last_day, weeks=verdicts)


async def _reconcile_week(
    valid_date: date,
    local_status: PartitionDayStatus,
    source: UsdmSourceCheck,
) -> DroughtWeekReconciliation:
    """Resolve one Tuesday's verdict, asking the source only when the listing left it unresolved."""
    if local_status == "data":
        return DroughtWeekReconciliation(valid_date, "written", NOT_CHECKED_ALREADY_WRITTEN)
    if local_status == "absent":
        return DroughtWeekReconciliation(valid_date, "recorded_absence", NOT_CHECKED_ALREADY_RECORDED)
    if local_status == "conflict":
        return DroughtWeekReconciliation(valid_date, "conflict", NOT_CHECKED_CONFLICT)
    published = await source.was_published(valid_date)
    if published:
        return DroughtWeekReconciliation(
            valid_date,
            "source_gap",
            f"USDM has a published release for {valid_date.isoformat()}; this warehouse never wrote it",
        )
    return DroughtWeekReconciliation(
        valid_date,
        "unrecorded_absence",
        f"USDM has not published a release for {valid_date.isoformat()}",
    )


def written_release_span(store: ObjectStore) -> tuple[date, date] | None:
    """Return `(oldest, newest)` `valid_date` this warehouse has actually written, or `None`.

    Computed from the object store's own listing -- never from `geo.drought_areas` or any other
    local intermediate table -- so a reconciliation window is never anchored to the unverified
    ~2022-08 floor `docs/lanes/drought.md` section 7 explicitly flags as inferred, not measured.
    """
    keys = store.list_partition_keys(DROUGHT_STREAM, DROUGHT_OBSERVED_KIND)
    written_days = {parsed.day for parsed in (try_parse_partition_path(key) for key in keys) if parsed is not None}
    if not written_days:
        return None
    return min(written_days), max(written_days)
