"""Coordinate parsing, withholding classification, the declared envelope, and WKB encoding."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Final, Literal

SpatialClass = Literal["exact", "generalized", "withheld", "nonspatial"]

#: Mean Earth radius, the value every great-circle distance in this lane is computed against.
EARTH_RADIUS_METERS: Final = 6_371_008.8

#: The lane's DECLARED ADMITTED ENVELOPE, and a PLACEHOLDER: it is the Pacific Northwest box the
#: occurrence track was scoped against, not a measured coverage claim about any admitted collection.
#: A record outside it is still published -- `within_envelope=False` -- because a specimen collected
#: outside the envelope is a real specimen; what the flag bounds is where `evaluated_zero` may be
#: asserted, since "we looked here and found nothing" is only honest inside admitted coverage.
#: CONFIGURABLE: the first admitted release's own EML coverage statement replaces these four numbers.
DECLARED_ENVELOPE: Final[tuple[float, float, float, float]] = (-125.0, 41.0, -110.0, 50.0)

#: Substrings that mark a location as deliberately coarsened or suppressed by the publisher. Matched
#: case-insensitively against `informationWithheld` and `dataGeneralizations`. A publisher phrase
#: this list has not learned leaves the record `exact`, which is why the two terms also survive
#: verbatim: this is a best-effort READ of a policy, not the policy itself.
WITHHELD_MARKERS: Final[tuple[str, ...]] = (
    "coordinate", "location", "locality", "georeference", "latitude", "longitude",
)
SUPPRESSION_MARKERS: Final[tuple[str, ...]] = ("withheld", "not for public", "redacted", "suppressed", "removed")
GENERALIZATION_MARKERS: Final[tuple[str, ...]] = (
    "generali", "obscur", "rounded", "fuzzed", "reduced precision", "centroid", "buffered",
)

#: A coordinate uncertainty at or above this is treated as generalized rather than exact, whatever
#: the publisher's prose says: a 10 km circle does not locate a specimen, it locates a neighbourhood.
GENERALIZED_UNCERTAINTY_METERS: Final = 10_000.0

#: Datums this lane will publish as WGS 84 without transforming. Anything else keeps its coordinates
#: but is marked `generalized`, because reprojecting a datum this lane cannot verify would move the
#: point by an unknown distance and still call it exact.
WGS84_DATUMS: Final[frozenset[str]] = frozenset({"", "wgs84", "wgs 84", "epsg:4326", "4326", "wgs_1984"})

_MAX_LATITUDE: Final = 90.0
_MAX_LONGITUDE: Final = 180.0


@dataclass(frozen=True, slots=True)
class ParsedCoordinate:
    """One record's spatial reading: where it is, how sure that is, and what may be claimed from it."""

    longitude: float | None
    latitude: float | None
    uncertainty_meters: float | None
    datum: str
    spatial_class: SpatialClass
    within_envelope: bool
    reasons: tuple[str, ...]


def parse_decimal(raw: str | None) -> float | None:
    """Parse a decimal ordinate, answering None rather than guessing at an unparseable one."""
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _matches(text: str | None, markers: tuple[str, ...]) -> bool:
    lowered = (text or "").casefold()
    return any(marker in lowered for marker in markers)


def within_declared_envelope(
    longitude: float,
    latitude: float,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
) -> bool:
    """Report whether a point lies inside the lane's declared admitted-coverage envelope."""
    min_longitude, min_latitude, max_longitude, max_latitude = envelope
    return min_longitude <= longitude <= max_longitude and min_latitude <= latitude <= max_latitude


