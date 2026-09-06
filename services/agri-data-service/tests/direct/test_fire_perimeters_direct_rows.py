"""Conforming one fetched WFIGS population to the registered fire-perimeters schema.

`publisher_named_day` and `chunk_row_indices_by_geometry_bytes` are pure and need nothing. The
`fire_perimeter_population` tests NEED DuckDB's `spatial` extension loadable, because every row's
geometry goes through `support.perimeter_geometries_to_wkb` -- the same requirement
`test_drought_rows.py` carries.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from agri_data_service.pipeline.direct.fire_perimeters.rows import (
    DIRECT_FEATURE_ID_PREFIX,
    OBSERVATION_DAY_KEYS,
    PUBLISHED_STATUS,
    chunk_row_indices_by_geometry_bytes,
    direct_feature_id,
    fire_perimeter_population,
    fire_perimeters_table,
    publisher_named_day,
)
from agri_data_service.pipeline.direct.fire_perimeters.source import FirePerimetersSource
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA

FETCHED_AT = datetime(2026, 9, 6, 14, 30, tzinfo=UTC)
SNAPSHOT_DAY = date(2026, 9, 6)

VALID_SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}


def _perimeter(identifier: str, **overrides: Any) -> dict[str, Any]:
    """One record shaped exactly as `ingest.wfigs.parse_perimeter_collection` emits it."""
    record: dict[str, Any] = {
        "uniqueFireIdentifier": identifier,
        "irwinId": f"irwin-{identifier}",
        "incidentName": f"{identifier} Fire",
        "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z",
        "polygonDateTime": "2026-08-30T18:45:00.000Z",
        "gisAcres": 1234.5,
        "fireCause": "Natural",
        "incidentTypeCategory": "WF",
        "pooState": "US-OR",
        "percentContained": 30.0,
        "geometry": VALID_SQUARE,
    }
    record.update(overrides)
    return record


def _source(*perimeters: dict[str, Any]) -> FirePerimetersSource:
    return FirePerimetersSource(
        perimeters=tuple(perimeters),
        bbox="-125,42,-116,49",
        fetched_at=FETCHED_AT,
        bytes_read=1024,
    )


def test_the_observation_day_chain_is_the_sql_functions_chain_in_order() -> None:
    """A transcription that dropped a key would silently re-date rows the day the document grows one."""
    assert OBSERVATION_DAY_KEYS == ("observedAt", "updatedAt", "polygonDateTime", "fireDiscoveryDateTime")


def test_the_polygon_time_wins_over_the_discovery_time() -> None:
    """Ranked above polygonDateTime, discovery would collapse a perimeter's revisions onto one day."""
    named = publisher_named_day(
        {"polygonDateTime": "2026-08-30T18:45:00.000Z", "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"}
    )

    assert named == date(2026, 8, 30)


def test_a_json_null_polygon_time_falls_through_to_the_discovery_time() -> None:
    """The 13-of-119 production rows migration 0018 exists for: dated, but not by their polygon."""
    named = publisher_named_day({"polygonDateTime": None, "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"})

    assert named == date(2026, 7, 16)


def test_a_present_but_empty_timestamp_stops_the_chain_instead_of_falling_through() -> None:
    """`COALESCE` treats '' as a value, not NULL, so PostgreSQL returns NULL without trying the next key."""
    named = publisher_named_day({"polygonDateTime": "", "fireDiscoveryDateTime": "2026-07-16T01:07:00.000Z"})

    assert named is None


def test_a_row_with_no_timestamp_at_all_is_undated_rather_than_dropped() -> None:
    assert publisher_named_day({"uniqueFireIdentifier": "OR-XYZ-000001"}) is None


def test_an_unreal_calendar_day_is_refused_the_way_pg_input_is_valid_refuses_it() -> None:
    """`to_date('2026-02-31', ...)` raises in PostgreSQL, and one raise inside ST_AsMVT blanks a tile."""
    assert publisher_named_day({"polygonDateTime": "2026-02-31T00:00:00.000Z"}) is None


def test_a_misshaped_day_prefix_is_refused_by_the_regex() -> None:
    assert publisher_named_day({"polygonDateTime": "30/08/2026"}) is None


def test_the_named_day_is_the_string_prefix_never_a_converted_instant() -> None:
    """The rule that moves 6,279 of 16,743 water-gauge rows when broken: prefix, not conversion."""
    assert publisher_named_day({"polygonDateTime": "2026-08-30T23:59:59.999Z"}) == date(2026, 8, 30)


def test_a_direct_feature_id_is_namespaced_and_carries_no_version_component() -> None:
    """Folding the day in would churn the column and the content digest on every version."""
    assert direct_feature_id("2026-OR-ABC-000123") == f"{DIRECT_FEATURE_ID_PREFIX}:2026-OR-ABC-000123"


