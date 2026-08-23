"""What a stream's partition DAY means: three lane natures, and the watermark a static lane keys to.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor
SQLAlchemy, httpx, asyncpg, or click. See `AGENTS.md` in this directory for the rationale.

THE PARTITION DAY IS NOT ONE THING ACROSS TWELVE STREAMS, and pretending it was is the defect
this module closes. `layer=<slug>/kind=observed/zoom=NN/year=/month=/day=` renders the same for all
of them, so the layout invited one interpretation -- "the day this was observed" -- onto three lanes
where no observation happened. A HUC12 boundary is not a measurement taken on a date; it is a
reference fact with a VERSION, and the day in its path is a version stamp. There is therefore no
per-day obligation for a static lane to miss.

A VERSION STAMP IS COARSER THAN THE CHANGE IT STANDS FOR, and that gap is the second defect this
module closes. Folding the source's change time down to a UTC date makes every later change on the
SAME day invisible: after day D's first snapshot a day-only comparison reads `current` until UTC
midnight. On `evacuation-zones` that is a life-safety failure, because an Oregon OEM level moves
from Be Ready to Go Now within hours. So the watermark carries the INSTANT beside the day, and
currency inside the watermark day is decided by comparing the export instant against it. The day in
the path does not change -- a static lane's export is a full re-export of the whole population, so
re-exporting day D IS how that version is corrected.

EVERY QUESTION HERE IS ASKED OF ONE ZOOM TIER, and the tier is required rather than defaulted. A
version stamp belongs to a tier: `zoom=00` holding day D says nothing about whether `zoom=13` was
ever written for it, so a tier-blind "newest covered day" would answer a z13 reader with a z0
snapshot and report a reference set current while its most detailed tier is empty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_partition_path,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date, datetime

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

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
    never an unattributed number. `instant` is that same change time BEFORE it was folded to a day,
    and is `None` for a lane with no source system to ask; without it currency can only be settled
    at day resolution, which cannot see a second change made later the same day.
    """

    day: date | None
    basis: str
    instant: datetime | None = None

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise LaneContractError(
                "a source watermark must cite the columns it came from; an uncited version stamp reads "
                "as a measurement and cannot be re-derived"
            )
        if self.instant is not None and self.instant.tzinfo is None:
            raise LaneContractError(
                "a source watermark instant came back timezone-naive; assuming a zone for a version stamp "
                f"would shift it by up to a day and could read a stale export as current ({self.basis})"
            )
        if self.day is None and self.instant is not None:
            raise LaneContractError(
                "a source watermark with no day cannot carry a change instant; an empty population has "
                f"nothing that changed ({self.basis})"
            )


def _newest_matching_day(  # noqa: PLR0913 - the six name one tier of one stream and which objects count
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    keys: Iterable[str],
    *,
    include_data: bool,
    include_markers: bool,
) -> date | None:
    """Return the newest day of one stream at one tier among the object kinds asked for, from keys alone."""
    newest: date | None = None
    for key in keys:
        parsed = try_parse_partition_path(key)
        if parsed is not None:
            if include_data and parsed.layer == layer and parsed.kind == kind and parsed.zoom == zoom:
                newest = parsed.day if newest is None else max(newest, parsed.day)
            continue
        marker = try_parse_absence_marker_path(key)
        if (
            marker is not None
            and include_markers
            and marker.layer == layer
            and marker.kind == kind
            and marker.zoom == zoom
        ):
            newest = marker.day if newest is None else max(newest, marker.day)
    return newest


def newest_data_day(*, layer: str, kind: PartitionKind, zoom: ZoomTier, keys: Iterable[str]) -> date | None:
    """Return the newest day of one stream's tier that holds a real part file, from keys alone.

    A static lane has no window to diff, so `partition_day_statuses` -- which needs one -- cannot
    answer its coverage question. What it needs instead is the newest VERSION already published,
    across the whole stream, and that is still a listing rather than a scan: `layer-lanes.md` §4's
    rule holds here unchanged.
    """
    return _newest_matching_day(layer, kind, zoom, keys, include_data=True, include_markers=False)


def newest_marker_day(*, layer: str, kind: PartitionKind, zoom: ZoomTier, keys: Iterable[str]) -> date | None:
    """Return the newest day of one stream's tier that holds a governed-absence marker, from keys alone."""
    return _newest_matching_day(layer, kind, zoom, keys, include_data=False, include_markers=True)


def newest_covered_day(*, layer: str, kind: PartitionKind, zoom: ZoomTier, keys: Iterable[str]) -> date | None:
    """Return the newest day of one stream's tier holding EITHER a part file or an absence marker.

    This merged answer may NOT decide whether a static lane is current -- `resolve_static_lane` takes
    the two days apart, because for a version stamp a marker and a part file make opposite claims.
    See that function's docstring for the asymmetry. It remains the right answer for a lane asking
    only "how far has this TIER of the stream been carried", such as the calendar dimension's own
    watermark -- and it is never the answer for the ladder as a whole, which has four of them.
    """
    return _newest_matching_day(layer, kind, zoom, keys, include_data=True, include_markers=True)


@dataclass(frozen=True, slots=True)
class StaticLaneVerdict:
    """Whether a static lane owes a snapshot, and if so which day that snapshot must carry."""

    state: StaticLaneState
    version_day: date | None
    detail: str


