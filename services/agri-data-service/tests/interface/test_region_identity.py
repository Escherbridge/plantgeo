"""Region request admission precedes endpoint/cache execution."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from types import SimpleNamespace
from typing import Any

import pytest

from agri_data_service.foundation.region.identity import definition_digest
from agri_data_service.foundation.region.manifest import load_region
from agri_data_service.interface.http import region_identity
from agri_data_service.interface.http.region_identity import (
    EXPECTED_IDENTITY_HEADER,
    IDENTITY_HEADER,
    install_region_identity,
)
from agri_data_service.parquet_ops import duckdb_session
from agri_data_service.pipeline.parquet.region_admission import RegionAdmissionError


class CapturingBlueprint:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def middleware(self, handler: Any, phase: str) -> Any:
        self.handlers[phase] = handler
        return handler


@pytest.mark.asyncio
async def test_wrong_identity_refuses_before_endpoint_and_stamps_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_REGION_MANIFEST_JSON", raising=False)
    monkeypatch.setenv("PLANTGEO_REGION", "pnw")
    blueprint: Any = CapturingBlueprint()
    install_region_identity(blueprint)
    request = SimpleNamespace(headers={EXPECTED_IDENTITY_HEADER: "wrong"}, ctx=SimpleNamespace())
    response = await blueprint.handlers["request"](request)
    expected_conflict = 409
    assert response.status == expected_conflict
    await blueprint.handlers["response"](request, response)
    assert response.headers[IDENTITY_HEADER] == definition_digest(load_region())


@pytest.mark.asyncio
async def test_packaged_legacy_missing_request_identity_remains_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLANTGEO_REGION_MANIFEST_JSON", raising=False)
    monkeypatch.setenv("PLANTGEO_REGION", "pnw")
    blueprint: Any = CapturingBlueprint()
    install_region_identity(blueprint)
    request = SimpleNamespace(headers={}, ctx=SimpleNamespace())
    assert await blueprint.handlers["request"](request) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_header", [False, True])
async def test_custom_identity_checks_precede_endpoint(monkeypatch: pytest.MonkeyPatch, matching_header: bool) -> None:
    fixture = (
        resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json").read_text()
    )
    monkeypatch.setenv("PLANTGEO_REGION_MANIFEST_JSON", fixture)
    monkeypatch.setenv("PLANTGEO_REGION", "custom-colorado")
    region = load_region()
    calls: list[str] = []

    def credentials() -> object:
        return object()

    def backend(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(client=object(), bucket="bucket")

    def refuse(_client: object, _bucket: str, _key: str) -> None:
        calls.append("namespace-before-endpoint")
        raise RegionAdmissionError("region_namespace_receipt_missing")

    monkeypatch.setattr(
        region_identity, "settings", SimpleNamespace(require_object_store=credentials, object_store_prefix="prefix")
    )
    monkeypatch.setattr(region_identity.BotoObjectStoreBackend, "from_credentials", backend)
    monkeypatch.setattr(region_identity, "guard_custom_operation", refuse)
    blueprint: Any = CapturingBlueprint()
    install_region_identity(blueprint)
    headers = {EXPECTED_IDENTITY_HEADER: definition_digest(region)} if matching_header else {}
    request = SimpleNamespace(headers=headers, ctx=SimpleNamespace())
    response = await blueprint.handlers["request"](request)
    expected_conflict = 409
    assert response.status == expected_conflict
    assert calls == (["namespace-before-endpoint"] if matching_header else [])
    await blueprint.handlers["response"](request, response)
    assert response.headers[IDENTITY_HEADER] == definition_digest(region)


@pytest.mark.asyncio
async def test_namespace_capacity_retains_slot_after_request_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = (
        resources.files("agri_data_service.foundation.region").joinpath("custom_colorado.fixture.json").read_text()
    )
    monkeypatch.setenv("PLANTGEO_REGION_MANIFEST_JSON", fixture)
    monkeypatch.setenv("PLANTGEO_REGION", "custom-colorado")
    release = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()
    calls: list[str] = []
    slot = threading.BoundedSemaphore(1)
    pool = ThreadPoolExecutor(max_workers=1)

    def credentials() -> object:
        return object()

    def backend(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(client=object(), bucket="bucket")

    def blocked(_client: object, _bucket: str, _key: str) -> None:
        calls.append("receipt")
        loop.call_soon_threadsafe(started.set)
        release.wait(timeout=5)
        raise RegionAdmissionError("region_namespace_receipt_missing")

    monkeypatch.setattr(duckdb_session, "_read_pool", pool)
    monkeypatch.setattr(duckdb_session, "_read_slot", slot)
    monkeypatch.setattr(
        region_identity, "settings", SimpleNamespace(require_object_store=credentials, object_store_prefix="prefix")
    )
    monkeypatch.setattr(region_identity.BotoObjectStoreBackend, "from_credentials", backend)
    monkeypatch.setattr(region_identity, "guard_custom_operation", blocked)
    blueprint: Any = CapturingBlueprint()
    install_region_identity(blueprint)
    request = SimpleNamespace(
        headers={EXPECTED_IDENTITY_HEADER: definition_digest(load_region())}, ctx=SimpleNamespace()
    )
    first = asyncio.create_task(blueprint.handlers["request"](request))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError, match=r"^$"):
            await first
        response = await asyncio.wait_for(blueprint.handlers["request"](request), timeout=1)
        expected_conflict = 409
        assert response.status == expected_conflict
        assert json.loads(response.body) == {"error": "region_namespace_at_capacity"}
        assert calls == ["receipt"]
        release.set()
        async with asyncio.timeout(1):
            while not slot.acquire(blocking=False):
                await asyncio.sleep(0.01)
        slot.release()
        response = await asyncio.wait_for(blueprint.handlers["request"](request), timeout=1)
        assert json.loads(response.body) == {"error": "region_namespace_receipt_missing"}
        assert calls == ["receipt", "receipt"]
    finally:
        release.set()
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        pool.shutdown(wait=True, cancel_futures=True)
