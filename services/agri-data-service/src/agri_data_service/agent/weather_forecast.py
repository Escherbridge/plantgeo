"""Unregistered forecast context adapter for agent and MCP consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from agri_data_service.planes.weather_forecast import (
    MAX_SAMPLE_DISTANCE_M,
    MAX_SELECTED_VALUES,
    ForecastSelection,
    SelectedWeatherForecast,
    read_selected_forecast,
)
from agri_data_service.warehouse.weather_forecast.contracts import (
    Finite,
    ForecastSeries,
    FrozenContract,
    SafeToken,
    UTCInstant,
)
from agri_data_service.warehouse.weather_forecast.variables import VARIABLES

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

TOOL_NAME = "weather_forecast_at_selected_location"
FORECAST_NOTE = (
    "Deterministic forecast estimates for the selected location and pinned model run. "
    "Values represent the returned sample coordinate; sample_distance_m reports its distance "
    "from the selection. They are not observations or an interpolated field. Provider issue "
    "may be unknown; initialization, fetch, admission and publication are different times. "
    "Retain explicit missingness and interval periods. A stale run can retain available values."
)


class WeatherForecastContext(FrozenContract):
    """Host-bound weather selection, never inferred from a viewport or model arguments."""

    mode: Literal["forecast"]
    location_source: Literal["picked", "search"]
    product_id: SafeToken
    selection: ForecastSelection
    requested_zoom: Annotated[int, Field(strict=True, ge=0, le=24)]
    max_distance_m: Annotated[Finite, Field(ge=0, le=MAX_SAMPLE_DISTANCE_M)]


def weather_forecast_tool_descriptor() -> dict[str, Any]:
    """Return the unregistered MCP descriptor for a host-bound selection read."""
    return {
        "name": TOOL_NAME,
        "description": (
            "Read the exact selected-location forecast shown by the weather experience, "
            "using its pinned run and valid-time window. The host supplies that selection. "
            "Unavailable without an explicit picked or searched location in forecast mode."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    }


def serialize_weather_forecast_context(
    context: WeatherForecastContext, response: SelectedWeatherForecast
) -> dict[str, Any]:
    """Preserve the plane response while refusing a changed selection or run."""
    context = WeatherForecastContext.model_validate(context.model_dump(mode="json"))
    response = SelectedWeatherForecast.model_validate(response.model_dump(mode="json"))
    if response.selection != context.selection:
        raise ValueError("forecast response changed the selected location, run, timezone or window")
    if len(response.values) > MAX_SELECTED_VALUES:
        raise ValueError("forecast response exceeds the selected-location value bound")
    if response.run is None:
        if response.values:
            raise ValueError("forecast values require their immutable run provenance")
    else:
        if response.run.run_id != context.selection.run_id or response.run.product_id != context.product_id:
            raise ValueError("forecast response changed the pinned product or run")
        ForecastSeries(run=response.run, values=response.values)
    if any(not context.selection.start <= row.valid_at < context.selection.end for row in response.values):
        raise ValueError("forecast response contains values outside the selected window")
    if len({row.sample_id for row in response.values}) > 1:
        raise ValueError("selected forecast response mixed sample locations")
    if response.values and (
        response.sample_distance_m is None or not 0 <= response.sample_distance_m <= context.max_distance_m
    ):
        raise ValueError("forecast values exceed the selected sample-distance bound")
    return {
        **response.model_dump(mode="json"),
        "context": context.model_dump(mode="json"),
        "units": {name: VARIABLES[name].unit for name in response.run.variables} if response.run else {},
        "note": FORECAST_NOTE,
    }


def read_weather_forecast_context(context: WeatherForecastContext, *, root: Path, now: datetime) -> dict[str, Any]:
    """Read the shared plane with an explicit host clock and validated UI selection."""
    context = WeatherForecastContext.model_validate(context.model_dump(mode="json"))
    now = TypeAdapter(UTCInstant).validate_python(now)
    response = read_selected_forecast(
        root=root,
        product_id=context.product_id,
        selection=context.selection,
        requested_zoom=context.requested_zoom,
        now=now,
        max_distance_m=context.max_distance_m,
    )
    return serialize_weather_forecast_context(context, response)
