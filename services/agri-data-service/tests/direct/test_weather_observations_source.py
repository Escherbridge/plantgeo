"""The current-conditions poll: one point's failure or staleness never discards the rest of the grid.

Also covers the customer-host switch (`OPEN_METEO_API_KEY`), SOFT-8's Retry-After-bounded retry, and
the shared per-poll retry budget -- all driven through a real `httpx.MockTransport`, never a stubbed
fetch function, so the actual host/key resolution and retry wiring in `source.py` is what is tested.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

import agri_data_service.pipeline.direct.weather_observations.source as source_module
from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.open_meteo import (
    CURRENT_FIELDS,
    OPEN_METEO_API_KEY_VARIABLE,
    OPEN_METEO_BASE_URL,
    OPEN_METEO_FORECAST_CUSTOMER_BASE_URL,
    CurrentWeatherResponse,
    current_weather_url,
)
from agri_data_service.pipeline.direct.weather_observations.source import (
    WeatherPollRequestBudget,
    poll_current_conditions,
)

NOW = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)
POINTS = [(46.0, -117.0), (47.0, -116.0), (48.0, -115.0)]

#: A real Open-Meteo `current` block: `observedAt` 2026-09-03T17:58:00Z as a unixtime, matching the
#: `timeformat=unixtime` this lane always requests.
CURRENT_BODY = {
    "current": {
        "time": 1_788_458_280,
        "temperature_2m": 12.3,
        "relative_humidity_2m": 40.0,
        "wind_speed_10m": 3.0,
        "wind_direction_10m": 180.0,
        "precipitation": 0.0,
    }
}


def _response(temperature: float) -> CurrentWeatherResponse:
    """One accepted point: the parsed reading beside the exact bytes `recovery.py` checkpoints."""
    return CurrentWeatherResponse(observation=_observation(temperature), body=b'{"current": {}}')


def _observation(temperature: float) -> dict[str, object]:
    return {
        "observedAt": "2026-09-03T17:58:00.000Z",
        "temperature": temperature,
        "humidity": 40.0,
        "windSpeed": 3.0,
        "windDirection": 180.0,
        "precipitation": 0.0,
    }


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the retry ladder's real backoff so a retry test runs at full speed."""

    async def fast(_seconds: float) -> None:
        return None

    monkeypatch.setattr(source_module.asyncio, "sleep", fast)


def _capturing_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace `asyncio.sleep` with one that records every delay it was asked to wait, instantly."""
    delays: list[float] = []

    async def capture(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(source_module.asyncio, "sleep", capture)
    return delays


# --- Grid-level behaviour: one point's fault never costs the others (seam: `_poll_one_point`) -------


def test_every_successful_point_is_kept_in_request_order(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_poll_one_point(
        _client: object, latitude: float, _longitude: float, _now: datetime | None, _budget: object
    ) -> CurrentWeatherResponse:
        return _response(temperature=latitude)

    monkeypatch.setattr(source_module, "_poll_one_point", fake_poll_one_point)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.points_sampled == len(POINTS)
    assert result.unavailable_points == 0
    assert [point.latitude for point in result.observations] == [46.0, 47.0, 48.0]
    assert result.fetched_at == NOW


def test_one_points_failure_does_not_discard_the_rest_of_the_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def flaky(
        _client: object, latitude: float, _longitude: float, _now: datetime | None, _budget: object
    ) -> CurrentWeatherResponse:
        if latitude == 47.0:
            raise UpstreamPayloadError("Open-Meteo returned a stale current observation")
        return _response(temperature=latitude)

    monkeypatch.setattr(source_module, "_poll_one_point", flaky)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.unavailable_points == 1
    assert [point.latitude for point in result.observations] == [46.0, 48.0]


def test_every_point_unavailable_returns_zero_observations_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_fails(*_args: object, **_kwargs: object) -> CurrentWeatherResponse:
        raise UpstreamPayloadError("Open-Meteo returned an invalid current observation")

    monkeypatch.setattr(source_module, "_poll_one_point", always_fails)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert result.observations == ()
    assert result.unavailable_points == len(POINTS)


def test_every_accepted_point_carries_the_exact_bytes_it_was_parsed_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The poll is the ONLY place these bytes exist: this feed has no archive to re-fetch them from."""

    async def distinct_bodies(
        _client: object, latitude: float, _longitude: float, _now: datetime | None, _budget: object
    ) -> CurrentWeatherResponse:
        return CurrentWeatherResponse(
            observation=_observation(temperature=latitude),
            body=f'{{"current": {{"temperature_2m": {latitude}}}}}'.encode(),
        )

    monkeypatch.setattr(source_module, "_poll_one_point", distinct_bodies)

    result = asyncio.run(poll_current_conditions(object(), POINTS, now=NOW))

    assert [point.response_body for point in result.observations] == [
        b'{"current": {"temperature_2m": 46.0}}',
        b'{"current": {"temperature_2m": 47.0}}',
        b'{"current": {"temperature_2m": 48.0}}',
    ]


