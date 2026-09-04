"""DuckDB spatial repair: the MakeValid/CollectionExtract/Multi round trip and the empty-geometry refusal.

NEEDS DuckDB's `spatial` extension loadable in the test environment (see
`pipeline/direct/AGENTS.md`, "Drought" -- the second trap this whole lane was built around).
"""

from __future__ import annotations

import pytest

from agri_data_service.ingest.usdm import DroughtArea
from agri_data_service.pipeline.direct.drought.support import (
    DroughtGeometryError,
    drought_geometry_session,
    repair_drought_areas_to_wkb,
)

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: A self-intersecting bow-tie ring: `ST_MakeValid` must repair it rather than reject the whole class.
BOWTIE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 46.0], [-119.0, 45.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
#: A zero-area ring: repairs to an empty geometry, which must be refused rather than stored.
DEGENERATE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-120.0, 45.0], [-120.0, 45.0], [-120.0, 45.0]]],
}


def test_a_valid_polygon_repairs_to_wkb_keyed_by_drought_class() -> None:
    areas = (DroughtArea(drought_monitor_category=0, geometry=VALID_SQUARE),)

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {0}
    assert isinstance(repaired[0], bytes)
    assert len(repaired[0]) > 0


def test_a_self_intersecting_ring_is_repaired_not_rejected() -> None:
    areas = (DroughtArea(drought_monitor_category=1, geometry=BOWTIE),)

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {1}
    assert len(repaired[1]) > 0


def test_multiple_classes_repair_in_one_round_trip_keyed_correctly() -> None:
    areas = (
        DroughtArea(drought_monitor_category=0, geometry=VALID_SQUARE),
        DroughtArea(drought_monitor_category=4, geometry=BOWTIE),
    )

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {0, 4}


def test_a_geometry_that_repairs_to_empty_refuses_the_whole_release() -> None:
    """DO NOT DELETE. A repaired-empty class must never store `MULTIPOLYGON EMPTY` as real coverage.

    Matches `sql/ingest/store_drought_area.sql`'s own refusal of the identical case in PostGIS.
    """
    areas = (
        DroughtArea(drought_monitor_category=0, geometry=VALID_SQUARE),
        DroughtArea(drought_monitor_category=1, geometry=DEGENERATE),
    )

    with drought_geometry_session() as session, pytest.raises(DroughtGeometryError, match="D1"):
        repair_drought_areas_to_wkb(session, areas)


def test_an_empty_area_sequence_returns_an_empty_mapping() -> None:
    with drought_geometry_session() as session:
        assert repair_drought_areas_to_wkb(session, ()) == {}
