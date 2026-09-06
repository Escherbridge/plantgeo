"""DuckDB spatial GeoJSON-to-WKB conversion: the bare (no-repair) round trip and the empty-geometry refusal.

NEEDS DuckDB's `spatial` extension loadable in the test environment. Unlike
`tests/direct/test_drought_support.py`, there is no `ST_MakeValid` here to repair a self-intersecting
ring -- see `pipeline/direct/watersheds/support.py`'s module docstring for why this lane's Postgres
write path never repairs either, and mirroring it exactly is the point.
"""

from __future__ import annotations

import pytest

from agri_data_service.pipeline.direct.watersheds.support import (
    WatershedsGeometryError,
    convert_watershed_geometries_to_wkb,
    watersheds_geometry_session,
)

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: An empty coordinates array parses to a genuinely empty Polygon -- the case `ST_IsEmpty` reports
#: True on WITHOUT a repair chain, since this module (deliberately) runs no `ST_MakeValid` to collapse
#: a merely-degenerate ring the way `tests/direct/test_drought_support.py`'s DEGENERATE fixture does.
EMPTY_POLYGON = {"type": "Polygon", "coordinates": []}


def test_a_valid_polygon_converts_to_wkb_keyed_by_huc12() -> None:
    with watersheds_geometry_session() as session:
        converted = convert_watershed_geometries_to_wkb(session, [("170900011201", VALID_SQUARE)])

    assert set(converted) == {"170900011201"}
    assert isinstance(converted["170900011201"], bytes)
    assert len(converted["170900011201"]) > 0


def test_multiple_basins_convert_in_one_round_trip_keyed_correctly() -> None:
    with watersheds_geometry_session() as session:
        converted = convert_watershed_geometries_to_wkb(
            session, [("170900011201", VALID_SQUARE), ("170900011202", VALID_SQUARE)]
        )

    assert set(converted) == {"170900011201", "170900011202"}


def test_an_empty_geometry_refuses_the_whole_population() -> None:
    """DO NOT DELETE. An empty-converting basin must never store `POLYGON EMPTY` as real boundary coverage."""
    with watersheds_geometry_session() as session, pytest.raises(WatershedsGeometryError, match="170900011203"):
        convert_watershed_geometries_to_wkb(session, [("170900011201", VALID_SQUARE), ("170900011203", EMPTY_POLYGON)])


def test_an_empty_geometry_sequence_returns_an_empty_mapping() -> None:
    with watersheds_geometry_session() as session:
        assert convert_watershed_geometries_to_wkb(session, []) == {}
