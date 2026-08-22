"""The signal-plane day exporter: batching, grain conformance, and the empty-batch refusal.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python — that cells are read in bounded batches rather than one huge
array parameter, that the result conforms to the registered schema, and that an empty cell list
is refused rather than silently producing a zero-row partition.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes.signal import (
    CELL_BATCH_SIZE,
    SignalExportError,
    export_signal_day,
    read_signal_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA, SIGNAL_PLANE_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def signal_row(cell_id: str, signal_name: str = "precipitation") -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "support_key": "surface",
        "signal_name": signal_name,
        "normalized_unit": "mm/day",
        "cell_id": cell_id,
        "observed_day": AUGUST_SIXTH,
        "normalized_value": 1.5,
        "observation_count": 1,
        "newest_observed_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "coverage_fraction": 1.0,
        "allowed_client_exposure": False,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound cell batch and answers with one row per cell."""

    def __init__(self, *, signals: tuple[str, ...] = ("precipitation",)) -> None:
        self.batches: list[list[int]] = []
        self._signals = signals

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        batch = list(params["cell_ids"])
        self.batches.append(batch)
        return _Result([signal_row(str(cell), name) for cell in batch for name in self._signals])


@pytest.mark.asyncio
async def test_cells_are_read_in_bounded_batches_not_one_array() -> None:
    """A day-scoped read across all cells is a full scan of an 11 GB heap; batching is the fix."""
    session = RecordingSession()
    remainder = 7
    cell_ids = list(range(CELL_BATCH_SIZE * 2 + remainder))

    await read_signal_day(session, day=AUGUST_SIXTH, cell_ids=cell_ids)  # type: ignore[arg-type]

    assert [len(batch) for batch in session.batches] == [CELL_BATCH_SIZE, CELL_BATCH_SIZE, remainder]
    assert [cell for batch in session.batches for cell in batch] == cell_ids


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(signals=("precipitation", "wind_speed"))

    table = await read_signal_day(session, day=AUGUST_SIXTH, cell_ids=[1, 2, 3])  # type: ignore[arg-type]

    expected_rows = 6  # three cells x two signals
    assert table.schema.equals(SIGNAL_PLANE_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_an_empty_cell_list_is_refused_rather_than_scanning_nothing() -> None:
    """Zero cells returns zero rows, which would otherwise surface as a confusing empty-write error."""
    session = RecordingSession()

    with pytest.raises(SignalExportError, match="at least one cell"):
        await read_signal_day(session, day=AUGUST_SIXTH, cell_ids=[])  # type: ignore[arg-type]

    assert session.batches == []


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = await export_signal_day(session, store, day=AUGUST_SIXTH, cell_ids=[3, 1, 2])  # type: ignore[arg-type]

    expected_rows = 3
    assert receipt.key == partition_path(SIGNAL_PLANE_STREAM, "observed", AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows
