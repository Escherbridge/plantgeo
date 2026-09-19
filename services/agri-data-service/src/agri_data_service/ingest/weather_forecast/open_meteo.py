"""Open-Meteo Single Runs adapter: one bounded model-run pull, parsed into the layer's own shape.

See `AGENTS.md` in this directory for the normalisation decisions this module makes at the source
boundary (`federation.md` §2) and for what remains an unverified assumption pending a live probe.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlencode

from agri_data_service.foundation.region.source_coverage import GLOBAL_SOURCE_COVERAGE
from agri_data_service.ingest.http import (
    HTTP_TOO_MANY_REQUESTS,
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded,
    upstream_client,
)
from agri_data_service.ingest.open_meteo import (
    MAX_ARCHIVE_LOCATIONS_PER_REQUEST,
    RATE_LIMIT_SCOPE_MARKERS,
    OpenMeteoRateLimitError,
)
from agri_data_service.ingest.policy import (
    MAX_LATITUDE,
    MAX_LONGITUDE,
    MIN_LATITUDE,
    MIN_LONGITUDE,
    format_javascript_number,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.foundation.region.source_coverage import SourceCoverageClaim

# --- Provider identity ------------------------------------------------------------------------
# The single named constant that carries this endpoint's URL, per this directory's AGENTS.md "one
# named constant, never a literal in a query builder". `single-runs-api` is a DIFFERENT host from
# `api.open-meteo.com` (the current-conditions poller, `ingest/open_meteo.py:62`) and from
# `archive-api.open-meteo.com` (the ERA5 archive, `ingest/open_meteo.py:92`): it is the one endpoint
# that preserves an individual model INITIALIZATION rather than stitching the newest run into one
# rolling operational series (`conductor/tracks/weather_forecast_parquet_lane_20260911/evidence/
# source-admission.md`, "Real-source investigation"). See AGENTS.md "Why single-runs-api".
OPEN_METEO_SINGLE_RUN_BASE_URL: Final = "https://single-runs-api.open-meteo.com/v1/forecast"

WEATHER_FORECAST_SOURCE_SLUG: Final = "open-meteo"
WEATHER_FORECAST_COVERAGE: Final[SourceCoverageClaim] = GLOBAL_SOURCE_COVERAGE

# The only model this branch has read-only probed (`source-admission.md`'s frozen probe,
# `2026-09-12`, `(40,-105)`, `run=2026-09-11T00:00`). A second model is a second reviewed literal,
# never a caller-supplied string -- see `ingest/open_meteo.py`'s `OpenMeteoArchiveModel` for the
# same closed-set convention on the archive endpoint.
OPEN_METEO_FORECAST_RUN_MODEL: Final = "gfs_global"
OpenMeteoForecastRunModel = Literal["gfs_global"]

# `nearest`, for the same reason `ingest/open_meteo.py:123`'s archive endpoint pins it: a coastal
# request must never be silently relocated to a land cell the caller never named.
WEATHER_FORECAST_CELL_SELECTION: Final = "nearest"

# A run pull is a different shape from the 128 KiB/5 s current-conditions poll
# (`ingest/open_meteo.py:63`): it carries `forecast_days * 24 * len(variables)` hourly rows for up
# to 200 locations in one response. Frozen at `.omc/ultrapilot-20260918/W8-E-PLAN.md` §2, "Bounded
# fetch budget (S2)".
WEATHER_FORECAST_RUN_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024 * 1024, timeout_seconds=120.0)

# Reused, not redeclared: the archive endpoint already measured and named this ceiling
# (`ingest/open_meteo.py:126`), and a run pull shares the same "one location list, one request"
# shape. A caller over budget is REFUSED, per the plan's acceptance -- see `_require_run_request`.
MAX_FORECAST_RUN_LOCATIONS: Final = MAX_ARCHIVE_LOCATIONS_PER_REQUEST

# `single-runs-api` documents the same `forecast_days` ceiling as the standard forecast endpoint
# (open-meteo.com/en/docs/single-runs-api, read 2026-09-18; documentation-sourced, NOT live-probed
# -- see AGENTS.md "Unverified against a live multi-day, multi-location response").
MIN_FORECAST_RUN_DAYS: Final = 1
MAX_FORECAST_RUN_DAYS: Final = 16

# "one run per invocation, 4 runs/day maximum" (`.omc/ultrapilot-20260918/W8-E-PLAN.md` §2, "Bounded
# fetch budget (S2)"). This module enforces "one run per invocation" structurally --
# `fetch_forecast_run` takes exactly one `model_init_time` -- and states the daily ceiling as a
# published budget for the S3 executor to schedule against; nothing in `ingest/` owns a clock or a
# call counter, so the ceiling cannot be enforced here without one.
MAX_FORECAST_RUNS_PER_DAY: Final = 4

# The six variables Open-Meteo publishes for this run; `wind_u_10m`/`wind_v_10m`/`wind_speed_10m`
# beyond `wind_speed_10m`/`wind_direction_10m` are DERIVED below, never requested from the provider
# (AGENTS.md "Wind: derived from speed/direction"). Sorted, matching the archive endpoint's own
# "daily variables must be sorted" contract (`ingest/open_meteo.py:231`).
WEATHER_FORECAST_UPSTREAM_VARIABLES: Final[tuple[str, ...]] = (
    "cloud_cover",
    "precipitation",
    "relative_humidity_2m",
    "temperature_2m",
    "wind_direction_10m",
    "wind_speed_10m",
)

# Plausible-value bounds for exactly the six upstream fields, duplicated from (and must stay equal
# to) `warehouse/schemas/weather_forecast.py`'s `VARIABLES` table. Duplicated deliberately: `ingest/`
# is not a layer `warehouse` is visible to import back from, matching `CURRENT_VALUE_BOUNDS`
# (`ingest/open_meteo.py:79-85`), which duplicates the same convention for the current-conditions
# poller rather than importing anything from `warehouse`.
WEATHER_FORECAST_UPSTREAM_VALUE_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "temperature_2m": (-100.0, 70.0),
    "relative_humidity_2m": (0.0, 100.0),
    "cloud_cover": (0.0, 100.0),
    "precipitation": (0.0, 1_000.0),
    "wind_speed_10m": (0.0, 150.0),
    "wind_direction_10m": (0.0, 360.0),
}

# `(variable, unit, statistic)` for every row this module ever emits -- the four upstream-bound
# variables plus the two wind values this module derives. Must stay equal to
# `warehouse/schemas/weather_forecast.py`'s `VARIABLES` table (S1); duplicated for the same reason
# as the bounds above.
WEATHER_FORECAST_UNIT_STATISTIC: Final[dict[str, tuple[str, str]]] = {
    "temperature_2m": ("degC", "instantaneous"),
    "relative_humidity_2m": ("%", "instantaneous"),
    "cloud_cover": ("%", "instantaneous"),
    "precipitation": ("mm", "sum_over_following_hour"),
    "wind_u_10m": ("m/s", "instantaneous_earth_relative"),
    "wind_v_10m": ("m/s", "instantaneous_earth_relative"),
    "wind_speed_10m": ("m/s", "derived_vector_magnitude"),
    "wind_direction_10m": ("degree", "meteorological_from_true_north"),
}

WEATHER_FORECAST_SUPPORT: Final = "sampled_point"
NOT_GENERATED: Final = "not_generated"

_ISO_HOUR_LENGTH: Final = len("2026-09-11T00:00")


class WeatherForecastRequestError(ValueError):
    """Raised when a run request would exceed the source's declared bounded-fetch budget."""


