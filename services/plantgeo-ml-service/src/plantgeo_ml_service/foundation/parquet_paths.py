"""The object-key grammar of the Parquet warehouse: build one, parse one, refuse anything else.

Layer L0: stdlib only. A COPY of agri-data-service's `foundation/parquet/paths.py` and
`foundation/parquet/zoom.py`, held honest by `tests/test_parquet_paths_parity.py` rather than by an
import. Rationale, and why `availability_lane_root` sits here instead of beside the writer, live in
`AGENTS.md` in this directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

# --- The zoom ladder ----------------------------------------------------------------------------

ZoomTier = Literal[0, 5, 9, 13]

ZOOM_TIERS: Final[tuple[ZoomTier, ...]] = (0, 5, 9, 13)

#: The rung nothing generalises: the most detailed tier, and the only one whose emptiness is a
#: governed absence rather than a derived-empty receipt.
BASE_PARTITION_ZOOM: Final[int] = ZOOM_TIERS[-1]

MIN_REQUEST_ZOOM: Final = 0
MAX_REQUEST_ZOOM: Final = 22

# --- The two streams a lane may publish -----------------------------------------------------------

PartitionKind = Literal["observed", "forecast"]
PartitionDayStatus = Literal["data", "absent", "conflict", "incomplete", "missing"]

PARTITION_KINDS: Final[tuple[PartitionKind, ...]] = ("observed", "forecast")

PARTITION_DAY_STATUSES: Final[tuple[PartitionDayStatus, ...]] = (
    "data",
    "absent",
    "conflict",
    "incomplete",
    "missing",
)

#: Days a reader may answer from by SERVING ROWS. Deliberately excludes `conflict`: a day carrying
#: both a release and a governed absence is an anomaly, and serving either half picks a side. A rule
#: spelled as a NEGATION (`status != "missing"`) silently accepts whatever member is added next.
COVERED_PARTITION_STATUSES: Final[frozenset[PartitionDayStatus]] = frozenset({"data", "absent"})

#: Days that still owe a completed export: a half-written day owes exactly what an unattempted one does.
UNFILLED_PARTITION_STATUSES: Final[frozenset[PartitionDayStatus]] = frozenset({"missing", "incomplete"})

# --- Object names ---------------------------------------------------------------------------------

PARQUET_SUFFIX: Final = ".parquet"
PART_FILE_STEM: Final = "part-"
ABSENCE_FILE_NAME: Final = "absent.json"
COMPLETION_FILE_NAME: Final = "_complete.json"
DERIVED_EMPTY_COMPLETION_FILE_NAME: Final = "_complete.empty.json"
PROMOTION_RECEIPT_FILE_NAME: Final = "promotion-receipt.json"
AVAILABILITY_SEGMENT: Final = "availability"
AVAILABILITY_POINTER_FILE_NAME: Final = "_LATEST.json"
AVAILABILITY_GENERATION_FILE_NAME: Final = "availability.parquet"
AVAILABILITY_BOOTSTRAP_SEGMENT: Final = "availability/bootstrap/"
AVAILABILITY_BOOTSTRAP_MARKER_FILE_NAME: Final = "_BOOTSTRAPPED.json"
AVAILABILITY_RETRY_SEGMENT: Final = "availability/pending/"
AVAILABILITY_RETRY_DAY_PREFIX: Final = "day="
AVAILABILITY_RETRY_SUFFIX: Final = ".json"
AVAILABILITY_RETRY_QUARANTINE_SUFFIX: Final = ".quarantined.json"

MAX_PART_INDEX: Final = 9_999
MIN_PARTITION_YEAR: Final = 1_000
MAX_PARTITION_YEAR: Final = 9_999
MONTHS_PER_YEAR: Final = 12

#: Zero-padded so a lexicographic listing walks the ladder in numeric order: unpadded, `zoom=13`
#: sorts between `zoom=0` and `zoom=5` and a tier walk silently runs out of order.
ZOOM_SEGMENT_DIGITS: Final = 2

_SHA256_LENGTH: Final = 64

LAYER_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")

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


class ZoomTierError(ValueError):
    """Raised when a zoom is not a published tier, or a requested zoom is off the web-map scale."""


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


def validate_zoom_tier(zoom: int) -> ZoomTier:
    """Return `zoom` narrowed to a tier every lane publishes, else raise."""
    for tier in ZOOM_TIERS:
        if zoom == tier:
            return tier
    raise ZoomTierError(
        f"zoom {zoom!r} is not one of the published tiers {ZOOM_TIERS}; writing it would strand the "
        f"rows under a prefix no reader resolves, and serving would answer from a tier that was never written"
    )


def serving_zoom_tier(requested_zoom: int) -> ZoomTier:
    """Return the published tier answering `requested_zoom`: the highest rung at or below it."""
    if requested_zoom < MIN_REQUEST_ZOOM or requested_zoom > MAX_REQUEST_ZOOM:
        raise ZoomTierError(
            f"requested zoom {requested_zoom!r} is outside the web-map scale {MIN_REQUEST_ZOOM}..{MAX_REQUEST_ZOOM}"
        )
    for tier in reversed(ZOOM_TIERS):
        if tier <= requested_zoom:
            return tier
    raise ZoomTierError(f"no published tier sits at or below zoom {requested_zoom}")


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
    """Return the object prefix owning one whole zoom tier of one stream."""
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


def absence_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key marking one stream-day at one tier as a governed absence."""
    return f"{day_prefix(layer, kind, zoom, day)}{ABSENCE_FILE_NAME}"


