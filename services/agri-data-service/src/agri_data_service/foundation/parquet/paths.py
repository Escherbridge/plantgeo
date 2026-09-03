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

A DAY PREFIX HOLDS FOUR OBJECT NAMES, NOT TWO (owner, 2026-08-23; RUNBOOK 0.34.1): the part file,
the governed-absence marker, the COMPLETION marker, and the DERIVED-EMPTY completion marker that
closes a coarse rung which generalised every base row away. See `AGENTS.md` in this directory,
"Completion is asserted, and emptiness has its own name".
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

#: The rung nothing generalises: the most detailed tier of the ladder, and the only one whose
#: emptiness is a governed absence rather than a derived-empty receipt. Spelled from the ladder's own
#: top rather than as a literal, and restated here because L0 may not import `warehouse`.
BASE_PARTITION_ZOOM: Final[int] = ZOOM_TIERS[-1]

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
# The SIBLING name a derived rung's emptiness is asserted under, so a partless ordinary marker stops
# being ambiguous. See `AGENTS.md`, "Completion is asserted, and emptiness has its own name".
DERIVED_EMPTY_COMPLETION_FILE_NAME: Final = "_complete.empty.json"

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
    r"/_complete(?P<empty>\.empty)?\.json$"
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
    """One completion marker, decomposed, and which of the two names it was written under."""

    layer: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date
    #: True for `_complete.empty.json`: a DERIVED rung that generalised every base row away and
    #: therefore holds no parts. The name is the claim, so no reader opens the body to tell a rung
    #: that is honestly empty from one whose parts were deleted out from under its marker.
    derived_empty: bool = False

    @property
    def key(self) -> str:
        """Rebuild the relative object key this instance was parsed from."""
        if self.derived_empty:
            return derived_empty_completion_marker_path(self.layer, self.kind, self.zoom, self.day)
        return completion_marker_path(self.layer, self.kind, self.zoom, self.day)


@dataclass(frozen=True, slots=True)
class TierDayObjects:
    """Which days one tier's listing names, split by object kind: the parse every classifier shares."""

    parts: frozenset[date]
    absences: frozenset[date]
    completions: frozenset[date]
    derived_empties: frozenset[date]

    @property
    def named_days(self) -> frozenset[date]:
        """Every day any object of this tier mentions, whatever it claims about it."""
        return self.parts | self.absences | self.completions | self.derived_empties


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


