"""DuckDB spatial GeoJSON-to-WKB conversion for `pipeline/lanes/watersheds/geometry.py`.

A DELIBERATE DUPLICATE of `tests/direct/test_watersheds_direct_support.py`, over the
`pipeline/lanes/watersheds/geometry.py` module this strategy's `rows()` calls, not the legacy
`pipeline/direct/watersheds/support.py` one: a strategy module never imports `pipeline/direct/**`
(`pipeline/lanes/AGENTS.md`), so its own geometry conversion needs its own conformance proof. Same
SQL, same session settings, same refusal rule -- see `geometry.py`'s module docstring for why the
duplication is intentional.

NEEDS DuckDB's `spatial` extension loadable in the test environment.
"""

from __future__ import annotations

import pytest

from agri_data_service.pipeline.lanes.watersheds.geometry import (
    WatershedsGeometryError,
    convert_watershed_geometries_to_wkb,
    watersheds_geometry_session,
)

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: An empty coordinates array parses to a genuinely empty Polygon -- the case `ST_IsEmpty` reports
#: True on WITHOUT a repair chain, since this module (deliberately) runs no `ST_MakeValid`.
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
