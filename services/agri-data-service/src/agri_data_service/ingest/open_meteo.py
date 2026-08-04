"""Open-Meteo current-conditions ingestion: the point adapter and the densified sample grid that feeds it."""

from __future__ import annotations

import asyncio
import math
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import structlog

from agri_data_service.ingest.http import UpstreamBounds, UpstreamPayloadError, fetch_bounded_json, upstream_client
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_weather_observation_identity,
    format_javascript_timestamp,
)
from agri_data_service.ingest.policy import (
    MAX_LATITUDE,
    MAX_LONGITUDE,
    MAX_WEATHER_SAMPLE_POINTS,
    MIN_LATITUDE,
    MIN_LONGITUDE,
    UNCONFIGURED_BBOX_REASON,
    format_javascript_number,
    is_fresh_observation,
    parse_bbox,
    resolve_bounded_bbox,
    resolve_weather_sample_spacing_degrees,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

OPEN_METEO_SOURCE: Final = "open-meteo"
OPEN_METEO_CHANNEL: Final = "layer:weather-observations"
OPEN_METEO_PROPERTY_SOURCE: Final = "Open-Meteo"
WEATHER_LAYER_VARIABLE: Final = "WEATHER_LAYER_ID"
DEFAULT_WEATHER_LAYER_NAME: Final = "weather-observations"

OPEN_METEO_BASE_URL: Final = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_BOUNDS: Final = UpstreamBounds(max_bytes=128 * 1024, timeout_seconds=5.0)
MAX_OBSERVATION_AGE: Final = timedelta(hours=3)

CURRENT_FIELDS: Final = "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation"

GRID_CELL_CENTRE_OFFSET: Final = 0.5
MIN_SPACING_GROWTH_FACTOR: Final = 1.01

# (upstream field, stored field, minimum, maximum) for every observation value Open-Meteo must supply.
CURRENT_VALUE_BOUNDS: Final = (
    ("temperature_2m", "temperature", -100.0, 70.0),
    ("relative_humidity_2m", "humidity", 0.0, 100.0),
    ("wind_speed_10m", "windSpeed", 0.0, 150.0),
    ("wind_direction_10m", "windDirection", 0.0, 360.0),
    ("precipitation", "precipitation", 0.0, 1_000.0),
)


def resolve_weather_layer_name() -> str:
    """Read WEATHER_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(WEATHER_LAYER_VARIABLE, "").strip() or DEFAULT_WEATHER_LAYER_NAME


def bounded_sample_points(
    bbox: str,
    spacing_degrees: float,
    max_points: int = MAX_WEATHER_SAMPLE_POINTS,
) -> list[tuple[float, float]]:
    """Sample centred grid points inside a bbox, growing the spacing until the grid fits rather than slicing it."""
    west, south, east, north = parse_bbox(bbox)
    longitude_extent = east - west
    latitude_extent = north - south

    spacing = spacing_degrees
    columns = max(1, math.ceil(longitude_extent / spacing))
    rows = max(1, math.ceil(latitude_extent / spacing))
    while columns * rows > max_points:
        spacing *= max(math.sqrt((columns * rows) / max_points), MIN_SPACING_GROWTH_FACTOR)
        columns = max(1, math.ceil(longitude_extent / spacing))
        rows = max(1, math.ceil(latitude_extent / spacing))

    return [
        (
            south + (latitude_extent * (row + GRID_CELL_CENTRE_OFFSET)) / rows,
            west + (longitude_extent * (column + GRID_CELL_CENTRE_OFFSET)) / columns,
        )
        for column in range(columns)
        for row in range(rows)
    ]


def current_weather_url(latitude: float, longitude: float) -> str:
    """Build the Open-Meteo current-conditions URL for one sample point."""
    query = urlencode(
        {
            "latitude": format_javascript_number(latitude),
            "longitude": format_javascript_number(longitude),
            "current": CURRENT_FIELDS,
            "wind_speed_unit": "ms",
            "timeformat": "unixtime",
            "timezone": "GMT",
        }
    )
    return f"{OPEN_METEO_BASE_URL}?{query}"


def _bounded_value(current: Mapping[str, object], field_name: str, minimum: float, maximum: float) -> float:
    """Return one validated observation value, rejecting a non-finite or out-of-range reading."""
    value = current.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")
    numeric = float(value)
    if not math.isfinite(numeric) or not minimum <= numeric <= maximum:
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")
    return numeric


def parse_current_weather(payload: object, now: datetime | None = None) -> dict[str, object]:
    """Validate one Open-Meteo response into the observation record the warehouse stores."""
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")
    current = payload.get("current")
    if not isinstance(current, dict):
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")

    observation_time = current.get("time")
    if isinstance(observation_time, bool) or not isinstance(observation_time, int) or observation_time < 0:
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")

    observed_at = datetime.fromtimestamp(observation_time, UTC)
    if not is_fresh_observation(observed_at, MAX_OBSERVATION_AGE, now):
        raise UpstreamPayloadError("Open-Meteo returned a stale current observation")

    observation: dict[str, object] = {"observedAt": format_javascript_timestamp(observed_at)}
    for upstream_field, stored_field, minimum, maximum in CURRENT_VALUE_BOUNDS:
        observation[stored_field] = _bounded_value(current, upstream_field, minimum, maximum)
    return observation


async def get_current_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    now: datetime | None = None,
) -> dict[str, object]:
    """Fetch and validate current conditions for one WGS84 point."""
    if (
        not math.isfinite(latitude)
        or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
        or not math.isfinite(longitude)
        or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
    ):
        raise ValueError("weather coordinates are outside WGS84 bounds")
    payload = await fetch_bounded_json(client, current_weather_url(latitude, longitude), OPEN_METEO_BOUNDS)
    return parse_current_weather(payload, now)


def build_weather_write(
    latitude: float,
    longitude: float,
    observation: Mapping[str, object],
    layer_name: str,
) -> FeatureWrite | None:
    """Build one observation's write, returning None when the upstream supplied no instant to key it by."""
    try:
        identity = build_weather_observation_identity(latitude, longitude, observation)
    except (MissingNativeKeyError, ValueError):
        return None
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            **observation,
            "source": OPEN_METEO_PROPERTY_SOURCE,
            "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        },
        channel=OPEN_METEO_CHANNEL,
    )