def classify_coordinate(  # noqa: PLR0913 - one DwC term per argument, and none may be folded together
    *,
    decimal_longitude: str | None,
    decimal_latitude: str | None,
    geodetic_datum: str | None = None,
    coordinate_uncertainty: str | None = None,
    information_withheld: str | None = None,
    data_generalizations: str | None = None,
    envelope: tuple[float, float, float, float] = DECLARED_ENVELOPE,
) -> ParsedCoordinate:
    """Decide what one record's coordinates may be used for, and record every reason for it.

    The order matters and is fail-closed. A publisher statement that the location is withheld wins
    over whatever numbers are in the coordinate columns -- a suppressed record that still carries a
    county centroid must not become a specimen point. Only after that do missing, out-of-range and
    null-island coordinates make the record nonspatial, and a wide uncertainty or an unverified datum
    demote it to generalized.
    """
    reasons: list[str] = []
    longitude = parse_decimal(decimal_longitude)
    latitude = parse_decimal(decimal_latitude)
    uncertainty = parse_decimal(coordinate_uncertainty)
    datum = (geodetic_datum or "").strip()

    suppressed = _matches(information_withheld, SUPPRESSION_MARKERS) and (
        _matches(information_withheld, WITHHELD_MARKERS) or longitude is None or latitude is None
    )
    if suppressed:
        reasons.append("publisher_withheld_location")
        return ParsedCoordinate(None, None, uncertainty, datum, "withheld", False, tuple(reasons))

    if longitude is None or latitude is None:
        reasons.append("no_decimal_coordinates")
        return ParsedCoordinate(None, None, uncertainty, datum, "nonspatial", False, tuple(reasons))
    if abs(latitude) > _MAX_LATITUDE or abs(longitude) > _MAX_LONGITUDE:
        reasons.append("coordinates_out_of_range")
        return ParsedCoordinate(None, None, uncertainty, datum, "nonspatial", False, tuple(reasons))
    if longitude == 0.0 and latitude == 0.0:
        # Null island is the shape a failed georeference takes, not a place plants are collected.
        reasons.append("null_island_coordinates")
        return ParsedCoordinate(None, None, uncertainty, datum, "nonspatial", False, tuple(reasons))

    spatial_class: SpatialClass = "exact"
    if _matches(data_generalizations, GENERALIZATION_MARKERS) or _matches(information_withheld, GENERALIZATION_MARKERS):
        reasons.append("publisher_generalized_location")
        spatial_class = "generalized"
    if uncertainty is not None and uncertainty >= GENERALIZED_UNCERTAINTY_METERS:
        reasons.append("uncertainty_at_or_above_generalized_threshold")
        spatial_class = "generalized"
    if datum.casefold() not in WGS84_DATUMS:
        reasons.append("unverified_geodetic_datum")
        spatial_class = "generalized"

    return ParsedCoordinate(
        longitude=longitude,
        latitude=latitude,
        uncertainty_meters=uncertainty,
        datum=datum,
        spatial_class=spatial_class,
        within_envelope=within_declared_envelope(longitude, latitude, envelope),
        reasons=tuple(reasons),
    )


def haversine_meters(
    longitude_a: float,
    latitude_a: float,
    longitude_b: float,
    latitude_b: float,
) -> float:
    """Great-circle distance in metres between two WGS 84 points."""
    first_latitude, second_latitude = math.radians(latitude_a), math.radians(latitude_b)
    delta_latitude = second_latitude - first_latitude
    delta_longitude = math.radians(longitude_b - longitude_a)
    inner = (
        math.sin(delta_latitude / 2) ** 2
        + math.cos(first_latitude) * math.cos(second_latitude) * math.sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(inner)))


#: Little-endian byte order marker, then the OGC geometry type code.
_LITTLE_ENDIAN: Final = 1
_WKB_POINT: Final = 1
_WKB_POLYGON: Final = 3


def point_wkb(longitude: float, latitude: float) -> bytes:
    """Encode a WGS 84 point as little-endian WKB. No SRID is carried; every reader must apply 4326."""
    return struct.pack("<BIdd", _LITTLE_ENDIAN, _WKB_POINT, longitude, latitude)


def polygon_wkb(ring: tuple[tuple[float, float], ...]) -> bytes:
    """Encode one closed exterior ring as little-endian polygon WKB, closing it if the caller did not."""
    closed = ring if ring[0] == ring[-1] else (*ring, ring[0])
    header = struct.pack("<BIII", _LITTLE_ENDIAN, _WKB_POLYGON, 1, len(closed))
    return header + b"".join(struct.pack("<dd", longitude, latitude) for longitude, latitude in closed)


__all__ = [
    "DECLARED_ENVELOPE",
    "EARTH_RADIUS_METERS",
    "GENERALIZED_UNCERTAINTY_METERS",
    "WGS84_DATUMS",
    "ParsedCoordinate",
    "SpatialClass",
    "classify_coordinate",
    "haversine_meters",
    "parse_decimal",
    "point_wkb",
    "polygon_wkb",
    "within_declared_envelope",
]
