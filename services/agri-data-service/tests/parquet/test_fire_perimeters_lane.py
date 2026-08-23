"""The fire-perimeters day exporter: WKB conformance, the empty-day absence path, and byte spillover.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that a day with zero incidents records a governed absence rather
than attempting a refused empty write, that geometry survives as binary WKB, and that a day whose
geometry bytes exceed the part budget spills into multiple grain-sorted, non-overlapping parts.
"""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.fire_perimeters import (
    FirePerimetersExportError,
    FirePerimetersExportOutcome,
    _chunk_row_indices_by_geometry_bytes,
    export_fire_perimeters_day,
    read_fire_perimeters_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_SCHEMA, FIRE_PERIMETERS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def pq_buffer(backend: RecordingBackend, key: str) -> io.BytesIO:
    """Wrap one recorded object's bytes in a seekable buffer `pyarrow.parquet.read_table` can read."""
    return io.BytesIO(backend.objects[key])


def fire_perimeter_row(
    *,
    feature_id: str = "11111111-1111-1111-1111-111111111111",
    unique_fire_identifier: str = "2026-CA-000123",
    observed_day: date = AUGUST_SIXTH,
    geometry_wkb: bytes = b"\x01\x02\x03",
) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "feature_id": feature_id,
        "unique_fire_identifier": unique_fire_identifier,
        "observed_day": observed_day,
        "incident_name": "Example Fire",
        "irwin_id": "irwin-1",
        "fire_discovery_at": datetime(2026, 8, 6, 10, tzinfo=UTC),
        "polygon_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "gis_acres": 1234.5,
        "fire_cause": "Lightning",
        "incident_type_category": "WF",
        "poo_state": "US-CA",
        "percent_contained": 45.0,
        "severity": "high",
        "status": "published",
        # 100% NULL in production today (conductor/RUNBOOK.md); never fabricated here either.
        "data_available_at": None,
        "updated_at": datetime(2026, 8, 6, 13, tzinfo=UTC),
        "geometry_wkb": geometry_wkb,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound `observed_day` and answers with the rows configured for it."""

    def __init__(self, *, rows_by_day: dict[date, list[dict[str, object]]] | None = None) -> None:
        self.observed_days: list[date] = []
        self._rows_by_day = rows_by_day or {}

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        day = params["observed_day"]
        self.observed_days.append(day)
        return _Result(self._rows_by_day.get(day, []))


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    rows = [fire_perimeter_row(unique_fire_identifier=f"2026-CA-{index:06d}") for index in range(3)]
    session = RecordingSession(rows_by_day={AUGUST_SIXTH: rows})

    table = await read_fire_perimeters_day(session, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert table.schema.equals(FIRE_PERIMETERS_SCHEMA.arrow_schema)
    assert table.num_rows == len(rows)
    assert table.column("geometry_wkb").type.equals(FIRE_PERIMETERS_SCHEMA.arrow_schema.field("geometry_wkb").type)


@pytest.mark.asyncio
async def test_a_day_with_no_incidents_reads_as_zero_rows_not_an_error() -> None:
    """Unlike the signal plane's cell-batch precondition, an empty result is a real answer here."""
    session = RecordingSession()

    table = await read_fire_perimeters_day(session, day=AUGUST_SIXTH)  # type: ignore[arg-type]

    assert table.num_rows == 0
    assert session.observed_days == [AUGUST_SIXTH]


@pytest.mark.asyncio
async def test_a_day_with_no_incidents_records_a_governed_absence_rather_than_an_empty_write() -> None:
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    outcome = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH, run_id="run-1")  # type: ignore[arg-type]

    assert outcome.parts == ()
    assert outcome.absence is not None
    assert store.absence_exists(FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert not store.partition_exists(FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    rows = [
        fire_perimeter_row(unique_fire_identifier="2026-CA-000003"),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000001"),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000002"),
    ]
    session = RecordingSession(rows_by_day={AUGUST_SIXTH: rows})
    backend = RecordingBackend()
    store = ObjectStore(backend)

    outcome = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH, run_id="run-1")  # type: ignore[arg-type]

    expected_rows = 3
    assert outcome.absence is None
    assert len(outcome.parts) == 1
    receipt = outcome.parts[0]
    assert receipt.key == partition_path(FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH, 0)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows

    written = pq.read_table(pq_buffer(backend, receipt.key))
    assert written.column("unique_fire_identifier").to_pylist() == [
        "2026-CA-000001",
        "2026-CA-000002",
        "2026-CA-000003",
    ]


@pytest.mark.asyncio
async def test_a_day_whose_geometry_bytes_exceed_the_part_budget_spills_into_multiple_parts() -> None:
    heavy_geometry = b"\x00" * (5 * 1024 * 1024)  # 5 MiB; two of these exceed the 8 MiB part budget.
    rows = [
        fire_perimeter_row(unique_fire_identifier="2026-CA-000002", geometry_wkb=heavy_geometry),
        fire_perimeter_row(unique_fire_identifier="2026-CA-000001", geometry_wkb=heavy_geometry),
    ]
    session = RecordingSession(rows_by_day={AUGUST_SIXTH: rows})
    backend = RecordingBackend()
    store = ObjectStore(backend)

    outcome = await export_fire_perimeters_day(session, store, day=AUGUST_SIXTH, run_id="run-1")  # type: ignore[arg-type]

    expected_part_count = 2
    assert outcome.absence is None
    assert len(outcome.parts) == expected_part_count
    for part_index, receipt in enumerate(outcome.parts):
        assert receipt.key == partition_path(
            FIRE_PERIMETERS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH, part_index
        )
        assert receipt.row_count == 1
    # Grain-sorted before chunking: part-0 carries the lexicographically smaller natural key.
    first_part = pq.read_table(pq_buffer(backend, outcome.parts[0].key))
    second_part = pq.read_table(pq_buffer(backend, outcome.parts[1].key))
    assert first_part.column("unique_fire_identifier").to_pylist() == ["2026-CA-000001"]
    assert second_part.column("unique_fire_identifier").to_pylist() == ["2026-CA-000002"]


def test_chunk_row_indices_keeps_contiguous_runs_under_the_byte_budget() -> None:
    assert _chunk_row_indices_by_geometry_bytes([3, 3, 3], max_bytes=5) == [[0], [1], [2]]
    assert _chunk_row_indices_by_geometry_bytes([2, 2, 2, 2], max_bytes=5) == [[0, 1], [2, 3]]


def test_chunk_row_indices_never_splits_a_single_oversized_row() -> None:
    """A perimeter whose own WKB already exceeds the budget still lands in one, unsplit chunk."""
    assert _chunk_row_indices_by_geometry_bytes([9], max_bytes=5) == [[0]]


def test_export_outcome_refuses_both_parts_and_an_absence() -> None:
    with pytest.raises(FirePerimetersExportError, match="never both or neither"):
        FirePerimetersExportOutcome(parts=("not-empty",), absence="not-none")  # type: ignore[arg-type]


def test_export_outcome_refuses_neither_parts_nor_an_absence() -> None:
    with pytest.raises(FirePerimetersExportError, match="never both or neither"):
        FirePerimetersExportOutcome(parts=(), absence=None)