@dataclass(frozen=True, slots=True)
class WeatherForecastSample:
    """One variable's reading at one valid instant, at one requested location. See `source_protocol.py`."""

    valid_time: datetime
    interval_start: datetime | None
    interval_end: datetime | None
    variable: str
    value: float | None
    unit: str
    statistic: str
    support: str
    missing_reason: str | None


@dataclass(frozen=True, slots=True)
class WeatherForecastLocation:
    """One requested point's full sample series for a run."""

    latitude: float
    longitude: float
    samples: tuple[WeatherForecastSample, ...]


@dataclass(frozen=True, slots=True)
class WeatherForecastRun:
    """One admitted NWP model run, in exactly the shape `WeatherForecastRunPayload` reads."""

    run_id: str
    model_init_time: datetime
    provider_issue_time: datetime | None
    fetched_at: datetime
    locations: tuple[WeatherForecastLocation, ...]


def forecast_run_id(model: OpenMeteoForecastRunModel, model_init_time: datetime) -> str:
    """`<provider>:<model>:<init_time ISO>`, the format frozen in `warehouse/schemas/weather_forecast.py:73`."""
    return f"{WEATHER_FORECAST_SOURCE_SLUG}:{model}:{model_init_time.isoformat()}"


def _require_run_request(
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    forecast_days: int,
) -> None:
    """Refuse a request over budget rather than truncate it silently.

    `.omc/ultrapilot-20260918/W8-E-PLAN.md` §2 row S2: "refuses over budget rather than truncating
    silently" is the acceptance criterion this function exists to satisfy.
    """
    if not coordinates or len(coordinates) > MAX_FORECAST_RUN_LOCATIONS:
        raise WeatherForecastRequestError(
            f"weather-forecast run requests must carry between one and {MAX_FORECAST_RUN_LOCATIONS} locations"
        )
    unreviewed_variables = set(variables) - set(WEATHER_FORECAST_UPSTREAM_VARIABLES)
    if not variables or sorted(variables) != list(variables) or unreviewed_variables:
        raise WeatherForecastRequestError(
            "weather-forecast run variables must be sorted, non-empty, and a subset of the reviewed catalogue"
        )
    if not MIN_FORECAST_RUN_DAYS <= forecast_days <= MAX_FORECAST_RUN_DAYS:
        raise WeatherForecastRequestError(
            f"weather-forecast forecast_days must be between {MIN_FORECAST_RUN_DAYS} and {MAX_FORECAST_RUN_DAYS}"
        )
    for latitude, longitude in coordinates:
        if (
            not math.isfinite(latitude)
            or not MIN_LATITUDE <= latitude <= MAX_LATITUDE
            or not math.isfinite(longitude)
            or not MIN_LONGITUDE <= longitude <= MAX_LONGITUDE
        ):
            raise WeatherForecastRequestError("weather-forecast run coordinates are outside WGS84 bounds")


