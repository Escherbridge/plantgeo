"""The Postgres-vs-Parquet parity receipt: a counted key comparison, never a write, never waved through.

Every "Postgres" side here is a fake in-memory session -- this test never opens a real database
connection, matching the module under test itself, which only ever READS Postgres.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.evacuation_zones.parity import (
    EvacuationZonesParityError,
    build_evacuation_zones_parity_receipt,
    parquet_published_version,
)
from agri_data_service.pipeline.direct.evacuation_zones.products import (
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
)
from agri_data_service.pipeline.direct.evacuation_zones.rows import evacuation_zones_table, natural_key_for
from agri_data_service.pipeline.direct.evacuation_zones.source import EvacuationZonesSource
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "evacuation-zones-parity-test"
FETCHED_AT = datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
DAY = date(2026, 9, 3)

SQUARE: dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-123.0, 44.0], [-122.9, 44.0], [-122.9, 44.1], [-123.0, 44.1], [-123.0, 44.0]]],
}


class FakePostgresResult:
    """Answers `for row in result` exactly as a real SQLAlchemy `Result` does for this query's shape."""

    def __init__(self, global_ids: tuple[str | None, ...]) -> None:
        self._rows = [SimpleNamespace(global_id=global_id) for global_id in global_ids]

    def __iter__(self):  # noqa: ANN204 - mirrors `sqlalchemy.engine.Result.__iter__`
        return iter(self._rows)


class FakePostgresSession:
    """Never opens a socket; answers whatever published `geo.features` rows the test staged."""

    def __init__(self, global_ids: tuple[str | None, ...] = ()) -> None:
        self._global_ids = global_ids

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakePostgresResult:  # noqa: ARG002
        return FakePostgresResult(self._global_ids)


def zone(global_id: str) -> dict[str, Any]:
    return {
        "globalId": global_id,
        "evacuationAreaName": "Milepost 97",
        "fireName": "Milepost 97 Fire",
        "county": "Douglas",
        "hazardType": "Wildfire",
        "editorName": "OEM Sync",
        "evacuationLevel": 3,
        "evacuationLevelLabel": "Go Now",
        "severity": "critical",
        "structuresWithin": 12.0,
        "addressesWithin": 30.0,
        "populationWithin": 74.0,
        "createdAt": datetime(2025, 4, 14, tzinfo=UTC),
        "createdDate": "2025-04-14T00:00:00Z",
        "lastEditedDate": "2026-09-03T17:29:00Z",
        "geometry": SQUARE,
    }


def publish_version(store: ObjectStore, *, day: date, global_ids: tuple[str, ...], complete: bool = True) -> None:
    captured = EvacuationZonesSource(
        bbox="-125,42,-111,49", fetched_at=FETCHED_AT, zones=tuple(zone(global_id) for global_id in global_ids)
    )
    table = evacuation_zones_table(captured, snapshot_day=day)
    store.write_partition(
        table,
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )
    if complete:
        store.write_completion_marker(
            PartitionCompletion(part_count=1, row_count=table.num_rows, completed_at=FETCHED_AT, run_id=RUN_ID),
            layer=EVACUATION_ZONES_STREAM,
            kind=EVACUATION_ZONES_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )


@pytest.mark.asyncio
async def test_parity_is_achieved_when_the_newest_version_holds_every_zone_postgres_publishes() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}", "{B}"))
    session = FakePostgresSession(("{A}", "{B}"))

    receipt = await build_evacuation_zones_parity_receipt(session, store)

    assert receipt.parity_achieved is True
    assert receipt.postgres_zones == receipt.parquet_zones == 2
    assert receipt.parquet_version_day == "2026-09-03"
    assert receipt.missing_from_parquet == 0


@pytest.mark.asyncio
async def test_a_zone_postgres_holds_and_parquet_does_not_fails_the_receipt_and_is_named() -> None:
    """DO NOT DELETE. Under-coverage is the exact thing D1's parity receipt exists to catch.

    It may well be innocent -- Oregon retires areas that `geo.features` never unpublishes, and the
    Postgres side is frozen -- but the receipt reports it and exits non-zero so a human decides,
    rather than a rule inside this module deciding on their behalf for a life-safety layer.
    """
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}",))
    session = FakePostgresSession(("{A}", "{RETIRED}"))

    receipt = await build_evacuation_zones_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.missing_from_parquet == 1
    assert receipt.missing_from_parquet_sample == (natural_key_for("{RETIRED}"),)


@pytest.mark.asyncio
async def test_a_zone_only_parquet_holds_is_over_coverage_and_never_fails_the_receipt() -> None:
    """Oregon published it after Postgres ingestion stopped; D1's bar is "covers AT LEAST"."""
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}", "{NEW}"))
    session = FakePostgresSession(("{A}",))

    receipt = await build_evacuation_zones_parity_receipt(session, store)

    assert receipt.parity_achieved is True
    assert receipt.only_in_parquet == 1


@pytest.mark.asyncio
async def test_a_half_written_version_is_reported_incomplete_and_never_counted_as_covering() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}",), complete=False)
    session = FakePostgresSession(("{A}",))

    receipt = await build_evacuation_zones_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.parquet_incomplete_versions == ("2026-09-03",)
    assert receipt.parquet_version_day is None


@pytest.mark.asyncio
async def test_a_published_absence_covers_nothing_and_says_so() -> None:
    store = ObjectStore(RecordingBackend())
    store.write_absence(
        GovernedAbsence(reason="quiet", upstream_response="{}", recorded_at=FETCHED_AT, run_id=RUN_ID),
        layer=EVACUATION_ZONES_STREAM,
        kind=EVACUATION_ZONES_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY,
    )
    session = FakePostgresSession(("{A}",))

    receipt = await build_evacuation_zones_parity_receipt(session, store)

    assert receipt.parquet_version_is_absence is True
    assert receipt.parity_achieved is False
    assert receipt.missing_from_parquet == 1


@pytest.mark.asyncio
async def test_zero_postgres_zones_is_refused_rather_than_reported_as_trivially_green() -> None:
    """651 published rows were censused; an empty read is a mistargeted database, not a finding."""
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}",))

    with pytest.raises(EvacuationZonesParityError, match="zero published evacuation zones"):
        await build_evacuation_zones_parity_receipt(FakePostgresSession(()), store)


@pytest.mark.asyncio
async def test_a_null_global_id_is_skipped_rather_than_mapped_onto_some_other_key() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=DAY, global_ids=("{A}",))

    receipt = await build_evacuation_zones_parity_receipt(FakePostgresSession(("{A}", None)), store)

    assert receipt.postgres_zones == 1
    assert receipt.parity_achieved is True


def test_the_newest_version_is_the_one_compared_even_when_older_ones_remain() -> None:
    store = ObjectStore(RecordingBackend())
    publish_version(store, day=date(2026, 9, 1), global_ids=("{OLD}",))
    publish_version(store, day=date(2026, 9, 3), global_ids=("{NEW}",))

    version_day, keys, is_absence, incomplete = parquet_published_version(store)

    assert version_day == "2026-09-03"
    assert keys == {natural_key_for("{NEW}")}
    assert is_absence is False
    assert incomplete == ()
