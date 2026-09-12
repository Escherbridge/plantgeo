"""Bounded HTTP reads for pinned sampled weather forecasts."""

from __future__ import annotations

import asyncio
import math
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002 - Sanic inspects handler annotations.

from agri_data_service.config import settings
from agri_data_service.planes.weather_forecast import (
    MAX_SAMPLE_DISTANCE_M,
    ForecastSelection,
    read_forecast_field,
    read_local_forecast_capability,
    read_selected_forecast,
)
from agri_data_service.warehouse.weather_forecast.variables import VARIABLES, VariableName

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

logger = structlog.get_logger()

weather_forecast_bp = Blueprint("weather_forecast", url_prefix="/weather-forecast")

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503
READ_TIMEOUT_SECONDS: Final = 14.0
MAX_RESPONSE_BYTES: Final = 4 * 1024 * 1024
MAX_FIELD_ROWS: Final = 10_000
FORECAST_ARTIFACT_DIRECTORY: Final = "weather-forecast"

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_PLAIN_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")


class ForecastRequestError(ValueError):
    """A malformed query, distinct from serving and warehouse state."""


def configured_artifact_root() -> Path:
    """Resolve the fixed forecast directory beneath the configured trusted local root."""
    return (settings.local_execution_root / FORECAST_ARTIFACT_DIRECTORY).resolve()


def utc_now() -> datetime:
    """Return the serving clock; patched by deterministic adapter tests."""
    return datetime.now(UTC)


@weather_forecast_bp.get("/capability")
async def read_capability(request: Request) -> HTTPResponse:
    """Resolve the verified local active run for one explicit forecast product."""
    try:
        product_id = _token(request, "product_id")
    except ForecastRequestError as exc:
        return _request_refusal(str(exc))

    return await _answer(
        lambda: read_local_forecast_capability(
            root=configured_artifact_root(),
            product_id=product_id,
            now=utc_now(),
        ),
        route="capability",
    )


@weather_forecast_bp.get("/selected")
async def read_selected(request: Request) -> HTTPResponse:
    """Read one pinned run for an explicit selected location and UTC window."""
    try:
        product_id = _token(request, "product_id")
        run_id = _token(request, "run_id")
        longitude = _bounded_float(request, "longitude", minimum=-180, maximum=180)
        latitude = _bounded_float(request, "latitude", minimum=-90, maximum=90)
        start, end = _window(request)
        timezone = _timezone(request)
        requested_zoom = _zoom(request)
        max_distance_m = _bounded_float(
            request,
            "max_distance_m",
            minimum=0,
            maximum=MAX_SAMPLE_DISTANCE_M,
        )
        selection = ForecastSelection(
            run_id=run_id,
            longitude=longitude,
            latitude=latitude,
            start=start,
            end=end,
            timezone=timezone,
        )
    except (ForecastRequestError, ValueError) as exc:
        return _request_refusal(str(exc))

    return await _answer(
        lambda: read_selected_forecast(
            root=configured_artifact_root(),
            product_id=product_id,
            selection=selection,
            requested_zoom=requested_zoom,
            now=utc_now(),
            max_distance_m=max_distance_m,
        ),
        route="selected",
    )


@weather_forecast_bp.get("/field")
async def read_field(request: Request) -> HTTPResponse:
    """Read one sampled variable from a pinned run within a bounded viewport and UTC window."""
    try:
        product_id = _token(request, "product_id")
        run_id = _token(request, "run_id")
        bbox = _bbox(request)
        start, end = _window(request)
        variable = _variable(request)
        requested_zoom = _zoom(request)
    except ForecastRequestError as exc:
        return _request_refusal(str(exc))

    return await _answer(
        lambda: read_forecast_field(
            root=configured_artifact_root(),
            product_id=product_id,
            run_id=run_id,
            requested_zoom=requested_zoom,
            bbox=bbox,
            start=start,
            end=end,
            variable=variable,
            now=utc_now(),
            max_rows=MAX_FIELD_ROWS,
        ),
        route="field",
    )


