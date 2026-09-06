"""Row building, and the three `geo.geometry` columns a direct capture has to reproduce without it.

Every assertion about `geometry_version_id`, `geometry_version_valid_from` and
`geometry_last_confirmed_at` here is a restatement of what the Postgres chain provably did, cited in
`pipeline/direct/evacuation_zones/rows.py`'s module docstring. They are the reason this lane was
called the hardest of the five, and they are the reason it turned out not to be.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.pipeline.direct.evacuation_zones.products import DIRECT_PRODUCER, DIRECT_SOURCE_LITERAL
from agri_data_service.pipeline.direct.evacuation_zones.rows import (
    CONTENT_DIGEST_COLUMNS,
    EvacuationZonesRowError,
    apply_carried_forward_updated_at,
    content_digest,
    direct_geometry_version_id,
    evacuation_zones_table,
    natural_key_for,
    row_content_digests,
    split_into_parts,
    updated_at_by_natural_key,
)
from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_SCHEMA

FETCHED_AT = datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
SNAPSHOT_DAY = date(2026, 9, 3)
CREATED_AT = datetime(2025, 4, 14, 8, 0, tzinfo=UTC)

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}
SHIFTED_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.8, 44.0], [-122.8, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


def zone(
    global_id: str = "{ABC}",
    *,
    level: int | None = 3,
    created_at: datetime | None = CREATED_AT,
    geometry: dict[str, Any] | None = None,
    area_name: str | None = "Milepost 97",
) -> dict[str, Any]:
    """One parsed record in exactly the shape `parse_evacuation_zone_collection` produces."""
    return {
        "globalId": global_id,
        "evacuationAreaName": area_name,
        "fireName": "Milepost 97 Fire",
        "county": "Douglas",
        "hazardType": "Wildfire",
        "editorName": "OEM Sync",
        "evacuationLevel": level,
        "evacuationLevelLabel": None if level is None else {1: "Be Ready", 2: "Be Set", 3: "Go Now"}[level],
        "severity": None if level is None else {1: "moderate", 2: "high", 3: "critical"}[level],
        "structuresWithin": 12.0,
        "addressesWithin": 30.0,
        "populationWithin": 74.0,
        "createdAt": created_at,
        "createdDate": None if created_at is None else created_at.isoformat(),
        "lastEditedDate": "2026-09-03T17:29:00Z",
        "geometry": geometry or SQUARE,
    }


def source(*zones: dict[str, Any], fetched_at: datetime = FETCHED_AT) -> EvacuationZonesSource:
    return EvacuationZonesSource(bbox="-125,42,-111,49", fetched_at=fetched_at, zones=tuple(zones))


def test_a_captured_zone_conforms_to_the_published_schema_with_the_producers_own_identity() -> None:
    table = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY)

    assert table.schema == EVACUATION_ZONES_SCHEMA.arrow_schema
    row = table.to_pylist()[0]
    assert row["global_id"] == "{ABC}"
    assert row["natural_key"] == f"{DIRECT_PRODUCER}:{{ABC}}"
    assert row["producer"] == DIRECT_PRODUCER
    assert row["source"] == DIRECT_SOURCE_LITERAL
    assert row["snapshot_day"] == SNAPSHOT_DAY
    assert row["evacuation_level"] == 3
    assert row["evacuation_level_label"] == "Go Now"


def test_observed_at_is_oregons_creation_stamp_and_never_the_edit_clock_or_the_wall_clock() -> None:
    """`last_edited_date` is re-stamped on unchanged areas every few minutes; dating by it is banned."""
    row = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]

    assert row["observed_at"] == CREATED_AT
    assert row["observed_at"] != FETCHED_AT


def test_an_area_oregon_never_dated_stays_undated_rather_than_acquiring_a_clock_reading() -> None:
    row = evacuation_zones_table(source(zone(created_at=None)), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]

    assert row["observed_at"] is None
    # NULL, never the dimension's `-infinity`: year 0001 is a real, sortable instant a reader would
    # take for a measurement, and the Arrow column is nullable for exactly this case.
    assert row["geometry_version_valid_from"] is None


def test_geometry_version_valid_from_reproduces_the_dimension_exactly_because_it_can_only_be_observed_at() -> None:
    """DO NOT DELETE. This is finding (2) about the retired `geo.geometry` join, as an assertion.

    `insert_geometry_versions.sql` is bound `version_valid_froms = [request.observed_at]`, and for
    this lane `observed_at` is Oregon's `created_date` and nothing else. The column was therefore
    always a duplicate of `observed_at`, and reproducing it needs no dimension at all.
    """
    row = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]

    assert row["geometry_version_valid_from"] == row["observed_at"] == CREATED_AT


def test_geometry_last_confirmed_at_is_this_captures_own_instant_for_every_row() -> None:
    """Finding (3): the column is a poll clock by design, and a direct capture confirms every row."""
    rows = evacuation_zones_table(source(zone("{A}"), zone("{B}")), snapshot_day=SNAPSHOT_DAY).to_pylist()

    assert {row["geometry_last_confirmed_at"] for row in rows} == {FETCHED_AT}


def test_geometry_version_id_is_deterministic_namespaced_and_moves_with_the_shape() -> None:
    """Finding (1): the dimension's value was `str(uuid4())` -- random, and frozen once undatable.

    The replacement is a `direct:` token nothing can mistake for a uuid, identical across two
    captures of the same shape, and DIFFERENT the moment the polygon moves -- which is the one thing
    the Postgres chain provably could not express for this lane.
    """
    first = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]
    again = evacuation_zones_table(
        source(zone(), fetched_at=datetime(2026, 9, 4, tzinfo=UTC)), snapshot_day=date(2026, 9, 4)
    ).to_pylist()[0]
    moved = evacuation_zones_table(source(zone(geometry=SHIFTED_SQUARE)), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]

    assert first["geometry_version_id"].startswith(f"direct:{DIRECT_PRODUCER}:")
    assert first["geometry_version_id"] == again["geometry_version_id"]
    assert first["geometry_version_id"] != moved["geometry_version_id"]
    assert direct_geometry_version_id("k", b"\x01") != direct_geometry_version_id("k", b"\x02")


def test_data_available_at_is_carried_forward_unpopulated_never_assumed() -> None:
    row = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY).to_pylist()[0]

    assert row["data_available_at"] is None


def test_the_content_digest_sees_a_shape_only_change_that_the_postgres_pair_could_not() -> None:
    """DO NOT DELETE. This is the repair the direct writer makes, expressed as an assertion.

    `refresh_features.sql` strips `geometry` from BOTH sides of its change test, so a shape-only
    revision fires no UPDATE, the `geo_features_sync_geom` trigger never runs, and the dimension
    files the change as `undatable`. Neither table learns the new shape. `content_digest` includes
    `geometry_wkb`, so this writer does.
    """
    before = evacuation_zones_table(source(zone()), snapshot_day=SNAPSHOT_DAY)
    after = evacuation_zones_table(source(zone(geometry=SHIFTED_SQUARE)), snapshot_day=SNAPSHOT_DAY)

    assert content_digest(before) != content_digest(after)


def test_the_content_digest_ignores_this_repos_own_clocks_so_an_unchanged_source_publishes_nothing() -> None:
    """A digest that moved with `fetched_at` would re-snapshot the whole layer on every tick."""
    monday = evacuation_zones_table(source(zone()), snapshot_day=date(2026, 9, 3))
    tuesday = evacuation_zones_table(
        source(zone(), fetched_at=datetime(2026, 9, 4, 1, tzinfo=UTC)), snapshot_day=date(2026, 9, 4)
    )

    assert content_digest(monday) == content_digest(tuesday)
    for excluded in ("snapshot_day", "geometry_last_confirmed_at", "feature_updated_at", "data_available_at"):
        assert excluded not in CONTENT_DIGEST_COLUMNS


def test_the_content_digest_sees_an_evacuation_level_moving_from_be_ready_to_go_now() -> None:
    """The life-safety change this whole lane exists to publish within hours."""
    be_ready = evacuation_zones_table(source(zone(level=1)), snapshot_day=SNAPSHOT_DAY)
    go_now = evacuation_zones_table(source(zone(level=3)), snapshot_day=SNAPSHOT_DAY)

    assert content_digest(be_ready) != content_digest(go_now)


def test_the_digest_is_independent_of_row_order() -> None:
    """A table reassembled from part files in any order must digest identically."""
    forwards = evacuation_zones_table(source(zone("{A}"), zone("{B}")), snapshot_day=SNAPSHOT_DAY)
    backwards = evacuation_zones_table(source(zone("{B}"), zone("{A}")), snapshot_day=SNAPSHOT_DAY)

    assert content_digest(forwards) == content_digest(backwards)


def test_an_empty_capture_digests_stably_so_two_quiet_days_compare_equal() -> None:
    empty = EVACUATION_ZONES_SCHEMA.arrow_schema.empty_table()

    assert content_digest(empty) == content_digest(evacuation_zones_table(source(), snapshot_day=SNAPSHOT_DAY))


def test_feature_updated_at_is_carried_forward_for_unchanged_rows_and_restamped_for_moved_ones() -> None:
    """Reproduces `refresh_features.sql`'s ROW-scoped UPDATE; a whole-snapshot re-stamp is a poll clock."""
    yesterday = datetime(2026, 9, 2, 12, tzinfo=UTC)
    published = evacuation_zones_table(
        source(zone("{A}"), zone("{B}"), fetched_at=yesterday), snapshot_day=date(2026, 9, 2)
    )
    captured = evacuation_zones_table(source(zone("{A}"), zone("{B}", level=1)), snapshot_day=SNAPSHOT_DAY)

    published_digests = row_content_digests(published)
    current_digests = row_content_digests(captured)
    carried = {
        key: stamp
        for key, stamp in updated_at_by_natural_key(published).items()
        if published_digests.get(key) == current_digests.get(key)
    }
    patched = apply_carried_forward_updated_at(captured, carried).to_pylist()
    by_key = {row["natural_key"]: row for row in patched}

    assert by_key[natural_key_for("{A}")]["feature_updated_at"] == yesterday
    assert by_key[natural_key_for("{B}")]["feature_updated_at"] == FETCHED_AT


def test_parts_are_sliced_from_one_globally_sorted_table_so_no_part_index_is_ever_parsed_back() -> None:
    zones = [zone(f"{{Z{index:03d}}}") for index in range(450)]
    table = evacuation_zones_table(source(*zones), snapshot_day=SNAPSHOT_DAY)

    parts = split_into_parts(table)

    assert [part.num_rows for part in parts] == [200, 200, 50]
    stitched = [key for part in parts for key in part.column("natural_key").to_pylist()]
    assert stitched == sorted(stitched)


def test_a_zone_that_reached_the_row_builder_with_no_identity_is_refused_never_synthesised() -> None:
    blank = zone()
    blank["globalId"] = "   "

    with pytest.raises(EvacuationZonesRowError):
        evacuation_zones_table(source(blank), snapshot_day=SNAPSHOT_DAY)
