"""Host-bound agent context must agree with the selected-location forecast plane."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agri_data_service.agent import weather_forecast
from agri_data_service.agent.weather_forecast import (
    WeatherForecastContext,
    read_weather_forecast_context,
    serialize_weather_forecast_context,
    weather_forecast_tool_descriptor,
)
from agri_data_service.planes.weather_forecast import SelectedWeatherForecast


@pytest.fixture
def context() -> WeatherForecastContext:
    return WeatherForecastContext.model_validate(
        {
            "mode": "forecast",
            "location_source": "picked",
            "product_id": "fixture-weather",
            "selection": {
                "run_id": "fixture-20260908",
                "longitude": -116.2,
                "latitude": 43.6,
                "start": "2026-09-08T01:00:00Z",
                "end": "2026-09-08T03:00:00Z",
                "timezone": "America/Denver",
            },
            "requested_zoom": 13,
            "max_distance_m": 1000,
        }
    )


@pytest.fixture
def response(context: WeatherForecastContext) -> SelectedWeatherForecast:
    return SelectedWeatherForecast.model_validate(
        {
            "selection": context.selection.model_dump(mode="json"),
            "status": "stale_run",
            "zoom": 13,
            "sample_distance_m": 100,
            "run": {
                "run_id": context.selection.run_id,
                "product_id": context.product_id,
                "provider": "fixture",
                "model": "fixture-deterministic",
                "model_init_at": "2026-09-08T00:00:00Z",
                "provider_issued_at": None,
                "fetched_at": "2026-09-08T01:00:00Z",
                "admitted_at": "2026-09-08T01:01:00Z",
                "published_at": "2026-09-08T01:02:00Z",
                "licence": "fixture only",
                "source_url": "https://example.test/weather",
                "source_payload_sha256": "b" * 64,
                "support": {"kind": "sampled_point"},
                "variables": ["temperature_2m"],
            },
            "values": [
                {
                    "run_id": context.selection.run_id,
                    "sample_id": "fixture-boise",
                    "longitude": -116.201,
                    "latitude": 43.6,
                    "valid_at": "2026-09-08T01:00:00Z",
                    "lead_seconds": 3600,
                    "variable": "temperature_2m",
                    "unit": "degC",
                    "status": "available",
                    "value": 0,
                },
                {
                    "run_id": context.selection.run_id,
                    "sample_id": "fixture-boise",
                    "longitude": -116.201,
                    "latitude": 43.6,
                    "valid_at": "2026-09-08T02:00:00Z",
                    "lead_seconds": 7200,
                    "variable": "temperature_2m",
                    "unit": "degC",
                    "status": "missing",
                    "value": None,
                },
            ],
        }
    )


def test_context_serialization_retains_exact_plane_response(
    context: WeatherForecastContext, response: SelectedWeatherForecast
) -> None:
    result = serialize_weather_forecast_context(context, response)
    for field, value in response.model_dump(mode="json").items():
        assert result[field] == value
    assert result["context"] == context.model_dump(mode="json")
    assert result["units"] == {"temperature_2m": "degC"}
    assert result["run"]["provider_issued_at"] is None
    assert result["values"][0]["value"] == 0
    assert result["values"][1]["value"] is None
    assert result["status"] == "stale_run"


@pytest.mark.parametrize(
    ("field", "value"), [("mode", "history"), ("mode", "current"), ("location_source", "viewport")]
)
def test_forecast_context_refuses_other_modes_or_viewport_substitutions(
    context: WeatherForecastContext, field: str, value: str
) -> None:
    data = context.model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        WeatherForecastContext.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [("longitude", -115.0), ("run_id", "newer-run"), ("start", "2026-09-08T00:00:00Z"), ("timezone", "UTC")],
)
def test_serializer_refuses_changed_selection(
    context: WeatherForecastContext, response: SelectedWeatherForecast, field: str, value: object
) -> None:
    data = response.model_dump(mode="json")
    data["selection"][field] = value
    with pytest.raises(ValueError, match="changed the selected"):
        serialize_weather_forecast_context(context, SelectedWeatherForecast.model_validate(data))


def test_serializer_refuses_wrong_product_and_distant_sample(
    context: WeatherForecastContext, response: SelectedWeatherForecast
) -> None:
    data = response.model_dump(mode="json")
    data["run"]["product_id"] = "other-product"
    with pytest.raises(ValueError, match="pinned product"):
        serialize_weather_forecast_context(context, SelectedWeatherForecast.model_validate(data))
    data = response.model_dump(mode="json")
    data["sample_distance_m"] = context.max_distance_m + 1
    with pytest.raises(ValueError, match="sample-distance"):
        serialize_weather_forecast_context(context, SelectedWeatherForecast.model_validate(data))


def test_not_yet_generated_preserves_selection_without_inventing_run(
    context: WeatherForecastContext, response: SelectedWeatherForecast
) -> None:
    data = response.model_dump(mode="json")
    data.update(run=None, values=[], status="not_yet_generated", sample_distance_m=None)
    result = serialize_weather_forecast_context(context, SelectedWeatherForecast.model_validate(data))
    assert result["run"] is None
    assert result["selection"] == context.selection.model_dump(mode="json")
    assert result["status"] == "not_yet_generated"
    assert result["values"] == []


def test_agent_reads_public_plane_with_host_selection_and_clock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    context: WeatherForecastContext,
    response: SelectedWeatherForecast,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_read(**kwargs: Any) -> SelectedWeatherForecast:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(weather_forecast, "read_selected_forecast", fake_read)
    now = datetime(2026, 9, 10, tzinfo=UTC)
    result = read_weather_forecast_context(context, root=tmp_path, now=now)
    assert calls == [
        {
            "root": tmp_path,
            "product_id": context.product_id,
            "selection": context.selection,
            "requested_zoom": context.requested_zoom,
            "now": now,
            "max_distance_m": context.max_distance_m,
        }
    ]
    assert result["selection"] == context.selection.model_dump(mode="json")


def test_descriptor_cannot_accept_model_generated_selection_arguments() -> None:
    schema = weather_forecast_tool_descriptor()["inputSchema"]
    assert schema == {"type": "object", "properties": {}, "additionalProperties": False}


def test_agent_refuses_an_ambiguous_host_clock_before_reading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, context: WeatherForecastContext
) -> None:
    def refuse_read(**_kwargs: Any) -> SelectedWeatherForecast:
        raise AssertionError("invalid host clock reached forecast reader")

    monkeypatch.setattr(weather_forecast, "read_selected_forecast", refuse_read)
    ambiguous_clock = datetime(2026, 9, 10, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValidationError, match="UTC"):
        read_weather_forecast_context(context, root=tmp_path, now=ambiguous_clock)
