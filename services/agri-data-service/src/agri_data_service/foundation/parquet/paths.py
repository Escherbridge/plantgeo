"""Partition-path computation for the object-store Parquet warehouse: build, parse, and diff.

Layer L0: stdlib only. May NOT import any first-party module outside `foundation`, nor
SQLAlchemy, httpx, asyncpg, or click. See `AGENTS.md` in this directory for the frozen layout
and why gap detection is a listing rather than a scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

PartitionKind = Literal["observed", "forecast"]

PARTITION_KINDS: Final[tuple[PartitionKind, ...]] = ("observed", "forecast")
PARQUET_SUFFIX: Final = ".parquet"
PART_FILE_STEM: Final = "part-"

# Explicit budgets at the boundary; every one of these has a wrong answer that is silently plausible.
MAX_PART_INDEX: Final = 9_999
MIN_PARTITION_YEAR: Final = 1_000
MAX_PARTITION_YEAR: Final = 9_999
MONTHS_PER_YEAR: Final = 12
MAX_GAP_WINDOW_DAYS: Final = 20_000

LAYER_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_PARTITION_PATH_PATTERN: Final = re.compile(
    r"^layer=(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"/kind=(?P<kind>observed|forecast)"
    r"/year=(?P<year>\d{4})"
    r"/month=(?P<month>\d{2})"
    r"/day=(?P<day>\d{2})"
    r"/part-(?P<part_index>\d+)\.parquet$"
)


class PartitionPathError(ValueError):
    """Raised when a slug, partition component, or object key breaks the frozen layout."""


@dataclass(frozen=True, slots=True)
class PartitionPath:
    """One partition file, decomposed: the inverse of `partition_path`."""

    layer: str
    kind: PartitionKind
    day: date
    part_index: int = 0

    @property
    def key(self) -> str:
        """Rebuild the relative object key this instance was parsed from."""
        return partition_path(self.layer, self.kind, self.day, self.part_index)


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


def year_prefix(layer: str, kind: PartitionKind, year: int) -> str:
    """Return the object prefix bounding a listing to one calendar year of one stream."""
    return f"{stream_prefix(layer, kind)}year={_validated_year(year):04d}/"


def month_prefix(layer: str, kind: PartitionKind, year: int, month: int) -> str:
    """Return the object prefix bounding a listing to one calendar month of one stream."""
    return f"{year_prefix(layer, kind, year)}month={_validated_month(month):02d}/"


def day_prefix(layer: str, kind: PartitionKind, day: date) -> str:
    """Return the object prefix holding every part file written for one day of one stream."""
    return f"{month_prefix(layer, kind, day.year, day.month)}day={day.day:02d}/"


def partition_path(layer: str, kind: PartitionKind, day: date, part_index: int = 0) -> str:
    """Return the relative object key for one part file of one layer-stream-day."""
    if part_index < 0 or part_index > MAX_PART_INDEX:
        raise PartitionPathError(f"part_index must be between 0 and {MAX_PART_INDEX}, got {part_index}")
    return f"{day_prefix(layer, kind, day)}{PART_FILE_STEM}{part_index}{PARQUET_SUFFIX}"


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
    try:
        day = date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None
    kind: PartitionKind = "observed" if match["kind"] == "observed" else "forecast"
    return PartitionPath(layer=match["layer"], kind=kind, day=day, part_index=part_index)


def missing_partition_days(
    *,
    layer: str,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
    keys: Iterable[str],
) -> tuple[date, ...]:
    """Return the days in `[first_day, last_day]` that `keys` holds no part file for."""
    validate_layer_slug(layer)
    validate_partition_kind(kind)
    if last_day < first_day:
        raise PartitionPathError(f"gap window {first_day}..{last_day} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_GAP_WINDOW_DAYS:
        raise PartitionPathError(f"gap window of {span} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    present = {
        parsed.day
        for parsed in (try_parse_partition_path(key) for key in keys)
        if parsed is not None and parsed.layer == layer and parsed.kind == kind
    }
    candidates = (first_day + timedelta(days=offset) for offset in range(span))
    return tuple(day for day in candidates if day not in present)


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
