"""The weather-observations day exporter: schema conformance, the layer_id guard, and one execution.

The governed SQL itself is exercised against a real database elsewhere; these tests pin the
behaviour that is pure Python -- that a blank `layer_id` is refused before any query runs, that the
result conforms to the registered schema, that exactly one statement is executed per day (no
cell-batching loop, unlike the signal plane -- `ix_features_layer_observation_day` makes one bounded
read sufficient), and that the export lands at the `kind=observed` partition.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.weather_observations import (
    WeatherObservationsExportError,
    export_weather_observations_day,
    read_weather_observations_day,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

AUGUST_SIXTH = date(2026, 8, 6)
LAYER_ID = "3c9a4f2e-6d1b-4a7f-9c3e-1a2b3c4d5e6f"


def weather_observation_row(latitude: float, longitude: float, *, external_id: str) -> dict[str, object]:
    """One exported grain row, shaped exactly as the SQL's column list returns it."""
    return {
        "latitude": latitude,
        "longitude": longitude,
        "observed_at": datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC),
        "observed_day": AUGUST_SIXTH,
        "external_id": external_id,
        "temperature_c": 21.5,
        "relative_humidity_pct": 44.0,
        "wind_speed_ms": 3.2,
        "wind_direction_deg": 187.0,
        "precipitation_mm": 0.0,
        "source": "Open-Meteo",
        "feature_id": "9f1c2d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f",
        "ingested_at": datetime(2026, 8, 6, 12, 5, 0, tzinfo=UTC),
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
    session = RecordingSession(
        rows=[
            weather_observation_row(43.6150, -116.2023, external_id="43.6150:-116.2023:a"),
            weather_observation_row(43.7, -116.1, external_id="43.7000:-116.1000:b"),
        ]
    )

    await read_weather_observations_day(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    assert session.calls == [{"layer_id": LAYER_ID, "observed_day": AUGUST_SIXTH}]


@pytest.mark.asyncio
async def test_the_read_conforms_to_the_registered_schema() -> None:
    session = RecordingSession(
        rows=[
            weather_observation_row(43.6150, -116.2023, external_id="a"),
            weather_observation_row(43.7, -116.1, external_id="b"),
            weather_observation_row(43.7, -116.1, external_id="c"),
        ]
    )

    table = await read_weather_observations_day(session, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    expected_rows = 3
    assert table.schema.equals(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)
    assert table.num_rows == expected_rows


@pytest.mark.asyncio
async def test_a_blank_layer_id_is_refused_rather_than_querying_with_no_scope() -> None:
    """An unresolved layer_id would otherwise reach the database as an empty/whitespace bind."""
    session = RecordingSession()

    with pytest.raises(WeatherObservationsExportError, match="resolved layer_id"):
        await read_weather_observations_day(session, day=AUGUST_SIXTH, layer_id="  ")  # type: ignore[arg-type]

    assert session.calls == []


@pytest.mark.asyncio
async def test_the_export_lands_at_the_observed_partition_sorted_to_the_grain() -> None:
    session = RecordingSession(
        rows=[
            weather_observation_row(43.7, -116.1, external_id="b"),
            weather_observation_row(43.6150, -116.2023, external_id="a"),
        ]
    )
    backend = RecordingBackend()
    store = ObjectStore(backend)

    receipt = await export_weather_observations_day(session, store, day=AUGUST_SIXTH, layer_id=LAYER_ID)  # type: ignore[arg-type]

    expected_rows = 2
    assert receipt.key == partition_path(WEATHER_OBSERVATIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, AUGUST_SIXTH)
    assert receipt.kind == "observed"
    assert receipt.row_count == expected_rows
