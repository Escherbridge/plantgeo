"""Optional credentials travel only where the provider file says, and never into a durable request identity."""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError

from agri_data_service.foundation.lane_config import ProviderConfig, load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.provider_client import ProviderConfigError, provider_endpoint_request
from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
from agri_data_service.pipeline.runner.contract import ProviderConfigurationError, SourceRequest
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.lanes.water_gauges.usgs_world import BOISE, DALLES, Reading, UsgsWaterDataWorld
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import ManualClock, MemoryLaneStore, ports_for, providers, run, spec_for

if TYPE_CHECKING:
    from typing import Any

    from agri_data_service.foundation.lane_config import LaneConfig

KEY_ENV = "USGS_WATER_DATA_API_KEY"
KEY_HEADER = "X-Api-Key"


def _shipped_water_lane() -> LaneConfig:
    """The shipped `water-gauges-daily` lane (enabled by its G3 activation)."""
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes["water-gauges-daily"]


def _provider_document() -> dict[str, Any]:
    provider = providers()["usgs-water-data"]
    return {
        **provider.model_dump(exclude={"endpoints"}),
        "endpoints": {name: endpoint.model_dump() for name, endpoint in provider.endpoints.items()},
    }


@pytest.mark.parametrize("endpoint", ["daily", "monitoring-locations"])
@pytest.mark.parametrize("key_state", ["missing", "blank", "present"])
async def test_optional_key_travels_only_in_a_header_and_never_changes_the_url(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, key_state: str
) -> None:
    key = secrets.token_hex(16)
    monkeypatch.delenv(KEY_ENV, raising=False)
    if key_state != "missing":
        monkeypatch.setenv(KEY_ENV, "  " if key_state == "blank" else f" {key} ")
    provider = providers()["usgs-water-data"]
    anonymous = provider_endpoint_request(provider, endpoint, {"limit": "1"}, environment={})
    resolved = provider_endpoint_request(provider, endpoint, {"limit": "1"})
    assert resolved.send_url.reveal() == resolved.request_url == anonymous.request_url
    assert key not in str(resolved)
    assert key not in repr(resolved)
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
        client = ConfigProviderClient(provider=provider, http=http, clock=ManualClock())
        response = await client.get(endpoint, {"limit": "1"})
    assert response.request_url == anonymous.request_url
    [request] = sent
    assert str(request.url) == anonymous.request_url
    assert key not in str(request.url)
    assert request.headers.get(KEY_HEADER) == (key if key_state == "present" else None)