def forecast_run_url(
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    model_init_time: datetime,
    forecast_days: int,
    *,
    model: OpenMeteoForecastRunModel = OPEN_METEO_FORECAST_RUN_MODEL,
) -> str:
    """Build the credential-free run URL; validates the whole bounded-fetch budget before any request.

    `run` is sent as `YYYY-MM-DDTHH:MM` (no seconds, no offset) matching the probed request shape
    (`source-admission.md`, "requested ... run=2026-09-11T00:00"); `model_init_time` must therefore
    already be UTC and minute-aligned -- a caller with seconds gets them silently truncated by
    `strftime`, which is why `AGENTS.md` states the caller floors to the minute before calling this.
    """
    _require_run_request(coordinates, variables, forecast_days)
    if model_init_time.utcoffset() is None:
        raise WeatherForecastRequestError("weather-forecast model_init_time must be timezone-aware")
    query = urlencode(
        {
            "latitude": ",".join(format_javascript_number(latitude) for latitude, _ in coordinates),
            "longitude": ",".join(format_javascript_number(longitude) for _, longitude in coordinates),
            "models": model,
            "run": model_init_time.strftime("%Y-%m-%dT%H:%M"),
            "hourly": ",".join(variables),
            "forecast_days": str(forecast_days),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
            "cell_selection": WEATHER_FORECAST_CELL_SELECTION,
        }
    )
    return f"{OPEN_METEO_SINGLE_RUN_BASE_URL}?{query}"


def _rate_limit_scope(body: str) -> tuple[str, str]:
    """Classify a 429 body into a quota window, reusing the current-conditions endpoint's own table.

    Same classification as `ingest/open_meteo.py:272`'s `_rate_limit_scope` (least-retryable-first
    marker order); duplicated rather than imported because that function is module-private and this
    is a different endpoint's response shape, not a call into the same one.
    """
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


