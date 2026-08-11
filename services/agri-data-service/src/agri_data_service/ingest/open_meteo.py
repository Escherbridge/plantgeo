"""Open-Meteo adapters: the current-conditions point sampler and the ERA5-Land archive reader."""

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlencode

import structlog

from agri_data_service.ingest.http import (
    HTTP_TOO_MANY_REQUESTS,
    UpstreamBounds,
    UpstreamError,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded,
    fetch_bounded_json,
    upstream_client,
)
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_weather_observation_identity,
    format_javascript_timestamp,
)
from agri_data_service.ingest.layer_binding import LayerBinding
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
from agri_data_service.ingest.source import HistoryCapability
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

OPEN_METEO_SOURCE: Final = "open-meteo"
OPEN_METEO_PROPERTY_SOURCE: Final = "Open-Meteo"

WEATHER_LAYER: Final = LayerBinding(
    variable="WEATHER_LAYER_ID",
    default="weather-observations",
    channel="layer:weather-observations",
)
OPEN_METEO_CHANNEL: Final = WEATHER_LAYER.channel
WEATHER_LAYER_VARIABLE: Final = WEATHER_LAYER.variable
DEFAULT_WEATHER_LAYER_NAME: Final = WEATHER_LAYER.default

OPEN_METEO_BASE_URL: Final = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_BOUNDS: Final = UpstreamBounds(max_bytes=128 * 1024, timeout_seconds=5.0)
MAX_OBSERVATION_AGE: Final = timedelta(hours=3)

# The forecast endpoint's documented `past_days` maximum (open-meteo.com/en/docs, read 2026-08-10;
# documentation-sourced, NOT live-probed). It is the deepest window this LAYER's product reaches --
# the ERA5 archive below goes to 1940 but is a different product. See ingest/AGENTS.md
# "history declarations, wave 2026-08-10".
OPEN_METEO_FORECAST_PAST_DAYS_MAXIMUM: Final = 92
OPEN_METEO_FORECAST_HISTORY_RETENTION: Final = timedelta(days=OPEN_METEO_FORECAST_PAST_DAYS_MAXIMUM)

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


# --- ERA5-Land archive endpoint -------------------------------------------------------------
# A separate endpoint from the forecast one above, with its own byte and time budget: an archive
# request carries years of daily rows for many locations, not one current observation.
# See ingest/AGENTS.md and execution/AGENTS.md §historical_open_meteo.
OPEN_METEO_ARCHIVE_BASE_URL: Final = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_ARCHIVE_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=300.0)

# The paid tier is a different host plus one query parameter. The key is read from the environment
# at call time and belongs to no plan, checkpoint, cache receipt, log line, or warehouse row.
# See ingest/AGENTS.md §"open_meteo.py: the paid archive host is an environment fact".
OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL: Final = "https://customer-archive-api.open-meteo.com/v1/archive"
OPEN_METEO_API_KEY_VARIABLE: Final = "OPEN_METEO_API_KEY"
OPEN_METEO_API_KEY_PARAMETER: Final = "apikey"

# A closed set, so a host can be carried through provenance and validated on the way back in.
OpenMeteoArchiveBaseUrl = Literal[
    "https://archive-api.open-meteo.com/v1/archive",
    "https://customer-archive-api.open-meteo.com/v1/archive",
]

# `models` MUST be sent. The endpoint's default is `era5` at 0.25 degrees; only this value
# selects the 0.1-degree ERA5-Land product whose layers match the CDS variable definitions.
OPEN_METEO_ERA5_LAND_MODEL: Final = "era5_land"

# The 0.25-degree parent reanalysis. It carries strictly more variables than ERA5-Land -- notably
# every radiation flux -- at a coarser lattice. See ingest/AGENTS.md §"the archive model is an
# argument, not a constant".
OPEN_METEO_ERA5_MODEL: Final = "era5"

# A closed set, because the model decides which variables come back with values at all, and a
# caller that does not state one is choosing by accident.
OpenMeteoArchiveModel = Literal["era5_land", "era5"]

# `nearest` is pinned rather than left to the default so a coastal request can never be silently
# relocated to a land cell that is not the one the analysis lattice names.
OPEN_METEO_ARCHIVE_CELL_SELECTION: Final = "nearest"

MAX_ARCHIVE_LOCATIONS_PER_REQUEST: Final = 200


class OpenMeteoRateLimitError(UpstreamError):
    """Raised when Open-Meteo refuses a request for quota reasons; the scope drives the backoff."""

    def __init__(self, scope: str, reason: str) -> None:
        """Record which quota window was exhausted and the provider's own wording."""
        super().__init__(f"Open-Meteo refused the request: {reason}")
        self.scope = scope
        self.reason = reason


def resolve_weather_layer_name() -> str:
    """Read WEATHER_LAYER_ID at call time so a cron environment change needs no restart."""
    return WEATHER_LAYER.resolve()


def weather_history_capability(now: datetime | None = None) -> HistoryCapability:
    """State the rolling past window the forecast endpoint serves; the floor moves, so it is resolved per call."""
    moment = now if now is not None else datetime.now(UTC)
    return HistoryCapability(supported=True, earliest=moment - OPEN_METEO_FORECAST_HISTORY_RETENTION)


