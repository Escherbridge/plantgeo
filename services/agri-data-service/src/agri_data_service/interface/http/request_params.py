"""Ingress validation for `/api/v1/parquet`: every query parameter, checked once, at the boundary.

Layer L4. Pure: no I/O and no clock. See `AGENTS.md` in this directory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import (
    PartitionPathError,
    validate_layer_slug,
    validate_partition_kind,
)
from agri_data_service.foundation.parquet.zoom import ZoomTierError, serving_zoom_tier

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

#: `YYYY-MM-DD` and nothing else. A `T` or a `Z` here is how 6,279 of 16,743 water-gauge rows once
#: moved to the following calendar day, so an instant is REFUSED rather than truncated to its date.
CALENDAR_DAY_PATTERN: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A plain tier integer, never the partition path's zero-padded segment. `09` is refused because a
#: client sending it has confused the wire with the object key, and silently accepting it hides that.
ZOOM_PATTERN: Final = re.compile(r"^(?:0|[1-9]\d?)$")

#: The longest closed range one window read may answer. A window is served by ONE bounded scan, and
#: the budget below is shared across it -- a longer span would truncate so hard the answer is noise.
MAX_WINDOW_DAYS: Final = 31

MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0
MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0
BBOX_COMPONENT_COUNT: Final = 4


class RequestError(ValueError):
    """A caller asked for something this plane will not put on the wire. Never a warehouse claim."""


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """A WGS84 request envelope, `west,south,east,north`, both ends inclusive."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if self.west > self.east:
            raise RequestError(f"bbox west {self.west} is east of east {self.east}; an antimeridian span is not served")
        if self.south > self.north:
            raise RequestError(f"bbox south {self.south} is north of north {self.north}")

    @property
    def as_envelope_arguments(self) -> tuple[float, float, float, float]:
        """The four ordinates in `ST_MakeEnvelope` order."""
        return (self.west, self.south, self.east, self.north)


@dataclass(frozen=True, slots=True)
class ReadScope:
    """What every row read shares: one lane, one stream, one tier, and an optional viewport."""

    layer: str
    kind: PartitionKind
    tier: ZoomTier
    bbox: BoundingBox | None


def parse_layer(raw: str | None) -> str:
    """Return a validated layer slug, or refuse."""
    if raw is None or not raw.strip():
        raise RequestError("layer is required and must be a lowercase hyphenated layer slug")
    try:
        return validate_layer_slug(raw.strip())
    except PartitionPathError as exc:
        raise RequestError(str(exc)) from exc


def parse_kind(raw: str | None) -> PartitionKind:
    """Return the requested stream, defaulting to `observed` when the caller named none."""
    if raw is None or not raw.strip():
        return "observed"
    try:
        return validate_partition_kind(raw.strip())
    except PartitionPathError as exc:
        raise RequestError(str(exc)) from exc


def parse_zoom(raw: str | None) -> ZoomTier:
    """Resolve a map zoom to the published tier that answers it: the highest rung at or below it."""
    if raw is None or not raw.strip():
        raise RequestError("zoom is required and travels as a plain tier integer, never a padded path segment")
    candidate = raw.strip()
    if not ZOOM_PATTERN.match(candidate):
        raise RequestError(
            f"zoom {candidate!r} must be a plain integer such as 9; the zero-padded 09 is the object key's "
            "spelling and the server pads it itself"
        )
    try:
        return serving_zoom_tier(int(candidate))
    except ZoomTierError as exc:
        raise RequestError(str(exc)) from exc


def parse_calendar_day(raw: str | None, field: str) -> date:
    """Return a `YYYY-MM-DD` day, refusing anything carrying a time or a zone."""
    if raw is None or not raw.strip():
        raise RequestError(f"{field} is required and must be a YYYY-MM-DD calendar day")
    candidate = raw.strip()
    if not CALENDAR_DAY_PATTERN.match(candidate):
        raise RequestError(
            f"{field} {candidate!r} must be a YYYY-MM-DD calendar day; a day carries no time and no zone, and "
            "converting one would move rows onto the neighbouring day"
        )
    try:
        return date.fromisoformat(candidate)
    except ValueError as exc:
        raise RequestError(f"{field} {candidate!r} is not a real calendar day") from exc


def parse_bbox(raw: str | None) -> BoundingBox | None:
    """Return the request envelope, or `None` for an unbounded read."""
    if raw is None or not raw.strip():
        return None
    parts = raw.split(",")
    if len(parts) != BBOX_COMPONENT_COUNT:
        raise RequestError(f"bbox {raw!r} must be four comma-separated ordinates: west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as exc:
        raise RequestError(f"bbox {raw!r} carries an ordinate that is not a number") from exc
    for name, value, low, high in (
        ("west", west, MIN_LONGITUDE, MAX_LONGITUDE),
        ("east", east, MIN_LONGITUDE, MAX_LONGITUDE),
        ("south", south, MIN_LATITUDE, MAX_LATITUDE),
        ("north", north, MIN_LATITUDE, MAX_LATITUDE),
    ):
        if not low <= value <= high:
            raise RequestError(f"bbox {name} {value} is outside {low}..{high}")
    return BoundingBox(west=west, south=south, east=east, north=north)


def parse_window(first_raw: str | None, last_raw: str | None) -> tuple[date, date]:
    """Return a closed, ascending, bounded day range."""
    first_day = parse_calendar_day(first_raw, "first_day")
    last_day = parse_calendar_day(last_raw, "last_day")
    if last_day < first_day:
        raise RequestError(f"day window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    span = (last_day - first_day).days + 1
    if span > MAX_WINDOW_DAYS:
        raise RequestError(
            f"day window of {span} days exceeds the {MAX_WINDOW_DAYS}-day budget; one window is answered by one "
            "bounded scan and a longer span would truncate so hard the answer would be noise"
        )
    return (first_day, last_day)


def parse_read_scope(
    *,
    layer: str | None,
    kind: str | None,
    zoom: str | None,
    bbox: str | None,
) -> ReadScope:
    """Validate the four parameters every row read carries, in one place so the routes cannot drift."""
    return ReadScope(
        layer=parse_layer(layer),
        kind=parse_kind(kind),
        tier=parse_zoom(zoom),
        bbox=parse_bbox(bbox),
    )
