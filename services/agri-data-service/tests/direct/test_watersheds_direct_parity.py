"""The Postgres-vs-Parquet parity receipt: a single counted comparison, never a write.

Every "Postgres" side here is a fake in-memory session -- this test never opens a real database
connection, matching the module under test itself, which only ever READS Postgres. See
`parity.py`'s module docstring for why this receipt is deliberately thinner than drought's or
vegetation's day-walking comparison.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.watersheds.adapter import WATERSHEDS_DIRECT_KIND
from agri_data_service.pipeline.direct.watersheds.parity import (
    WatershedsParityError,
    build_watersheds_parity_receipt,
    postgres_watersheds_row_count,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "parity-test"
COMPLETED_AT = datetime(2026, 8, 8, tzinfo=UTC)
VERSION_DAY = date(2026, 8, 7)


class FakeScalarResult:
    """Answers `.scalar_one()` exactly as a real SQLAlchemy `Result` does for a `count(*)` query."""

    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakePostgresSession:
    """Never opens a socket; answers whatever row count the test staged."""

    def __init__(self, row_count: int) -> None:
        self._row_count = row_count

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakeScalarResult:  # noqa: ARG002
        return FakeScalarResult(self._row_count)


def _table(huc12_count: int) -> pa.Table:
    rows = [
        {
            "huc12": f"{index:012d}",
            "name": None,
            "areasqkm": None,
            "tohuc": None,
            "states": None,
            "hutype": None,
            "source": "USGS NHDPlus HR WBDHU12",
            "observed_at": None,
            "data_available_at": None,
            "release_day": VERSION_DAY,
            "feature_id": f"direct:{index:012d}",
            "geom": b"\x00\x01",
        }
        for index in range(huc12_count)
    ]
    return pa.Table.from_pylist(rows, schema=WATERSHEDS_SCHEMA.arrow_schema)


def write_completed_version(store: ObjectStore, *, huc12_count: int) -> None:
    """Write a full part-plus-marker completed base-rung version, the only state `parity.py` counts."""
    store.write_partition(
        _table(huc12_count),
        layer=WATERSHEDS_STREAM,
        kind=WATERSHEDS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=huc12_count, completed_at=COMPLETED_AT, run_id=RUN_ID),
        layer=WATERSHEDS_STREAM,
        kind=WATERSHEDS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )


@pytest.mark.asyncio
async def test_parity_achieved_when_postgres_and_parquet_row_counts_match() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_version(store, huc12_count=3)
    session = FakePostgresSession(3)

    receipt = await build_watersheds_parity_receipt(session, store)

    assert receipt.parity_achieved is True
    assert receipt.postgres_rows == receipt.parquet_rows == 3  # noqa: PLR2004 - the fixture's own basin count
    assert receipt.parquet_version_day == VERSION_DAY.isoformat()
    assert receipt.parquet_complete is True


@pytest.mark.asyncio
async def test_a_row_count_mismatch_is_reported_not_averaged_away() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_version(store, huc12_count=1)
    session = FakePostgresSession(3)

    receipt = await build_watersheds_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.row_count_match is False
    assert receipt.postgres_rows == 3  # noqa: PLR2004 - three held against one published is the mismatch
    assert receipt.parquet_rows == 1


@pytest.mark.asyncio
async def test_no_parquet_version_at_all_is_reported_not_raised() -> None:
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession(9396)

    receipt = await build_watersheds_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.parquet_version_day is None
    assert receipt.parquet_rows == 0


@pytest.mark.asyncio
async def test_a_written_but_uncompleted_version_is_reported_incomplete_and_never_counted_as_parity() -> None:
    """Parts without a completion marker are a half-finished export, not evidence of anything."""
    store = ObjectStore(RecordingBackend())
    store.write_partition(
        _table(1),
        layer=WATERSHEDS_STREAM,
        kind=WATERSHEDS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=VERSION_DAY,
    )
    session = FakePostgresSession(1)

    receipt = await build_watersheds_parity_receipt(session, store)

    assert receipt.parquet_complete is False
    assert receipt.parity_achieved is False


@pytest.mark.asyncio
async def test_an_empty_postgres_relation_is_a_reportable_finding_not_a_refusal() -> None:
    """Unlike `drought/parity.py`, zero Postgres rows is an EXPECTED end state once `postgres-watersheds`
    retires -- see `parity.py`'s module docstring -- so this must report, never raise."""
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession(0)

    row_count = await postgres_watersheds_row_count(session)
    receipt = await build_watersheds_parity_receipt(session, store)

    assert row_count == 0
    assert receipt.postgres_rows == 0


@pytest.mark.asyncio
async def test_a_postgres_read_failure_is_reraised_as_the_typed_parity_error() -> None:
    class BrokenSession:
        async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:  # noqa: ARG002
            raise RuntimeError("connection refused")

    with pytest.raises(WatershedsParityError, match="could not read"):
        await postgres_watersheds_row_count(BrokenSession())
