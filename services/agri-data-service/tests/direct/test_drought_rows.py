"""Building the base-rung Arrow table for one fetched USDM release.

NEEDS DuckDB's `spatial` extension loadable in the test environment -- `drought_release_table` calls
through `support.drought_geometry_session` on every row it builds.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, datetime

from agri_data_service.ingest.usdm import DroughtArea, DroughtRelease
from agri_data_service.pipeline.direct.drought.rows import DIRECT_AREA_ID_PREFIX, direct_area_id, drought_release_table
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA

VALID_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[-120.0, 45.0], [-119.0, 45.0], [-119.0, 46.0], [-120.0, 46.0], [-120.0, 45.0]]],
}
FETCHED_AT = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)


def _release(valid_date: str = "2026-08-18") -> DroughtRelease:
    return DroughtRelease(
        valid_date=valid_date,
        source_url=f"https://droughtmonitor.unl.edu/data/json/usdm_{valid_date.replace('-', '')}.json",
        areas=(
            DroughtArea(drought_monitor_category=0, geometry=VALID_SQUARE),
            DroughtArea(drought_monitor_category=2, geometry=VALID_SQUARE),
        ),
    )


def test_the_table_conforms_to_the_registered_schema_and_carries_every_class() -> None:
    table = drought_release_table(_release(), ingested_at=FETCHED_AT)

    assert table.schema == DROUGHT_SCHEMA.arrow_schema
    assert table.num_rows == 2
    rows = {row["dm_category"]: row for row in table.to_pylist()}
    assert set(rows) == {0, 2}


def test_a_direct_row_carries_a_direct_namespaced_area_id_never_a_postgres_id() -> None:
    """There is no Postgres row behind a direct fetch; `area_id` must say so rather than imitate one."""
    table = drought_release_table(_release("2026-08-18"), ingested_at=FETCHED_AT)

    for row in table.to_pylist():
        assert row["area_id"] == direct_area_id("2026-08-18", row["dm_category"])
        assert row["area_id"].startswith(f"{DIRECT_AREA_ID_PREFIX}:")


def test_ingested_at_is_the_callers_fetch_instant_not_a_postgres_write_time() -> None:
    table = drought_release_table(_release(), ingested_at=FETCHED_AT)

    assert all(row["ingested_at"] == FETCHED_AT for row in table.to_pylist())


def test_source_url_and_valid_date_are_carried_from_the_release() -> None:
    built = _release("2026-08-18")

    table = drought_release_table(built, ingested_at=FETCHED_AT)

    for row in table.to_pylist():
        assert row["source_url"] == built.source_url
        assert row["valid_date"].isoformat() == "2026-08-18"