@pytest.mark.parametrize("status", [302, 401, 403])
async def test_optional_key_redirects_and_errors_never_forward_or_report_the_credential(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """httpx keeps a custom header across a cross-origin redirect, so a keyed send never follows one."""
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, headers={"location": "https://other.example/items"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
        client = ConfigProviderClient(provider=providers()["usgs-water-data"], http=http, clock=ManualClock())
        with pytest.raises((UpstreamHttpError, ProviderConfigurationError)) as caught:
            await client.get("daily", {})
    assert [request.url.host for request in sent] == ["api.waterdata.usgs.gov"]
    assert key not in str(caught.value)
    assert key not in repr(caught.value)


async def test_a_keyed_water_turn_sends_the_key_only_as_a_header_and_records_it_nowhere(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """A real turn: every send carries the header; no URL, log, report, receipt or checkpoint holds the key."""
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=lambda _gauge, day: Reading(str(day.day)))
    store, checkpoints = MemoryLaneStore(), MemoryAvailabilityStorage()
    lane = _shipped_water_lane()
    spec = spec_for(lane)
    assert spec.provider is not None
    clock = ManualClock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as http:
        ports = ports_for(
            resolve_strategy(lane), store, clock=clock, checkpoint_store=SourceResponseCheckpoints(checkpoints)
        )
        ports.client = ConfigProviderClient(provider=spec.provider, http=http, clock=clock)
        exit_code, payload = await run(spec, ports)

    assert exit_code == 0, payload
    assert world.requests
    assert {request.headers.get(KEY_HEADER) for request in world.requests} == {key}
    assert all(key not in str(request.url) for request in world.requests)
    assert store.writes()
    assert checkpoints.objects
    durable = [
        json.dumps(payload, default=str).encode(),
        *(receipt.to_payload() for receipt in store.receipts.values()),
        *(stored_key.encode() + stored.payload for stored_key, stored in checkpoints.objects.items()),
    ]
    captured = capsys.readouterr()
    assert all(key.encode() not in blob for blob in durable)
    assert all(key not in text for text in (captured.out, captured.err, caplog.text))


def test_each_provider_file_names_where_its_key_travels() -> None:
    """Open-Meteo's customer host takes `apikey` in the query (unchanged); USGS takes `X-Api-Key` on its one host."""
    key = secrets.token_hex(16)
    open_meteo = provider_endpoint_request(
        providers()["open-meteo"], "archive", {"daily": "x"}, environment={"OPEN_METEO_API_KEY": key}
    )
    usgs = provider_endpoint_request(
        providers()["usgs-water-data"], "daily", {"limit": "1"}, environment={KEY_ENV: key}
    )

    assert httpx.URL(open_meteo.send_url.reveal()).host == "customer-archive-api.open-meteo.com"
    assert httpx.URL(open_meteo.send_url.reveal()).params["apikey"] == key
    assert open_meteo.send_headers.reveal() == {}
    assert usgs.send_url.reveal() == usgs.request_url
    assert usgs.send_headers.reveal() == {KEY_HEADER: key}
    assert all(key not in text for request in (open_meteo, usgs) for text in (str(request), repr(request)))


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        ({"api_key_env": None}, "optional_api_key needs"),
        ({"api_key_header": None}, "api_key_header is declared exactly"),
        ({"api_key_transport": "query"}, "api_key_header is declared exactly"),
        ({"api_key_parameter": "api_key"}, "sends no query parameter"),
        ({"api_key_header": "Authorization"}, "api_key_header"),
        (
            {"api_key_parameter": "unredacted", "api_key_transport": "query", "api_key_header": None},
            "api_key_parameter",
        ),
    ],
)
def test_a_provider_key_declaration_must_name_one_transport_and_its_redacted_name(
    change: dict[str, object], refusal: str
) -> None:
    document = {**_provider_document(), **change}
    with pytest.raises(ValidationError, match=refusal):
        ProviderConfig.model_validate(document)


def test_optional_key_cannot_weaken_customer_host_requirement() -> None:
    document = _provider_document()
    document["endpoints"]["daily"]["customer_host"] = "customer.example.com"
    with pytest.raises(ValidationError, match="cannot weaken"):
        ProviderConfig.model_validate(document)


def test_optional_key_requires_endpoint_opt_in() -> None:
    document = _provider_document()
    document["endpoints"]["daily"]["optional_api_key"] = False
    provider = ProviderConfig.model_validate(document)
    resolved = provider_endpoint_request(provider, "daily", {}, environment={KEY_ENV: secrets.token_hex(16)})
    assert resolved.send_url.reveal() == resolved.request_url
    assert resolved.send_headers.reveal() == {}


@pytest.mark.parametrize("parameter", ["api_key", "apikey", "API_KEY"])
def test_caller_cannot_put_credentials_into_checkpoint_identity(parameter: str) -> None:
    key = secrets.token_hex(16)
    with pytest.raises(ProviderConfigError) as caught:
        provider_endpoint_request(providers()["usgs-water-data"], "daily", {parameter: key}, environment={})
    assert key not in str(caught.value)


def test_durable_checkpoint_identity_survives_optional_key_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = providers()["usgs-water-data"]
    request = SourceRequest(unit="probe", endpoint="daily", days=(), parameters=(("limit", "1"),))
    checkpoints = TurnCheckpoints(store=None, lane_id="water-gauges-daily", provider=provider)
    monkeypatch.delenv(KEY_ENV, raising=False)
    anonymous = checkpoints.identity(request)
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    keyed = checkpoints.identity(request)
    assert keyed == anonymous
    assert keyed.request_url == provider_endpoint_request(provider, "daily", request.query()).request_url
    assert key not in repr(keyed)