# --- Free/customer host resolution: a real client, a real `httpx.MockTransport` ----------------------


def test_free_host_is_used_and_byte_identical_to_the_legacy_url_when_no_key_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CURRENT_BODY)

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, [POINTS[0]], now=NOW)

    result = asyncio.run(run())

    assert len(captured) == 1
    sent = str(captured[0].url)
    assert sent == current_weather_url(*POINTS[0])
    assert sent.startswith(OPEN_METEO_BASE_URL)
    assert "apikey" not in sent
    assert result.unavailable_points == 0
    assert result.observations[0].observation["temperature"] == 12.3


def test_customer_host_is_used_and_keyed_when_api_key_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "the-real-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CURRENT_BODY)

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, [POINTS[0]], now=NOW)

    result = asyncio.run(run())

    sent = str(captured[0].url)
    assert sent.startswith(OPEN_METEO_FORECAST_CUSTOMER_BASE_URL)
    assert "apikey=the-real-key" in sent
    assert sent.endswith("apikey=the-real-key")  # the key rides LAST, after every governed parameter
    assert httpx.QueryParams(httpx.URL(sent).query)["current"] == CURRENT_FIELDS
    assert result.unavailable_points == 0


def test_the_api_key_never_reaches_a_raised_errors_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing send must still never leak the key into an exception that could reach a log line."""
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "the-real-key")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            budget = WeatherPollRequestBudget(remaining=source_module.WEATHER_POLL_RETRY_BUDGET_DEFAULT)
            with pytest.raises(UpstreamHttpError) as failure:
                await source_module._poll_one_point(client, *POINTS[0], NOW, budget)
            assert "the-real-key" not in str(failure.value)

    asyncio.run(run())


# --- SOFT-8: the retry ladder and its Retry-After clamp, plus the shared poll budget -----------------


def test_a_429_with_retry_after_is_retried_and_the_delay_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=CURRENT_BODY)

    delays = _capturing_sleep(monkeypatch)

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, [POINTS[0]], now=NOW)

    result = asyncio.run(run())

    assert attempts["n"] == 2
    assert delays == [7.0]
    assert result.unavailable_points == 0
    assert result.observations[0].observation["temperature"] == 12.3


def test_a_retry_after_past_the_ladders_ceiling_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json=CURRENT_BODY)

    delays = _capturing_sleep(monkeypatch)

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, [POINTS[0]], now=NOW)

    result = asyncio.run(run())

    assert delays == [source_module.WEATHER_CURRENT_RETRY_LADDER.max_delay_seconds]
    assert result.unavailable_points == 0


def test_a_persistent_429_is_bounded_not_hammered_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    _no_sleep(monkeypatch)

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, [POINTS[0]], now=NOW)

    result = asyncio.run(run())

    assert attempts["n"] == source_module.WEATHER_CURRENT_RETRY_LADDER.max_attempts
    assert result.unavailable_points == 1
    assert result.observations == ()


def test_the_shared_retry_budget_caps_total_retries_across_the_whole_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider-wide outage that throttles EVERY point must not multiply into unbounded retries."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    sends = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        sends["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"})

    _no_sleep(monkeypatch)
    many_points = [(float(latitude), -117.0) for latitude in range(10)]

    async def run() -> source_module.WeatherPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await poll_current_conditions(client, many_points, now=NOW, request_budget=3)

    result = asyncio.run(run())

    # Every point still gets its (unbudgeted) first attempt -- 10 sends at minimum. Only 3 RETRIES
    # are funded in total across the whole poll, so the ceiling is 10 first attempts + 3 funded
    # retries = 13, far below the 10 * (max_attempts - 1) = 20 retries an unbounded ladder would
    # allow if every one of the 10 points were throttled independently.
    assert len(many_points) <= sends["n"] <= len(many_points) + 3
    assert result.unavailable_points == len(many_points)
    assert result.observations == ()