@dataclass(frozen=True, slots=True)
class ArchiveDailyRequest:
    """One resolved archive request: the host provenance records, and the URL actually sent."""

    base_url: OpenMeteoArchiveBaseUrl
    # Carries the paid-tier credential when one is configured. Never persist, log, or checksum it.
    request_url: str


def resolve_open_meteo_api_key() -> str | None:
    """Read OPEN_METEO_API_KEY at call time; absent is the free tier, which is not an error."""
    return os.environ.get(OPEN_METEO_API_KEY_VARIABLE, "").strip() or None


def open_meteo_archive_base_url() -> OpenMeteoArchiveBaseUrl:
    """Return the archive host this process will really call: customer when a key is set, free otherwise."""
    return _archive_base_url(resolve_open_meteo_api_key())


def require_archive_base_url(value: str) -> OpenMeteoArchiveBaseUrl:
    """Accept only a reviewed archive host, so a tampered local receipt cannot be persisted as provenance."""
    if value == OPEN_METEO_ARCHIVE_BASE_URL:
        return OPEN_METEO_ARCHIVE_BASE_URL
    if value == OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL:
        return OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL
    raise ValueError("Open-Meteo archive base URL is not a reviewed endpoint")


def archive_daily_url(  # noqa: PLR0913 - one argument per governed query parameter, none defaultable
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    model: OpenMeteoArchiveModel,
    base_url: str | None = None,
) -> str:
    """Build the credential-free archive URL, whose parameter order is stable for a checksum.

    Safe to persist, and structurally unable to carry a key. `base_url` defaults to the host this
    process would call now; pass the host a past retrieval really used when recording provenance
    for cached bytes. `model` has no default on purpose -- see ingest/AGENTS.md.
    """
    resolved = open_meteo_archive_base_url() if base_url is None else require_archive_base_url(base_url)
    parameters = _archive_daily_parameters(coordinates, daily_variables, start_date, end_date, model)
    return f"{resolved}?{urlencode(parameters)}"


def archive_daily_request(
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    *,
    model: OpenMeteoArchiveModel,
) -> ArchiveDailyRequest:
    """Resolve the credential once, returning the host to record and the credentialed URL to send.

    The key is appended after every governed parameter, so the keyless URL never shifts.
    """
    api_key = resolve_open_meteo_api_key()
    base_url = _archive_base_url(api_key)
    parameters = _archive_daily_parameters(coordinates, daily_variables, start_date, end_date, model)
    if api_key is not None:
        parameters[OPEN_METEO_API_KEY_PARAMETER] = api_key
    return ArchiveDailyRequest(base_url=base_url, request_url=f"{base_url}?{urlencode(parameters)}")


def _archive_base_url(api_key: str | None) -> OpenMeteoArchiveBaseUrl:
    return OPEN_METEO_ARCHIVE_BASE_URL if api_key is None else OPEN_METEO_ARCHIVE_CUSTOMER_BASE_URL


def _archive_daily_parameters(
    coordinates: Sequence[tuple[float, float]],
    daily_variables: Sequence[str],
    start_date: date,
    end_date: date,
    model: OpenMeteoArchiveModel,
) -> dict[str, str]:
    """Validate one multi-location archive request and order its governed parameters for a checksum."""
    if not coordinates or len(coordinates) > MAX_ARCHIVE_LOCATIONS_PER_REQUEST:
        raise ValueError("Open-Meteo archive requests must carry between one and 200 locations")
    if not daily_variables or sorted(daily_variables) != list(daily_variables):
        raise ValueError("Open-Meteo archive daily variables must be sorted and non-empty")
    if end_date < start_date:
        raise ValueError("Open-Meteo archive end_date must not precede start_date")
    for latitude, longitude in coordinates:
        if (
            not math.isfinite(latitude)
            or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
            or not math.isfinite(longitude)
            or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
        ):
            raise ValueError("Open-Meteo archive coordinates are outside WGS84 bounds")
    return {
        "latitude": ",".join(format_javascript_number(latitude) for latitude, _ in coordinates),
        "longitude": ",".join(format_javascript_number(longitude) for _, longitude in coordinates),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": ",".join(daily_variables),
        "models": model,
        "timezone": "GMT",
        "cell_selection": OPEN_METEO_ARCHIVE_CELL_SELECTION,
    }


async def fetch_archive_daily(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one archive response as raw text so the caller can checksum exactly what arrived."""
    response = await fetch_bounded(client, url, OPEN_METEO_ARCHIVE_BOUNDS)
    if response.status == HTTP_TOO_MANY_REQUESTS:
        raise OpenMeteoRateLimitError(*_rate_limit_scope(response.text))
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("Open-Meteo archive response was not JSON")
    return response.text


# The provider's own adjectives, in LEAST-retryable-first order so an ambiguous body
# ("Daily API request limit exceeded. Please try again in 60 minutes.") resolves to `day`, not
# `minute`. Measured against live 429 bodies on 2026-08-06; see ingest/AGENTS.md.
RATE_LIMIT_SCOPE_MARKERS: Final = (
    ("day", ("daily", "day")),
    ("hour", ("hourly", "hour")),
    ("minute", ("minutely", "minute")),
)


def _rate_limit_scope(body: str) -> tuple[str, str]:
    """Classify a 429 body into a quota window; an unrecognised body stays `unknown`, never `minute`."""
    reason = ""
    try:
        parsed = json.loads(body)
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
