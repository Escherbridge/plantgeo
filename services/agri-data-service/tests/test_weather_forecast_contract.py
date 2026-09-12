"""Forecast identity, support, missingness and canonical unit contracts."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agri_data_service.warehouse.weather_forecast.contracts import (
    ForecastRun,
    ForecastSeries,
    ForecastValue,
    SpatialSupport,
)


@pytest.fixture
def run_data() -> dict[str, Any]:
    return {
        "run_id": "fixture-20260908T000000Z",
        "product_id": "test-weather-samples",
        "provider": "fixture",
        "model": "fixture-deterministic",
        "model_init_at": "2026-09-08T00:00:00Z",
        "provider_issued_at": None,
        "fetched_at": "2026-09-08T06:00:00Z",
        "admitted_at": "2026-09-08T06:01:00Z",
        "published_at": "2026-09-08T06:02:00Z",
        "licence": "test fixture only",
        "source_url": "https://example.test/weather",
        "source_payload_sha256": "a" * 64,
        "support": {"kind": "sampled_point"},
        "variables": ("temperature_2m", "precipitation", "wind_speed_10m"),
    }


@pytest.fixture
def value_data() -> dict[str, Any]:
    return {
        "run_id": "fixture-20260908T000000Z",
        "sample_id": "boise",
        "longitude": -116.2,
        "latitude": 43.6,
        "variable": "temperature_2m",
        "unit": "degC",
        "valid_at": "2026-09-08T01:00:00Z",
        "lead_seconds": 3600,
        "value": 0,
        "status": "available",
    }


def test_run_keeps_unknown_issue_and_separate_lifecycle(run_data: dict[str, Any]) -> None:
    run = ForecastRun.model_validate(run_data)
    wire = run.model_dump(mode="json")
    assert wire["provider_issued_at"] is None
    assert wire["model_version"] is None
    assert wire["model_init_at"] == "2026-09-08T00:00:00Z"
    assert wire["fetched_at"] != wire["admitted_at"] != wire["published_at"]
    assert ForecastRun.model_validate_json(run.model_dump_json()) == run
    with pytest.raises(ValidationError, match="frozen"):
        run.run_id = "changed"


@pytest.mark.parametrize(
    "timestamp",
    ["2026-09-08", "2026-09-08T00:00:00", "2026-09-08T00:00:00-06:00", 1_788_825_600, True],
)
def test_run_refuses_ambiguous_or_non_utc_times(run_data: dict[str, Any], timestamp: object) -> None:
    run_data["model_init_at"] = timestamp
    with pytest.raises(ValidationError, match="UTC"):
        ForecastRun.model_validate(run_data)


@pytest.mark.parametrize(
    ("field", "timestamp"),
    [
        ("fetched_at", "2026-09-07T23:59:59Z"),
        ("admitted_at", "2026-09-08T05:59:59Z"),
        ("published_at", "2026-09-08T06:00:59Z"),
        ("provider_issued_at", "2026-09-07T23:59:59Z"),
        ("provider_issued_at", "2026-09-08T06:00:01Z"),
    ],
)
def test_run_refuses_out_of_order_times(run_data: dict[str, Any], field: str, timestamp: str) -> None:
    run_data[field] = timestamp
    with pytest.raises(ValidationError, match=r"lifecycle|provider issue"):
        ForecastRun.model_validate(run_data)


@pytest.mark.parametrize("kind", ["native_grid", "derived_field"])
def test_unadmitted_field_support_is_refused(kind: str) -> None:
    with pytest.raises(ValidationError, match="separately admitted"):
        SpatialSupport.model_validate({"kind": kind})


def test_resolution_evidence_does_not_expand_sample_support() -> None:
    support = SpatialSupport(kind="sampled_point", source_resolution_m=9000, resolution_evidence="fixture reference")
    assert support.represented_support == "sample_coordinate"
    with pytest.raises(ValidationError, match="together"):
        SpatialSupport(kind="sampled_point", source_resolution_m=9000)


@pytest.mark.parametrize("token", ["../other", "/absolute", "a/b", "a\\b", "x" * 161, "", " whitespace"])
def test_run_tokens_cannot_escape_artifact_paths(run_data: dict[str, Any], token: str) -> None:
    run_data["run_id"] = token
    with pytest.raises(ValidationError):
        ForecastRun.model_validate(run_data)


def test_zero_and_null_remain_distinct(value_data: dict[str, Any]) -> None:
    assert ForecastValue.model_validate(value_data).value == 0
    value_data.update(value=None, status="upstream_unavailable")
    assert ForecastValue.model_validate(value_data).value is None
    value_data.update(status="available")
    with pytest.raises(ValidationError, match="available requires"):
        ForecastValue.model_validate(value_data)
    value_data.update(value=0, status="missing")
    with pytest.raises(ValidationError, match="absence requires"):
        ForecastValue.model_validate(value_data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("longitude", True), ("latitude", False), ("value", True), ("value", float("nan")), ("lead_seconds", True)],
)
def test_numeric_values_refuse_boolean_and_nonfinite_coercion(
    value_data: dict[str, Any], field: str, value: object
) -> None:
    value_data[field] = value
    with pytest.raises(ValidationError):
        ForecastValue.model_validate(value_data)


def test_variable_units_are_canonical_and_ranges_are_checked(value_data: dict[str, Any]) -> None:
    value_data["unit"] = "m/s"
    with pytest.raises(ValidationError, match="requires unit"):
        ForecastValue.model_validate(value_data)
    value_data.update(variable="relative_humidity_2m", unit="%", value=101)
    with pytest.raises(ValidationError, match="above variable support"):
        ForecastValue.model_validate(value_data)


def test_precipitation_interval_is_required_and_bound_to_valid_time(value_data: dict[str, Any]) -> None:
    value_data.update(variable="precipitation", unit="mm")
    with pytest.raises(ValidationError, match="requires its interval"):
        ForecastValue.model_validate(value_data)
    value_data.update(interval_start="2026-09-08T00:00:00Z", interval_end="2026-09-08T01:00:00Z")
    assert ForecastValue.model_validate(value_data).value == 0
    value_data.update(interval_end="2026-09-08T00:30:00Z")
    with pytest.raises(ValidationError, match="end at valid time"):
        ForecastValue.model_validate(value_data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("run_id", "a-new-run", "pinned run"),
        ("lead_seconds", 0, "lead_seconds"),
        ("variable", "apparent_temperature", "variable inventory"),
    ],
)
def test_series_refuses_mixed_run_lead_and_inventory(
    run_data: dict[str, Any], value_data: dict[str, Any], field: str, value: object, message: str
) -> None:
    value_data[field] = value
    with pytest.raises(ValidationError, match=message):
        ForecastSeries.model_validate({"run": run_data, "values": [value_data]})


def test_series_refuses_duplicate_or_moved_samples(run_data: dict[str, Any], value_data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ForecastSeries.model_validate({"run": run_data, "values": [value_data, value_data]})
    moved = {**value_data, "longitude": -115.0, "valid_at": "2026-09-08T02:00:00Z", "lead_seconds": 7200}
    with pytest.raises(ValidationError, match="change coordinates"):
        ForecastSeries.model_validate({"run": run_data, "values": [value_data, moved]})


def test_utc_datetime_input_and_series_wire_round_trip(run_data: dict[str, Any], value_data: dict[str, Any]) -> None:
    initialized = datetime(2026, 9, 8, tzinfo=UTC)
    run_data["model_init_at"] = initialized
    value_data["valid_at"] = initialized + timedelta(hours=1)
    series = ForecastSeries.model_validate({"run": run_data, "values": [value_data]})
    assert ForecastSeries.model_validate_json(series.model_dump_json()) == series
    assert isinstance(series.values, tuple)


def test_lead_zero_missing_interval_does_not_become_a_pre_run_forecast(
    run_data: dict[str, Any], value_data: dict[str, Any]
) -> None:
    value_data.update(
        variable="precipitation",
        unit="mm",
        valid_at="2026-09-08T00:00:00Z",
        lead_seconds=0,
        interval_start="2026-09-07T23:00:00Z",
        interval_end="2026-09-08T00:00:00Z",
        value=None,
        status="missing",
    )
    series = ForecastSeries.model_validate({"run": run_data, "values": [value_data]})
    assert series.values[0].status == "missing"
    value_data.update(value=0, status="available")
    with pytest.raises(ValidationError, match="before model initialization"):
        ForecastSeries.model_validate({"run": run_data, "values": [value_data]})


def test_maximum_gust_requires_its_aggregation_period(value_data: dict[str, Any]) -> None:
    value_data.update(variable="wind_gusts_10m", unit="m/s")
    with pytest.raises(ValidationError, match="requires its interval"):
        ForecastValue.model_validate(value_data)


@pytest.mark.parametrize(
    "status", ["exact_absence", "outside_domain", "stale_run", "not_yet_generated", "upstream_unavailable", "missing"]
)
def test_missing_statuses_survive_wire_round_trip(value_data: dict[str, Any], status: str) -> None:
    value_data.update(value=None, status=status)
    row = ForecastValue.model_validate(value_data)
    assert ForecastValue.model_validate_json(row.model_dump_json()).status == status
