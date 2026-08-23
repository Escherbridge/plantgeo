"""What a stream's partition DAY means: three lane natures, and the watermark a static lane keys to.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor
SQLAlchemy, httpx, asyncpg, or click. See `AGENTS.md` in this directory for the rationale.

THE PARTITION DAY IS NOT ONE THING ACROSS TWELVE STREAMS, and pretending it was is the defect
this module closes. `layer=<slug>/kind=observed/year=/month=/day=` renders the same for all of
them, so the layout invited one interpretation -- "the day this was observed" -- onto three lanes
where no observation happened. A HUC12 boundary is not a measurement taken on a date; it is a
reference fact with a VERSION, and the day in its path is a version stamp. There is therefore no
per-day obligation for a static lane to miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_partition_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind

# daily_series   -- genuine per-day observations. The partition day IS the observation day.
#                   Forecastable. Every cadence step in the window is a candidate to fill.
# release_series -- discrete dated publications (USDM weekly, MTBS quarterly). Each release IS a
#                   dated fact, so the partition day is the release's own valid/issue date.
#                   Forecastable only where the release history supports it.
# static_lookup  -- reference data with a VERSION and no time axis. The partition day is a version
#                   stamp. NEVER forecastable: there is no series to project along.
LaneNature = Literal["daily_series", "release_series", "static_lookup"]

LANE_NATURES: Final[tuple[LaneNature, ...]] = ("daily_series", "release_series", "static_lookup")

# What a static lane's coverage actually is, reported as a first-class state rather than inferred
# from a zero gap count. "0 gaps because the lane is current" and "0 gaps because nobody looked"
# are different claims, and a census that renders them identically is lying by omission.
StaticLaneState = Literal["current", "stale", "source_empty", "watermark_unread"]


class LaneContractError(ValueError):
    """Raised when a nature is unknown, or a source watermark cannot be honestly acted on."""


def validate_lane_nature(value: str) -> LaneNature:
    """Return `value` narrowed to one of the three natures, else raise."""
    if value in LANE_NATURES:
        # `value in LANE_NATURES` does not narrow a `str` for mypy, so restate the three arms.
        if value == "daily_series":
            return "daily_series"
        if value == "release_series":
            return "release_series"
        return "static_lookup"
    raise LaneContractError(f"lane nature {value!r} must be one of {LANE_NATURES}")


def nature_has_time_axis(nature: LaneNature) -> bool:
    """True when the partition day is a time the source itself stamped, not a version we assigned."""
    return nature != "static_lookup"


def nature_permits_forecast(nature: LaneNature) -> bool:
    """True when a lane of this nature MAY ship a forecaster; a static lookup never may."""
    return nature_has_time_axis(nature)


def nature_permits_cadence(nature: LaneNature) -> bool:
    """True only for `release_series`: a step above one day is a publication rhythm, not a gap filter.

    A `daily_series` with a cadence above one is a contradiction -- it would declare every day an
    observation and then decline to look at most of them. A `static_lookup` has no step at all.
    """
    return nature == "release_series"


@dataclass(frozen=True, slots=True)
class SourceWatermark:
    """The source's own answer to "when did this reference set last change", plus what answered it.

    `day` is `None` when the source holds nothing at all -- an honest empty population, not a
    failure and not a gap. `basis` names the columns that produced the day, so a version stamp is
    never an unattributed number.
    """

    day: date | None
    basis: str

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise LaneContractError(
                "a source watermark must cite the columns it came from; an uncited version stamp reads "
                "as a measurement and cannot be re-derived"
            )


def newest_covered_day(*, layer: str, kind: PartitionKind, keys: Iterable[str]) -> date | None:
    """Return the newest day of one stream that holds a part file or an absence marker, from keys alone.

    A static lane has no window to diff, so `partition_day_statuses` -- which needs one -- cannot
    answer its coverage question. What it needs instead is the newest VERSION already published,
    across the whole stream, and that is still a listing rather than a scan: `layer-lanes.md` §4's
    rule holds here unchanged.
    """
    newest: date | None = None
    for key in keys:
        parsed = try_parse_partition_path(key)
        if parsed is not None and parsed.layer == layer and parsed.kind == kind:
            newest = parsed.day if newest is None else max(newest, parsed.day)
            continue
        marker = try_parse_absence_marker_path(key)
        if marker is not None and marker.layer == layer and marker.kind == kind:
            # A governed absence at a version day means the source was asked at that version and
            # had nothing. That is coverage, not a gap -- the same rule the series lanes apply.
            newest = marker.day if newest is None else max(newest, marker.day)
    return newest


@dataclass(frozen=True, slots=True)
class StaticLaneVerdict:
    """Whether a static lane owes a snapshot, and if so which day that snapshot must carry."""

    state: StaticLaneState
    version_day: date | None
    detail: str


def resolve_static_lane(
    *,
    watermark: SourceWatermark | None,
    newest_covered_day: date | None,
    today: date,
) -> StaticLaneVerdict:
    """Decide a static lane's coverage from its watermark and what the object listing already holds.

    THE RULE, and it is the whole watermark model: if a partition already exists dated at or after
    the watermark, there is nothing to do -- not a gap, not an absence, just current. Otherwise ONE
    snapshot is owed, dated at the WATERMARK day, never at the cron's run date. Nothing can be
    "missed" because no day carried an obligation in the first place.

    `watermark=None` means nobody read it this run (a listing-only `--dry-run`), which is reported
    as `watermark_unread` rather than silently as zero gaps.
    """
    if watermark is None:
        return StaticLaneVerdict(
            state="watermark_unread",
            version_day=None,
            detail=(
                "no source watermark was read this run, so this lane's coverage is UNKNOWN -- that is "
                "not the same claim as being current"
            ),
        )
    if watermark.day is None:
        return StaticLaneVerdict(
            state="source_empty",
            version_day=None,
            detail=f"the source holds no rows to version, so there is nothing to snapshot ({watermark.basis})",
        )
    if watermark.day > today:
        raise LaneContractError(
            f"source watermark {watermark.day.isoformat()} is later than {today.isoformat()}; writing an "
            f"observed partition dated in the future is never right. Basis: {watermark.basis}"
        )
    if newest_covered_day is not None and newest_covered_day >= watermark.day:
        return StaticLaneVerdict(
            state="current",
            version_day=None,
            detail=(
                f"version {newest_covered_day.isoformat()} is at or after the source watermark "
                f"{watermark.day.isoformat()}, so this reference set is current ({watermark.basis})"
            ),
        )
    return StaticLaneVerdict(
        state="stale",
        version_day=watermark.day,
        detail=(
            f"the source changed at {watermark.day.isoformat()} and the newest version held is "
            f"{'none' if newest_covered_day is None else newest_covered_day.isoformat()} "
            f"({watermark.basis})"
        ),
    )
