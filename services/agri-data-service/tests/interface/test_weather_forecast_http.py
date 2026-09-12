"""The sampled forecast HTTP adapter validates and preserves the plane contract."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.interface.http import weather_forecast
from agri_data_service.planes.weather_forecast import ForecastField, ForecastSelection, SelectedWeatherForecast
from agri_data_service.warehouse.weather_forecast.contracts import (
    ForecastRun,
    ForecastValue,
    SpatialSupport,
    ValueStatus,
)

if TYPE_CHECKING:
    from sanic.response import HTTPResponse

NOW = datetime(2026, 9, 8, 2, tzinfo=UTC)
START = datetime(2026, 9, 8, tzinfo=UTC)
END = START + timedelta(hours=2)
PRODUCT_ID = "ecmwf-ifs-single-run"
RUN_ID = "ecmwf-ifs-20260908T0000"


def request_with(**query: str) -> Any:
    return SimpleNamespace(args=query)


def selected_query(**overrides: str) -> dict[str, str]:
    query = {
        "product_id": PRODUCT_ID,
        "run_id": RUN_ID,
        "longitude": "-116.2023",
        "latitude": "43.615",
        "start": "2026-09-08T00:00:00Z",
        "end": "2026-09-08T02:00:00Z",
        "timezone": "America/Boise",
        "zoom": "13",
        "max_distance_m": "10000",
    }
    query.update(overrides)
    return query


def field_query(**overrides: str) -> dict[str, str]:
    query = {
        "product_id": PRODUCT_ID,
        "run_id": RUN_ID,
        "bbox": "-117,43,-115,44",
        "start": "2026-09-08T00:00:00Z",
        "end": "2026-09-08T02:00:00Z",
        "variable": "temperature_2m",
        "zoom": "13",
    }
    query.update(overrides)
    return query


def payload_of(response: HTTPResponse) -> dict[str, Any]:
    assert response.body is not None
    return json.loads(response.body)


@pytest.fixture
def run() -> ForecastRun:
    return ForecastRun(
        run_id=RUN_ID,
        product_id=PRODUCT_ID,
        provider="open-meteo",
        model="ecmwf-ifs",
        model_version=None,
        model_init_at=START,
        provider_issued_at=None,
        fetched_at=START + timedelta(minutes=20),
        admitted_at=START + timedelta(minutes=30),
        published_at=START + timedelta(minutes=40),
        licence="CC BY 4.0 provider attribution required",
        source_url="https://example.test/single-runs",
        source_payload_sha256="a" * 64,
        support=SpatialSupport(kind="sampled_point", source_resolution_m=9000, resolution_evidence="provider grid"),
        variables=("temperature_2m",),
    )


@pytest.fixture
def values() -> tuple[ForecastValue, ...]:
    common = {
        "run_id": RUN_ID,
        "sample_id": "sample-1",
        "longitude": -116.1875,
        "latitude": 43.625,
        "variable": "temperature_2m",
        "unit": "degC",
    }
    return (
        ForecastValue(**common, valid_at=START, lead_seconds=0, value=18.5, status="available"),
        ForecastValue(**common, valid_at=START + timedelta(hours=1), lead_seconds=3600, value=None, status="missing"),
    )


@pytest.mark.asyncio
async def test_selected_route_uses_only_configured_root_and_serializes_full_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run: ForecastRun,
    values: tuple[ForecastValue, ...],
) -> None:
    captured: dict[str, object] = {}
    result = SelectedWeatherForecast(
        selection=ForecastSelection(
            run_id=RUN_ID,
            longitude=-116.2023,
            latitude=43.615,
            start=START,
            end=END,
            timezone="America/Boise",
        ),
        status="available",
        run=run,
        zoom=13,
        sample_distance_m=1517.25,
        values=values,
    )

    def fake_read(**kwargs: object) -> SelectedWeatherForecast:
        captured.update(kwargs)
        return result

    monkeypatch.setattr(weather_forecast.settings, "local_execution_root", tmp_path)
    monkeypatch.setattr(weather_forecast, "utc_now", lambda: NOW)
    monkeypatch.setattr(weather_forecast, "read_selected_forecast", fake_read)

    response = await weather_forecast.read_selected(request_with(**selected_query()))
    payload = payload_of(response)

    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert captured == {
        "root": (tmp_path / "weather-forecast").resolve(),
        "product_id": PRODUCT_ID,
        "selection": result.selection,
        "requested_zoom": 13,
        "now": NOW,
        "max_distance_m": 10000.0,
    }
    assert payload == result.model_dump(mode="json")
    assert payload["run"]["provider_issued_at"] is None
    assert payload["run"]["support"] == {
        "kind": "sampled_point",
        "represented_support": "sample_coordinate",
        "source_resolution_m": 9000.0,
        "resolution_evidence": "provider grid",
    }
    assert payload["sample_distance_m"] == 1517.25
    assert [value["status"] for value in payload["values"]] == ["available", "missing"]


@pytest.mark.asyncio
async def test_field_route_delegates_exact_bounds_and_serializes_sample_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run: ForecastRun,
    values: tuple[ForecastValue, ...],
) -> None:
    captured: dict[str, object] = {}
    result = ForecastField(
        product_id=PRODUCT_ID,
        run_id=RUN_ID,
        bbox=(-117, 43, -115, 44),
        start=START,
        end=END,
        variable="temperature_2m",
        zoom=13,
        status="available",
        run=run,
        values=values,
    )

    def fake_read(**kwargs: object) -> ForecastField:
        captured.update(kwargs)
        return result

    monkeypatch.setattr(weather_forecast.settings, "local_execution_root", tmp_path)
    monkeypatch.setattr(weather_forecast, "utc_now", lambda: NOW)
    monkeypatch.setattr(weather_forecast, "read_forecast_field", fake_read)

    response = await weather_forecast.read_field(request_with(**field_query()))

    assert response.status == 200
    assert captured == {
        "root": (tmp_path / "weather-forecast").resolve(),
        "product_id": PRODUCT_ID,
        "run_id": RUN_ID,
        "requested_zoom": 13,
        "bbox": (-117.0, 43.0, -115.0, 44.0),
        "start": START,
        "end": END,
        "variable": "temperature_2m",
        "now": NOW,
        "max_rows": weather_forecast.MAX_FIELD_ROWS,
    }
    assert payload_of(response) == result.model_dump(mode="json")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("product_id", "../weather"),
        ("run_id", "run/other"),
        ("longitude", "181"),
        ("latitude", "nan"),
        ("start", "2026-09-08T00:00:00-06:00"),
        ("start", "2026-09-08T00:30:00Z"),
        ("end", "2026-09-19T00:00:00Z"),
        ("timezone", "Mars/Olympus"),
        ("zoom", "09"),
        ("zoom", "23"),
        ("max_distance_m", "100001"),
    ],
)
@pytest.mark.asyncio
async def test_selected_route_rejects_every_untrusted_query_axis(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    called = False

    def fake_read(**_kwargs: object) -> SelectedWeatherForecast:
        nonlocal called
        called = True
        raise AssertionError("invalid request reached the plane")

    monkeypatch.setattr(weather_forecast, "read_selected_forecast", fake_read)
    response = await weather_forecast.read_selected(request_with(**selected_query(**{field: value})))

    assert response.status == 400
    assert payload_of(response)["error"]["code"] == "invalid_request"
    assert called is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bbox", "-117,43,-115"),
        ("bbox", "170,-20,-170,20"),
        ("bbox", "-117,43,inf,44"),
        ("variable", "imaginary_temperature"),
        ("zoom", "-1"),
    ],
)
@pytest.mark.asyncio
async def test_field_route_rejects_invalid_spatial_variable_and_zoom_axes(
    monkeypatch: pytest.MonkeyPatch, field: str, value: str
) -> None:
    called = False

    def fake_read(**_kwargs: object) -> ForecastField:
        nonlocal called
        called = True
        raise AssertionError("invalid request reached the plane")

    monkeypatch.setattr(weather_forecast, "read_forecast_field", fake_read)
    response = await weather_forecast.read_field(request_with(**field_query(**{field: value})))

    assert response.status == 400
    assert payload_of(response)["error"]["code"] == "invalid_request"
    assert called is False


@pytest.mark.parametrize(
    "status",
    ["exact_absence", "outside_domain", "stale_run", "not_yet_generated", "upstream_unavailable", "missing"],
)
@pytest.mark.asyncio
async def test_forecast_content_states_remain_http_200(
    monkeypatch: pytest.MonkeyPatch,
    status: ValueStatus,
) -> None:
    selection = ForecastSelection(
        run_id=RUN_ID,
        longitude=-116.2023,
        latitude=43.615,
        start=START,
        end=END,
        timezone="UTC",
    )
    result = SelectedWeatherForecast(
        selection=selection,
        status=status,
        run=None,
        zoom=13,
        sample_distance_m=None,
        values=(),
    )
    monkeypatch.setattr(weather_forecast, "read_selected_forecast", lambda **_kwargs: result)
    query = selected_query(timezone="UTC")

    response = await weather_forecast.read_selected(request_with(**query))

    assert response.status == 200
    assert payload_of(response)["status"] == status


@pytest.mark.asyncio
async def test_response_byte_overflow_is_an_explicit_conflict(
    monkeypatch: pytest.MonkeyPatch,
    run: ForecastRun,
    values: tuple[ForecastValue, ...],
) -> None:
    result = SelectedWeatherForecast(
        selection=ForecastSelection(
            run_id=RUN_ID,
            longitude=-116.2023,
            latitude=43.615,
            start=START,
            end=END,
            timezone="America/Boise",
        ),
        status="available",
        run=run,
        zoom=13,
        sample_distance_m=1517.25,
        values=values,
    )
    monkeypatch.setattr(weather_forecast, "read_selected_forecast", lambda **_kwargs: result)
    monkeypatch.setattr(weather_forecast, "MAX_RESPONSE_BYTES", 1)

    response = await weather_forecast.read_selected(request_with(**selected_query()))

    assert response.status == 409
    assert payload_of(response)["error"]["code"] == "forecast_response_over_budget"


@pytest.mark.asyncio
async def test_read_timeout_is_an_explicit_service_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timed_out(_read: object) -> object:
        raise TimeoutError

    monkeypatch.setattr(weather_forecast.asyncio, "to_thread", timed_out)

    response = await weather_forecast.read_selected(request_with(**selected_query()))

    assert response.status == 503
    assert payload_of(response)["error"]["code"] == "forecast_read_timed_out"


@pytest.mark.asyncio
async def test_plane_budget_refusal_and_unexpected_fault_have_explicit_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def over_budget(**_kwargs: object) -> ForecastField:
        raise ValueError("field response exceeds row budget; narrow the request")

    monkeypatch.setattr(weather_forecast, "read_forecast_field", over_budget)
    budget = await weather_forecast.read_field(request_with(**field_query()))

    def broken(**_kwargs: object) -> ForecastField:
        raise RuntimeError("filesystem detail that must not leave the service")

    monkeypatch.setattr(weather_forecast, "read_forecast_field", broken)
    failed = await weather_forecast.read_field(request_with(**field_query()))

    assert budget.status == 409
    assert payload_of(budget)["error"]["code"] == "forecast_read_over_budget"
    assert failed.status == 503
    assert payload_of(failed) == {
        "error": {
            "code": "forecast_read_unavailable",
            "message": "weather forecast artifacts could not be read",
        }
    }
