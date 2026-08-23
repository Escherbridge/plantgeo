"""The fire-detections day exporter: schema conformance, the layer_id guard, and the absence branch.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that a blank `layer_id` is refused before any query runs, that the
result conforms to the registered cell-day schema (including a nullable `frp_sum` staying NULL
rather than being coerced to zero), that exactly one statement is executed per day (no
cell-batching loop, unlike the signal plane -- `ix_features_layer_observation_day` makes one bounded
read sufficient), that the export lands at the `kind=observed` partition sorted to the grain, and
that a day with zero rows is recorded as a governed absence rather than raising or silently
succeeding -- the exact failure mode `docs/lanes/fire-detections.md` section 5.1 warns this lane's
own ingest history already produced once.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.fire_detections import (
    FireDetectionsExportError,
    export_fire_detections_day,
    read_fire_detections_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)
LAYER_ID = "7d6c5b4a-3f2e-4a1b-9c8d-0e1f2a3b4c5d"


def fire_detection_row(  # noqa: PLR0913 - one keyword per exported grain/measure column
    cell_longitude: float,
    cell_latitude: float,
    *,
    detection_count: int = 1,
    frp_sum: float | None = 4.27,
    frp_observation_count: int = 1,
    high_confidence_detection_count: int = 0,
) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "cell_longitude": cell_longitude,
        "cell_latitude": cell_latitude,
        "observed_day": AUGUST_SIXTH,
        "detection_count": detection_count,
        "frp_sum": frp_sum,
        "frp_observation_count": frp_observation_count,
        "high_confidence_detection_count": high_confidence_detection_count,
        "newest_observed_at": datetime(2026, 8, 6, 13, 42, tzinfo=UTC),
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures every statement execution and answers with a fixed row set."""

    def __init__(self, rows: Sequence[dict[str, object]] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = rows

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_one_execution_reads_the_whole_day_no_batching_loop() -> None:
    """Unlike the signal plane, `ix_features_layer_observation_day` needs no cell-batching loop."""
    session = RecordingSession(rows=[fire_detection_row(-116.200, 43.615), fire_detection_row(-116.195, 43.615)])

    await read_fire_detections_day(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    assert session.calls == [{"layer_id": LAYER_ID, "observed_day": AUGUST_SIXTH}]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(
        rows=[
            fire_detection_row(-116.200, 43.615, detection_count=3, frp_observation_count=2),
            fire_detection_row(-116.195, 43.615),
        ]
    )

    table = await read_fire_detections_day(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    expected_rows = 2
    assert table.schema.equals(FIRE_DETECTIONS_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_a_null_frp_sum_stays_null_rather_than_becoming_a_fabricated_zero() -> None:
    """A cell-day where no detection published FRP is a real absence of that measurement, not 0 MW."""
    session = RecordingSession(rows=[fire_detection_row(-116.200, 43.615, frp_sum=None, frp_observation_count=0)])

    table = await read_fire_detections_day(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    assert table.column("frp_sum").to_pylist() == [None]
    assert table.column("frp_observation_count").to_pylist() == [0]


@pytest.mark.asyncio
async def test_a_blank_layer_id_is_refused_rather_than_querying_with_no_scope() -> None:
    """An unresolved layer_id would otherwise reach the database as an empty/whitespace bind."""
    session = RecordingSession()

    with pytest.raises(FireDetectionsExportError, match="resolved layer_id"):
        await read_fire_detections_day(session, day=AUGUST_SIXTH, layer_id="  ")  # type: ignore[arg-type]

    assert session.calls == []


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession(rows=[fire_detection_row(-116.195, 43.615), fire_detection_row(-116.200, 43.615)])
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = await export_fire_detections_day(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_SIXTH,
        layer_id=LAYER_ID,
        run_id="run-0001",
    )

    expected_rows = 2
    assert isinstance(receipt, ParquetWriteReceipt)
    assert receipt.key == partition_path(FIRE_DETECTIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows


@pytest.mark.asyncio
async def test_a_zero_row_day_is_recorded_as_a_governed_absence_not_a_silent_zero() -> None:
    """The exact failure mode section 5.1 of the lane contract warns this lane's ingest history hid.

    `write_partition` refuses a zero-row table by construction; the exporter must not let that
    surface as an uncaught error where a governed absence, with evidence, is what the contract asks
    for instead.
    """
    session = RecordingSession(rows=[])
    backend = RecordingBackend()
    store = ObjectStore(backend)
    recorded_at = datetime(2026, 8, 7, 6, 0, tzinfo=UTC)

    receipt = await export_fire_detections_day(
        session,  # type: ignore[arg-type]
        store,
        day=AUGUST_SIXTH,
        layer_id=LAYER_ID,
        run_id="run-0002",
        now=recorded_at,
    )

    expected_key = absence_marker_path(FIRE_DETECTIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert receipt.key == expected_key
    assert list(backend.objects) == [expected_key]
    absence = GovernedAbsence.from_json_bytes(backend.objects[expected_key])
    assert absence.run_id == "run-0002"
    assert absence.recorded_at == recorded_at
    assert LAYER_ID in absence.upstream_response
    assert AUGUST_SIXTH.isoformat() in absence.upstream_response
