"""Bounded Single Runs sampling and normalization; see AGENTS.md."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    UpstreamTimeoutError,
    fetch_bounded,
)
from agri_data_service.ingest.policy import MAX_LATITUDE, MAX_LONGITUDE, MIN_LATITUDE, MIN_LONGITUDE
from agri_data_service.warehouse.weather_forecast.contracts import ForecastRun, ForecastSeries, ForecastValue
from agri_data_service.warehouse.weather_forecast.variables import VARIABLES, VariableName
from agri_data_service.warehouse.weather_forecast.wind import meteorological_vector

if TYPE_CHECKING:
    import httpx

ENDPOINT = "https://single-runs-api.open-meteo.com/v1/forecast"
MODEL = "ecmwf_ifs"
PRODUCT = "open-meteo-single-runs-ecmwf-ifs-sampled-v1"
PROVIDER = "open-meteo"
LICENCE = "CC-BY-4.0; attribution: Open-Meteo / ECMWF"
SOURCE_BOUNDS = UpstreamBounds(max_bytes=1024 * 1024, timeout_seconds=15.0)
FORECAST_DAYS = 10
HOURS_PER_DAY = 24
HOUR_SECONDS = 3600
STEP_COUNT = FORECAST_DAYS * HOURS_PER_DAY
SOURCE_UNITS: dict[VariableName, str] = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "apparent_temperature": "°C",
    "dew_point_2m": "°C",
    "cloud_cover": "%",
    "pressure_msl": "hPa",
    "precipitation": "mm",
    "weather_code": "wmo code",
    "wind_speed_10m": "m/s",
    "wind_direction_10m": "°",
    "wind_gusts_10m": "m/s",
}
SOURCE_VARIABLES = tuple(SOURCE_UNITS)
OUTPUT_VARIABLES: tuple[VariableName, ...] = (*SOURCE_VARIABLES, "wind_u_10m", "wind_v_10m")
INTERVAL_VARIABLES = frozenset(("precipitation", "wind_gusts_10m"))


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite numeric data")
    return float(value)


@dataclass(frozen=True, slots=True)
class SingleRunRequest:
    """One sampled location on the probed midnight model cycle."""

    latitude: float
    longitude: float
    model_init_at: datetime

    def __post_init__(self) -> None:
        latitude = _number(self.latitude, "latitude")
        longitude = _number(self.longitude, "longitude")
        if not MIN_LATITUDE <= latitude <= MAX_LATITUDE or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE:
            raise ValueError("requested coordinates must be WGS84")
        instant = self.model_init_at
        if instant.utcoffset() != timedelta(0) or (
            instant.hour,
            instant.minute,
            instant.second,
            instant.microsecond,
        ) != (0, 0, 0, 0):
            raise ValueError("the probed product requires an explicit UTC midnight initialization")

    @property
    def url(self) -> str:
        parameters = {
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "models": MODEL,
            "run": self.model_init_at.strftime("%Y-%m-%dT%H:%M"),
            "hourly": ",".join(SOURCE_VARIABLES),
            "timezone": "GMT",
            "timeformat": "unixtime",
            "wind_speed_unit": "ms",
            "temperature_unit": "celsius",
            "precipitation_unit": "mm",
            "cell_selection": "nearest",
            "elevation": "nan",
            "forecast_days": str(FORECAST_DAYS),
        }
        return f"{ENDPOINT}?{urlencode(parameters)}"


async def fetch_single_run(client: httpx.AsyncClient, request: SingleRunRequest) -> str:
    """Fetch one capped run without HTTP-status retries or publication."""
    try:
        async with asyncio.timeout(SOURCE_BOUNDS.timeout_seconds):
            response = await fetch_bounded(client, request.url, SOURCE_BOUNDS)
    except TimeoutError as error:
        raise UpstreamTimeoutError("Single Runs exceeded the total fetch deadline") from error
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is None or "json" not in response.content_type.lower():
        raise UpstreamPayloadError("Single Runs response must be JSON")
    if "\ufffd" in response.text:
        raise UpstreamPayloadError("Single Runs response contains invalid decoded text")
    return response.text


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key}")
        result[key] = value
    return result


def _source_payload(raw_text: str) -> dict[str, Any]:
    if len(raw_text.encode("utf-8")) > SOURCE_BOUNDS.max_bytes:
        raise ValueError("Single Runs payload exceeds byte cap")
    result = json.loads(raw_text, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    if not isinstance(result, dict) or result.get("error"):
        raise ValueError("Single Runs payload must be a successful single location")
    if type(result.get("utc_offset_seconds")) is not int or result["utc_offset_seconds"] != 0:
        raise ValueError("Single Runs payload must use a numeric zero UTC offset")
    if result.get("timezone") != "GMT":
        raise ValueError("Single Runs payload must use GMT")
    units = result.get("hourly_units")
    expected_units: dict[str, str] = {str(name): unit for name, unit in SOURCE_UNITS.items()}
    expected_units["time"] = "unixtime"
    if not isinstance(units, dict) or units != expected_units:
        raise ValueError("Single Runs units or variable inventory changed")
    hourly = result.get("hourly")
    if not isinstance(hourly, dict) or set(hourly) != {"time", *SOURCE_VARIABLES}:
        raise ValueError("Single Runs hourly variable inventory changed")
    if any(not isinstance(values, list) or len(values) != STEP_COUNT for values in hourly.values()):
        raise ValueError("Single Runs must contain exactly 240 hourly values for every variable")
    return result


def _validate_run(raw_text: str, request: SingleRunRequest, run: ForecastRun) -> None:
    if (
        run.provider != PROVIDER
        or run.model != MODEL
        or run.product_id != PRODUCT
        or run.model_init_at != request.model_init_at
        or run.source_url != request.url
        or set(run.variables) != set(OUTPUT_VARIABLES)
        or run.support.kind != "sampled_point"
        or run.provider_issued_at is not None
        or run.model_version is not None
        or run.licence != LICENCE
    ):
        raise ValueError("run provenance does not bind the exact sampled Single Runs request")
    if run.source_payload_sha256 != hashlib.sha256(raw_text.encode("utf-8")).hexdigest():
        raise ValueError("run payload checksum does not match source text")


def _values_at(hourly: dict[str, Any], index: int) -> dict[VariableName, float | None]:
    values: dict[VariableName, float | None] = {
        variable: None if hourly[variable][index] is None else _number(hourly[variable][index], variable)
        for variable in SOURCE_VARIABLES
    }
    speed, direction = values["wind_speed_10m"], values["wind_direction_10m"]
    if speed is None or direction is None:
        values["wind_u_10m"], values["wind_v_10m"] = None, None
    else:
        vector = meteorological_vector(speed, direction)
        values["wind_u_10m"], values["wind_v_10m"] = vector.u, vector.v
    return values


def normalize_single_run(raw_text: str, request: SingleRunRequest, run: ForecastRun) -> ForecastSeries:
    """Validate exact source identity and retain every sampled value or missing value."""
    _validate_run(raw_text, request, run)
    payload = _source_payload(raw_text)
    latitude = _number(payload.get("latitude"), "returned latitude")
    longitude = _number(payload.get("longitude"), "returned longitude")
    _number(payload.get("elevation"), "returned elevation")
    sample_digest = hashlib.sha256(f"{latitude.hex()},{longitude.hex()}".encode()).hexdigest()[:24]
    sample_id = f"ecmwf-ifs-{sample_digest}"
    hourly = payload["hourly"]
    values: list[ForecastValue] = []
    start_epoch = int(request.model_init_at.timestamp())
    for index, epoch in enumerate(hourly["time"]):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != start_epoch + index * HOUR_SECONDS:
            raise ValueError("Single Runs time axis must be contiguous and anchored to initialization")
        valid_at = datetime.fromtimestamp(epoch, UTC)
        for variable, value in _values_at(hourly, index).items():
            has_interval = variable in INTERVAL_VARIABLES
            values.append(
                ForecastValue(
                    run_id=run.run_id,
                    sample_id=sample_id,
                    latitude=latitude,
                    longitude=longitude,
                    variable=variable,
                    unit=VARIABLES[variable].unit,
                    valid_at=valid_at,
                    lead_seconds=index * HOUR_SECONDS,
                    interval_start=valid_at - timedelta(hours=1) if has_interval else None,
                    interval_end=valid_at if has_interval else None,
                    value=value,
                    status="missing" if value is None else "available",
                )
            )
    return ForecastSeries(run=run, values=tuple(values))
