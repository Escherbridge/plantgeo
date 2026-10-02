"""Optional credentials reach only declared endpoints and never durable request identities."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError

from agri_data_service.foundation.lane_config import ProviderConfig
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.provider_client import ProviderConfigError, provider_endpoint_request
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
from agri_data_service.pipeline.runner.contract import ProviderConfigurationError, SourceRequest
from tests.runner.fakes import ManualClock, providers

if TYPE_CHECKING:
    from typing import Any

KEY_ENV = "USGS_WATER_DATA_API_KEY"


def _provider_document() -> dict[str, Any]:
    provider = providers()["usgs-water-data"]
    return {
        **provider.model_dump(exclude={"endpoints"}),
        "endpoints": {name: endpoint.model_dump() for name, endpoint in provider.endpoints.items()},
    }


@pytest.mark.parametrize("endpoint", ["daily", "monitoring-locations"])
@pytest.mark.parametrize("key_state", ["missing", "blank", "present"])
async def test_optional_key_preserves_identity_and_reaches_only_the_declared_host(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, key_state: str
) -> None:
    key = secrets.token_hex(16)
    monkeypatch.delenv(KEY_ENV, raising=False)
    if key_state != "missing":
        monkeypatch.setenv(KEY_ENV, "  " if key_state == "blank" else f" {key} ")
    provider = providers()["usgs-water-data"]
    anonymous = provider_endpoint_request(provider, endpoint, {"limit": "1"}, environment={})
    resolved = provider_endpoint_request(provider, endpoint, {"limit": "1"})
    assert resolved.request_url == anonymous.request_url
    assert key not in str(resolved)
    assert key not in repr(resolved)
    assert "api_key=" not in resolved.request_url
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"features": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
        client = ConfigProviderClient(provider=provider, http=http, clock=ManualClock())
        response = await client.get(endpoint, {"limit": "1"})
    assert response.request_url == anonymous.request_url
    assert [(request.url.host, request.url.path, request.url.params.get("api_key")) for request in sent] == [
        (provider.endpoints[endpoint].host, provider.endpoints[endpoint].path, key if key_state == "present" else None)
    ]


@pytest.mark.parametrize("status", [302, 401, 403])
async def test_optional_key_redirects_and_errors_never_forward_or_report_the_credential(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    sent: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(status, headers={"location": f"https://other.example/items?api_key={key}"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as http:
        client = ConfigProviderClient(provider=providers()["usgs-water-data"], http=http, clock=ManualClock())
        with pytest.raises((UpstreamHttpError, ProviderConfigurationError)) as caught:
            await client.get("daily", {})
    assert [request.url.host for request in sent] == ["api.waterdata.usgs.gov"]
    assert key not in str(caught.value)
    assert key not in repr(caught.value)


@pytest.mark.parametrize("field", ["api_key_env", "api_key_parameter"])
def test_optional_endpoint_requires_both_credential_declarations(field: str) -> None:
    document = _provider_document()
    document.pop(field)
    with pytest.raises(ValidationError, match="optional_api_key needs"):
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


@pytest.mark.parametrize("parameter", ["api_key", "apikey", "API_KEY"])
def test_caller_cannot_put_credentials_into_checkpoint_identity(parameter: str) -> None:
    key = secrets.token_hex(16)
    with pytest.raises(ProviderConfigError) as caught:
        provider_endpoint_request(providers()["usgs-water-data"], "daily", {parameter: key}, environment={})
    assert key not in str(caught.value)


def test_arbitrary_unredacted_query_parameter_is_rejected() -> None:
    document = _provider_document()
    document["api_key_parameter"] = "unredacted"
    with pytest.raises(ValidationError, match="api_key_parameter"):
        ProviderConfig.model_validate(document)


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
