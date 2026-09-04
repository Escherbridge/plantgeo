"""The Postgres-vs-Parquet parity receipt: a counted comparison, never a write, never averaged away.

Every "Postgres" side here is a fake in-memory session -- this test never opens a real database
connection, matching the module under test itself, which only ever READS Postgres.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.drought.parity import (
    DROUGHT_PARITY_KIND,
    DroughtParityError,
    build_drought_parity_receipt,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "parity-test"
COMPLETED_AT = datetime(2026, 8, 20, tzinfo=UTC)


class FakePostgresResult:
    """Answers `for row in result` exactly as a real SQLAlchemy `Result` does for this query's shape."""

    def __init__(self, day_counts: tuple[tuple[str, int], ...]) -> None:
        self._rows = [
            SimpleNamespace(valid_date=valid_date, row_count=row_count) for valid_date, row_count in day_counts
        ]

    def __iter__(self):  # noqa: ANN204 - mirrors `sqlalchemy.engine.Result.__iter__`
        return iter(self._rows)


class FakePostgresSession:
    """Never opens a socket; answers whatever `geo.drought_areas` day-count rows the test staged."""

    def __init__(self, day_counts: tuple[tuple[str, int], ...] = ()) -> None:
        self._day_counts = day_counts

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakePostgresResult:  # noqa: ARG002
        return FakePostgresResult(self._day_counts)


def _drought_table(day: date, *, dm_categories: tuple[int, ...]) -> pa.Table:
    rows = [
        {
            "area_id": f"test:{day.isoformat()}:{category}",
            "valid_date": day,
            "dm_category": category,
            "source_url": "https://droughtmonitor.unl.edu/data/json/usdm_test.json",
            "ingested_at": COMPLETED_AT,
            "geom": b"\x01\x02",
        }
        for category in dm_categories
    ]
    return pa.Table.from_pylist(rows, schema=DROUGHT_SCHEMA.arrow_schema)


def write_completed_release(store: ObjectStore, *, day: date, dm_categories: tuple[int, ...]) -> None:
    """Write a full part-plus-marker completed z13 day, the only state `parity.py` counts as covering."""
    store.write_partition(
        _drought_table(day, dm_categories=dm_categories),
        layer=DROUGHT_STREAM,
        kind=DROUGHT_PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=len(dm_categories), completed_at=COMPLETED_AT, run_id=RUN_ID),
        layer=DROUGHT_STREAM,
        kind=DROUGHT_PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


@pytest.mark.asyncio
async def test_parity_achieved_when_every_postgres_day_matches_parquet_exactly() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_release(store, day=date(2026, 8, 18), dm_categories=(0, 1, 2))
    session = FakePostgresSession((("2026-08-18", 3),))

    receipt = await build_drought_parity_receipt(session, store)

    assert receipt.parity_achieved is True
    assert receipt.postgres_days == receipt.parquet_days == 1
    assert receipt.postgres_rows == receipt.parquet_rows == 3
    assert receipt.missing_from_parquet == ()
    assert receipt.row_count_mismatches == ()


@pytest.mark.asyncio
async def test_a_postgres_day_parquet_never_wrote_is_reported_missing_not_silently_dropped() -> None:
    """DO NOT DELETE. Under-coverage is the exact thing D1's parity receipt exists to catch."""
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession((("2026-08-18", 3),))

    receipt = await build_drought_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.missing_from_parquet == ("2026-08-18",)


@pytest.mark.asyncio
async def test_a_row_count_mismatch_is_reported_not_averaged_away() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_release(store, day=date(2026, 8, 18), dm_categories=(0,))
    session = FakePostgresSession((("2026-08-18", 3),))

    receipt = await build_drought_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.row_count_mismatches == ({"valid_date": "2026-08-18", "postgres_rows": 3, "parquet_rows": 1},)


@pytest.mark.asyncio
async def test_a_written_but_uncompleted_day_is_reported_incomplete_and_never_counted_as_covering() -> None:
    """Parts without a completion marker are a half-finished export, not evidence of anything."""
    store = ObjectStore(RecordingBackend())
    store.write_partition(
        _drought_table(date(2026, 8, 18), dm_categories=(0,)),
        layer=DROUGHT_STREAM,
        kind=DROUGHT_PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=date(2026, 8, 18),
    )
    session = FakePostgresSession((("2026-08-18", 1),))

    receipt = await build_drought_parity_receipt(session, store)

    assert receipt.parquet_incomplete_days == ("2026-08-18",)
    assert receipt.missing_from_parquet == ("2026-08-18",)
    assert receipt.parity_achieved is False


@pytest.mark.asyncio
async def test_an_empty_postgres_relation_refuses_rather_than_reports_trivial_parity() -> None:
    """DO NOT DELETE. `geo.drought_areas` holds 209 measured releases since 2022-08-09 -- zero
    Postgres days is far more likely a mistargeted `LOCAL_SOURCE_LOADER_DATABASE_URL` than a
    genuinely empty relation, and reporting `parity_achieved=True` for it would be a GREEN receipt
    for a table this run never actually read.
    """
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession(())

    with pytest.raises(DroughtParityError, match="zero days"):
        await build_drought_parity_receipt(session, store)
