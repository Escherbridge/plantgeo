"""`/health` is liveness only; `/ready` refuses with a typed reason until the bucket answers.

The handlers are awaited directly rather than driven through `app.test_client`: the test client
needs `sanic-testing`, which is not a dependency, and these handlers take no request state, so a
direct call exercises exactly the same code. Route registration is asserted separately.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any, cast

import pytest

from plantgeo_ml_service import app as app_module
from plantgeo_ml_service.app import (
    bucket_readiness_reason,
    create_app,
    liveness,
    readiness,
)
from plantgeo_ml_service.config import Settings, get_settings, names_a_database_variable

if TYPE_CHECKING:
    from sanic import Request
    from sanic.response import HTTPResponse

HTTP_OK = 200
HTTP_SERVICE_UNAVAILABLE = 503

#: The handlers ignore their request argument; this names that rather than hiding it behind a mock.
NO_REQUEST = cast("Request", None)

#: A probe budget small enough that the timeout case costs milliseconds, not the production 5 s.
PROBE_TIMEOUT_FOR_TEST_SECONDS = 0.05
#: How far past that budget the stubbed probe sleeps, so a slow CI box cannot win the race.
HANG_MULTIPLE = 20


@pytest.fixture(autouse=True)
def without_inherited_database_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings refuse to construct while any database variable is present, so clear them first."""
    for name in list(os.environ):
        if names_a_database_variable(name):
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def _payload(response: HTTPResponse) -> Any:
    """Return one Sanic JSON response's decoded body."""
    return json.loads(bytes(response.body or b"").decode("utf-8"))


async def test_health_is_ok_without_any_dependency() -> None:
    response = await liveness(NO_REQUEST)

    assert response.status == HTTP_OK
    assert _payload(response) == {"status": "ok"}


async def test_ready_refuses_with_a_typed_reason_when_the_bucket_is_unconfigured() -> None:
    response = await readiness(NO_REQUEST)

    assert response.status == HTTP_SERVICE_UNAVAILABLE
    assert _payload(response) == {"status": "not_ready", "reason": "object_store_unconfigured"}


async def test_the_readiness_reason_is_unconfigured_before_it_is_unreachable() -> None:
    assert await bucket_readiness_reason() == "object_store_unconfigured"


async def test_an_unreachable_bucket_reads_as_unreachable_not_as_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = Settings(
        _env_file=None,
        object_store_endpoint_url="https://bucket.example.test",
        object_store_bucket="plantgeo-parquet",
        object_store_access_key_id="key-id",
        object_store_secret_access_key="key-secret",
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: configured)

    def refuse(_credentials: object) -> None:
        raise ConnectionError("no route to bucket")

    monkeypatch.setattr(app_module, "head_bucket", refuse)

    assert await bucket_readiness_reason() == "object_store_unreachable"


async def test_a_bucket_that_never_answers_reads_as_a_timeout_not_as_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung HEAD must land on its own reason: `unreachable` would send an operator after DNS."""
    configured = Settings(
        _env_file=None,
        object_store_endpoint_url="https://bucket.example.test",
        object_store_bucket="plantgeo-parquet",
        object_store_access_key_id="key-id",
        object_store_secret_access_key="key-secret",
    )
    monkeypatch.setattr(app_module, "get_settings", lambda: configured)
    monkeypatch.setattr(app_module, "BUCKET_PROBE_TIMEOUT_SECONDS", PROBE_TIMEOUT_FOR_TEST_SECONDS)

    def hang(_credentials: object) -> None:
        time.sleep(PROBE_TIMEOUT_FOR_TEST_SECONDS * HANG_MULTIPLE)

    monkeypatch.setattr(app_module, "head_bucket", hang)

    assert await bucket_readiness_reason() == "object_store_timeout"

    response = await readiness(NO_REQUEST)
    assert response.status == HTTP_SERVICE_UNAVAILABLE
    assert _payload(response) == {"status": "not_ready", "reason": "object_store_timeout"}


def test_the_factory_mounts_health_readiness_and_the_four_versioned_reads() -> None:
    """Phase 2C replaced the 501 artifacts placeholder; `tests/test_routes_*.py` cover behaviour."""
    application = create_app()
    paths = {route.uri for route in application.router.routes}

    assert "/health" in paths
    assert "/ready" in paths
    assert "/api/v1/ml/fire-risk" in paths
    assert "/api/v1/ml/analogs" in paths
    assert "/api/v1/ml/forecast-summary" in paths
    assert any(path.startswith("/api/v1/ml/artifacts") for path in paths)
