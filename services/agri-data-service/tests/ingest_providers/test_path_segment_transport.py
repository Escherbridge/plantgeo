"""Path-template transport: per-request path segments and the `{api_key}` segment, driven through the real client.

`httpx.MockTransport` is the provider edge; the provider files are the shipped `usdm.toml` and
`nasa-firms.toml`. What the edge saw is the observable outcome; the key must reach the send URL and
nothing else (no `request_url`, error, repr, log line or stdout).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Final

import httpx
import pytest

from agri_data_service.ingest.firms import FIRMS_AREA_CSV_DATED_TEMPLATE, FIRMS_AVAILABILITY_CSV_TEMPLATE
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.provider_client import provider_endpoint_request
from agri_data_service.ingest.usdm import usdm_source_url
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.contract import ProviderConfigurationError, SourceUnavailableError
from tests.runner.fakes import ManualClock, providers

if TYPE_CHECKING:
    from collections.abc import Mapping

KEY_VARIABLE: Final = "NASA_FIRMS_KEY"
AREA: Final = {"source": "VIIRS_SNPP_NRT", "area": "-125.5,41.9,-116.4,49.1", "day_range": "1"}


def _fake_key() -> str:
    """An obviously fake MAP_KEY, unique per test so a leak can only be this run's."""
    return f"FAKE-firms-{secrets.token_hex(12)}"


class Edge:
    """The scripted provider edge: answers every send with one status and records the URL it saw."""

    def __init__(self, status: int = 200, body: str = "ok") -> None:
        self.status = status
        self.body = body
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        return httpx.Response(self.status, text=self.body)


async def _get(edge: Edge, provider_id: str, endpoint: str, parameters: Mapping[str, str]) -> object:
    async with httpx.AsyncClient(transport=httpx.MockTransport(edge)) as http:
        client = ConfigProviderClient(provider=providers()[provider_id], http=http, clock=ManualClock())
        return await client.get(endpoint, parameters)


async def test_a_usdm_release_is_sent_to_its_dated_path_exactly_as_the_legacy_url() -> None:
    edge = Edge()

    response = await _get(edge, "usdm", "release", {"release": "20260106"})

    assert edge.urls == [usdm_source_url("2026-01-06")]
    assert getattr(response, "request_url", None) == usdm_source_url("2026-01-06")


async def test_a_placeholder_never_reaches_the_query_and_the_rest_of_the_parameters_do() -> None:
    edge = Edge()

    await _get(edge, "usdm", "release", {"release": "20260106", "format": "json"})

    assert edge.urls == [f"{usdm_source_url('2026-01-06')}?format=json"]


async def test_a_firms_send_fills_the_key_segment_and_every_other_surface_shows_only_the_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    key = _fake_key()
    monkeypatch.setenv(KEY_VARIABLE, key)
    edge = Edge(body="latitude,longitude\n")

    response = await _get(edge, "nasa-firms", "area-csv-dated", {**AREA, "start_date": "2026-10-01"})

    assert edge.urls == [FIRMS_AREA_CSV_DATED_TEMPLATE.format(api_key=key, **AREA, start_date="2026-10-01")]
    request_url = getattr(response, "request_url", "")
    assert request_url == FIRMS_AREA_CSV_DATED_TEMPLATE.format(api_key="{api_key}", **AREA, start_date="2026-10-01")
    resolved = provider_endpoint_request(providers()["nasa-firms"], "area-csv-dated", {**AREA, "start_date": "x"})
    captured = capsys.readouterr()
    surfaces = [request_url, str(resolved), repr(resolved), str(resolved.send_url), captured.out, captured.err]
    assert all(key not in text for text in [*surfaces, caplog.text])


