"""The one lattice: the integer form agrees with the sibling's float form on every edge that matters.

M7 replaced three per-lane copies of this arithmetic with `foundation/lattice.py`. The sibling still
bins in FLOATS (`agri-data-service/src/agri_data_service/warehouse/parquet/tiers.py::floor_to_resolution`,
with `FLOOR_SNAP_TOLERANCE = 1e-9`), and the two warehouses must place a cell at the same origin or a
coarse rung written here would not contain the base cells written there.

The sibling's function is RESTATED below rather than imported: `tiers.py` pulls in DuckDB and the
whole `agri_data_service` package, which this service does not depend on and its Docker image does
not carry. No parity FIXTURE is involved, so nothing here is regenerated.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import pytest

from plantgeo_ml_service.foundation.lattice import (
    LATITUDE_ORIGIN_MICRO,
    LONGITUDE_ORIGIN_MICRO,
    MICRO_DEGREES_PER_DEGREE,
    TIER_PITCH_MICRO,
    TIER_RESOLUTION_DEGREES,
    LatticeError,
    axis_origin_micro,
    floor_coordinate,
    from_micro_degrees,
    tier_pitch_micro,
    to_micro_degrees,
)
from plantgeo_ml_service.foundation.parquet_paths import BASE_PARTITION_ZOOM

if TYPE_CHECKING:
    from plantgeo_ml_service.foundation.parquet_paths import ZoomTier

#: Verbatim from the sibling's `warehouse/parquet/tiers.py`.
FLOOR_SNAP_TOLERANCE: Final = 1e-9

#: The published coordinate precision both warehouses round to before writing.
COORDINATE_DECIMALS: Final = 6

#: WGS 84 bounds, so a synthesized edge outside the world is skipped rather than asserted on.
MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0

#: One sample inside a z5 cell, and the origin it must floor to.
SAMPLE_LONGITUDE: Final = -120.15
SAMPLE_CELL_ORIGIN: Final = -120.2

#: A coordinate whose micro-degree form must not pick up float dust.
ROUND_TRIP_DEGREES: Final = -119.9995
ROUND_TRIP_MICRO: Final = -119_999_500

EXPECTED_MICRO_DEGREES_PER_DEGREE: Final = 1_000_000

#: Longitudes: the PNW envelope, exact multiples of every pitch, a sign change, and both poles of
#: the antimeridian. Latitudes: the 46-degree envelope edges the memory note names, the equator and
#: the two poles.
EDGE_LONGITUDES: Final[tuple[float, ...]] = (
    -180.0,
    -179.99,
    -125.0,
    -124.5,
    -123.456789,
    -120.2,
    -120.0,
    -119.9999,
    -100.0,
    -0.01,
    0.0,
    0.01,
    32.8,
    120.0,
    179.99,
)

EDGE_LATITUDES: Final[tuple[float, ...]] = (
    -90.0,
    -46.0,
    -0.2,
    0.0,
    0.2,
    41.9999,
    42.0,
    45.999999,
    46.0,
    46.000001,
    46.1,
    49.0,
    49.2,
    90.0,
)

DERIVED_TIERS: Final[tuple[int, ...]] = tuple(sorted(TIER_PITCH_MICRO))


def sibling_floor_to_resolution(value: float, resolution: float) -> float:
    """The sibling's float form, restated: snap a near-integer quotient, else floor it."""
    quotient = value / resolution
    nearest = round(quotient)
    origin_cell = nearest if abs(quotient - nearest) < FLOOR_SNAP_TOLERANCE else math.floor(quotient)
    return origin_cell * resolution