async def fetch_forecast_run_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch one run response as raw text so the caller can checksum exactly what arrived.

    Mirrors `ingest/open_meteo.py::fetch_archive_daily`'s contract exactly: a 429 raises
    `OpenMeteoRateLimitError` ONCE, with no retry loop around it (the plan's S2 acceptance); the
    only retrying that happens at all is `fetch_bounded`'s own transport-fault retry, which never
    retries a status code.
    """
    response = await fetch_bounded(client, url, WEATHER_FORECAST_RUN_BOUNDS)
    if response.status == HTTP_TOO_MANY_REQUESTS:
        raise OpenMeteoRateLimitError(*_rate_limit_scope(response.text))
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("Open-Meteo run response was not JSON")
    return response.text


def _parse_hour(raw_time: object) -> datetime:
    """Parse one `hourly.time` entry (`"YYYY-MM-DDTHH:MM"`, UTC per the `timezone=UTC` request parameter)."""
    if not isinstance(raw_time, str) or len(raw_time) != _ISO_HOUR_LENGTH:
        raise UpstreamPayloadError("Open-Meteo run response carried an unexpected hourly time entry")
    try:
        return datetime.fromisoformat(raw_time).replace(tzinfo=UTC)
    except ValueError as error:
        raise UpstreamPayloadError("Open-Meteo run response carried an unparseable hourly time entry") from error


def _bounded_series(hourly: Mapping[str, object], field_name: str, expected_length: int) -> list[float | None]:
    """Read one upstream hourly field, validating its physical range and rejecting a misaligned series.

    A `None` entry (the provider's own "not generated this hour" answer) survives as `None`; a
    non-numeric, non-null entry is a payload error, never silently dropped.
    """
    raw = hourly.get(field_name)
    if not isinstance(raw, list) or len(raw) != expected_length:
        raise UpstreamPayloadError(f"Open-Meteo run response's {field_name} does not align with its time axis")
    minimum, maximum = WEATHER_FORECAST_UPSTREAM_VALUE_BOUNDS[field_name]
    values: list[float | None] = []
    for entry in raw:
        if entry is None:
            values.append(None)
            continue
        if isinstance(entry, bool) or not isinstance(entry, int | float) or not math.isfinite(entry):
            raise UpstreamPayloadError(f"Open-Meteo run response's {field_name} carried a non-numeric value")
        numeric = float(entry)
        if not minimum <= numeric <= maximum:
            raise UpstreamPayloadError(f"Open-Meteo run response's {field_name} carried a value outside its range")
        values.append(numeric)
    return values


def _wind_components(speed: float, direction_degrees: float) -> tuple[float, float]:
    """Earth-relative 10 m u/v from a meteorological (from-true-north) speed/direction pair.

    `u=-speed*sin(direction)`, `v=-speed*cos(direction)` -- see AGENTS.md "Wind: derived from
    speed/direction" for the derivation and its one prior citation
    (`conductor/tracks/weather_forecast_parquet_lane_20260911/evidence/source-admission.md`,
    "Frozen synthetic fixture contract", row "Wind").
    """
    radians = math.radians(direction_degrees)
    return -speed * math.sin(radians), -speed * math.cos(radians)


def _instantaneous_sample(
    valid_time: datetime,
    variable: str,
    value: float | None,
    *,
    missing_reason: str | None = None,
) -> WeatherForecastSample:
    unit, statistic = WEATHER_FORECAST_UNIT_STATISTIC[variable]
    # `missing_reason` is populated exactly when `value` is absent: a caller may name a specific
    # reason (e.g. calm-derived wind, `NOT_GENERATED` explicitly), and any other `None` value
    # defaults to `NOT_GENERATED` -- never left null-as-zero (`warehouse/schemas/weather_forecast.py:18-20`).
    resolved_reason = missing_reason if value is None else None
    if value is None and resolved_reason is None:
        resolved_reason = NOT_GENERATED
    return WeatherForecastSample(
        valid_time=valid_time,
        interval_start=None,
        interval_end=None,
        variable=variable,
        value=value,
        unit=unit,
        statistic=statistic,
        support=WEATHER_FORECAST_SUPPORT,
        missing_reason=resolved_reason,
    )


def _location_samples(
    hourly: Mapping[str, object],
    variables: Sequence[str],
    hours: Sequence[datetime],
) -> tuple[WeatherForecastSample, ...]:
    """Build every sample row for one location: the requested upstream variables plus derived wind.

    See AGENTS.md "The precipitation window": `precipitation`'s `valid_time` is shifted one hour
    EARLIER than the provider's own timestamp, so the lane's `sum_over_following_hour` convention
    (`warehouse/schemas/weather_forecast.py:71`) names the same real-world window Open-Meteo's
    preceding-hour accumulation actually covers.
    """
    series: dict[str, list[float | None]] = {
        field_name: _bounded_series(hourly, field_name, len(hours))
        for field_name in variables
        if field_name in WEATHER_FORECAST_UPSTREAM_VALUE_BOUNDS
    }
    samples: list[WeatherForecastSample] = []
    for index, hour in enumerate(hours):
        samples.extend(
            _instantaneous_sample(hour, field_name, series[field_name][index])
            for field_name in ("temperature_2m", "relative_humidity_2m", "cloud_cover")
            if field_name in series
        )
        if "precipitation" in series:
            samples.append(_precipitation_sample(hour, series["precipitation"][index]))
        if "wind_speed_10m" in series and "wind_direction_10m" in series:
            samples.extend(_wind_samples(hour, series["wind_speed_10m"][index], series["wind_direction_10m"][index]))
    return tuple(samples)


def _precipitation_sample(hour: datetime, millimetres: float | None) -> WeatherForecastSample:
    unit, statistic = WEATHER_FORECAST_UNIT_STATISTIC["precipitation"]
    valid_time = hour - timedelta(hours=1)
    interval_start, interval_end = (None, None) if millimetres is None else (valid_time, hour)
    return WeatherForecastSample(
        valid_time=valid_time,
        interval_start=interval_start,
        interval_end=interval_end,
        variable="precipitation",
        value=millimetres,
        unit=unit,
        statistic=statistic,
        support=WEATHER_FORECAST_SUPPORT,
        missing_reason=None if millimetres is not None else NOT_GENERATED,
    )


def _wind_samples(hour: datetime, speed: float | None, direction: float | None) -> tuple[WeatherForecastSample, ...]:
    """Four wind rows per hour: the provider's own speed/direction, plus their derived u/v components.

    Calm has no defined bearing (AGENTS.md "Wind: derived from speed/direction", "calm direction is
    explicitly absent"): `speed == 0.0` makes `direction`, `wind_u_10m` and `wind_v_10m` a governed
    absence even when the provider supplied a (meaningless) direction reading; `wind_speed_10m`
    itself stays the real, valid zero.
    """
    speed_sample = _instantaneous_sample(hour, "wind_speed_10m", speed)
    if speed is None or direction is None or speed == 0.0:
        # Three distinct reasons collapse to the same governed absence: the provider generated
        # neither value, generated speed but not direction (or vice versa), or generated both but
        # the bearing is physically undefined at calm. `MISSING_REASONS` has no finer-grained member
        # for "undefined, not absent" (`warehouse/schemas/weather_forecast.py:41-43`), so all three
        # are `not_generated` -- documented here rather than silently merged without comment.
        return (
            speed_sample,
            _instantaneous_sample(hour, "wind_direction_10m", None, missing_reason=NOT_GENERATED),
            _instantaneous_sample(hour, "wind_u_10m", None, missing_reason=NOT_GENERATED),
            _instantaneous_sample(hour, "wind_v_10m", None, missing_reason=NOT_GENERATED),
        )
    east, north = _wind_components(speed, direction)
    return (
        speed_sample,
        _instantaneous_sample(hour, "wind_direction_10m", direction),
        _instantaneous_sample(hour, "wind_u_10m", east),
        _instantaneous_sample(hour, "wind_v_10m", north),
    )


def parse_forecast_run_payload(  # noqa: PLR0913 - one argument per fact the caller's bounded fetch already resolved
    raw_text: str,
    *,
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    model: OpenMeteoForecastRunModel,
    model_init_time: datetime,
    fetched_at: datetime,
) -> WeatherForecastRun:
    """Parse one run response into the layer's own shape; one entry per requested coordinate, in order.

    Open-Meteo's multi-location response is a JSON array with the first entry's `location_id`
    omitted and the rest numbered from 1, exactly like the archive endpoint's own multi-location
    contract (`execution/open_meteo_lane.py::ordered_locations`); a single-coordinate request may
    answer with a bare object instead of a one-element array, so both shapes are accepted here. See
    AGENTS.md "Unverified against a live multi-day, multi-location response" -- this branch is
    documentation-derived from the archive endpoint's proven shape, not itself live-probed.
    """
    try:
        parsed = json.loads(raw_text)
    except ValueError as error:
        raise UpstreamPayloadError("Open-Meteo run response was not valid JSON") from error
    entries = parsed if isinstance(parsed, list) else [parsed]
    if len(entries) != len(coordinates):
        raise UpstreamPayloadError("Open-Meteo run response does not carry one entry per requested location")

    locations: list[WeatherForecastLocation] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise UpstreamPayloadError("Open-Meteo run response entries must be location objects")
        if index and entry.get("location_id") != index:
            raise UpstreamPayloadError("Open-Meteo run response locations are not in the requested order")
        latitude = entry.get("latitude")
        longitude = entry.get("longitude")
        hourly = entry.get("hourly")
        if (
            isinstance(latitude, bool)
            or not isinstance(latitude, int | float)
            or isinstance(longitude, bool)
            or not isinstance(longitude, int | float)
            or not isinstance(hourly, dict)
        ):
            raise UpstreamPayloadError("Open-Meteo run response entry was missing latitude, longitude or hourly")
        raw_hours = hourly.get("time")
        if not isinstance(raw_hours, list) or not raw_hours:
            raise UpstreamPayloadError("Open-Meteo run response entry carried no hourly time axis")
        hours = [_parse_hour(value) for value in raw_hours]
        locations.append(
            WeatherForecastLocation(
                latitude=float(latitude),
                longitude=float(longitude),
                samples=_location_samples(hourly, variables, hours),
            )
        )

    return WeatherForecastRun(
        run_id=forecast_run_id(model, model_init_time),
        model_init_time=model_init_time,
        # Open-Meteo does not echo a model version, run identifier or provider issue/release time
        # (`source-admission.md`, "does not echo ... provider issue/release time"). Always `None`.
        provider_issue_time=None,
        fetched_at=fetched_at,
        locations=tuple(locations),
    )


class OpenMeteoWeatherForecastSource:
    """The pilot's weather-forecast binding: Open-Meteo's Single Runs endpoint, GFS global.

    A class rather than the bare module functions for the same reason
    `pipeline/direct/drought/usdm.py::UsdmDroughtSource` is one: the binding has to be a VALUE a
    region manifest can resolve to, and `isinstance` against `WeatherForecastSource` is then a real
    conformance check. This class adds no behaviour beyond delegating to this module's functions.
    """

    source_slug = WEATHER_FORECAST_SOURCE_SLUG
    coverage = WEATHER_FORECAST_COVERAGE

    async def fetch_run(
        self,
        *,
        model_init_time: datetime,
        coordinates: Sequence[tuple[float, float]],
        variables: Sequence[str],
        forecast_days: int,
    ) -> WeatherForecastRun:
        """Fetch one bounded run; see module docstring and `fetch_forecast_run`, which this forwards to."""
        return await fetch_forecast_run(
            model_init_time=model_init_time,
            coordinates=coordinates,
            variables=variables,
            forecast_days=forecast_days,
        )


async def fetch_forecast_run(
    *,
    model_init_time: datetime,
    coordinates: Sequence[tuple[float, float]],
    variables: Sequence[str],
    forecast_days: int,
    model: OpenMeteoForecastRunModel = OPEN_METEO_FORECAST_RUN_MODEL,
) -> WeatherForecastRun:
    """Build the bounded URL, fetch it once under `WEATHER_FORECAST_RUN_BOUNDS`, and parse the result.

    No retry loop wraps this call: a rate limit raises `OpenMeteoRateLimitError` and a transport
    fault is retried only inside `fetch_bounded` (never a status code). Opens and closes its own
    `httpx.AsyncClient` via `ingest.http.upstream_client`, matching `ingest/open_meteo.py`'s archive
    fetch shape.
    """
    url = forecast_run_url(coordinates, variables, model_init_time, forecast_days, model=model)
    async with upstream_client(WEATHER_FORECAST_RUN_BOUNDS) as client:
        raw_text = await fetch_forecast_run_text(client, url)
    return parse_forecast_run_payload(
        raw_text,
        coordinates=coordinates,
        variables=variables,
        model=model,
        model_init_time=model_init_time,
        fetched_at=datetime.now(UTC),
    )


#: The single instance a region manifest's `open-meteo` weather-forecast binding resolves to.
OPEN_METEO_WEATHER_FORECAST_SOURCE: Final = OpenMeteoWeatherForecastSource()


__all__ = [
    "MAX_FORECAST_RUNS_PER_DAY",
    "MAX_FORECAST_RUN_DAYS",
    "MAX_FORECAST_RUN_LOCATIONS",
    "MIN_FORECAST_RUN_DAYS",
    "OPEN_METEO_FORECAST_RUN_MODEL",
    "OPEN_METEO_SINGLE_RUN_BASE_URL",
    "OPEN_METEO_WEATHER_FORECAST_SOURCE",
    "WEATHER_FORECAST_COVERAGE",
    "WEATHER_FORECAST_RUN_BOUNDS",
    "WEATHER_FORECAST_SOURCE_SLUG",
    "WEATHER_FORECAST_UPSTREAM_VARIABLES",
    "OpenMeteoForecastRunModel",
    "OpenMeteoWeatherForecastSource",
    "WeatherForecastLocation",
    "WeatherForecastRequestError",
    "WeatherForecastRun",
    "WeatherForecastSample",
    "fetch_forecast_run",
    "fetch_forecast_run_text",
    "forecast_run_id",
    "forecast_run_url",
    "parse_forecast_run_payload",
]
