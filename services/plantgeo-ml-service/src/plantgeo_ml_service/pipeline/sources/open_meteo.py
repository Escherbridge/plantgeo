"""Open-Meteo Single Runs adapter: one bounded model-run pull, parsed into the lane's own shape.

Layer L3, the only module in this service that performs HTTP. Lifted from agri-data-service's
deleted `ingest/weather_forecast/open_meteo.py` (`c9c5256c`, fixes `a1b3a497`) with three
corrections recorded in `AGENTS.md`: the one-hour precipitation shift is gone, multi-location
pairing is VERIFIED by snapped coordinate rather than trusted by order, and the variable catalogue
is imported from `warehouse/weather_forecast.py` rather than restated here.
"""

from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import httpx

# Re-exported rather than re-declared: the parsing half moved out when this module passed the size
# ceiling, and every existing importer names these here.
from plantgeo_ml_service.pipeline.sources.open_meteo_parsing import (
    MAX_SNAP_DEGREES,
    OPEN_METEO_FORECAST_RUN_MODEL,
    WEATHER_FORECAST_COVERAGE,
    WEATHER_FORECAST_SOURCE_SLUG,
    OpenMeteoForecastRunModel,
    WeatherForecastLocation,
    WeatherForecastRun,
    WeatherForecastSample,
    forecast_run_id,
    paired_entry_indexes,
    parse_forecast_run,
    redact_credentials,
    wind_components,
)
from plantgeo_ml_service.pipeline.sources.protocol import (
    SourceBounds,
    SourceCoverage,
    SourcePayloadError,
    SourceRateLimitError,
    SourceRequestError,
    SourceTransportError,
    WeatherForecastSource,
)
from plantgeo_ml_service.warehouse.weather_forecast import UPSTREAM_VARIABLES

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

# --- Provider identity ---------------------------------------------------------------------------
# `single-runs-api` is a DIFFERENT host from `api.open-meteo.com` (which stitches the newest run
# into one rolling series and cannot pin a past initialization) and from `archive-api...` (settled
# reanalysis). Only this endpoint preserves one model's one initialization as an addressable object,
# which is what the `run_id` column exists to name. See `AGENTS.md`.
OPEN_METEO_SINGLE_RUN_BASE_URL: Final = "https://single-runs-api.open-meteo.com/v1/forecast"

#: `nearest`: a coastal request must never be silently relocated to a land cell nobody named.
WEATHER_FORECAST_CELL_SELECTION: Final = "nearest"

#: A run pull carries `forecast_days * 24 * len(variables)` hourly rows for up to 200 locations, so
#: it is a different shape from a current-conditions poll. Frozen at W8-E-PLAN section 2.
WEATHER_FORECAST_RUN_BOUNDS: Final = SourceBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=120.0)

#: The most locations one request may carry; a longer cell list is fetched in bounded batches.
MAX_FORECAST_RUN_LOCATIONS: Final = 200

MIN_FORECAST_RUN_DAYS: Final = 1
#: Documentation-sourced (open-meteo.com single-runs-api docs, read 2026-09-18), not live-probed.
MAX_FORECAST_RUN_DAYS: Final = 16

#: A published budget for the daily executor to schedule against. Nothing here owns a clock or a
#: call counter, so a stateless fetch adapter cannot enforce a per-day ceiling itself.
MAX_FORECAST_RUNS_PER_DAY: Final = 4

MIN_LATITUDE: Final = -90.0
MAX_LATITUDE: Final = 90.0
MIN_LONGITUDE: Final = -180.0
MAX_LONGITUDE: Final = 180.0

HTTP_TOO_MANY_REQUESTS: Final = 429

#: Least-retryable window first, so a body naming both a daily and a minutely quota classifies as
#: the one that takes longest to clear.
RATE_LIMIT_SCOPE_MARKERS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("day", ("daily", "day")),
    ("hour", ("hourly", "hour")),
    ("minute", ("minutely", "minute")),
)

_COORDINATE_DECIMALS: Final = 6


class OpenMeteoRateLimitError(SourceRateLimitError):
    """Raised ONCE on a 429; the caller persists a cooldown from the classified scope."""


