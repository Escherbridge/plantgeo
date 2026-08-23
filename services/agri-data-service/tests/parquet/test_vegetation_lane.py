"""The vegetation-plane day exporter: batching, grain conformance, and the empty-batch refusal.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that cells are read in bounded batches rather than one huge array
parameter, that the result conforms to the registered schema, and that an empty cell list is
refused rather than silently producing a zero-row partition. Mirrors
`tests/parquet/test_signal_lane_export.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.vegetation import (
    CELL_BATCH_SIZE,
    VegetationExportError,
    export_vegetation_day,
    read_vegetation_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def vegetation_row(cell_id: str) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "cell_id": cell_id,
        "grid_name": "sentinel2-ndvi-0p25deg",
        "metric_name": "ndvi",
        "metric_unit": "ndvi_index",
        "observed_day": AUGUST_SIXTH,
        "metric_value": 0.62,
        "observation_checksum": "a" * 64,
        "data_available_at": datetime(2026, 8, 6, 12, tzinfo=UTC),
        "release_count": 1,
        "allowed_client_exposure": True,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound cell batch and answers with one row per cell."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        batch = list(params["cell_ids"])
        self.batches.append(batch)
        return _Result([vegetation_row(cell_id) for cell_id in batch])


@pytest.mark.asyncio
async def test_cells_are_read_in_bounded_batches_not_one_array() -> None:
    """Reuses `execution/vegetation_ndvi_plane.py:57`'s established batch size for this lane."""
    session = RecordingSession()
    remainder = 7
    cell_ids = [uuid4() for _ in range(CELL_BATCH_SIZE * 2 + remainder)]

    await read_vegetation_day(session, day=AUGUST_SIXTH, cell_ids=cell_ids)  # type: ignore[arg-type]

    assert [len(batch) for batch in session.batches] == [CELL_BATCH_SIZE, CELL_BATCH_SIZE, remainder]
    assert [UUID(cell_id) for batch in session.batches for cell_id in batch] == cell_ids


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession()
    cell_ids = [uuid4(), uuid4(), uuid4()]

    table = await read_vegetation_day(session, day=AUGUST_SIXTH, cell_ids=cell_ids)  # type: ignore[arg-type]

    assert table.schema.equals(VEGETATION_PLANE_SCHEMA.arrow_schema)
    assert table.num_rows == len(cell_ids)


@pytest.mark.asyncio
async def test_an_empty_cell_list_is_refused_rather_than_scanning_nothing() -> None:
    """Zero cells returns zero rows, which would otherwise surface as a confusing empty-write error."""
    session = RecordingSession()

    with pytest.raises(VegetationExportError, match="at least one cell"):
        await read_vegetation_day(session, day=AUGUST_SIXTH, cell_ids=[])  # type: ignore[arg-type]

    assert session.batches == []


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    cell_ids = [uuid4(), uuid4(), uuid4()]

    receipt = await export_vegetation_day(session, store, day=AUGUST_SIXTH, cell_ids=cell_ids)  # type: ignore[arg-type]

    assert receipt.key == partition_path(VEGETATION_PLANE_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == len(cell_ids)
