"""The Postgres-vs-Parquet parity receipt: a counted comparison, never a write, never averaged away.

Every "Postgres" side here is a fake in-memory session -- this test never opens a real database
connection, matching the module under test itself, which only ever READS Postgres. Tables are built
by hand against `BURN_SEVERITY_SCHEMA` rather than through `rows.py`, so this file has no DuckDB
dependency at all -- unlike `test_burn_severity_direct_rows.py`/`_support.py`/`_adapter.py`.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.burn_severity.parity import (
    BurnSeverityParityError,
    build_burn_severity_parity_receipt,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA, BURN_SEVERITY_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

RUN_ID = "parity-test"
COMPLETED_AT = datetime(2020, 11, 26, tzinfo=UTC)
PARITY_KIND = "observed"


class FakePostgresResult:
    """Answers `for row in result` exactly as a real SQLAlchemy `Result` does for this query's shape."""

    def __init__(self, day_counts: tuple[tuple[str, int], ...]) -> None:
        self._rows = [
            SimpleNamespace(release_day=release_day, row_count=row_count) for release_day, row_count in day_counts
        ]

    def __iter__(self):  # noqa: ANN204 - mirrors `sqlalchemy.engine.Result.__iter__`
        return iter(self._rows)


class FakePostgresSession:
    """Never opens a socket; answers whatever `geo.features` burn-severity day-count rows the test staged."""

    def __init__(self, day_counts: tuple[tuple[str, int], ...] = ()) -> None:
        self._day_counts = day_counts

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> FakePostgresResult:  # noqa: ARG002
        return FakePostgresResult(self._day_counts)


def _burn_severity_table(day: date, *, fire_ids: tuple[str, ...]) -> pa.Table:
    rows = [
        {
            "feature_id": f"direct:{fire_id}",
            "fire_id": fire_id,
            "natural_key": f"mtbs:{fire_id}",
            "release_identifier": f"mtbs-2018-release-{day.isoformat()}",
            "mapping_revision": f"mtbs-2018-release-{day.isoformat()}|{fire_id}",
            "fire_year": 2018,
            "ignition_date": date(2018, 8, 1),
            "observed_day": day,
            "data_available_at": datetime(day.year, day.month, day.day, tzinfo=UTC),
            "fire_name": "Test Fire",
            "fire_type": "Wildfire",
            "assessment_type": "Initial",
            "acres": 100.0,
            "severity_class": None,
            "dnbr_offset": None,
            "dnbr_standard_deviation": None,
            "nodata_threshold": None,
            "greenness_threshold": None,
            "low_threshold": None,
            "moderate_threshold": None,
            "high_threshold": None,
            "allowed_client_exposure": False,
            "geom": b"\x01\x02",
        }
        for fire_id in fire_ids
    ]
    return pa.Table.from_pylist(rows, schema=BURN_SEVERITY_SCHEMA.arrow_schema)


def write_completed_release(store: ObjectStore, *, day: date, fire_ids: tuple[str, ...]) -> None:
    """Write a full part-plus-marker completed z13 day, the only state `parity.py` counts as covering."""
    store.write_partition(
        _burn_severity_table(day, fire_ids=fire_ids),
        layer=BURN_SEVERITY_STREAM,
        kind=PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=len(fire_ids), completed_at=COMPLETED_AT, run_id=RUN_ID),
        layer=BURN_SEVERITY_STREAM,
        kind=PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


@pytest.mark.asyncio
async def test_parity_achieved_when_every_postgres_day_matches_parquet_exactly() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_release(store, day=date(2020, 11, 24), fire_ids=("FIRE_A", "FIRE_B", "FIRE_C"))
    session = FakePostgresSession((("2020-11-24", 3),))

    receipt = await build_burn_severity_parity_receipt(session, store)

    assert receipt.parity_achieved is True
    assert receipt.postgres_days == receipt.parquet_days == 1
    assert receipt.postgres_rows == receipt.parquet_rows == 3
    assert receipt.missing_from_parquet == ()
    assert receipt.row_count_mismatches == ()


@pytest.mark.asyncio
async def test_a_postgres_day_parquet_never_wrote_is_reported_missing_not_silently_dropped() -> None:
    """DO NOT DELETE. Under-coverage is the exact thing this receipt exists to catch."""
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession((("2020-11-24", 3),))

    receipt = await build_burn_severity_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.missing_from_parquet == ("2020-11-24",)


@pytest.mark.asyncio
async def test_a_row_count_mismatch_is_reported_not_averaged_away() -> None:
    store = ObjectStore(RecordingBackend())
    write_completed_release(store, day=date(2020, 11, 24), fire_ids=("FIRE_A",))
    session = FakePostgresSession((("2020-11-24", 3),))

    receipt = await build_burn_severity_parity_receipt(session, store)

    assert receipt.parity_achieved is False
    assert receipt.row_count_mismatches == ({"release_day": "2020-11-24", "postgres_rows": 3, "parquet_rows": 1},)


@pytest.mark.asyncio
async def test_a_written_but_uncompleted_day_is_reported_incomplete_and_never_counted_as_covering() -> None:
    """Parts without a completion marker are a half-finished export, not evidence of anything."""
    store = ObjectStore(RecordingBackend())
    store.write_partition(
        _burn_severity_table(date(2020, 11, 24), fire_ids=("FIRE_A",)),
        layer=BURN_SEVERITY_STREAM,
        kind=PARITY_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        day=date(2020, 11, 24),
    )
    session = FakePostgresSession((("2020-11-24", 1),))

    receipt = await build_burn_severity_parity_receipt(session, store)

    assert receipt.parquet_incomplete_days == ("2020-11-24",)
    assert receipt.missing_from_parquet == ("2020-11-24",)
    assert receipt.parity_achieved is False


@pytest.mark.asyncio
async def test_an_empty_postgres_relation_refuses_rather_than_reports_trivial_parity() -> None:
    """DO NOT DELETE. `docs/lanes/burn-severity.md` section 5 measures 541 published rows across
    five release days -- zero Postgres days is far more likely a mistargeted
    `LOCAL_SOURCE_LOADER_DATABASE_URL` than a genuinely empty relation, and reporting
    `parity_achieved=True` for it would be a GREEN receipt for a table this run never actually read.
    """
    store = ObjectStore(RecordingBackend())
    session = FakePostgresSession(())

    with pytest.raises(BurnSeverityParityError, match="zero"):
        await build_burn_severity_parity_receipt(session, store)
