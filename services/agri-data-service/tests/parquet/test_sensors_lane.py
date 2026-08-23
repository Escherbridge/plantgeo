"""The sensors-lane day exporter: batching, grain conformance, and the empty-batch refusal.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that stations are read in bounded batches rather than one huge
array parameter, that the result conforms to the registered schema (the sixteen captured
measurement fields, not just the four the current tile serves), and that an empty station list
is refused rather than silently producing a zero-row partition.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes.sensors import (
    STATION_BATCH_SIZE,
    SensorsExportError,
    export_sensors_day,
    read_sensors_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)


def sensor_row(station_id: str, measurement_name: str = "temperature") -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "sensor_id": station_id,
        "station_name": f"{station_id} STATION",
        "network": "ASOS",
        "observed_day": AUGUST_SIXTH,
        "observed_at": datetime(2026, 8, 6, 23, tzinfo=UTC),
        "measurement_name": measurement_name,
        "value": 21.5,
        "unit_code": "wmoUnit:degC",
        "quality_control": "V",
        "feature_id": f"feature-{station_id}",
        "data_available_at": None,
    }


class _Result:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> Sequence[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures each statement's bound station batch and answers with one row per station."""

    def __init__(self, *, measurements: tuple[str, ...] = ("temperature",)) -> None:
        self.batches: list[list[str]] = []
        self._measurements = measurements

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        batch = list(params["station_ids"])
        self.batches.append(batch)
        return _Result([sensor_row(station, name) for station in batch for name in self._measurements])


@pytest.mark.asyncio
async def test_stations_are_read_in_bounded_batches_not_one_array() -> None:
    """A day-scoped read across the whole layer walks every row the layer has ever held; batching bounds it."""
    session = RecordingSession()
    remainder = 7
    station_ids = [f"S{i}" for i in range(STATION_BATCH_SIZE * 2 + remainder)]

    await read_sensors_day(session, day=AUGUST_SIXTH, station_ids=station_ids)  # type: ignore[arg-type]

    assert [len(batch) for batch in session.batches] == [STATION_BATCH_SIZE, STATION_BATCH_SIZE, remainder]
    assert [station for batch in session.batches for station in batch] == station_ids


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(measurements=("temperature", "windSpeed"))

    table = await read_sensors_day(session, day=AUGUST_SIXTH, station_ids=["KMSO", "KDLN", "KGPI"])  # type: ignore[arg-type]

    expected_rows = 6  # three stations x two measurements
    assert table.schema.equals(SENSORS_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_an_empty_station_list_is_refused_rather_than_scanning_nothing() -> None:
    """Zero stations returns zero rows, which would otherwise surface as a confusing empty-write error."""
    session = RecordingSession()

    with pytest.raises(SensorsExportError, match="at least one station"):
        await read_sensors_day(session, day=AUGUST_SIXTH, station_ids=[])  # type: ignore[arg-type]

    assert session.batches == []


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession()
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = await export_sensors_day(session, store, day=AUGUST_SIXTH, station_ids=["KGPI", "KDLN", "KMSO"])  # type: ignore[arg-type]

    expected_rows = 3
    assert receipt.key == partition_path(SENSORS_STREAM, "observed", AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows
