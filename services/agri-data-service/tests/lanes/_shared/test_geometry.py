"""`_shared/geometry.py`: the three chain modes stay distinct, and every lane seam still emits its legacy twin's bytes.

The byte-parity tests drive each lane's own seam beside its legacy `pipeline/direct/**` twin over the same
geometries -- the proof that moving five restated chains onto one helper changed no published WKB byte.
NEEDS DuckDB's `spatial` extension loadable in the test environment.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Final

from agri_data_service.pipeline.direct.burn_severity import support as legacy_burn_severity
from agri_data_service.pipeline.direct.drought import support as legacy_drought
from agri_data_service.pipeline.direct.fire_perimeters import support as legacy_fire_perimeters
from agri_data_service.pipeline.direct.watersheds import support as legacy_watersheds
from agri_data_service.pipeline.lanes._shared.geometry import convert_geojson_to_wkb, spatial_session
from agri_data_service.pipeline.lanes.burn_severity import release_rows as lane_burn_severity
from agri_data_service.pipeline.lanes.drought import geometry as lane_drought
from agri_data_service.pipeline.lanes.evacuation_zones import support as lane_evacuation_zones
from agri_data_service.pipeline.lanes.fire_perimeters import support as lane_fire_perimeters
from agri_data_service.pipeline.lanes.watersheds import geometry as lane_watersheds

#: Self-intersecting: MakeValid actually runs.
BOW_TIE: Final[dict[str, object]] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 46.0], [-119.0, 45.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: Already valid: the pass-through mode must leave its bytes untouched.
SQUARE: Final[dict[str, object]] = {
    "type": "Polygon",
    "coordinates": [[[-118.0, 45.0], [-117.0, 45.0], [-117.0, 46.0], [-118.0, 46.0], [-118.0, 45.0]]],
}
WKB_POLYGON: Final = 3
WKB_MULTIPOLYGON: Final = 6


def _wkb_type(wkb: bytes) -> int:
    """The OGC geometry type code from a WKB header (byte 0 is the byte order)."""
    (code,) = struct.unpack("<I" if wkb[0] == 1 else ">I", wkb[1:5])
    return int(code)


def test_each_chain_mode_runs_its_own_chain() -> None:
    """bare repairs nothing; always_multi repairs and normalises everything; pass_through repairs only the invalid."""
    with spatial_session(memory_limit="1600MB", threads=3, max_temp_directory_size="0GiB") as session:
        bare = convert_geojson_to_wkb(session, [BOW_TIE, SQUARE], mode="bare")
        always_multi = convert_geojson_to_wkb(session, [BOW_TIE, SQUARE], mode="always_multi")
        pass_through = convert_geojson_to_wkb(session, [BOW_TIE, SQUARE], mode="pass_through_flagged")

    assert [_wkb_type(shape.wkb) for shape in bare] == [WKB_POLYGON, WKB_POLYGON]
    assert [shape.is_valid for shape in bare] == [None, None]
    assert [_wkb_type(shape.wkb) for shape in always_multi] == [WKB_MULTIPOLYGON, WKB_MULTIPOLYGON]

    repaired_bow_tie, untouched_square = pass_through
    assert (repaired_bow_tie.was_repaired, repaired_bow_tie.is_valid) == (True, True)
    assert repaired_bow_tie.wkb != bare[0].wkb
    # The invariant the pass-through exists for: a valid shape's bytes never round-trip through MakeValid.
    assert (untouched_square.was_repaired, untouched_square.area_change) == (False, None)
    assert untouched_square.wkb == bare[1].wkb
    assert _wkb_type(untouched_square.wkb) == WKB_POLYGON


@dataclass(frozen=True)
class _DroughtArea:
    drought_intensity_class: int
    geometry: dict[str, object]


def test_watersheds_bare_conversion_is_byte_identical_to_the_legacy_writer() -> None:
    basins = [("170900011201", BOW_TIE), ("170900011202", SQUARE)]
    with lane_watersheds.watersheds_geometry_session() as session:
        lane = lane_watersheds.convert_watershed_geometries_to_wkb(session, basins)
    with legacy_watersheds.watersheds_geometry_session() as session:
        legacy = legacy_watersheds.convert_watershed_geometries_to_wkb(session, basins)
    assert lane == legacy


def test_drought_repair_is_byte_identical_to_the_legacy_writer() -> None:
    areas = (_DroughtArea(0, SQUARE), _DroughtArea(2, BOW_TIE))
    with lane_drought.drought_geometry_session() as session:
        lane = lane_drought.repair_drought_areas_to_wkb(session, areas)
    with legacy_drought.drought_geometry_session() as session:
        legacy = legacy_drought.repair_drought_areas_to_wkb(session, areas)
    assert lane == legacy


def test_burn_severity_repair_is_byte_identical_to_the_legacy_writer() -> None:
    fires = [("FIRE_A", BOW_TIE), ("FIRE_B", SQUARE)]
    with lane_burn_severity.geometry_session() as session:
        lane = lane_burn_severity.repair_geometries_to_wkb(session, fires)
    with legacy_burn_severity.burn_severity_geometry_session() as session:
        legacy = legacy_burn_severity.repair_burn_severity_geometries_to_wkb(session, fires)
    assert lane == legacy


def test_fire_perimeters_and_evacuation_zones_repair_byte_identically_to_the_legacy_trigger_chain() -> None:
    """Both lanes run the trigger's pass-through chain; the legacy fire-perimeters writer is its surviving copy."""
    geometries: list[object] = [BOW_TIE, SQUARE]
    identities = ["{A}", "{B}"]
    with lane_fire_perimeters.fire_perimeter_geometry_session() as session:
        lane = lane_fire_perimeters.repair_perimeter_geometries_to_wkb(session, geometries, identities)
    with lane_evacuation_zones.evacuation_zones_geometry_session() as session:
        zones = lane_evacuation_zones.repair_zone_geometries_to_wkb(session, [("{A}", BOW_TIE), ("{B}", SQUARE)])
    with legacy_fire_perimeters.fire_perimeter_geometry_session() as session:
        legacy = legacy_fire_perimeters.repair_perimeter_geometries_to_wkb(session, geometries, identities)

    assert [(shape.wkb, shape.repaired, shape.area_change) for shape in lane] == [
        (shape.wkb, shape.repaired, shape.area_change) for shape in legacy
    ]
    assert [zones[identity] for identity in identities] == [shape.wkb for shape in legacy]
