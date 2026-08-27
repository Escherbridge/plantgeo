"""Exact current-weather audit keeps the Historical Forecast prefix outside governance."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.weather_observations_exact import audit_exact_weather_observations
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 8, 1)
PREFIX_DAY = date(2022, 9, 6)
NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
BASE_ZOOM = ZOOM_TIERS[-1]


class _Session:
    async def rollback(self) -> None:
        return None


def _table(day: date = DAY) -> pa.Table:
    observed_at = datetime(day.year, day.month, day.day, 12, tzinfo=UTC)
    return pa.Table.from_pylist(
        [
            {
                "latitude": 44.0,
                "longitude": -116.0,
                "observed_at": observed_at,
                "observed_day": day,
                "external_id": f"44.0000:-116.0000:{observed_at.isoformat()}",
                "temperature_c": 21.5,
                "relative_humidity_pct": 35.0,
                "wind_speed_ms": 3.2,
                "wind_direction_deg": 270.0,
                "precipitation_mm": 0.0,
                "source": "Open-Meteo",
                "feature_id": f"feature-{day.isoformat()}",
                "ingested_at": observed_at,
            }
        ],
        schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema,
    )


def _write_complete_ladder(store: ObjectStore, table: pa.Table, *, day: date) -> None:
    receipt = store.write_partition(
        table,
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=BASE_ZOOM,
        day=day,
    )
    derive_and_write_day_tiers(
        store,
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        day=day,
        run_id="exact-test",
        now=lambda: NOW,
    )
    store.write_completion_marker(
        PartitionCompletion(part_count=1, row_count=receipt.row_count, completed_at=NOW, run_id="exact-test"),
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=BASE_ZOOM,
        day=day,
    )


@pytest.mark.asyncio
async def test_clean_audit_compares_every_tier_but_excludes_the_prefloor_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_ladder(store, _table(), day=DAY)
    store.write_partition(
        _table(PREFIX_DAY),
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=BASE_ZOOM,
        day=PREFIX_DAY,
    )

    async def source(*_args: object, **_kwargs: object) -> pa.Table:
        return _table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        source,
    )
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert report.is_clean
    summary = report.to_summary()
    assert summary["day_count"] == 1
    prefix = summary["scope"]["excluded_historical_forecast_prefix"]  # type: ignore[index]
    assert prefix["distinct_object_present_days"] == 1  # type: ignore[index]
    assert all(
        summary["tiers"][str(zoom)]["actual_sha256"]  # type: ignore[index]
        == summary["tiers"][str(zoom)]["expected_sha256"]  # type: ignore[index]
        for zoom in ZOOM_TIERS
    )


@pytest.mark.asyncio
async def test_governed_empty_day_requires_same_decodable_absence_at_all_four_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    evidence = GovernedAbsence(
        reason="poller produced no rows",
        upstream_response="bounded current-weather read returned zero rows",
        recorded_at=NOW,
        run_id="exact-test",
    )
    for zoom in ZOOM_TIERS:
        store.write_absence(evidence, layer=WEATHER_OBSERVATIONS_STREAM, kind="observed", zoom=zoom, day=DAY)

    async def no_source(*_args: object, **_kwargs: object) -> pa.Table:
        return WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        no_source,
    )
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert report.is_clean
    assert all(
        report.to_summary()["days"][0]["tiers"][str(zoom)]["status"] == "absent"  # type: ignore[index]
        for zoom in ZOOM_TIERS
    )


@pytest.mark.asyncio
async def test_malformed_completion_marker_cannot_pass_on_key_presence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _write_complete_ladder(store, _table(), day=DAY)
    completion_key = completion_marker_path(WEATHER_OBSERVATIONS_STREAM, "observed", BASE_ZOOM, DAY)
    backend.objects[store.key_for(completion_key)] = b"{bad-json"

    async def source(*_args: object, **_kwargs: object) -> pa.Table:
        return _table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        source,
    )
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert not report.is_clean
    assert any(finding.kind == "completion_marker" for finding in report.findings)
