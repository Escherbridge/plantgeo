"""`ingest/http.py::retry_after_seconds_for_response` (SOFT-8's status-aware helper) and its wiring
into `fetch_bounded`'s `BoundedResponse.retry_after_seconds`.

Table-driven pure-logic unit tests for the status/header branching, plus one flow test proving
`fetch_bounded` itself populates the field from a real (mocked-transport) response -- the seam
`ingest/provider_client.py` actually depends on.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamBounds, fetch_bounded, retry_after_seconds_for_response

BOUNDS = UpstreamBounds(max_bytes=1024, timeout_seconds=1.0)


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        pytest.param(429, {"retry-after": "5"}, 5.0, id="429-honoured"),
        pytest.param(503, {"retry-after": "12"}, 12.0, id="5xx-honoured"),
        pytest.param(500, {"retry-after": "1"}, 1.0, id="server-error-floor-honoured"),
        pytest.param(429, {}, None, id="429-no-header"),
        pytest.param(404, {"retry-after": "5"}, None, id="404-header-ignored-not-eligible"),
        pytest.param(200, {"retry-after": "5"}, None, id="200-header-ignored-not-eligible"),
        pytest.param(429, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None, id="http-date-form-not-parsed"),
        pytest.param(429, {"retry-after": "not-a-number"}, None, id="garbled-value"),
    ],
)
def test_retry_after_is_status_aware(status: int, headers: dict[str, str], expected: float | None) -> None:
    assert retry_after_seconds_for_response(status, httpx.Headers(headers)) == expected


async def test_fetch_bounded_carries_the_retry_after_seconds_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, headers={"Retry-After": "7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await fetch_bounded(client, "https://upstream.test/x", BOUNDS)

    assert response.status == 429
    assert response.retry_after_seconds == 7.0


async def test_fetch_bounded_leaves_retry_after_seconds_none_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await fetch_bounded(client, "https://upstream.test/x", BOUNDS)

    assert response.retry_after_seconds is None
