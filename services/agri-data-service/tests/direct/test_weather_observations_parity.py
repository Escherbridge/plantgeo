"""The D2 parity receipt: Postgres is the ground list, and an under-covered day is never silent."""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import agri_data_service.pipeline.direct.weather_observations.parity as parity_module
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.direct.weather_observations.parity import (
    build_parity_receipt,
    parquet_day_coverage,
    postgres_day_counts,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Sequence

DAY_ONE = date(2026, 9, 1)
DAY_TWO = date(2026, 9, 2)
LAYER_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "parity-test"
COMPLETED_AT = datetime(2026, 9, 2, tzinfo=UTC)


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class RecordingSession:
    """Captures the bound layer_id and answers with a fixed per-day row-count population."""

    def __init__(self, rows: Sequence[dict[str, object]] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rows = list(rows)

    async def execute(self, _statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append(params)
        return _Result(self._rows)


def _weather_row(day: date, index: int) -> dict[str, object]:
    return {
        "latitude": 46.0 + index,
        "longitude": -117.0,
        "observed_at": datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
        "observed_day": day,
        "external_id": f"row-{day.isoformat()}-{index}",
        "temperature_c": 20.0,
        "relative_humidity_pct": 40.0,
        "wind_speed_ms": 3.0,
        "wind_direction_deg": 180.0,
        "precipitation_mm": 0.0,
        "source": "Open-Meteo",
        "feature_id": f"direct:row-{day.isoformat()}-{index}",
        "ingested_at": datetime(day.year, day.month, day.day, 12, tzinfo=UTC),
    }


def _write_parquet_day(store: ObjectStore, *, day: date, row_count: int) -> None:
    """Write a full part-plus-marker completed z13 day, the only state `parity.py` counts as covering."""
    table = pa.Table.from_pylist(
        [_weather_row(day, index) for index in range(row_count)], schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema
    )
    store.write_partition(table, layer=WEATHER_OBSERVATIONS_STREAM, kind="observed", zoom=LANE_BASE_ZOOM_TIER, day=day)
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=row_count, completed_at=COMPLETED_AT, run_id=RUN_ID),
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )


@pytest.mark.asyncio
async def test_postgres_day_counts_binds_the_resolved_layer_id() -> None:
    session = RecordingSession([{"observed_day": DAY_ONE, "row_count": 5}])

    counts = await postgres_day_counts(session, layer_id=LAYER_ID)  # type: ignore[arg-type]

    assert counts == {DAY_ONE: 5}
    assert session.calls[0]["layer_id"] == LAYER_ID


def test_parquet_day_coverage_reports_missing_data_and_absent() -> None:
    store = ObjectStore(RecordingBackend())
    _write_parquet_day(store, day=DAY_ONE, row_count=3)
    store.write_absence(
        GovernedAbsence(
            reason="never polled",
            upstream_response="{}",
            recorded_at=datetime(2026, 9, 2, tzinfo=UTC),
            run_id="test",
        ),
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=DAY_TWO,
    )

    assert parquet_day_coverage(store, DAY_ONE) == ("data", 3)
    assert parquet_day_coverage(store, DAY_TWO) == ("absent", 0)
    assert parquet_day_coverage(store, date(2026, 9, 3)) == ("missing", 0)


@pytest.mark.asyncio
async def test_receipt_matches_when_parquet_covers_every_postgres_row(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve_layer_id(_session: object, _reference: str) -> str:
        return LAYER_ID

    monkeypatch.setattr(parity_module, "resolve_layer_id", fake_resolve_layer_id)
    session = RecordingSession([{"observed_day": DAY_ONE, "row_count": 2}, {"observed_day": DAY_TWO, "row_count": 1}])
    store = ObjectStore(RecordingBackend())
    _write_parquet_day(store, day=DAY_ONE, row_count=2)
    _write_parquet_day(store, day=DAY_TWO, row_count=4)  # Parquet may hold MORE, never fewer.

    receipt = await build_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.verdict == "parity_matched"
    assert receipt.postgres_days == 2
    assert receipt.postgres_rows == 3
    assert receipt.under_covered == ()


@pytest.mark.asyncio
async def test_receipt_flags_under_coverage_for_a_missing_and_a_thin_day(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve_layer_id(_session: object, _reference: str) -> str:
        return LAYER_ID

    monkeypatch.setattr(parity_module, "resolve_layer_id", fake_resolve_layer_id)
    session = RecordingSession([{"observed_day": DAY_ONE, "row_count": 5}, {"observed_day": DAY_TWO, "row_count": 2}])
    store = ObjectStore(RecordingBackend())
    _write_parquet_day(store, day=DAY_ONE, row_count=2)  # thin: Postgres holds 5

    receipt = await build_parity_receipt(session, store)  # type: ignore[arg-type]

    assert receipt.verdict == "under_coverage"
    under_covered_days = {coverage.day for coverage in receipt.under_covered}
    assert under_covered_days == {DAY_ONE, DAY_TWO}
    summary = receipt.to_summary()
    assert summary["under_covered_day_count"] == 2
