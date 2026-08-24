"""Partition-path computation for the object-store Parquet warehouse: build, parse, and diff.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor
SQLAlchemy, httpx, asyncpg, or click. See `AGENTS.md` in this directory for the layout
and why gap detection is a listing rather than a scan.

`zoom=` sits ABOVE `year=` (owner, 2026-08-23), which retires the earlier "the layout does not
change" rule for this one axis. Polars and DuckDB prune a whole tier by directory before reading a
byte, and serving becomes a path template rather than a scan. Zoom is ORTHOGONAL to the day, not a
second version stamp: the DAY still says which version of the data a key holds, and each tier of one
day describes that same day at a different resolution.

Zoom is REQUIRED on every builder. A default would quietly write four tiers into one prefix and the
mistake would not surface until serving read geometry at the wrong resolution -- long after the run
that caused it. A key without `zoom=` is not of this layout and does not parse: the objects written
before this axis existed are being discarded and re-drained, never migrated, so a tolerant parse
would only let a stale key read as a covered day.

A DAY PREFIX HOLDS THREE OBJECT KINDS, NOT TWO (owner, 2026-08-23; RUNBOOK 0.34.1). Alongside the
part file and the governed-absence marker there is now a COMPLETION marker, written after the last
part of an export and required before that day counts as `data`. This deliberately gives up a
constraint the layout held from the start -- "a day prefix holds exactly two kinds" -- and the cost
was weighed: a day that cannot say whether it finished is worse than a layout with three kinds.
A run killed between two part uploads leaves a PREFIX of a release behind, every part of it new, so
freshness alone reads the wreckage as a completed export. Completion is therefore ASSERTED by an
object rather than inferred from the parts, and `partition_day_statuses` reports a day holding parts
without that assertion as `incomplete` -- a fourth status, distinct from `missing` so an operator can
see the difference, and filled exactly like `missing` so the driver repairs it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, validate_zoom_tier

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agri_data_service.foundation.parquet.zoom import ZoomTier

PartitionKind = Literal["observed", "forecast"]
PartitionDayStatus = Literal["data", "absent", "conflict", "incomplete", "missing"]

PARTITION_KINDS: Final[tuple[PartitionKind, ...]] = ("observed", "forecast")
PARQUET_SUFFIX: Final = ".parquet"
PART_FILE_STEM: Final = "part-"
ABSENCE_FILE_NAME: Final = "absent.json"
# Leading underscore, so a day listing sorts it ahead of `absent.json` and `part-`, and so a reader
# scanning a day prefix by eye reads it as metadata rather than as another record of the population.
# It is JSON, never Parquet, which is what keeps every `*.parquet` scan glob in `planes/` blind to it.
COMPLETION_FILE_NAME: Final = "_complete.json"

# Explicit budgets at the boundary; every one of these has a wrong answer that is silently plausible.
MAX_PART_INDEX: Final = 9_999
MIN_PARTITION_YEAR: Final = 1_000
MAX_PARTITION_YEAR: Final = 9_999
MONTHS_PER_YEAR: Final = 12
MAX_GAP_WINDOW_DAYS: Final = 20_000

# Zero-padded like `month=` and `day=`, so a lexicographic listing walks the ladder in numeric order:
# unpadded, `zoom=13` sorts between `zoom=0` and `zoom=5` and a tier walk silently runs out of order.
ZOOM_SEGMENT_DIGITS: Final = 2

# EVERY STATUS, AND THE TWO SETS WORTH NAMING. A reader that spells its rule as a NEGATION
# (`status != "missing"`) silently accepts whatever member is added next -- which is exactly what
# `incomplete` did to four readers the day it landed. Ask for the set you mean instead.
PARTITION_DAY_STATUSES: Final[tuple[PartitionDayStatus, ...]] = (
    "data",
    "absent",
    "conflict",
    "incomplete",
    "missing",
)

#: Days that still owe a completed export. `incomplete` is here because a day holding half a
#: release owes exactly the work a day holding none does.
UNFILLED_PARTITION_STATUSES: Final[frozenset[PartitionDayStatus]] = frozenset({"missing", "incomplete"})

#: Days a reader may answer from by SERVING ROWS. Deliberately excludes `conflict`: a day carrying
#: both a release and a governed absence is an admin-only anomaly, and serving either half picks a
#: side.
#:
#: NOT the right set for choosing which day to RESOLVE. A reader that reports a conflict explicitly
#: -- `planes/evacuation_zones.py` answers one with `conflicted` -- has to see conflict days to
#: refuse them, and filtering them out here makes that refusal unreachable and quietly substitutes
#: an older day. Such readers exclude `UNFILLED_PARTITION_STATUSES` and nothing else.
COVERED_PARTITION_STATUSES: Final[frozenset[PartitionDayStatus]] = frozenset({"data", "absent"})

LAYER_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_PARTITION_PATH_PATTERN: Final = re.compile(
    r"^layer=(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"/kind=(?P<kind>observed|forecast)"
    r"/zoom=(?P<zoom>\d{2})"
    r"/year=(?P<year>\d{4})"
    r"/month=(?P<month>\d{2})"
    r"/day=(?P<day>\d{2})"
    r"/part-(?P<part_index>\d+)\.parquet$"
)

_ABSENCE_PATH_PATTERN: Final = re.compile(
    r"^layer=(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"/kind=(?P<kind>observed|forecast)"
    r"/zoom=(?P<zoom>\d{2})"
    r"/year=(?P<year>\d{4})"
    r"/month=(?P<month>\d{2})"
    r"/day=(?P<day>\d{2})"
    r"/absent\.json$"
)

_COMPLETION_PATH_PATTERN: Final = re.compile(
    r"^layer=(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"/kind=(?P<kind>observed|forecast)"
    r"/zoom=(?P<zoom>\d{2})"
    r"/year=(?P<year>\d{4})"
    r"/month=(?P<month>\d{2})"
    r"/day=(?P<day>\d{2})"
    r"/_complete\.json$"
)


class PartitionPathError(ValueError):
    """Raised when a slug, partition component, or object key breaks the frozen layout."""


@dataclass(frozen=True, slots=True)
class PartitionPath:
    """One partition file, decomposed: the inverse of `partition_path`."""

    layer: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date
    part_index: int = 0

    @property
    def key(self) -> str:
        """Rebuild the relative object key this instance was parsed from."""
        return partition_path(self.layer, self.kind, self.zoom, self.day, self.part_index)


@dataclass(frozen=True, slots=True)
class AbsenceMarkerPath:
    """One governed-absence marker, decomposed: the inverse of `absence_marker_path`."""

    layer: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date

    @property
    def key(self) -> str:
        """Rebuild the relative object key this instance was parsed from."""
        return absence_marker_path(self.layer, self.kind, self.zoom, self.day)


@dataclass(frozen=True, slots=True)
class CompletionMarkerPath:
    """One completion marker, decomposed: the inverse of `completion_marker_path`."""

    layer: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date

    @property
    def key(self) -> str:
        """Rebuild the relative object key this instance was parsed from."""
        return completion_marker_path(self.layer, self.kind, self.zoom, self.day)


def validate_layer_slug(slug: str) -> str:
    """Return `slug` if it is a lowercase hyphenated layer name, else raise."""
    if not LAYER_SLUG_PATTERN.match(slug):
        raise PartitionPathError(f"layer slug {slug!r} must be lowercase alphanumerics joined by single hyphens")
    return slug


def validate_partition_kind(kind: str) -> PartitionKind:
    """Return `kind` narrowed to the two streams a lane may publish, else raise."""
    if kind == "observed":
        return "observed"
    if kind == "forecast":
        return "forecast"
    raise PartitionPathError(f"partition kind {kind!r} must be one of {PARTITION_KINDS}")


def layer_prefix(layer: str) -> str:
    """Return the object prefix owning every stream of one layer."""
    return f"layer={validate_layer_slug(layer)}/"


def stream_prefix(layer: str, kind: PartitionKind) -> str:
    """Return the object prefix owning one layer's observed or forecast stream."""
    return f"{layer_prefix(layer)}kind={validate_partition_kind(kind)}/"


