"""Fetch one current-conditions poll of the sample grid, using the paid customer host when a key is set.

THE ACQUISITION MODEL IS NOT AN ARCHIVE FETCH. `climate/source.py` and `soil/source.py` ask an
archive endpoint for ONE SETTLED PAST DAY and get it back complete in one response. Open-Meteo's
`current` endpoint (`ingest/open_meteo.py::current_weather_url`) has no `start_date`/`end_date` and
answers only the instant closest to now, gated fresh by `MAX_OBSERVATION_AGE` (3 hours). There is no
day this lane's writer can ask the source for by date -- every "day" downstream of this module is
assembled by BUCKETING whatever instants many polls, over time, happen to return. See
`pipeline/direct/AGENTS.md`, "Weather observations", for the consequence that follows: no
`--product`-style enumeration and no settled-day retry ladder, because there is no day to retry.

THE HOST SWITCH REUSES `ingest/provider_client.py::FORECAST_ENDPOINT`. `ingest/open_meteo.py`'s own
`current_weather_url` never gained a customer-host twin (soil's ERA5-Land archive did, via
`archive_daily_request`/`archive_daily_url`; plan 1C's `provider_client.py` paired the forecast
endpoint's free and customer hosts for exactly this later use). Rather than restate a second ad hoc
switch here, this module resolves every send through `open_meteo_endpoint.py::open_meteo_product_request`,
which already implements the "customer host + key when `OPEN_METEO_API_KEY` is set, free host and no
key otherwise" rule. `_current_weather_parameters` is held byte-identical -- key order included -- to
`current_weather_url`'s own query, deliberately WITHOUT the `cell_selection` parameter
`provider_client.py::single_location_parameters` would otherwise add: that keeps the keyless send the
exact URL this lane has always sent, so every retained checkpoint keyed by it
(`recovery.py::weather_checkpoint_identity`, which still calls `current_weather_url` directly for the
identity) stays valid. When a key IS configured, the resolved host becomes
`OPEN_METEO_FORECAST_CUSTOMER_BASE_URL` and the key rides as the LAST query parameter
(`open_meteo_product_request`'s own rule, never logged -- see `KeyedRequestUrl` below) -- a host
`foundation/observability/usage.py`'s host rules already resolve to the `open-meteo-paid` pool, not a
new metering branch.

RESILIENCE: SOFT-8, BOUNDED TWICE. Until this change a non-2xx status (chiefly a 429 under this
lane's ~150-point concurrent fan-out) failed a point on the FIRST try: `fetch_bounded`'s own
transport retry (`ingest/http.py`, 3 attempts) covers a connection fault, never a status response.
Every point's fetch is now wrapped in `ingest/upstream_retry.py::retry_upstream` -- the same SOFT-8
ladder and Retry-After clamp `provider_client.py::fetch_single_location` already proved --
via `WEATHER_CURRENT_RETRY_POLICY`. `WeatherPollRequestBudget` is the SECOND bound, shared across
the whole poll's `asyncio.gather` rather than sized per point: a provider-wide throttle that
rate-limits every one of the (at most `ingest/policy.py::MAX_WEATHER_SAMPLE_POINTS`, 150) points
could otherwise multiply into `points * (max_attempts - 1)` extra retries in one run. The budget
charges one unit per RETRY -- never a point's first attempt, so every point is still tried once --
and raises `WeatherPollRequestBudgetExhaustedError` when it is spent; that error is caught nowhere
special, so it reaches `poll_current_conditions`'s own `asyncio.gather(..., return_exceptions=True)`
and the point is reported `unavailable`, exactly like any other fetch fault.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from agri_data_service.ingest.http import UpstreamError, UpstreamHttpError, UpstreamPayloadError, fetch_bounded
from agri_data_service.ingest.open_meteo import CURRENT_FIELDS, CurrentWeatherResponse, parse_current_weather
from agri_data_service.ingest.open_meteo_endpoint import (
    open_meteo_product_base_url,
    open_meteo_product_request,
    require_wgs84_request_coordinates,
)
from agri_data_service.ingest.policy import format_javascript_number
from agri_data_service.ingest.provider_client import FORECAST_ENDPOINT, KeyedRequestUrl
from agri_data_service.ingest.upstream_retry import RetryLadder, UpstreamRetryPolicy, retry_upstream

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx


@dataclass(frozen=True, slots=True)
class WeatherPointObservation:
    """One sample point's validated current-conditions reading, exactly as `parse_current_weather` built it."""

    latitude: float
    longitude: float
    observation: dict[str, object]
    #: The exact provider bytes this observation was parsed from, when the poll was asked to keep
    #: them; `None` for the ordinary capture-free poll. Never a re-render of `observation`.
    response_body: bytes | None = None


