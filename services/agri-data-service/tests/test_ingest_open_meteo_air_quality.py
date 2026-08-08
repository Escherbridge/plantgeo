"""Open-Meteo CAMS adapter: domain threading, GMT pinning, host provenance, and quota typing."""

# ruff: noqa: PLR2004

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.open_meteo import OPEN_METEO_API_KEY_VARIABLE, OpenMeteoRateLimitError
from agri_data_service.ingest.open_meteo_air_quality import (
    CAMS_DOMAINS,
    HOURS_PER_DAY,
    OPEN_METEO_AIR_QUALITY_BASE_URL,
    OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL,
    OPEN_METEO_AIR_QUALITY_ENDPOINT,
    air_quality_hourly_parameters,
    air_quality_hourly_request,
    air_quality_hourly_url,
    fetch_air_quality_hourly,
)

START = date(2022, 4, 30)
END = date(2022, 5, 30)
COORDINATES = ((44.0, -116.0), (44.8, -116.0))
VARIABLES = ("pm10", "pm2_5")


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def test_air_quality_parameters_carry_the_requested_domain_and_pin_gmt() -> None:
    sent = {
        domain: air_quality_hourly_parameters(COORDINATES, VARIABLES, START, END, domain) for domain in CAMS_DOMAINS
    }

    # The domain reaches the wire for every reviewed product; nothing downstream may substitute a default.
    assert {domain: parameters["domains"] for domain, parameters in sent.items()} == dict(
        zip(CAMS_DOMAINS, CAMS_DOMAINS, strict=True)
    )
    assert sent["cams_global"]["timezone"] == "GMT"
    assert sent["cams_global"]["cell_selection"] == "nearest"
    assert sent["cams_global"]["hourly"] == "pm10,pm2_5"


def test_hours_per_day_is_the_shared_axis_length() -> None:
    assert HOURS_PER_DAY == 24


def test_air_quality_parameters_reject_an_unreviewed_domain() -> None:
    with pytest.raises(ValueError, match="domain must be one of"):
        air_quality_hourly_parameters(COORDINATES, VARIABLES, START, END, "cams_pacific")


def test_air_quality_parameters_reject_unsorted_variables_and_an_inverted_window() -> None:
    with pytest.raises(ValueError, match="sorted and non-empty"):
        air_quality_hourly_parameters(COORDINATES, ("pm2_5", "pm10"), START, END, "cams_global")
    with pytest.raises(ValueError, match="must not precede"):
        air_quality_hourly_parameters(COORDINATES, VARIABLES, END, START, "cams_global")


def test_air_quality_url_is_credential_free_and_the_request_url_carries_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPEN_METEO_API_KEY_VARIABLE, "secret-key")
    keyless = air_quality_hourly_url(COORDINATES, VARIABLES, START, END, "cams_global")
    request = air_quality_hourly_request(COORDINATES, VARIABLES, START, END, "cams_global")

    assert "secret-key" not in keyless
    assert keyless.startswith(OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL)
    assert request.base_url == OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL
    assert _query(request.request_url)["apikey"] == ["secret-key"]
    assert request.request_url.startswith(keyless)


def test_air_quality_url_uses_the_free_host_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPEN_METEO_API_KEY_VARIABLE, raising=False)
    url = air_quality_hourly_url(COORDINATES, VARIABLES, START, END, "cams_global")

    assert url.startswith(OPEN_METEO_AIR_QUALITY_BASE_URL)
    assert "apikey" not in _query(url)


def test_the_endpoints_own_host_rule_refuses_an_unreviewed_host() -> None:
    # One rule for every Open-Meteo product: OpenMeteoEndpoint.require_base_url, not a per-lane copy.
    endpoint = OPEN_METEO_AIR_QUALITY_ENDPOINT
    assert endpoint.require_base_url(OPEN_METEO_AIR_QUALITY_BASE_URL) == OPEN_METEO_AIR_QUALITY_BASE_URL
    assert (
        endpoint.require_base_url(OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL) == OPEN_METEO_AIR_QUALITY_CUSTOMER_BASE_URL
    )
    with pytest.raises(ValueError, match="not a reviewed endpoint"):
        endpoint.require_base_url("https://air-quality-api.open-meteo.example/v1/air-quality")


async def test_fetch_air_quality_hourly_types_a_minutely_quota_refusal() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"reason": "Minutely API request limit exceeded."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OpenMeteoRateLimitError) as error:
            await fetch_air_quality_hourly(client, OPEN_METEO_AIR_QUALITY_BASE_URL)

    assert error.value.scope == "minute"


async def test_fetch_air_quality_hourly_refuses_a_non_json_body() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>", headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamPayloadError, match="was not JSON"):
            await fetch_air_quality_hourly(client, OPEN_METEO_AIR_QUALITY_BASE_URL)
