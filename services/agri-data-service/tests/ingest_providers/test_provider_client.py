"""Flow tests for `provider_client.py::fetch_single_location`: free/customer host selection, the
single-location bare-object body (spec NEW-4), SOFT-8's retry ladder with its Retry-After clamp, and
`KeyedRequestUrl`'s redaction guarantee (LOG-3).

Fakes only the process edge (the HTTP transport, via `httpx.MockTransport`) and the retry clock (via
`fetch_single_location`'s `sleep` seam, the same one `upstream_retry.py::retry_upstream` itself
exposes) -- everything else is the real client, the real free/customer host resolution, and the real
retry ladder.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.open_meteo import OPEN_METEO_API_KEY_VARIABLE, OPEN_METEO_FORECAST_CUSTOMER_BASE_URL
from agri_data_service.ingest.open_meteo_endpoint import OPEN_METEO_CELL_SELECTION
from agri_data_service.ingest.provider_client import (
    FORECAST_ENDPOINT,
    PROVIDER_CLIENT_RETRY_POLICY,
    KeyedRequestUrl,
    ProviderConfigError,
    fetch_single_location,
    required_single_location_request,
    resolve_required_open_meteo_api_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable

LOCATION_BODY = {"latitude": 45.4, "longitude": -122.7, "current": {"temperature_2m": 12.3}}


def _client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """An httpx client whose transport is the supplied handler; no socket is ever opened."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _no_sleep(_delay: float) -> None:
    """Skip the backoff wait so a retry test runs at full speed."""


# --- Free/customer host resolution (builds on open_meteo_endpoint.py's pattern) --------------------


async def test_the_free_host_is_used_and_governed_parameters_precede_the_callers_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=LOCATION_BODY)

    async with _client_for(handler) as client:
        result = await fetch_single_location(
            client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={"current": "temperature_2m"}
        )

    assert result.body == LOCATION_BODY
    sent = str(captured[0].url)
    assert sent.startswith(FORECAST_ENDPOINT.free_base_url)
    assert "apikey" not in sent
    assert f"cell_selection={OPEN_METEO_CELL_SELECTION}" in sent
    assert "current=temperature_2m" in sent


async def test_the_customer_host_is_used_and_keyed_when_a_key_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "the-real-key")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=LOCATION_BODY)

    async with _client_for(handler) as client:
        await fetch_single_location(
            client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={"current": "temperature_2m"}
        )

    sent = str(captured[0].url)
    assert sent.startswith(OPEN_METEO_FORECAST_CUSTOMER_BASE_URL)
    assert "apikey=the-real-key" in sent


# --- The required-customer-host path: a second OPEN_METEO_API_KEY consumer (CA9) --------------------


async def test_a_required_but_empty_key_raises_a_named_config_error_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise AssertionError("no request should be sent when the required key is empty")

    async with _client_for(handler) as client:
        with pytest.raises(ProviderConfigError, match=OPEN_METEO_API_KEY_VARIABLE):
            await fetch_single_location(
                client,
                FORECAST_ENDPOINT,
                latitude=45.4,
                longitude=-122.7,
                parameters={},
                require_customer_host=True,
            )
    assert sent == []


def test_resolve_required_open_meteo_api_key_trims_and_rejects_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "  padded-key  ")
    assert resolve_required_open_meteo_api_key() == "padded-key"

    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "   ")
    with pytest.raises(ProviderConfigError):
        resolve_required_open_meteo_api_key()


def test_the_required_request_always_resolves_the_customer_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "the-real-key")
    request = required_single_location_request(FORECAST_ENDPOINT, 45.4, -122.7, {"current": "temperature_2m"})
    assert request.base_url == OPEN_METEO_FORECAST_CUSTOMER_BASE_URL
    assert "apikey=the-real-key" in request.request_url


# --- The single-location bare-object body (spec NEW-4) ----------------------------------------------


async def test_a_multi_location_array_body_is_refused_as_a_payload_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """This client requests exactly one location; an ARRAY response is the multi-location shape, refused."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=[LOCATION_BODY])

    async with _client_for(handler) as client:
        with pytest.raises(UpstreamPayloadError, match="not a single-location object"):
            await fetch_single_location(
                client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={}, sleep=_no_sleep
            )


async def test_coordinates_outside_wgs84_bounds_are_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        raise AssertionError("no request should be sent for an out-of-bounds coordinate")

    async with _client_for(handler) as client:
        with pytest.raises(ValueError, match="WGS84"):
            await fetch_single_location(client, FORECAST_ENDPOINT, latitude=95.0, longitude=-122.7, parameters={})
    assert sent == []


# --- SOFT-8: the retry ladder and its Retry-After clamp ----------------------------------------------


async def test_a_503_is_retried_and_the_result_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(503)
        return httpx.Response(200, json=LOCATION_BODY)

    async with _client_for(handler) as client:
        result = await fetch_single_location(
            client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={}, sleep=_no_sleep
        )

    assert result.body == LOCATION_BODY
    assert attempts["n"] == 2


async def test_a_429_retry_after_header_replaces_the_ladders_own_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """SOFT-8: the upstream's own `Retry-After` (5s) replaces the ladder's exponential delay outright."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json=LOCATION_BODY)

    async def capturing_sleep(delay: float) -> None:
        delays.append(delay)

    async with _client_for(handler) as client:
        result = await fetch_single_location(
            client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={}, sleep=capturing_sleep
        )

    assert result.body == LOCATION_BODY
    assert attempts["n"] == 2
    assert delays == [5.0]


async def test_a_retry_after_past_the_ladders_ceiling_is_clamped_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Retry-After longer than the ladder's own ceiling must never park a turn that long."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json=LOCATION_BODY)

    async def capturing_sleep(delay: float) -> None:
        delays.append(delay)

    async with _client_for(handler) as client:
        await fetch_single_location(
            client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={}, sleep=capturing_sleep
        )

    assert delays == [PROVIDER_CLIENT_RETRY_POLICY.ladder.max_delay_seconds]


async def test_a_400_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client error names a real mistake in the request; retrying it would only repeat the mistake."""
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        attempts["n"] += 1
        return httpx.Response(400)

    async with _client_for(handler) as client:
        with pytest.raises(UpstreamHttpError) as failure:
            await fetch_single_location(
                client, FORECAST_ENDPOINT, latitude=45.4, longitude=-122.7, parameters={}, sleep=_no_sleep
            )

    assert failure.value.status == 400
    assert attempts["n"] == 1


# --- KeyedRequestUrl: the structural redaction guard (LOG-3) -----------------------------------------


def test_keyed_request_url_redacts_str_and_repr_but_reveal_returns_the_real_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "super-secret-key-value")
    keyed = KeyedRequestUrl("https://customer-api.open-meteo.com/v1/forecast?apikey=super-secret-key-value")

    assert keyed.reveal() == "https://customer-api.open-meteo.com/v1/forecast?apikey=super-secret-key-value"
    assert "super-secret-key-value" not in str(keyed)
    assert "super-secret-key-value" not in repr(keyed)
    # The one accidental-logging shape LOG-3 exists to catch: an f-string interpolation.
    assert "super-secret-key-value" not in f"failed: {keyed}"