@dataclass(frozen=True, slots=True)
class WeatherPollResult:
    """Everything one poll of the bounded sample grid produced, successes and failures counted."""

    fetched_at: datetime
    points_sampled: int
    observations: tuple[WeatherPointObservation, ...]
    unavailable_points: int


#: SOFT-8's ladder for this lane's `current` endpoint, sized for a ~150-point concurrent fan-out
#: rather than ArcGIS's `DEFAULT_RETRY_LADDER` (6 attempts, 60s wall clock): at most two retries per
#: point (three real sends total) and a 30s wall-clock ceiling -- a quarter of this lane's default
#: 120s turn budget (`forward.py::WEATHER_OBSERVATIONS_DEFAULT_TIME_BUDGET_SECONDS`) -- so one
#: throttled point can never eat the turn the other ~149 points still need their own attempt inside.
WEATHER_CURRENT_RETRY_LADDER: Final = RetryLadder(
    max_attempts=3,
    base_delay_seconds=2.0,
    max_delay_seconds=15.0,
    wall_clock_ceiling_seconds=30.0,
)

WEATHER_CURRENT_RETRY_POLICY: Final = UpstreamRetryPolicy(
    event="weather_observations_current_retry",
    exhausted_message="the Open-Meteo current-conditions endpoint exhausted its retry ladder",
    ladder=WEATHER_CURRENT_RETRY_LADDER,
)

#: One RETRY, on average, per sample point (`ingest/policy.py::MAX_WEATHER_SAMPLE_POINTS`) -- the
#: shared ceiling `WeatherPollRequestBudget` enforces across the whole poll. A point's OWN first
#: attempt is never charged against it (see the module docstring), so even a provider-wide outage
#: that rate-limits every point in the grid cannot make one poll spend more than roughly 2x its
#: baseline request volume, however deep `WEATHER_CURRENT_RETRY_LADDER.max_attempts` is raised later.
WEATHER_POLL_RETRY_BUDGET_DEFAULT: Final = 150


class WeatherPollRequestBudgetExhaustedError(UpstreamError):
    """Raised when one poll's shared retry budget is spent; the point is reported unavailable, not hammered."""


@dataclass(slots=True)
class WeatherPollRequestBudget:
    """Bounds how many RETRIES one poll may spend in total, across every sample point.

    Shared (not copied) across the whole `asyncio.gather` in `poll_current_conditions`: single-
    threaded asyncio never preempts between the read and the decrement below, so no lock is needed --
    the same reasoning `pipeline/direct/soil/source.py::SoilSourceCache` relies on for its own
    per-turn counters.
    """

    remaining: int

    async def bounded_sleep(self, seconds: float) -> None:
        """The `retry_upstream` sleep seam (mirrors `soil/source.py::deadline_bounded_sleep`): charge
        one retry before backing off, or refuse to retry further rather than keep hammering."""
        if self.remaining <= 0:
            raise WeatherPollRequestBudgetExhaustedError(
                "this poll's shared retry budget is exhausted; refusing further retries rather than "
                "hammering the provider"
            )
        self.remaining -= 1
        await asyncio.sleep(seconds)


def _current_weather_parameters(latitude: float, longitude: float) -> dict[str, str]:
    """Match `ingest/open_meteo.py::current_weather_url`'s query, key order included.

    `open_meteo_product_request` sends this dict verbatim (appending `apikey` last only when a key
    is configured), so holding this byte-identical to the legacy URL is what keeps the keyless send
    -- and every checkpoint identity built from `current_weather_url` (`recovery.py`) -- unchanged.
    """
    return {
        "latitude": format_javascript_number(latitude),
        "longitude": format_javascript_number(longitude),
        "current": CURRENT_FIELDS,
        "wind_speed_unit": "ms",
        "timeformat": "unixtime",
        "timezone": "GMT",
    }