async def run_weather_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Fetch current weather for a bounded sample grid and write the points that answered."""
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(OPEN_METEO_SOURCE, UNCONFIGURED_BBOX_REASON)

    points = bounded_sample_points(area, resolve_weather_sample_spacing_degrees())
    if client is None:
        async with upstream_client(OPEN_METEO_BOUNDS) as owned_client:
            observations = await _gather_observations(owned_client, points, now)
    else:
        observations = await _gather_observations(client, points, now)

    layer_name = resolve_weather_layer_name()
    writes: list[FeatureWrite] = []
    unavailable_points = 0
    for (latitude, longitude), observation in zip(points, observations, strict=True):
        if isinstance(observation, BaseException):
            unavailable_points += 1
            continue
        write = build_weather_write(latitude, longitude, observation, layer_name)
        if write is None:
            unavailable_points += 1
            continue
        writes.append(write)
    if unavailable_points:
        logger.info("weather_sample_points_unavailable", points=unavailable_points, sampled=len(points))

    return IngestionJobResult(
        source=OPEN_METEO_SOURCE,
        status="ingested",
        records_seen=len(points),
        records_written=await write_features(writes),
        details={"unavailable_points": unavailable_points},
    )


async def _gather_observations(
    client: httpx.AsyncClient,
    points: list[tuple[float, float]],
    now: datetime | None,
) -> list[dict[str, object] | BaseException]:
    """Fetch every sample point, keeping one point's failure from discarding the rest of the grid."""
    return await asyncio.gather(
        *(get_current_weather(client, latitude, longitude, now) for latitude, longitude in points),
        return_exceptions=True,
    )
