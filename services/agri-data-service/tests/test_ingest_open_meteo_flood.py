"""Open-Meteo GloFAS adapter: model threading, parameter ordering, host provenance, and quota typing."""


from __future__ import annotations

import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agri_data_service.ingest.open_meteo import OPEN_METEO_API_KEY_VARIABLE, OpenMeteoRateLimitError
from agri_data_service.ingest.open_meteo_flood import (
    GLOFAS_MODELS,
    OPEN_METEO_FLOOD_BASE_URL,
    OPEN_METEO_FLOOD_CUSTOMER_BASE_URL,
    OPEN_METEO_FLOOD_ENDPOINT,
    fetch_flood_daily,
    flood_daily_parameters,
    flood_daily_request,
    flood_daily_url,
)

START = date(2022, 4, 30)
END = date(2026, 4, 30)
COORDINATES = ((44.0, -116.0), (44.25, -116.25))
VARIABLES = ("river_discharge",)


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_flood_parameters_carry_the_requested_model_not_a_hardcoded_one() -> None:
    sent = {model: flood_daily_parameters(COORDINATES, VARIABLES, START, END, model) for model in GLOFAS_MODELS}

    # The model reaches the wire for every reviewed product; nothing downstream may substitute a default.
    assert {model: parameters["models"] for model, parameters in sent.items()} == dict(
        zip(GLOFAS_MODELS, GLOFAS_MODELS, strict=True)
    )
    assert sent["forecast_v4"]["cell_selection"] == "nearest"
    assert sent["forecast_v4"]["latitude"] == "44,44.25"
    assert sent["forecast_v4"]["longitude"] == "-116,-116.25"


def test_flood_parameters_reject_an_unreviewed_model() -> None:
    with pytest.raises(ValueError, match="model must be one of"):
        flood_daily_parameters(COORDINATES, VARIABLES, START, END, "forecast_v9")


def test_flood_parameters_reject_unsorted_or_empty_variables() -> None:
    with pytest.raises(ValueError, match="sorted and non-empty"):
        flood_daily_parameters(COORDINATES, ("river_discharge_mean", "river_discharge"), START, END, "forecast_v4")
    with pytest.raises(ValueError, match="sorted and non-empty"):
        flood_daily_parameters(COORDINATES, (), START, END, "forecast_v4")


def test_flood_parameters_reject_an_inverted_window_and_off_globe_coordinates() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        flood_daily_parameters(COORDINATES, VARIABLES, END, START, "forecast_v4")
    with pytest.raises(ValueError, match="WGS84"):
        flood_daily_parameters(((91.0, -116.0),), VARIABLES, START, END, "forecast_v4")


def test_flood_url_is_credential_free_and_the_request_url_carries_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "secret-key")
    keyless = flood_daily_url(COORDINATES, VARIABLES, START, END, "consolidated_v4")
    request = flood_daily_request(COORDINATES, VARIABLES, START, END, "consolidated_v4")

    assert "secret-key" not in keyless
    assert keyless.startswith(OPEN_METEO_FLOOD_CUSTOMER_BASE_URL)
    assert request.base_url == OPEN_METEO_FLOOD_CUSTOMER_BASE_URL
    assert _query(request.request_url)["apikey"] == ["secret-key"]
    # The key is appended last, so the governed prefix a checksum covers never shifts.
    assert request.request_url.startswith(keyless)


def test_flood_url_uses_the_free_host_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    url = flood_daily_url(COORDINATES, VARIABLES, START, END, "consolidated_v4")

    assert url.startswith(OPEN_METEO_FLOOD_BASE_URL)
    assert "apikey" not in _query(url)


def test_flood_url_records_the_host_a_past_retrieval_really_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    url = flood_daily_url(
        COORDINATES, VARIABLES, START, END, "consolidated_v4", base_url=OPEN_METEO_FLOOD_CUSTOMER_BASE_URL
    )

    assert url.startswith(OPEN_METEO_FLOOD_CUSTOMER_BASE_URL)


def test_the_endpoints_own_host_rule_refuses_an_unreviewed_host() -> None:
    # One rule for every Open-Meteo product: OpenMeteoEndpoint.require_base_url, not a per-lane copy.
    assert OPEN_METEO_FLOOD_ENDPOINT.require_base_url(OPEN_METEO_FLOOD_BASE_URL) == OPEN_METEO_FLOOD_BASE_URL
    assert (
        OPEN_METEO_FLOOD_ENDPOINT.require_base_url(OPEN_METEO_FLOOD_CUSTOMER_BASE_URL)
        == OPEN_METEO_FLOOD_CUSTOMER_BASE_URL
    )
    with pytest.raises(ValueError, match="not a reviewed endpoint"):
        OPEN_METEO_FLOOD_ENDPOINT.require_base_url("https://flood-api.open-meteo.example/v1/flood")


async def test_fetch_flood_daily_types_a_quota_refusal_by_scope() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"reason": "Daily API request limit exceeded. Please try again in 60 minutes."},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OpenMeteoRateLimitError) as error:
            await fetch_flood_daily(client, OPEN_METEO_FLOOD_BASE_URL)

    # An ambiguous body resolves to the least retryable scope, so a day-long wall is never slept on.
    assert error.value.scope == "day"


async def test_fetch_flood_daily_returns_the_exact_bytes_that_arrived() -> None:
    body = json.dumps([{"latitude": 44.0, "longitude": -116.0}])

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_flood_daily(client, OPEN_METEO_FLOOD_BASE_URL) == body