async def _attempt_current_weather(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    now: datetime | None,
) -> CurrentWeatherResponse:
    """One bounded send: free host keyless, customer host keyed, exactly as `open_meteo_product_request` resolves it."""
    request = open_meteo_product_request(FORECAST_ENDPOINT, _current_weather_parameters(latitude, longitude))
    keyed_url = KeyedRequestUrl(request.request_url)
    response = await fetch_bounded(client, keyed_url.reveal(), FORECAST_ENDPOINT.bounds)
    if not response.ok:
        raise UpstreamHttpError(response.status, retry_after_seconds=response.retry_after_seconds)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("Open-Meteo current-conditions response was not JSON")
    try:
        payload = json.loads(response.text)
    except ValueError as error:
        raise UpstreamPayloadError("Open-Meteo current-conditions response contained invalid JSON") from error
    return CurrentWeatherResponse(observation=parse_current_weather(payload, now), body=response.text.encode("utf-8"))


async def _poll_one_point(
    client: httpx.AsyncClient,
    latitude: float,
    longitude: float,
    now: datetime | None,
    budget: WeatherPollRequestBudget,
) -> CurrentWeatherResponse:
    """Fetch and validate one WGS84 point, retried under SOFT-8 and this poll's shared request budget."""
    require_wgs84_request_coordinates([(latitude, longitude)])
    host = urlsplit(open_meteo_product_base_url(FORECAST_ENDPOINT)).hostname

    async def _attempt() -> CurrentWeatherResponse:
        return await _attempt_current_weather(client, latitude, longitude, now)

    return await retry_upstream(
        _attempt,
        WEATHER_CURRENT_RETRY_POLICY,
        host=host,
        context={"latitude": latitude, "longitude": longitude},
        sleep=budget.bounded_sleep,
    )


async def poll_current_conditions(
    client: httpx.AsyncClient,
    points: Sequence[tuple[float, float]],
    *,
    now: datetime | None = None,
    request_budget: int = WEATHER_POLL_RETRY_BUDGET_DEFAULT,
) -> WeatherPollResult:
    """Fetch every sample point, keeping one point's failure or staleness from discarding the rest.

    Every point goes through `_poll_one_point`: the same bounds check, host/key resolution,
    SOFT-8 retry ladder, value-range validation and `MAX_OBSERVATION_AGE` freshness gate the ingest
    cron applied before this lane existed, so a direct-written row and a Postgres-written row would
    have made an identical accept/reject decision on the same response. `request_budget` bounds the
    RETRIES this one poll may spend in total (see `WeatherPollRequestBudget`); the default covers an
    ordinary throttled hour without threading a new argument through `forward.py`. No concurrency
    limiter is added beyond that budget: `run_weather_ingestion_job` already fanned the full grid (at
    most `MAX_WEATHER_SAMPLE_POINTS` = 150 points) out unbounded, and `ingest/http.py::upstream_client`
    already caps real concurrent sockets per client (`MAX_UPSTREAM_CONNECTIONS` = 10).

    Every accepted point keeps its exact parser input in `response_body`: this feed has no archive to
    re-fetch, so `recovery.py` checkpoints those bytes before the first write. There is deliberately
    no capture-free variant -- a second poll shape would be a second place for the accept/reject rule
    to drift, and the body is discarded by the caller, not by the fetch.
    """
    fetched_at = now if now is not None else datetime.now(UTC)
    budget = WeatherPollRequestBudget(remaining=request_budget)
    results = await asyncio.gather(
        *(_poll_one_point(client, latitude, longitude, fetched_at, budget) for latitude, longitude in points),
        return_exceptions=True,
    )
    observations: list[WeatherPointObservation] = []
    unavailable_points = 0
    for (latitude, longitude), result in zip(points, results, strict=True):
        if isinstance(result, BaseException):
            unavailable_points += 1
            continue
        observations.append(
            WeatherPointObservation(
                latitude=latitude,
                longitude=longitude,
                observation=result.observation,
                response_body=result.body,
            )
        )
    return WeatherPollResult(
        fetched_at=fetched_at,
        points_sampled=len(points),
        observations=tuple(observations),
        unavailable_points=unavailable_points,
    )


__all__ = [
    "WEATHER_CURRENT_RETRY_LADDER",
    "WEATHER_CURRENT_RETRY_POLICY",
    "WEATHER_POLL_RETRY_BUDGET_DEFAULT",
    "WeatherPointObservation",
    "WeatherPollRequestBudget",
    "WeatherPollRequestBudgetExhaustedError",
    "WeatherPollResult",
    "poll_current_conditions",
]