def format_coordinate(value: float) -> str:
    """Render one coordinate for the query string: fixed precision, no trailing zeros, no exponent."""
    if not math.isfinite(value):
        raise SourceRequestError("a coordinate must be a finite number")
    rendered = f"{value:.{_COORDINATE_DECIMALS}f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def require_run_request(
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    forecast_days: int,
) -> None:
    """Refuse a request over budget rather than truncate it silently."""
    if not coordinates or len(coordinates) > MAX_FORECAST_RUN_LOCATIONS:
        raise SourceRequestError(
            f"a weather-forecast run request carries between one and {MAX_FORECAST_RUN_LOCATIONS} locations, "
            f"got {len(coordinates)}"
        )
    unreviewed = set(variables) - set(UPSTREAM_VARIABLES)
    if not variables or sorted(variables) != list(variables) or unreviewed:
        raise SourceRequestError(
            "weather-forecast run variables must be sorted, non-empty, and a subset of the reviewed catalogue "
            f"{UPSTREAM_VARIABLES}"
        )
    if not MIN_FORECAST_RUN_DAYS <= forecast_days <= MAX_FORECAST_RUN_DAYS:
        raise SourceRequestError(
            f"forecast_days must be between {MIN_FORECAST_RUN_DAYS} and {MAX_FORECAST_RUN_DAYS}, got {forecast_days}"
        )
    for latitude, longitude in coordinates:
        outside = (
            not math.isfinite(latitude)
            or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
            or not math.isfinite(longitude)
            or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
        )
        if outside:
            raise SourceRequestError("weather-forecast run coordinates are outside WGS 84 bounds")


def forecast_run_url(
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    model_init_time: datetime,
    forecast_days: int,
    *,
    model: OpenMeteoForecastRunModel = OPEN_METEO_FORECAST_RUN_MODEL,
) -> str:
    """Build the credential-free run URL, validating the whole fetch budget before any request.

    `run` is sent as `YYYY-MM-DDTHH:MM`, matching the probed request shape, so `model_init_time`
    must already be UTC and minute-aligned: `strftime` would otherwise truncate seconds in silence.
    """
    require_run_request(coordinates, variables, forecast_days)
    if model_init_time.utcoffset() is None:
        raise SourceRequestError("model_init_time must be timezone-aware")
    if model_init_time.second or model_init_time.microsecond:
        raise SourceRequestError("model_init_time must be minute-aligned; the endpoint renders no seconds")
    query = urlencode(
        {
            "latitude": ",".join(format_coordinate(latitude) for latitude, _ in coordinates),
            "longitude": ",".join(format_coordinate(longitude) for _, longitude in coordinates),
            "models": model,
            "run": model_init_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M"),
            "hourly": ",".join(variables),
            "forecast_days": str(forecast_days),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
            "cell_selection": WEATHER_FORECAST_CELL_SELECTION,
        },
        # The probed request carries literal commas and colons; keeping them unescaped makes the
        # recorded URL in a source receipt compare directly against the probe evidence.
        safe=",:",
    )
    return f"{OPEN_METEO_SINGLE_RUN_BASE_URL}?{query}"