def completion_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key asserting that one stream-day at one tier finished exporting."""
    return f"{day_prefix(layer, kind, zoom, day)}{COMPLETION_FILE_NAME}"


def derived_empty_completion_marker_path(layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
    """Return the relative object key asserting that one DERIVED rung finished holding nothing."""
    return f"{day_prefix(layer, kind, zoom, day)}{DERIVED_EMPTY_COMPLETION_FILE_NAME}"


def promotion_receipt_path(layer: str, kind: PartitionKind, day: date) -> str:
    """Return the relative object key of one lane-day's governed-plane promotion receipt, zoom-independent."""
    return (
        f"{stream_prefix(layer, kind)}year={_validated_year(day.year):04d}"
        f"/month={_validated_month(day.month):02d}/day={day.day:02d}/{PROMOTION_RECEIPT_FILE_NAME}"
    )


def availability_lane_root(layer: str, kind: PartitionKind) -> str:
    """Return the `layer=<slug>/kind=<kind>` root the availability contract keys everything beneath.

    The two kinds have SEPARATE roots, so an observed promotion lane and this service's forecast
    publisher never contend for one pointer.
    """
    return stream_prefix(layer, kind).rstrip("/")


def availability_pointer_path(layer: str, kind: PartitionKind) -> str:
    """Return the mutable pointer key beneath one lane root."""
    return f"{availability_lane_root(layer, kind)}/{AVAILABILITY_SEGMENT}/{AVAILABILITY_POINTER_FILE_NAME}"


def availability_generation_path(layer: str, kind: PartitionKind, generation_sha256: str) -> str:
    """Return the immutable content-addressed generation key beneath one lane root."""
    if not _SHA256_PATTERN.match(generation_sha256):
        raise PartitionPathError(f"generation sha256 must be {_SHA256_LENGTH} lowercase hex characters")
    return (
        f"{availability_lane_root(layer, kind)}/{AVAILABILITY_SEGMENT}"
        f"/generation={generation_sha256}/{AVAILABILITY_GENERATION_FILE_NAME}"
    )


def availability_bootstrap_marker_key(lane_root: str) -> str:
    """Return the DETERMINISTIC key naming one lane's immutable bootstrap receipt."""
    return f"{require_lane_root(lane_root)}/{AVAILABILITY_BOOTSTRAP_SEGMENT}{AVAILABILITY_BOOTSTRAP_MARKER_FILE_NAME}"


def availability_retry_prefix(layer: str, kind: PartitionKind) -> str:
    """Return the prefix holding every availability retry claim of one lane."""
    return f"{availability_lane_root(layer, kind)}/{AVAILABILITY_RETRY_SEGMENT}"


def availability_retry_path(layer: str, kind: PartitionKind, day: date) -> str:
    """Return the relative key of one lane-day's availability retry marker."""
    return (
        f"{availability_retry_prefix(layer, kind)}"
        f"{AVAILABILITY_RETRY_DAY_PREFIX}{day.isoformat()}{AVAILABILITY_RETRY_SUFFIX}"
    )


def availability_retry_quarantine_path(layer: str, kind: PartitionKind, day: date) -> str:
    """Return where a MALFORMED retry claim is parked so it stops occupying an oldest-first slot.

    Under the SAME prefix as the live claims and readable by an operator for exactly that reason;
    `try_parse_availability_retry_path` refuses the quarantined name, so a sweep walks straight past
    it and one unparseable day can no longer starve the retries a lane gets per tick.
    """
    return (
        f"{availability_retry_prefix(layer, kind)}"
        f"{AVAILABILITY_RETRY_DAY_PREFIX}{day.isoformat()}{AVAILABILITY_RETRY_QUARANTINE_SUFFIX}"
    )