def test_the_population_conforms_to_the_registered_schema() -> None:
    population = fire_perimeter_population(_source(_perimeter("OR-A"), _perimeter("OR-B")))

    table = fire_perimeters_table(population, snapshot_day=SNAPSHOT_DAY)

    assert table.schema.equals(FIRE_PERIMETERS_SCHEMA.arrow_schema)
    assert table.num_rows == 2
    assert population.rejected == 0
    assert population.collapsed == 0


def test_every_row_carries_the_fetch_instant_as_updated_at_and_the_stamped_version_day() -> None:
    population = fire_perimeter_population(_source(_perimeter("OR-A")))

    rows = fire_perimeters_table(population, snapshot_day=SNAPSHOT_DAY).to_pylist()

    assert [row["updated_at"] for row in rows] == [FETCHED_AT]
    assert [row["snapshot_day"] for row in rows] == [SNAPSHOT_DAY]


def test_every_row_carries_the_published_status_and_a_null_availability_time() -> None:
    """`status` reproduces geo.features' schema default; `data_available_at` is 100% NULL in production."""
    population = fire_perimeter_population(_source(_perimeter("OR-A")))

    row = population.rows[0]

    assert row["status"] == PUBLISHED_STATUS
    assert row["data_available_at"] is None


def test_an_undated_perimeter_survives_into_the_table_with_a_null_observed_day() -> None:
    """The retired day export dropped these rows outright; the tile draws them at EVERY slider date."""
    population = fire_perimeter_population(
        _source(_perimeter("OR-UNDATED", polygonDateTime=None, fireDiscoveryDateTime=None))
    )

    rows = fire_perimeters_table(population, snapshot_day=SNAPSHOT_DAY).to_pylist()

    assert len(rows) == 1
    assert rows[0]["observed_day"] is None
    assert rows[0]["unique_fire_identifier"] == "OR-UNDATED"


def test_severity_is_the_bucket_the_shared_builder_graded_not_a_fabricated_default() -> None:
    graded = fire_perimeter_population(_source(_perimeter("OR-A", percentContained=10.0)))
    ungraded = fire_perimeter_population(_source(_perimeter("OR-B", percentContained=None)))

    assert graded.rows[0]["severity"] == "critical"
    assert ungraded.rows[0]["severity"] is None
    assert ungraded.rows[0]["percent_contained"] is None


def test_rows_are_sorted_by_fire_identifier_so_every_part_slices_one_global_order() -> None:
    population = fire_perimeter_population(_source(_perimeter("OR-C"), _perimeter("OR-A"), _perimeter("OR-B")))

    assert [row["unique_fire_identifier"] for row in population.rows] == ["OR-A", "OR-B", "OR-C"]


def test_a_repeated_fire_identifier_collapses_last_one_winning_and_is_counted() -> None:
    """geo.features holds one row per external_id, so a second record refreshes rather than adds."""
    population = fire_perimeter_population(
        _source(_perimeter("OR-A", percentContained=10.0), _perimeter("OR-A", percentContained=90.0))
    )

    assert len(population.rows) == 1
    assert population.collapsed == 1
    assert population.rows[0]["percent_contained"] == 90.0


def test_the_upstream_timestamps_are_cast_to_aware_utc_instants() -> None:
    population = fire_perimeter_population(_source(_perimeter("OR-A")))

    row = population.rows[0]

    assert row["polygon_at"] == datetime(2026, 8, 30, 18, 45, tzinfo=UTC)
    assert row["fire_discovery_at"] == datetime(2026, 7, 16, 1, 7, tzinfo=UTC)


def test_an_empty_input_still_yields_one_chunk_so_a_caller_always_makes_one_write_call() -> None:
    assert chunk_row_indices_by_geometry_bytes([], max_bytes=10) == [[]]


def test_chunks_split_on_geometry_bytes_and_never_split_a_single_oversized_row() -> None:
    chunks = chunk_row_indices_by_geometry_bytes([4, 4, 4, 100], max_bytes=10)

    assert chunks == [[0, 1], [2], [3]]


def test_chunk_indices_stay_contiguous_and_cover_every_row_exactly_once() -> None:
    """Parts are read back by parsed integer index, but the SLICES must still tile the population."""
    chunks = chunk_row_indices_by_geometry_bytes([3] * 11, max_bytes=8)

    flattened = [index for chunk in chunks for index in chunk]
    assert flattened == list(range(11))
    assert len(chunks) > 1


@pytest.mark.parametrize("bad_day", ["2026-13-01", "2026-04-31", "20260830"])
def test_a_day_prefix_that_is_not_a_real_iso_day_is_undated(bad_day: str) -> None:
    assert publisher_named_day({"polygonDateTime": f"{bad_day}T00:00:00.000Z"}) is None