def zoom_prefix(layer: str, kind: PartitionKind, zoom: ZoomTier) -> str:
    """Return the object prefix owning one whole zoom tier of one stream: one listing lists or prunes a tier."""
    tier = validate_zoom_tier(zoom)
    return f"{stream_prefix(layer, kind)}zoom={tier:0{ZOOM_SEGMENT_DIGITS}d}/"


def year_prefix(layer: str, kind: PartitionKind, zoom: ZoomTier, year: int) -> str:
    """Return the object prefix bounding a listing to one calendar year of one stream at one tier."""
    return f"{zoom_prefix(layer, kind, zoom)}year={_validated_year(year):04d}/"


def month_prefix(layer: str, kind: PartitionKind, zoom: ZoomTier, year: int, month: int) -> str:
    """Return the object prefix bounding a listing to one calendar month of one stream at one tier."""
    return f"{year_prefix(layer, kind, zoom, year)}month={_validated_month(month):02d}/"


def day_prefix(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the object prefix holding every part file written for one day of one stream at one tier."""
    return f"{month_prefix(layer, kind, zoom, day.year, day.month)}day={day.day:02d}/"


def partition_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date, part_index: int = 0) -> str:
    """Return the relative object key for one part file of one layer-stream-zoom-day."""
    if part_index < 0 or part_index > MAX_PART_INDEX:
        raise PartitionPathError(f"part_index must be between 0 and {MAX_PART_INDEX}, got {part_index}")
    return f"{day_prefix(layer, kind, zoom, day)}{PART_FILE_STEM}{part_index}{PARQUET_SUFFIX}"


def parse_partition_path(path: str) -> PartitionPath:
    """Decompose a relative object key into its partition components, or raise."""
    parsed = try_parse_partition_path(path)
    if parsed is None:
        raise PartitionPathError(f"{path!r} is not a partition path of the frozen layout")
    return parsed


def try_parse_partition_path(path: str) -> PartitionPath | None:
    """Decompose a relative object key, returning `None` for anything that is not a part file."""
    match = _PARTITION_PATH_PATTERN.match(path.replace("\\", "/"))
    if match is None:
        return None
    part_index = int(match["part_index"])
    if part_index > MAX_PART_INDEX:
        return None
    zoom = _parsed_zoom_tier(match["zoom"])
    if zoom is None:
        return None
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    kind: PartitionKind = "observed" if match["kind"] == "observed" else "forecast"
    return PartitionPath(layer=match["layer"], kind=kind, zoom=zoom, day=day, part_index=part_index)


def absence_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key marking one stream-day at one tier as a governed absence."""
    return f"{day_prefix(layer, kind, zoom, day)}{ABSENCE_FILE_NAME}"


def try_parse_absence_marker_path(path: str) -> AbsenceMarkerPath | None:
    """Decompose a relative object key, returning `None` for anything that is not an absence marker."""
    match = _ABSENCE_PATH_PATTERN.match(path.replace("\\", "/"))
    if match is None:
        return None
    zoom = _parsed_zoom_tier(match["zoom"])
    if zoom is None:
        return None
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    kind: PartitionKind = "observed" if match["kind"] == "observed" else "forecast"
    return AbsenceMarkerPath(layer=match["layer"], kind=kind, zoom=zoom, day=day)


def completion_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key asserting that one stream-day at one tier finished exporting."""
    return f"{day_prefix(layer, kind, zoom, day)}{COMPLETION_FILE_NAME}"


def try_parse_completion_marker_path(path: str) -> CompletionMarkerPath | None:
    """Decompose a relative object key, returning `None` for anything that is not a completion marker."""
    match = _COMPLETION_PATH_PATTERN.match(path.replace("\\", "/"))
    if match is None:
        return None
    zoom = _parsed_zoom_tier(match["zoom"])
    if zoom is None:
        return None
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    kind: PartitionKind = "observed" if match["kind"] == "observed" else "forecast"
    return CompletionMarkerPath(layer=match["layer"], kind=kind, zoom=zoom, day=day)


def completed_partition_days(
    keys: Iterable[str],
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
) -> set[date]:
    """Return the days of one stream-tier whose export ASSERTED that it finished, from `keys` alone.

    The one primitive every reader shares. A day holding part files but no completion marker is a
    release that stopped part-way through uploading, and nothing -- census, validation or serving --
    may count it: `planes/` enumerate their published days by parsing part keys directly rather than
    through `partition_day_statuses`, so without this they would each have re-derived the rule, and
    the one that got it wrong would put half a release on the map.
    """
    validate_layer_slug(layer)
    validate_partition_kind(kind)
    validate_zoom_tier(zoom)
    return {
        marker.day
        for key in keys
        if (marker := try_parse_completion_marker_path(key)) is not None
        and marker.layer == layer
        and marker.kind == kind
        and marker.zoom == zoom
    }


def partition_day_statuses(  # noqa: PLR0913 - the six are one gap census's whole scope: stream, tier, window, keys.
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    first_day: date,
    last_day: date,
    keys: Iterable[str],
) -> dict[date, PartitionDayStatus]:
    """Classify every day in `[first_day, last_day]` at one tier from `keys` alone, in chronological order.

    `data` = at least one part file AND the completion marker that export wrote last; `incomplete` =
    part files with no such marker, which is a release that stopped part-way through uploading;
    `absent` = a governed-absence marker; `conflict` = data and a governed absence together, which
    only a manual admin action should ever produce; `missing` = nothing at all, a real gap.

    `incomplete` IS NOT FOLDED INTO `missing`, and the difference is the whole point of reporting it:
    both are filled, but "this day was never attempted" and "this day was attempted and the container
    died holding half a release" are different facts about the warehouse, and an operator reading a
    census that showed only `missing` could not tell a backlog from a repeated crash. A governed
    absence needs no completion marker of its own -- it is ONE object, so it cannot be half-written.

    Keys of another tier are ignored rather than counted: a day published at z0 says nothing about
    whether z13 was written, and blending the tiers would report a covered day over a real gap.
    """
    validate_layer_slug(layer)
    validate_partition_kind(kind)
    validate_zoom_tier(zoom)
    if last_day < first_day:
        raise PartitionPathError(f"gap window {first_day}..{last_day} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_GAP_WINDOW_DAYS:
        raise PartitionPathError(f"gap window of {span} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    # `keys` is an Iterable and is consumed exactly once, so the three kinds are separated in one
    # pass rather than by handing the same (possibly generator) argument to a second helper.
    part_days: set[date] = set()
    absent_days: set[date] = set()
    complete_days: set[date] = set()
    for key in keys:
        parsed = try_parse_partition_path(key)
        if parsed is not None and parsed.layer == layer and parsed.kind == kind and parsed.zoom == zoom:
            part_days.add(parsed.day)
            continue
        marker = try_parse_absence_marker_path(key)
        if marker is not None and marker.layer == layer and marker.kind == kind and marker.zoom == zoom:
            absent_days.add(marker.day)
            continue
        finished = try_parse_completion_marker_path(key)
        if finished is not None and finished.layer == layer and finished.kind == kind and finished.zoom == zoom:
            complete_days.add(finished.day)
    statuses: dict[date, PartitionDayStatus] = {}
    for offset in range(span):
        day = first_day + timedelta(days=offset)
        if day in part_days:
            # `conflict` outranks completion: a day carrying both a release and a governed absence is
            # a contradiction to escalate, and reporting it as merely unfinished would hide that.
            if day in absent_days:
                statuses[day] = "conflict"
            else:
                statuses[day] = "data" if day in complete_days else "incomplete"
        elif day in absent_days:
            statuses[day] = "absent"
        else:
            # A completion marker with no parts beside it is not a covered day. It is the residue of
            # a day whose parts were deleted out from under it, and calling it `data` would serve
            # nothing while claiming coverage -- so it falls through to `missing` and is re-exported.
            statuses[day] = "missing"
    return statuses


def missing_partition_days(  # noqa: PLR0913 - mirrors `partition_day_statuses`, whose scope it narrows.
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    first_day: date,
    last_day: date,
    keys: Iterable[str],
) -> tuple[date, ...]:
    """Return the days of one tier in `[first_day, last_day]` holding NEITHER a part file nor a marker.

    Strictly `missing`. It deliberately does NOT include `incomplete`, even though both owe work:
    the reconciliation validators report this set to an operator as days the pipeline never wrote,
    and a day holding a half-uploaded release is not one of those. Widening this set silently turned
    two of those reports into false statements, which is the failure mode this project rates worst.
    The DRIVER wants the union and asks `unfilled_partition_days` for it by name.
    """
    statuses = partition_day_statuses(
        layer=layer, kind=kind, zoom=zoom, first_day=first_day, last_day=last_day, keys=keys
    )
    return tuple(day for day, status in statuses.items() if status == "missing")


def unfilled_partition_days(  # noqa: PLR0913 - mirrors `partition_day_statuses`, whose scope it narrows.
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    first_day: date,
    last_day: date,
    keys: Iterable[str],
) -> tuple[date, ...]:
    """Return the days of one tier in `[first_day, last_day]` that still owe a COMPLETED export.

    `missing` and `incomplete` together: repairing a crash-truncated day is the same operation as
    filling one nobody ever attempted, so the driver treats them alike. Anything reporting to a
    HUMAN wants `missing_partition_days`, which keeps the two apart.
    """
    statuses = partition_day_statuses(
        layer=layer, kind=kind, zoom=zoom, first_day=first_day, last_day=last_day, keys=keys
    )
    return tuple(day for day, status in statuses.items() if status in UNFILLED_PARTITION_STATUSES)


def _parsed_zoom_tier(segment: str) -> ZoomTier | None:
    value = int(segment)
    for tier in ZOOM_TIERS:
        if value == tier:
            return tier
    return None


def _validated_year(year: int) -> int:
    if year < MIN_PARTITION_YEAR or year > MAX_PARTITION_YEAR:
        raise PartitionPathError(
            f"year must be between {MIN_PARTITION_YEAR} and {MAX_PARTITION_YEAR} to render as four digits, got {year}"
        )
    return year


def _validated_month(month: int) -> int:
    if month < 1 or month > MONTHS_PER_YEAR:
        raise PartitionPathError(f"month must be between 1 and {MONTHS_PER_YEAR}, got {month}")
    return month
