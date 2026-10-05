"""Great-circle distances from a selected point to cell supports, and the expanding nearest-cell boxes.

See agent/AGENTS.md, "Closest-datapoint reads (2026-10-04)", for why the search is bounded this way.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

#: IUGG mean Earth radius; the haversine below is a sphere, so `distance_km` says "great_circle".
EARTH_RADIUS_KM: Final = 6371.0088

#: The first nearest-cell box half-width, in degrees of latitude: one NDVI/soil lattice cell.
NEAREST_CELL_START_DEGREES: Final = 0.25
#: Each further box is this many times wider, so a PNW-sized envelope is reached in four steps.
NEAREST_CELL_GROWTH: Final = 4.0
#: Hard step cap: 0.25 x 4^5 = 256 degrees, wider than the globe, so the loop always terminates.
NEAREST_CELL_MAX_STEPS: Final = 6
_COSINE_FLOOR: Final = 0.01
_QUARTER_TURN_DEGREES: Final = 90.0
_HALF_TURN_DEGREES: Final = 180.0

Box = tuple[float, float, float, float]


def haversine_km(longitude: float, latitude: float, other_longitude: float, other_latitude: float) -> float:
    """Great-circle distance in km between two WGS84 points (haversine formula)."""
    phi, other_phi = math.radians(latitude), math.radians(other_latitude)
    half_dphi = (other_phi - phi) / 2
    half_dlambda = math.radians(other_longitude - longitude) / 2
    a = math.sin(half_dphi) ** 2 + math.cos(phi) * math.cos(other_phi) * math.sin(half_dlambda) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def distance_to_box_km(longitude: float, latitude: float, box: Sequence[float]) -> float:
    """Great-circle distance from a point to the nearest point of a lon/lat box; 0 when inside it.

    Clamping each ordinate onto the box finds its nearest point exactly on the latitude axis and to
    well under a metre on the longitude axis at cell scale (a parallel is not a great circle).
    """
    west, south, east, north = box
    nearest_longitude = min(max(longitude, west), east)
    nearest_latitude = min(max(latitude, south), north)
    if nearest_longitude == longitude and nearest_latitude == latitude:
        return 0.0
    return haversine_km(longitude, latitude, nearest_longitude, nearest_latitude)


def search_boxes(longitude: float, latitude: float, start_degrees: float) -> Iterator[Box]:
    """Probe-centred boxes growing by `NEAREST_CELL_GROWTH`, square in km, clipped to WGS84."""
    half = max(start_degrees, NEAREST_CELL_START_DEGREES)
    cosine = max(math.cos(math.radians(latitude)), _COSINE_FLOOR)
    for _ in range(NEAREST_CELL_MAX_STEPS):
        longitude_half = half / cosine
        yield (
            max(-_HALF_TURN_DEGREES, longitude - longitude_half),
            max(-_QUARTER_TURN_DEGREES, latitude - half),
            min(_HALF_TURN_DEGREES, longitude + longitude_half),
            min(_QUARTER_TURN_DEGREES, latitude + half),
        )
        half *= NEAREST_CELL_GROWTH


def inscribed_radius_km(longitude: float, latitude: float, box: Box) -> float:
    """Radius of the largest probe-centred circle inside `box`; a hit nearer than this is the global nearest."""
    west, south, east, north = box
    radii = []
    if north < _QUARTER_TURN_DEGREES:
        radii.append(haversine_km(longitude, latitude, longitude, north))
    if south > -_QUARTER_TURN_DEGREES:
        radii.append(haversine_km(longitude, latitude, longitude, south))
    cosine = math.cos(math.radians(latitude))
    for edge in (west, east):
        if abs(edge) < _HALF_TURN_DEGREES:
            # Point-to-meridian great-circle distance: R * asin(cos(phi) * sin(delta lambda)).
            delta = min(abs(edge - longitude), _QUARTER_TURN_DEGREES)
            radii.append(EARTH_RADIUS_KM * math.asin(min(1.0, cosine * math.sin(math.radians(delta)))))
    return min(radii) if radii else math.inf


def box_contains(outer: Box, inner: Sequence[float]) -> bool:
    """Whether `outer` covers all of `inner`; reaching the region envelope ends the search."""
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]
