"""The DuckDB spatial repair chain, and the two ways a transcription of it can quietly be wrong.

`geo.sync_feature_geom_from_properties` (`drizzle/0004_repair_ingested_geometries.sql`) produced every
byte of `geo.features.geom` this lane's export SQL read. The chain restated in `support.py` has to
match it, and the two mismatches that would go unnoticed are both asserted here: adding `ST_Multi`
(which `drought/support.py` legitimately does and this lane must not), and assuming an SRID survives
`ST_AsWKB`.
"""

from __future__ import annotations

from typing import Any

import pytest

from agri_data_service.pipeline.direct.evacuation_zones.support import (
    EvacuationZonesGeometryError,
    evacuation_zones_geometry_session,
    repair_zone_geometries_to_wkb,
)

#: WKB geometry-type codes, little-endian: byte 0 is the endianness flag, bytes 1-4 the type.
_WKB_POLYGON = 3
_WKB_MULTIPOLYGON = 6

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}
#: A self-intersecting "bowtie": invalid, and repairable into polygonal parts. Publishers emit these.
BOWTIE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
}
#: Zero-area: every ordinate identical. `ST_MakeValid` cannot make a polygon of it.
DEGENERATE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]]],
}


def _wkb_type(payload: bytes) -> int:
    """Read the geometry type out of a little-endian WKB header."""
    assert payload[0] == 1, "expected little-endian WKB"
    return int.from_bytes(payload[1:5], "little")


def test_a_valid_polygon_is_published_as_a_polygon_and_never_promoted_to_multipolygon() -> None:
    """DO NOT DELETE. `ST_Multi` belongs in the drought chain and would be a defect in this one.

    PostGIS's trigger leaves a valid Polygon exactly as it found it. Promoting it here would change
    the geometry type of every zone the map has ever drawn, on a lane whose render contract is
    `native_polygon` at every band.
    """
    with evacuation_zones_geometry_session() as session:
        repaired = repair_zone_geometries_to_wkb(session, {"producer:{A}": SQUARE})

    assert _wkb_type(repaired["producer:{A}"]) == _WKB_POLYGON
    assert _wkb_type(repaired["producer:{A}"]) != _WKB_MULTIPOLYGON


def test_the_published_bytes_are_plain_wkb_with_no_srid_envelope() -> None:
    """WKB carries no SRID header; every reader applies EVACUATION_ZONES_GEOMETRY_SRID itself.

    A little-endian EWKB would set the 0x20000000 flag in the type word. Asserting the type word is
    exactly 3 is what proves nothing set one.
    """
    with evacuation_zones_geometry_session() as session:
        repaired = repair_zone_geometries_to_wkb(session, {"producer:{A}": SQUARE})

    assert int.from_bytes(repaired["producer:{A}"][1:5], "little") == _WKB_POLYGON


def test_an_invalid_ring_is_repaired_rather_than_refused_matching_the_trigger() -> None:
    with evacuation_zones_geometry_session() as session:
        repaired = repair_zone_geometries_to_wkb(session, {"producer:{A}": BOWTIE})

    assert repaired["producer:{A}"]
    assert _wkb_type(repaired["producer:{A}"]) in {_WKB_POLYGON, _WKB_MULTIPOLYGON}


def test_an_unrepairable_polygon_refuses_the_whole_snapshot_rather_than_dropping_one_zone() -> None:
    """The trigger RAISEs, which aborts the whole statement; dropping the zone silently would publish
    a statewide evacuation picture with one advisory area missing from it."""
    with evacuation_zones_geometry_session() as session, pytest.raises(EvacuationZonesGeometryError):
        repair_zone_geometries_to_wkb(session, {"producer:{A}": SQUARE, "producer:{B}": DEGENERATE})


def test_an_empty_capture_needs_no_round_trip() -> None:
    with evacuation_zones_geometry_session() as session:
        assert repair_zone_geometries_to_wkb(session, {}) == {}


def test_every_zone_handed_in_comes_back_out() -> None:
    zones = {f"producer:{{Z{index}}}": SQUARE for index in range(5)}

    with evacuation_zones_geometry_session() as session:
        repaired = repair_zone_geometries_to_wkb(session, zones)

    assert repaired.keys() == zones.keys()