async def _answer(read: Callable[[], BaseModel], *, route: str) -> HTTPResponse:
    """Run a bounded local read and preserve the content-state/transport-fault split."""
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS):
            result = await asyncio.to_thread(read)
    except TimeoutError:
        return _serving_refusal(
            "forecast_read_timed_out",
            "weather forecast read exceeded its serving deadline",
            HTTP_SERVICE_UNAVAILABLE,
        )
    except ValueError as exc:
        message = str(exc)
        if "exceeds" in message and "budget" in message:
            return _serving_refusal("forecast_read_over_budget", message, HTTP_CONFLICT)
        logger.exception("weather_forecast_read_failed", route=route, fault=type(exc).__name__)
        return _serving_refusal(
            "forecast_read_unavailable",
            "weather forecast artifacts could not be read",
            HTTP_SERVICE_UNAVAILABLE,
        )
    except Exception as exc:
        logger.exception("weather_forecast_read_failed", route=route, fault=type(exc).__name__)
        return _serving_refusal(
            "forecast_read_unavailable",
            "weather forecast artifacts could not be read",
            HTTP_SERVICE_UNAVAILABLE,
        )

    response = json(result.model_dump(mode="json"), status=HTTP_OK, headers={"Cache-Control": "no-store"})
    if response.body is None or len(response.body) > MAX_RESPONSE_BYTES:
        return _serving_refusal(
            "forecast_response_over_budget",
            "weather forecast response exceeds the byte budget; narrow the request",
            HTTP_CONFLICT,
        )
    return response


def _required(request: Request, name: str) -> str:
    value = request.args.get(name)
    if value is None or not value.strip():
        raise ForecastRequestError(f"{name} is required")
    return value.strip()


def _token(request: Request, name: str) -> str:
    value = _required(request, name)
    if _TOKEN.fullmatch(value) is None:
        raise ForecastRequestError(f"{name} is not a valid forecast identity")
    return value


def _instant(request: Request, name: str) -> datetime:
    value = _required(request, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ForecastRequestError(f"{name} must be an ISO-8601 UTC timestamp") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ForecastRequestError(f"{name} must be an explicit UTC timestamp")
    if parsed.minute or parsed.second or parsed.microsecond:
        raise ForecastRequestError(f"{name} must be on a UTC hour boundary")
    return parsed.astimezone(UTC)


def _window(request: Request) -> tuple[datetime, datetime]:
    start = _instant(request, "start")
    end = _instant(request, "end")
    if not timedelta(0) < end - start <= timedelta(days=10):
        raise ForecastRequestError("forecast window must be positive and no longer than ten days")
    return start, end


def _timezone(request: Request) -> str:
    value = _required(request, "timezone")
    if len(value) > 100:
        raise ForecastRequestError("timezone must contain at most 100 characters")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ForecastRequestError("timezone must be a known IANA timezone") from exc
    return value


def _bounded_float(request: Request, name: str, *, minimum: float, maximum: float) -> float:
    value = _required(request, name)
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ForecastRequestError(f"{name} must be a number") from exc
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ForecastRequestError(f"{name} must be between {minimum:g} and {maximum:g}")
    return parsed


def _zoom(request: Request) -> int:
    value = _required(request, "zoom")
    if _PLAIN_INTEGER.fullmatch(value) is None:
        raise ForecastRequestError("zoom must be a plain integer")
    parsed = int(value)
    if not 0 <= parsed <= 22:
        raise ForecastRequestError("zoom must be between 0 and 22")
    return parsed


def _bbox(request: Request) -> tuple[float, float, float, float]:
    parts = _required(request, "bbox").split(",")
    if len(parts) != 4:
        raise ForecastRequestError("bbox must contain west,south,east,north")
    try:
        bbox = tuple(float(value) for value in parts)
    except ValueError as exc:
        raise ForecastRequestError("bbox ordinates must be numbers") from exc
    west, south, east, north = bbox
    if not all(math.isfinite(value) for value in bbox) or not (
        -180 <= west < east <= 180 and -90 <= south < north <= 90
    ):
        raise ForecastRequestError(
            "bbox must be bounded west,south,east,north; split antimeridian windows into two requests"
        )
    return cast("tuple[float, float, float, float]", bbox)


def _variable(request: Request) -> VariableName:
    value = _required(request, "variable")
    if value not in VARIABLES:
        raise ForecastRequestError(f"variable must be one of {', '.join(VARIABLES)}")
    return cast("VariableName", value)


def _request_refusal(message: str) -> HTTPResponse:
    return _serving_refusal("invalid_request", message, HTTP_BAD_REQUEST)


def _serving_refusal(code: str, message: str, status: int) -> HTTPResponse:
    return json({"error": {"code": code, "message": message}}, status=status)


__all__ = ["weather_forecast_bp"]