@pytest.mark.parametrize("zoom", DERIVED_TIERS)
@pytest.mark.parametrize("longitude", EDGE_LONGITUDES)
def test_the_integer_form_and_the_siblings_float_form_agree_on_every_edge_longitude(
    zoom: ZoomTier, longitude: float
) -> None:
    """Negative longitudes are the whole envelope, and they are where floor and modulo disagree."""
    integer_form = floor_coordinate(longitude, zoom=zoom, axis="longitude")
    float_form = sibling_floor_to_resolution(longitude, TIER_RESOLUTION_DEGREES[zoom])

    assert round(integer_form, COORDINATE_DECIMALS) == round(float_form, COORDINATE_DECIMALS)


@pytest.mark.parametrize("zoom", DERIVED_TIERS)
@pytest.mark.parametrize("latitude", EDGE_LATITUDES)
def test_the_integer_form_and_the_siblings_float_form_agree_on_every_edge_latitude(
    zoom: ZoomTier, latitude: float
) -> None:
    integer_form = floor_coordinate(latitude, zoom=zoom, axis="latitude")
    float_form = sibling_floor_to_resolution(latitude, TIER_RESOLUTION_DEGREES[zoom])

    assert round(integer_form, COORDINATE_DECIMALS) == round(float_form, COORDINATE_DECIMALS)


@pytest.mark.parametrize("zoom", DERIVED_TIERS)
def test_a_coordinate_that_is_an_exact_multiple_lands_in_the_cell_that_starts_there(zoom: ZoomTier) -> None:
    """The float form only gets this right because it snaps; the integer form has nothing to snap."""
    pitch_degrees = TIER_RESOLUTION_DEGREES[zoom]
    for step in (-900, -5, -1, 0, 1, 5, 90):
        edge = round(step * pitch_degrees, COORDINATE_DECIMALS)
        if not MIN_LONGITUDE <= edge <= MAX_LONGITUDE:
            continue
        assert floor_coordinate(edge, zoom=zoom, axis="longitude") == pytest.approx(edge)


def test_the_origin_is_stored_never_the_centroid() -> None:
    """A coarse cell contains exactly the base cells whose own ORIGIN floors into it."""
    zoom: ZoomTier = 5
    pitch = TIER_RESOLUTION_DEGREES[zoom]
    inside = floor_coordinate(SAMPLE_LONGITUDE, zoom=zoom, axis="longitude")

    assert inside == pytest.approx(SAMPLE_CELL_ORIGIN)
    assert inside <= SAMPLE_LONGITUDE < inside + pitch
    # The centre would be half a pitch higher, and nothing in this warehouse stores it.
    assert inside != pytest.approx(SAMPLE_CELL_ORIGIN + pitch / 2)


def test_the_base_rung_has_no_pitch_and_says_why() -> None:
    """z13 is written at the grain the lane's own producer has, so it has nothing to derive from."""
    assert BASE_PARTITION_ZOOM not in TIER_PITCH_MICRO
    with pytest.raises(LatticeError, match="base rung"):
        tier_pitch_micro(BASE_PARTITION_ZOOM)


def test_the_lattice_origins_are_multiples_of_every_pitch() -> None:
    """Snapping to the antimeridian and the south pole only composes if the origins divide evenly.

    If they did not, shifting by the origin would move every cell boundary and the integer form
    would stop agreeing with the sibling's zero-anchored float form.
    """
    for pitch in TIER_PITCH_MICRO.values():
        assert LONGITUDE_ORIGIN_MICRO % pitch == 0
        assert LATITUDE_ORIGIN_MICRO % pitch == 0
    assert axis_origin_micro("longitude") == LONGITUDE_ORIGIN_MICRO
    assert axis_origin_micro("latitude") == LATITUDE_ORIGIN_MICRO


def test_micro_degrees_round_trip_without_float_dust() -> None:
    assert to_micro_degrees(ROUND_TRIP_DEGREES) == ROUND_TRIP_MICRO
    assert from_micro_degrees(ROUND_TRIP_MICRO) == pytest.approx(ROUND_TRIP_DEGREES)
    assert MICRO_DEGREES_PER_DEGREE == EXPECTED_MICRO_DEGREES_PER_DEGREE
