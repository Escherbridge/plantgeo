"""The installed agent bridge middleware rejects identity before tool execution."""

from __future__ import annotations

import json
from importlib import resources
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agri_data_service.agent import tools
from agri_data_service.foundation.region import load_region
from agri_data_service.foundation.region.identity import definition_digest
from agri_data_service.interface.http import region_identity
from agri_data_service.interface.http.region_identity import EXPECTED_IDENTITY_HEADER, IDENTITY_HEADER
from agri_data_service.pipeline.parquet.region_admission import RegionAdmissionError
from agri_data_service.routes import agent_tools as route
from tests.agent_fakes import FakeAgentWarehouse

CONFLICT = 409
OK = 200
SELECTED_DAY = "2024-03-14"


def _custom(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json")
    monkeypatch.setenv("PLANTGEO_REGION", "custom-colorado")
    monkeypatch.setenv("PLANTGEO_REGION_MANIFEST_JSON", fixture.read_text(encoding="utf-8"))


async def _dispatch(request: Any, *, catalogue: bool = False) -> Any:
    blueprint: Any = route.agent_tools_bp
    middleware = {entry.attach_to: entry.middleware for entry in blueprint._future_middleware}
    response = await middleware["request"](request)
    if response is None:
        response = await (route.list_agent_tools(request) if catalogue else route.call_agent_tool(request))
    await middleware["response"](request, response)
    return response


def _request(header: str | None) -> Any:
    payload = {
        "name": "observation_coverage_on_day",
        "arguments": {"surface_name": "weather-observations", "day": SELECTED_DAY},
    }
    return SimpleNamespace(
        json=payload,
        body=json.dumps(payload).encode(),
        ctx=SimpleNamespace(),
        headers={} if header is None else {EXPECTED_IDENTITY_HEADER: header},
    )


def _admission(monkeypatch: pytest.MonkeyPatch, error: str | None = None) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        region_identity,
        "settings",
        SimpleNamespace(require_object_store=object, object_store_prefix="prefix"),
    )
    monkeypatch.setattr(
        region_identity.BotoObjectStoreBackend,
        "from_credentials",
        lambda *_args, **_kwargs: SimpleNamespace(client=object(), bucket="bucket"),
    )

    def guard(_client: object, _bucket: str, _key: str) -> None:
        calls.append("admission")
        if error:
            raise RegionAdmissionError(error)

    monkeypatch.setattr(region_identity, "guard_custom_operation", guard)
    return calls


@pytest.mark.parametrize("catalogue", [False, True])
@pytest.mark.parametrize("header", [None, "wrong"])
async def test_custom_identity_refuses_before_catalogue_or_tool(
    monkeypatch: pytest.MonkeyPatch, catalogue: bool, header: str | None
) -> None:
    _custom(monkeypatch)
    admission = _admission(monkeypatch)
    invoked = AsyncMock()
    monkeypatch.setattr(route, "tool_by_name", lambda _name: SimpleNamespace(call=invoked))
    response = await _dispatch(_request(header), catalogue=catalogue)
    assert response.status == CONFLICT
    assert json.loads(response.body)["error"] == "region_definition_mismatch"
    assert response.headers[IDENTITY_HEADER] == definition_digest(load_region())
    invoked.assert_not_called()
    assert admission == []


@pytest.mark.parametrize("custom", [False, True])
async def test_matching_custom_or_legacy_builtin_preserves_selected_day(
    monkeypatch: pytest.MonkeyPatch, custom: bool
) -> None:
    if custom:
        _custom(monkeypatch)
    else:
        monkeypatch.setenv("PLANTGEO_REGION", "pnw")
        monkeypatch.delenv("PLANTGEO_REGION_MANIFEST_JSON", raising=False)
    admission = _admission(monkeypatch)
    invoked = AsyncMock(return_value=json.dumps({"requested_day": SELECTED_DAY}))
    monkeypatch.setattr(route, "tool_by_name", lambda _name: SimpleNamespace(call=invoked))
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=FakeAgentWarehouse()))
    expected = definition_digest(load_region())
    response = await _dispatch(_request(expected if custom else None))
    assert response.status == OK
    assert json.loads(response.body)["result"]["requested_day"] == SELECTED_DAY
    invoked.assert_awaited_once_with({"surface_name": "weather-observations", "day": SELECTED_DAY})
    assert admission == (["admission"] if custom else [])
    assert response.headers[IDENTITY_HEADER] == expected


@pytest.mark.parametrize("reason", ["region_namespace_receipt_missing", "region_source_support_not_admitted"])
async def test_namespace_and_source_refusals_are_not_argument_errors(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _custom(monkeypatch)
    _admission(monkeypatch, reason)
    invoked = AsyncMock()
    monkeypatch.setattr(route, "tool_by_name", lambda _name: SimpleNamespace(call=invoked))
    response = await _dispatch(_request(definition_digest(load_region())))
    assert response.status == CONFLICT
    assert json.loads(response.body)["error"] == reason
    invoked.assert_not_called()


async def test_late_admission_refusal_is_sanitized_before_value_error_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "unexpected-private-storage-detail"
    invoked = AsyncMock(side_effect=RegionAdmissionError(secret))
    monkeypatch.setattr(route, "tool_by_name", lambda _name: SimpleNamespace(call=invoked))
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=FakeAgentWarehouse()))
    response = await route.call_agent_tool(_request(None))
    assert response.status == CONFLICT
    assert json.loads(response.body)["code"] == "region_admission_refused"
    assert secret not in response.body.decode()