@asynccontextmanager
async def upstream_client(bounds: SourceBounds) -> AsyncIterator[httpx.AsyncClient]:
    """Open one client whose every phase carries an EXPLICIT timeout, and close it on the way out."""
    timeout = httpx.Timeout(
        connect=bounds.timeout_seconds,
        read=bounds.timeout_seconds,
        write=bounds.timeout_seconds,
        pool=bounds.timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        yield client


async def fetch_forecast_run_bytes(
    client: httpx.AsyncClient,
    url: str,
    bounds: SourceBounds = WEATHER_FORECAST_RUN_BOUNDS,
) -> bytes:
    """Fetch one run response as raw bytes so the caller can checksum exactly what arrived.

    The body is read in chunks and REFUSED the moment it passes the byte ceiling, so an endpoint
    answering with something far larger than a run costs the ceiling rather than the whole object.
    A 429 raises `OpenMeteoRateLimitError` exactly once; nothing here retries a status code.
    """
    chunks: list[bytes] = []
    byte_count = 0
    try:
        async with client.stream("GET", url, timeout=bounds.timeout_seconds) as response:
            async for chunk in response.aiter_bytes():
                byte_count += len(chunk)
                if byte_count > bounds.max_bytes:
                    raise SourceTransportError(
                        f"the Open-Meteo run response passed its {bounds.max_bytes}-byte ceiling"
                    )
                chunks.append(chunk)
            status = response.status_code
            content_type = response.headers.get("content-type")
    except httpx.HTTPError as error:
        raise SourceTransportError(f"the Open-Meteo run fetch failed: {error}") from error
    payload = b"".join(chunks)
    if status == HTTP_TOO_MANY_REQUESTS:
        raise OpenMeteoRateLimitError(*rate_limit_scope(payload))
    if status >= httpx.codes.BAD_REQUEST:
        raise SourceTransportError(f"the Open-Meteo run request answered HTTP {status}")
    if content_type is not None and "json" not in content_type.lower():
        raise SourcePayloadError("the Open-Meteo run response was not JSON")
    return payload


def rate_limit_scope(payload: bytes) -> tuple[str, str]:
    """Classify a 429 body into the quota window a caller must wait out; unrecognised stays `unknown`."""
    reason = ""
    try:
        parsed = json.loads(payload.decode("utf-8", errors="replace"))
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        raw_reason = parsed.get("reason")
        reason = raw_reason if isinstance(raw_reason, str) else ""
    lowered = reason.lower()
    for scope, markers in RATE_LIMIT_SCOPE_MARKERS:
        if any(marker in lowered for marker in markers):
            return scope, reason or "rate limited"
    return "unknown", reason or "rate limited"


async def fetch_forecast_run(
    *,
    model_init_time: datetime,
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str] = UPSTREAM_VARIABLES,
    forecast_days: int = MAX_FORECAST_RUN_DAYS,
    model: OpenMeteoForecastRunModel = OPEN_METEO_FORECAST_RUN_MODEL,
) -> WeatherForecastRun:
    """Build the bounded URL, fetch it ONCE under the run bounds, and parse what came back."""
    url = forecast_run_url(coordinates, variables, model_init_time, forecast_days, model=model)
    async with upstream_client(WEATHER_FORECAST_RUN_BOUNDS) as client:
        payload = await fetch_forecast_run_bytes(client, url)
    return parse_forecast_run(
        payload,
        coordinates=coordinates,
        variables=variables,
        model=model,
        model_init_time=model_init_time,
        fetched_at=datetime.now(tz=UTC),
        request_url=url,
    )


@dataclass(frozen=True, slots=True)
class OpenMeteoWeatherForecastSource:
    """The pilot binding: Open-Meteo's Single Runs endpoint, GFS global, global coverage.

    A VALUE rather than bare module functions, so a region manifest can resolve to it and an
    `isinstance` check against `WeatherForecastSource` is a real conformance test.
    """

    source_slug: str = WEATHER_FORECAST_SOURCE_SLUG
    coverage: SourceCoverage = WEATHER_FORECAST_COVERAGE
    max_locations_per_request: int = MAX_FORECAST_RUN_LOCATIONS

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str] = UPSTREAM_VARIABLES,
        forecast_days: int = MAX_FORECAST_RUN_DAYS,
    ) -> WeatherForecastRun:
        """Fetch one bounded run; see `fetch_forecast_run`, which this forwards to unchanged."""
        return await fetch_forecast_run(
            model_init_time=model_init_time,
            coordinates=coordinates,
            variables=variables,
            forecast_days=forecast_days,
        )


#: The single instance the daily lane resolves its `open-meteo` binding to.
OPEN_METEO_WEATHER_FORECAST_SOURCE: Final[WeatherForecastSource] = OpenMeteoWeatherForecastSource()


__all__ = [
    "MAX_FORECAST_RUNS_PER_DAY",
    "MAX_FORECAST_RUN_DAYS",
    "MAX_FORECAST_RUN_LOCATIONS",
    "MAX_SNAP_DEGREES",
    "MIN_FORECAST_RUN_DAYS",
    "OPEN_METEO_FORECAST_RUN_MODEL",
    "OPEN_METEO_SINGLE_RUN_BASE_URL",
    "OPEN_METEO_WEATHER_FORECAST_SOURCE",
    "WEATHER_FORECAST_COVERAGE",
    "WEATHER_FORECAST_RUN_BOUNDS",
    "WEATHER_FORECAST_SOURCE_SLUG",
    "OpenMeteoForecastRunModel",
    "OpenMeteoRateLimitError",
    "OpenMeteoWeatherForecastSource",
    "WeatherForecastLocation",
    "WeatherForecastRun",
    "WeatherForecastSample",
    "fetch_forecast_run",
    "fetch_forecast_run_bytes",
    "forecast_run_id",
    "forecast_run_url",
    "paired_entry_indexes",
    "parse_forecast_run",
    "redact_credentials",
    "wind_components",
]