def derived_empty_completion_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key asserting that one DERIVED rung finished holding nothing."""
    return f"{day_prefix(layer, kind, zoom, day)}{DERIVED_EMPTY_COMPLETION_FILE_NAME}"


def try_parse_completion_marker_path(path: str) -> CompletionMarkerPath | None:
    """Decompose either completion-marker name, returning `None` for anything that is neither.

    ONE PARSER FOR BOTH NAMES, deliberately: every guard that asks "is this key a member of the
    layout" (`warehouse_reader._is_layout_object`, `objectstore.list_partition_objects`, the legacy
    sweep in `drain.py`) must accept the derived-empty receipt without being taught a fourth parser,
    and a guard that missed it would read a published rung as a stray object and offer it for delete.
    """
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
    return CompletionMarkerPath(
        layer=match["layer"], kind=kind, zoom=zoom, day=day, derived_empty=match["empty"] is not None
    )


def tier_day_objects(
    keys: Iterable[str],
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
) -> TierDayObjects:
    """Split one tier's listing into the four day sets every status rule is decided from, in ONE pass.

    `keys` is consumed exactly once, so a generator argument is safe to hand here and nowhere twice.
    """
    validate_layer_slug(layer)
    validate_partition_kind(kind)
    validate_zoom_tier(zoom)
    parts: set[date] = set()
    absences: set[date] = set()
    completions: set[date] = set()
    derived_empties: set[date] = set()
    for key in keys:
        partition = try_parse_partition_path(key)
        if partition is not None and (partition.layer, partition.kind, partition.zoom) == (layer, kind, zoom):
            parts.add(partition.day)
            continue
        absence = try_parse_absence_marker_path(key)
        if absence is not None and (absence.layer, absence.kind, absence.zoom) == (layer, kind, zoom):
            absences.add(absence.day)
            continue
        finished = try_parse_completion_marker_path(key)
        if finished is not None and (finished.layer, finished.kind, finished.zoom) == (layer, kind, zoom):
            (derived_empties if finished.derived_empty else completions).add(finished.day)
    return TierDayObjects(
        parts=frozenset(parts),
        absences=frozenset(absences),
        completions=frozenset(completions),
        derived_empties=frozenset(derived_empties),
    )


def classify_partition_day(day: date, objects: TierDayObjects, *, zoom: ZoomTier) -> PartitionDayStatus:
    """Return the ONE status one day of one tier holds, from the day sets alone.

    THE SINGLE DEFINITION. `partition_day_statuses` and `parquet_ops/serving.py::day_status_sets` both
    resolve here, so a census and a reader cannot disagree about what a day is. See `AGENTS.md`,
    "Completion is asserted, and emptiness has its own name", for why each branch reads as it does.
    """
    has_parts = day in objects.parts
    has_absence = day in objects.absences
    if has_parts and has_absence:
        return "conflict"
    if has_parts:
        return "data" if day in objects.completions else "incomplete"
    if has_absence:
        return "absent"
    if day in objects.derived_empties and zoom != BASE_PARTITION_ZOOM:
        return "data"
    if day in objects.completions or day in objects.derived_empties:
        return "incomplete"
    return "missing"


def completed_partition_days(
    keys: Iterable[str],
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
) -> set[date]:
    """Return the days of one stream-tier whose export ASSERTED that it finished, under EITHER name.

    The primitive `planes/` share, and it is deliberately about the ASSERTION alone: those readers
    parse part keys themselves and intersect with this set, so a derived-empty receipt has to be in
    it or a rung that honestly holds nothing would read as never finished. A caller asking "is this
    rung `data`" wants `completed_rung_days`, which applies the whole status rule.
    """
    objects = tier_day_objects(keys, layer=layer, kind=kind, zoom=zoom)
    return set(objects.completions | objects.derived_empties)


def completed_rung_days(
    keys: Iterable[str],
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
) -> set[date]:
    """Return the days of one tier that are `data`: parts and their marker, or a derived-empty receipt.

    STRICTER THAN `completed_partition_days`, and the ladder census wants this one: a marker whose
    parts were deleted out from under it is a LOST rung, not a finished one, and counting it as
    finished is what left such a rung unrepairable by any tick. See `AGENTS.md`.
    """
    objects = tier_day_objects(keys, layer=layer, kind=kind, zoom=zoom)
    return {day for day in objects.named_days if classify_partition_day(day, objects, zoom=zoom) == "data"}


def partition_day_statuses(  # noqa: PLR0913 - the six are one gap census's whole scope: stream, tier, window, keys.
    *,
    layer: str,
    kind: PartitionKind,
    zoom: ZoomTier,
    first_day: date,
    last_day: date,
    keys: Iterable[str],
) -> dict[date, PartitionDayStatus]:
    """Classify every day in `[first_day, last_day]` at one tier from `keys` alone, chronologically.

    Every branch is `classify_partition_day`'s, so this and `parquet_ops/serving.py::day_status_sets`
    cannot drift. Keys of another tier are ignored rather than counted. See `AGENTS.md`.
    """
    if last_day < first_day:
        raise PartitionPathError(f"gap window {first_day}..{last_day} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_GAP_WINDOW_DAYS:
        raise PartitionPathError(f"gap window of {span} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget")
    objects = tier_day_objects(keys, layer=layer, kind=kind, zoom=zoom)
    return {
        day: classify_partition_day(day, objects, zoom=zoom)
        for day in (first_day + timedelta(days=offset) for offset in range(span))
    }


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