def _render_instant(value: datetime) -> str:
    """Render one instant for a verdict's detail string, always in UTC so two are comparable by eye."""
    return value.astimezone(UTC).isoformat()


def _resolve_watermark_day(
    *,
    watermark: SourceWatermark,
    version_day: date,
    export_instant: datetime | None,
) -> StaticLaneVerdict:
    """Decide currency for a version stamped ON the watermark day, where the day alone cannot.

    An export READS the source at write time, so a part file written at or after the source's own
    change instant necessarily captured that change; written before it, it cannot hold that change
    however recent its version stamp looks. When either instant is unknown the two states are
    indistinguishable, and the verdict says so rather than dressing a day-resolution answer up as an
    instant-resolution one.
    """
    if watermark.instant is None or export_instant is None:
        unknown = (
            "the source reported no change instant"
            if watermark.instant is None
            else "the store reported no export instant for that version"
        )
        return StaticLaneVerdict(
            state="current",
            version_day=None,
            detail=(
                f"version {version_day.isoformat()} is current at the source watermark day, but {unknown}, "
                "so this is a DAY-RESOLUTION answer: a source change made later the same day would not be "
                f"seen ({watermark.basis})"
            ),
        )
    if export_instant >= watermark.instant:
        return StaticLaneVerdict(
            state="current",
            version_day=None,
            detail=(
                f"version {version_day.isoformat()} was exported at {_render_instant(export_instant)}, at or "
                f"after the source's own change at {_render_instant(watermark.instant)}, so that export read "
                f"the current state and this reference set is current ({watermark.basis})"
            ),
        )
    return StaticLaneVerdict(
        state="stale",
        version_day=watermark.day,
        detail=(
            f"version {version_day.isoformat()} was exported at {_render_instant(export_instant)}, BEFORE the "
            f"source changed at {_render_instant(watermark.instant)} the SAME day, so it cannot hold that "
            f"change and must be re-exported at the same version day ({watermark.basis})"
        ),
    )


def resolve_static_lane(
    *,
    watermark: SourceWatermark | None,
    newest_data_day: date | None,
    newest_data_instant: datetime | None,
    newest_marker_day: date | None,
    today: date,
) -> StaticLaneVerdict:
    """Decide a static lane's coverage from its watermark and what the object listing already holds.

    THE RULE, and it is the whole watermark model: if a PART FILE already exists dated AFTER the
    watermark day, there is nothing to do -- not a gap, not an absence, just current. Otherwise ONE
    snapshot is owed, dated at the WATERMARK day, never at the cron's run date. Nothing can be
    "missed" because no day carried an obligation in the first place.

    ON the watermark day the day cannot decide it, and `_resolve_watermark_day` compares
    `newest_data_instant` -- when that day's part files were exported -- against the watermark's own
    instant. A `stale` verdict there names the SAME day as the version owed: a static lane re-exports
    its whole population, so overwriting day D is exactly how that version is corrected.

    A GOVERNED ABSENCE IS NOT COVERAGE HERE, AND THAT ASYMMETRY IS THE POINT. For a `daily_series` a
    marker is terminal: the day is settled and the source genuinely had nothing to give. For a
    `static_lookup` the day is a VERSION STAMP, so "nothing at version W" contradicts a watermark
    that asserts version W exists over N published rows -- it is a FAILED READ of that version, not a
    settled fact, and it must be retried. Counting it as coverage latched the lane to `current`
    forever, because a static watermark is meant to sit still and would never move past the marker.
    `source_empty` already exists for the honestly-empty source, and it is reached from
    `watermark.day is None` above, never from a marker.

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
    if newest_data_instant is not None and newest_data_instant.tzinfo is None:
        raise LaneContractError(
            "the newest version's export instant came back timezone-naive; comparing it against a version "
            f"stamp would shift the answer by up to a day. Basis: {watermark.basis}"
        )
    if newest_data_day is not None and newest_data_day > watermark.day:
        return StaticLaneVerdict(
            state="current",
            version_day=None,
            detail=(
                f"version {newest_data_day.isoformat()} is later than the source watermark "
                f"{watermark.day.isoformat()}, so this reference set is current ({watermark.basis})"
            ),
        )
    if newest_data_day is not None and newest_data_day == watermark.day:
        return _resolve_watermark_day(
            watermark=watermark,
            version_day=newest_data_day,
            export_instant=newest_data_instant,
        )
    if newest_marker_day is not None and newest_marker_day >= watermark.day:
        return StaticLaneVerdict(
            state="stale",
            version_day=watermark.day,
            detail=(
                f"a governed absence at version {newest_marker_day.isoformat()} is the only coverage at or "
                f"after the source watermark {watermark.day.isoformat()}, but that watermark asserts this "
                f"version HAS rows ({watermark.basis}). For a static lookup the day is a version stamp, so "
                "an empty read of it is a failed read to RETRY, never a settled absence"
            ),
        )
    return StaticLaneVerdict(
        state="stale",
        version_day=watermark.day,
        detail=(
            f"the source changed at {watermark.day.isoformat()} and the newest version held is "
            f"{'none' if newest_data_day is None else newest_data_day.isoformat()} "
            f"({watermark.basis})"
        ),
    )
