"""The DuckDB spatial session and the GeoJSON-to-WKB conversion that reproduces the PostGIS trigger.

NEEDS DuckDB's `spatial` extension loadable in the test environment: every test here opens
`fire_perimeter_geometry_session`.
"""

from __future__ import annotations

from typing import Any

import pytest

from agri_data_service.pipeline.direct.fire_perimeters.support import (
    FirePerimeterGeometryError,
    fire_perimeter_geometry_session,
    perimeter_geometries_to_wkb,
)

VALID_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
# A self-intersecting "bowtie": ST_IsValid is false in GEOS, so geo_features_sync_geom raises 22023
# and PostgreSQL never held such a row.
BOWTIE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]],
}
OTHER_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-100.0, 40.0], [-99.0, 40.0], [-99.0, 41.0], [-100.0, 41.0], [-100.0, 40.0]]],
}


def test_a_valid_polygon_converts_to_non_empty_wkb() -> None:
    with fire_perimeter_geometry_session() as session:
        converted = perimeter_geometries_to_wkb(session, [VALID_SQUARE], ["OR-A"])

    assert len(converted) == 1
    assert isinstance(converted[0], bytes)
    assert len(converted[0]) > 0


def test_an_empty_input_needs_no_round_trip() -> None:
    with fire_perimeter_geometry_session() as session:
        assert perimeter_geometries_to_wkb(session, [], []) == ()


def test_the_answer_is_positionally_aligned_with_the_input() -> None:
    """A silent reorder would leave every perimeter present, valid, counted -- and drawn on other ground."""
    with fire_perimeter_geometry_session() as session:
        first = perimeter_geometries_to_wkb(session, [VALID_SQUARE], ["OR-A"])
        second = perimeter_geometries_to_wkb(session, [OTHER_SQUARE], ["OR-B"])
        both = perimeter_geometries_to_wkb(session, [VALID_SQUARE, OTHER_SQUARE], ["OR-A", "OR-B"])

    assert both == (first[0], second[0])


def test_an_invalid_polygon_refuses_the_whole_snapshot_and_names_the_perimeter() -> None:
    """No MakeValid here: PostGIS raises 22023 rather than repairing, so repairing would ADD a row."""
    with fire_perimeter_geometry_session() as session, pytest.raises(FirePerimeterGeometryError) as refusal:
        perimeter_geometries_to_wkb(session, [VALID_SQUARE, BOWTIE], ["OR-A", "OR-BOWTIE"])

    assert "OR-BOWTIE" in str(refusal.value)
    assert "22023" in str(refusal.value)


def test_a_refusal_that_could_not_name_its_perimeter_is_refused_first() -> None:
    with fire_perimeter_geometry_session() as session, pytest.raises(FirePerimeterGeometryError, match="identities"):
        perimeter_geometries_to_wkb(session, [VALID_SQUARE, OTHER_SQUARE], ["OR-A"])


def test_a_document_duckdb_cannot_read_as_geojson_is_refused_the_way_postgis_refuses_it() -> None:
    with fire_perimeter_geometry_session() as session, pytest.raises(FirePerimeterGeometryError, match="22023"):
        perimeter_geometries_to_wkb(session, [{"type": "Polygon", "coordinates": "not-a-ring"}], ["OR-BAD"])
