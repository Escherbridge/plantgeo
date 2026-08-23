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
PartitionDayStatus = Literal["data", "absent", "conflict", "missing"]

PARTITION_KINDS: Final[tuple[PartitionKind, ...]] = ("observed", "forecast")
PARQUET_SUFFIX: Final = ".parquet"
PART_FILE_STEM: Final = "part-"
ABSENCE_FILE_NAME: Final = "absent.json"

# Explicit budgets at the boundary; every one of these has a wrong answer that is silently plausible.
MAX_PART_INDEX: Final = 9_999
MIN_PARTITION_YEAR: Final = 1_000
MAX_PARTITION_YEAR: Final = 9_999
MONTHS_PER_YEAR: Final = 12
MAX_GAP_WINDOW_DAYS: Final = 20_000

# Zero-padded like `month=` and `day=`, so a lexicographic listing walks the ladder in numeric order:
# unpadded, `zoom=13` sorts between `zoom=0` and `zoom=5` and a tier walk silently runs out of order.
ZOOM_SEGMENT_DIGITS: Final = 2

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

    `data` = at least one part file; `absent` = a governed-absence marker; `conflict` = both,
    which only a manual admin action should ever produce; `missing` = neither, a real gap.

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
    data_days: set[date] = set()
    absent_days: set[date] = set()
    for key in keys:
        parsed = try_parse_partition_path(key)
        if parsed is not None and parsed.layer == layer and parsed.kind == kind and parsed.zoom == zoom:
            data_days.add(parsed.day)
            continue
        marker = try_parse_absence_marker_path(key)
        if marker is not None and marker.layer == layer and marker.kind == kind and marker.zoom == zoom:
            absent_days.add(marker.day)
    statuses: dict[date, PartitionDayStatus] = {}
    for offset in range(span):
        day = first_day + timedelta(days=offset)
        if day in data_days:
            statuses[day] = "conflict" if day in absent_days else "data"
        elif day in absent_days:
            statuses[day] = "absent"
        else:
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
    """Return the days of one tier in `[first_day, last_day]` covered by neither a part file nor a marker."""
    statuses = partition_day_statuses(
        layer=layer,
        kind=kind,
        zoom=zoom,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )
    return tuple(day for day, status in statuses.items() if status == "missing")


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
