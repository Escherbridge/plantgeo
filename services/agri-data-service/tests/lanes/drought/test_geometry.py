"""DuckDB spatial repair: the MakeValid/CollectionExtract/Multi round trip and the empty-geometry refusal.

Deliberately a near-duplicate of `tests/direct/test_drought_support.py`: this package's `geometry.py`
is itself a duplicate of `pipeline/direct/drought/support.py` (never an import -- see `geometry.py`'s
module docstring), so the two test files pin the two copies to stay byte-identical. NEEDS DuckDB's
`spatial` extension loadable in the test environment (`pipeline/direct/AGENTS.md`, "Drought").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agri_data_service.pipeline.lanes.drought.geometry import (
    DroughtGeometryError,
    drought_geometry_session,
    repair_drought_areas_to_wkb,
)

if TYPE_CHECKING:
    from agri_data_service.pipeline.lanes.drought.source_protocol import DroughtAreaPayload


def _area(drought_intensity_class: int, geometry: dict[str, object]) -> DroughtAreaPayload:
    """A minimal `DroughtAreaPayload`: the protocol is structural, so a tiny namedtuple-ish object suffices."""

    class _Area:
        def __init__(self) -> None:
            self.drought_intensity_class = drought_intensity_class
            self.geometry = geometry

    return _Area()  # type: ignore[return-value]


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
    areas = (_area(0, VALID_SQUARE),)

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {0}
    assert isinstance(repaired[0], bytes)
    assert len(repaired[0]) > 0


def test_a_self_intersecting_ring_is_repaired_not_rejected() -> None:
    areas = (_area(1, BOWTIE),)

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {1}
    assert len(repaired[1]) > 0


def test_multiple_classes_repair_in_one_round_trip_keyed_correctly() -> None:
    areas = (_area(0, VALID_SQUARE), _area(4, BOWTIE))

    with drought_geometry_session() as session:
        repaired = repair_drought_areas_to_wkb(session, areas)

    assert set(repaired) == {0, 4}


def test_a_geometry_that_repairs_to_empty_refuses_the_whole_release() -> None:
    """DO NOT DELETE. A repaired-empty class must never store `MULTIPOLYGON EMPTY` as real coverage,
    matching `sql/ingest/store_drought_area.sql`'s own refusal of the identical case in PostGIS."""
    areas = (_area(0, VALID_SQUARE), _area(1, DEGENERATE))

    with drought_geometry_session() as session, pytest.raises(DroughtGeometryError, match="D1"):
        repair_drought_areas_to_wkb(session, areas)


def test_an_empty_area_sequence_returns_an_empty_mapping() -> None:
    with drought_geometry_session() as session:
        assert repair_drought_areas_to_wkb(session, ()) == {}