def try_parse_availability_retry_path(path: str) -> date | None:
    """Return the day one availability retry marker owes, or `None` when the key is not one."""
    return _parsed_retry_day(path, suffix=AVAILABILITY_RETRY_SUFFIX)


def try_parse_availability_retry_quarantine_path(path: str) -> date | None:
    """Return the day one QUARANTINED retry claim named, or `None` when the key is not one."""
    return _parsed_retry_day(path, suffix=AVAILABILITY_RETRY_QUARANTINE_SUFFIX)


def require_lane_root(lane_root: str) -> str:
    """Return `lane_root` if it is a relative, traversal-free `layer=...` prefix, else raise."""
    if (
        not lane_root
        or lane_root != lane_root.strip("/")
        or "\\" in lane_root
        or ".." in lane_root
        or not lane_root.startswith("layer=")
    ):
        raise PartitionPathError(f"{lane_root!r} is not a relative layer=... availability lane root")
    return lane_root


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
    day = _parsed_day(match)
    if day is None:
        return None
    return PartitionPath(
        layer=match["layer"],
        kind=_parsed_kind(match["kind"]),
        zoom=zoom,
        day=day,
        part_index=part_index,
    )


def parse_absence_marker_path(path: str) -> AbsenceMarkerPath:
    """Decompose a relative object key into its absence-marker components, or raise."""
    parsed = try_parse_absence_marker_path(path)
    if parsed is None:
        raise PartitionPathError(f"{path!r} is not an absence marker of the frozen layout")
    return parsed


def try_parse_absence_marker_path(path: str) -> AbsenceMarkerPath | None:
    """Decompose a relative object key, returning `None` for anything that is not an absence marker."""
    match = _ABSENCE_PATH_PATTERN.match(path.replace("\\", "/"))
    if match is None:
        return None
    zoom = _parsed_zoom_tier(match["zoom"])
    if zoom is None:
        return None
    day = _parsed_day(match)
    if day is None:
        return None
    return AbsenceMarkerPath(layer=match["layer"], kind=_parsed_kind(match["kind"]), zoom=zoom, day=day)


def parse_completion_marker_path(path: str) -> CompletionMarkerPath:
    """Decompose a relative object key into its completion-marker components, or raise."""
    parsed = try_parse_completion_marker_path(path)
    if parsed is None:
        raise PartitionPathError(f"{path!r} is not a completion marker of the frozen layout")
    return parsed


def try_parse_completion_marker_path(path: str) -> CompletionMarkerPath | None:
    """Decompose either completion-marker name, returning `None` for anything that is neither."""
    match = _COMPLETION_PATH_PATTERN.match(path.replace("\\", "/"))
    if match is None:
        return None
    zoom = _parsed_zoom_tier(match["zoom"])
    if zoom is None:
        return None
    day = _parsed_day(match)
    if day is None:
        return None
    return CompletionMarkerPath(
        layer=match["layer"],
        kind=_parsed_kind(match["kind"]),
        zoom=zoom,
        day=day,
        derived_empty=match["empty"] is not None,
    )


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


def tier_day_objects(keys: Iterable[str], *, layer: str, kind: PartitionKind, zoom: ZoomTier) -> TierDayObjects:
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
    """Return the ONE status one day of one tier holds, from the day sets alone."""
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


def _parsed_kind(segment: str) -> PartitionKind:
    """Narrow an already-pattern-matched `kind=` segment without re-raising on it."""
    return "observed" if segment == "observed" else "forecast"


def _parsed_day(match: re.Match[str]) -> date | None:
    """Assemble the `year=/month=/day=` segments, returning `None` for an impossible calendar day."""
    try:
        return date(int(match["year"]), int(match["month"]), int(match["day"]))
    except ValueError:
        return None


def _parsed_retry_day(path: str, *, suffix: str) -> date | None:
    """Read the ISO day out of one retry-claim key with the given suffix, refusing any other shape."""
    # No backslash normalisation, unlike the three partition parsers: the sibling's own retry parser
    # has none, and a copy that accepted one more shape than the original is a drift, not a fix.
    marker, separator, tail = path.partition(f"/{AVAILABILITY_RETRY_SEGMENT}")
    if not separator or not marker or not tail.startswith(AVAILABILITY_RETRY_DAY_PREFIX):
        return None
    if not tail.endswith(suffix):
        return None
    rendered = tail[len(AVAILABILITY_RETRY_DAY_PREFIX) : -len(suffix)]
    try:
        parsed = date.fromisoformat(rendered)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == rendered else None


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
