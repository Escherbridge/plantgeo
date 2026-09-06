"""DuckDB spatial repair: the MakeValid/CollectionExtract/Multi round trip and the empty-geometry refusal.

NEEDS DuckDB's `spatial` extension loadable in the test environment (see
`pipeline/direct/AGENTS.md`, "Drought" -- the second trap this whole family of geometry lanes was
built around, restated here for burn-severity's Fire_ID-keyed variant).
"""

from __future__ import annotations

import pytest

from agri_data_service.pipeline.direct.burn_severity.support import (
    BurnSeverityGeometryError,
    burn_severity_geometry_session,
    repair_burn_severity_geometries_to_wkb,
)

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: A self-intersecting bow-tie ring: `ST_MakeValid` must repair it rather than reject the whole feature.
BOWTIE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 46.0], [-119.0, 45.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: A zero-area ring: repairs to an empty geometry, which must be refused rather than stored.
DEGENERATE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-120.0, 45.0], [-120.0, 45.0], [-120.0, 45.0]]],
}


def test_a_valid_polygon_repairs_to_wkb_keyed_by_fire_id() -> None:
    features = (("FIRE_A", VALID_SQUARE),)

    with burn_severity_geometry_session() as session:
        repaired = repair_burn_severity_geometries_to_wkb(session, features)

    assert set(repaired) == {"FIRE_A"}
    assert isinstance(repaired["FIRE_A"], bytes)
    assert len(repaired["FIRE_A"]) > 0


def test_a_self_intersecting_ring_is_repaired_not_rejected() -> None:
    features = (("FIRE_A", BOWTIE),)

    with burn_severity_geometry_session() as session:
        repaired = repair_burn_severity_geometries_to_wkb(session, features)

    assert set(repaired) == {"FIRE_A"}
    assert len(repaired["FIRE_A"]) > 0


def test_multiple_fires_repair_in_one_round_trip_keyed_correctly() -> None:
    features = (("FIRE_A", VALID_SQUARE), ("FIRE_B", BOWTIE))

    with burn_severity_geometry_session() as session:
        repaired = repair_burn_severity_geometries_to_wkb(session, features)

    assert set(repaired) == {"FIRE_A", "FIRE_B"}


def test_a_geometry_that_repairs_to_empty_refuses_the_whole_release_day() -> None:
    """DO NOT DELETE. A repaired-empty fire must never store `MULTIPOLYGON EMPTY` as real burned area.

    Matches `geo.sync_feature_geom_from_properties`'s own refusal of the identical case in PostGIS
    (`drizzle/0004_repair_ingested_geometries.sql`).
    """
    features = (("FIRE_A", VALID_SQUARE), ("FIRE_B", DEGENERATE))

    with burn_severity_geometry_session() as session, pytest.raises(BurnSeverityGeometryError, match="FIRE_B"):
        repair_burn_severity_geometries_to_wkb(session, features)


def test_a_duplicate_fire_id_across_the_day_is_refused_not_silently_collapsed() -> None:
    """Unlike drought's five known drought classes, a Fire_ID repeat means two cohorts collided."""
    features = (("FIRE_A", VALID_SQUARE), ("FIRE_A", BOWTIE))

    with burn_severity_geometry_session() as session, pytest.raises(BurnSeverityGeometryError, match="duplicate"):
        repair_burn_severity_geometries_to_wkb(session, features)


def test_an_empty_feature_sequence_returns_an_empty_mapping() -> None:
    with burn_severity_geometry_session() as session:
        assert repair_burn_severity_geometries_to_wkb(session, ()) == {}