async def test_the_availability_endpoint_needs_no_request_value_beyond_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = _fake_key()
    monkeypatch.setenv(KEY_VARIABLE, key)
    edge = Edge()

    await _get(edge, "nasa-firms", "data-availability", {})

    assert edge.urls == [FIRMS_AVAILABILITY_CSV_TEMPLATE.format(api_key=key)]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, ProviderConfigurationError), (403, ProviderConfigurationError), (404, UpstreamHttpError),
     (503, SourceUnavailableError)],
)
async def test_a_refused_firms_send_never_carries_the_key_into_its_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status: int,
    error_type: type[Exception],
) -> None:
    key = _fake_key()
    monkeypatch.setenv(KEY_VARIABLE, key)
    edge = Edge(status=status)

    with pytest.raises(error_type) as caught:
        await _get(edge, "nasa-firms", "area-csv", AREA)

    assert len(edge.urls) == 1
    assert key in edge.urls[0]
    assert all(key not in text for text in (str(caught.value), repr(caught.value), caplog.text))


@pytest.mark.parametrize("blank", ["", "   "])
async def test_an_opted_in_key_segment_with_no_key_is_refused_before_any_send(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """FR-2 under path transport: the customer_host opt-in REQUIRES the key; nothing keyless is sent."""
    monkeypatch.setenv(KEY_VARIABLE, blank)
    edge = Edge()

    with pytest.raises(ProviderConfigurationError, match=KEY_VARIABLE):
        await _get(edge, "nasa-firms", "area-csv", AREA)

    assert edge.urls == []


async def test_a_request_missing_a_placeholder_value_is_refused_before_any_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_VARIABLE, _fake_key())
    edge = Edge()

    with pytest.raises(ProviderConfigurationError, match=r"no request value for \['day_range'\]"):
        await _get(edge, "nasa-firms", "area-csv", {"source": "VIIRS_SNPP_NRT", "area": "-125,41,-116,49"})
    with pytest.raises(ProviderConfigurationError, match=r"no request value for \['release'\]"):
        await _get(edge, "usdm", "release", {})

    assert edge.urls == []


@pytest.mark.parametrize("value", ["", "  ", ".", "..", "../etc", "2026/01/06", "a..b", "a\\b"])
async def test_a_path_value_that_could_escape_its_segment_is_refused_before_any_send(value: str) -> None:
    edge = Edge()

    with pytest.raises(ProviderConfigurationError, match=r"\{release\}"):
        await _get(edge, "usdm", "release", {"release": value})

    assert edge.urls == []


async def test_query_and_fragment_characters_in_a_path_value_are_escaped_not_obeyed() -> None:
    edge = Edge()

    await _get(edge, "usdm", "release", {"release": "20260106?admin=1#x"})

    [sent] = edge.urls
    assert httpx.URL(sent).path == "/data/json/usdm_20260106?admin=1#x.json"
    assert httpx.URL(sent).query == b""
    assert "%3F" in sent
    assert "%23" in sent


@pytest.mark.parametrize("shape", ["slash", "dots"])
async def test_a_key_that_cannot_be_one_path_segment_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    key = f"FAKE/{secrets.token_hex(8)}" if shape == "slash" else f"FAKE..{secrets.token_hex(8)}"
    monkeypatch.setenv(KEY_VARIABLE, key)
    edge = Edge()

    with pytest.raises(ProviderConfigurationError) as caught:
        await _get(edge, "nasa-firms", "area-csv", AREA)

    assert edge.urls == []
    assert key not in str(caught.value)
    assert "{api_key}" in str(caught.value)


def test_the_credential_free_url_is_identical_with_the_key_absent_present_or_rotated() -> None:
    """The durable identity (reports, checkpoints) never moves with the key, so rotation never re-keys history."""
    firms = providers()["nasa-firms"]
    urls = {
        provider_endpoint_request(firms, "area-csv", AREA, environment={KEY_VARIABLE: _fake_key()}).request_url
        for _ in range(3)
    }

    [url] = urls
    assert url.startswith("https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/VIIRS_SNPP_NRT/")
