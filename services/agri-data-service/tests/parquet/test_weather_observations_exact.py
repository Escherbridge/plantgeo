"""Exact current-weather audit keeps the Historical Forecast prefix outside governance."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import absence_marker_path, completion_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.validation.weather_observations_exact import (
    _schema_is_read_compatible,
    audit_exact_weather_observations,
)
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
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    async def execute(self, statement: object) -> None:
        self.events.append(("execute", str(statement)))

    async def rollback(self) -> None:
        self.events.append(("rollback", None))


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


def test_registered_schema_accepts_stricter_but_not_relaxed_nullability() -> None:
    expected = WEATHER_OBSERVATIONS_SCHEMA.arrow_schema
    stricter = pa.schema(
        [pa.field(field.name, field.type, nullable=False, metadata=field.metadata) for field in expected]
    )
    relaxed_required = pa.schema(
        [
            pa.field(field.name, field.type, nullable=True, metadata=field.metadata)
            if field.name == "latitude"
            else field
            for field in expected
        ]
    )

    assert _schema_is_read_compatible(stricter, expected)
    assert not _schema_is_read_compatible(relaxed_required, expected)


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
    session = _Session()
    report = await audit_exact_weather_observations(
        session,  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert report.is_clean
    summary = report.to_summary()
    assert summary["day_count"] == 1
    prefix = summary["scope"]["excluded_historical_forecast_prefix"]  # type: ignore[index]
    assert prefix["distinct_object_present_days"] == 1
    assert all(
        summary["tiers"][str(zoom)]["actual_sha256"]  # type: ignore[index]
        == summary["tiers"][str(zoom)]["expected_sha256"]  # type: ignore[index]
        for zoom in ZOOM_TIERS
    )
    assert summary["source_stability"]["stable"]  # type: ignore[index]
    assert summary["object_stability"]["stable"]  # type: ignore[index]
    assert session.events == [
        ("rollback", None),
        ("execute", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"),
    ]


@pytest.mark.asyncio
async def test_governed_empty_day_requires_base_absence_and_empty_coarse_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    evidence = GovernedAbsence(
        reason="poller produced no rows",
        upstream_response="bounded current-weather read returned zero rows",
        recorded_at=NOW,
        run_id="exact-test",
    )
    store.write_absence(
        evidence,
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=BASE_ZOOM,
        day=DAY,
    )

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
    day = report.to_summary()["days"][0]  # type: ignore[index]
    assert day["tiers"][str(BASE_ZOOM)]["status"] == "absent"
    assert all(day["tiers"][str(zoom)]["status"] == "missing" for zoom in ZOOM_TIERS[:-1])


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


@pytest.mark.asyncio
async def test_source_change_between_opening_and_closing_snapshots_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_ladder(store, _table(), day=DAY)
    calls = 0

    async def changing_source(*_args: object, **_kwargs: object) -> pa.Table:
        nonlocal calls
        calls += 1
        return (
            _table()
            if calls == 1
            else _table().set_column(
                _table().column_names.index("temperature_c"),
                "temperature_c",
                pa.array([22.0], type=pa.float64()),
            )
        )

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        changing_source,
    )
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert not report.is_clean
    assert not report.source_is_stable
    assert any(finding.kind == "source_changed_during_reconciliation" for finding in report.findings)


@pytest.mark.asyncio
async def test_object_key_change_between_opening_and_closing_inventories_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_ladder(store, _table(), day=DAY)

    async def source(*_args: object, **_kwargs: object) -> pa.Table:
        return _table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        source,
    )
    original_list = store.list_partition_keys
    calls = 0

    def changing_list(layer: str, kind: str, zoom: ZoomTier) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        keys = original_list(layer, kind, zoom)  # type: ignore[arg-type]
        if calls > len(ZOOM_TIERS) and zoom == BASE_ZOOM:
            return (*keys, absence_marker_path(WEATHER_OBSERVATIONS_STREAM, "observed", BASE_ZOOM, DAY))
        return keys

    monkeypatch.setattr(store, "list_partition_keys", changing_list)
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert not report.is_clean
    assert not report.object_plane_is_stable
    assert any(finding.kind == "object_inventory_changed_during_reconciliation" for finding in report.findings)


@pytest.mark.asyncio
async def test_same_key_partition_overwrite_between_reads_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_ladder(store, _table(), day=DAY)

    async def source(*_args: object, **_kwargs: object) -> pa.Table:
        return _table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        source,
    )
    original_read = store.read_partition
    base_reads = 0

    def changing_read(layer: str, kind: str, zoom: ZoomTier, day: date) -> pa.Table:
        nonlocal base_reads
        table = original_read(layer, kind, zoom, day)  # type: ignore[arg-type]
        if zoom != BASE_ZOOM:
            return table
        base_reads += 1
        if base_reads == 1:
            return table
        return table.set_column(
            table.column_names.index("temperature_c"),
            "temperature_c",
            pa.array([22.0], type=pa.float64()),
        )

    monkeypatch.setattr(store, "read_partition", changing_read)
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    assert not report.is_clean
    assert report.object_inventory_sha256 == report.closing_object_inventory_sha256
    assert not report.object_plane_is_stable
    assert any(finding.kind == "object_content_changed_during_reconciliation" for finding in report.findings)


@pytest.mark.asyncio
async def test_backend_failure_details_are_redacted_from_persistable_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObjectStore(RecordingBackend())
    _write_complete_ladder(store, _table(), day=DAY)

    async def source(*_args: object, **_kwargs: object) -> pa.Table:
        return _table()

    monkeypatch.setattr(
        "agri_data_service.pipeline.validation.weather_observations_exact.read_weather_observations_day",
        source,
    )
    leaked = "https://secret-endpoint.invalid bucket=private access_key=do-not-emit"

    def failing_completion(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(leaked)

    monkeypatch.setattr(store, "read_completion_marker", failing_completion)
    report = await audit_exact_weather_observations(
        _Session(),  # type: ignore[arg-type]
        store,
        layer_id="weather-layer-id",
        governed_last_day=DAY,
    )

    payload = json.dumps(report.to_summary())
    assert leaked not in payload
    assert "secret-endpoint" not in payload
    assert any(finding.detail == "RuntimeError: backend detail redacted" for finding in report.findings)
