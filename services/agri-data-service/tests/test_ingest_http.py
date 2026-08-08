"""Bounded upstream fetch: the byte cap, the timeout, and the typed errors that keep a 404 distinguishable."""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.http import (
    TRANSPORT_RETRY_ATTEMPTS,
    BoundedResponse,
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    fetch_bounded,
    fetch_bounded_json,
    fetch_bounded_text,
    upstream_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

SMALL_BOUNDS = UpstreamBounds(max_bytes=64, timeout_seconds=1.0)
TINY_BOUNDS = UpstreamBounds(max_bytes=10, timeout_seconds=1.0)
UPSTREAM_URL = "https://upstream.test/records?MAP_KEY=secret-api-key"


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """An httpx client whose transport is the supplied handler, so no socket is ever opened."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def responding(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that answers every request with one prepared response."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    return handler


def raising(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    """A handler that fails the request with the supplied transport error."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise error

    return handler


async def chunked_body(chunk: bytes, count: int) -> AsyncIterator[bytes]:
    """A streaming body, which keeps httpx from declaring a content-length."""
    for _ in range(count):
        yield chunk


# --- the typed error split USDM's 404 handling depends on ----------------------------------------


async def test_a_404_raises_an_http_error_carrying_the_status() -> None:
    """USDM reads a 404 as 'not published yet' and walks back a week, so the status must survive."""
    async with client_for(responding(httpx.Response(404))) as client:
        with pytest.raises(UpstreamHttpError) as failure:
            await fetch_bounded_json(client, UPSTREAM_URL, SMALL_BOUNDS)
    assert failure.value.status == 404


async def test_a_garbled_body_raises_a_payload_error_not_an_http_error() -> None:
    """A parse failure must stay distinguishable from a status the caller can interpret."""
    response = httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"})
    async with client_for(responding(response)) as client:
        with pytest.raises(UpstreamPayloadError, match="invalid JSON"):
            await fetch_bounded_json(client, UPSTREAM_URL, SMALL_BOUNDS)


async def test_status_is_raised_before_a_body_failure() -> None:
    """A 429 answering with an oversized error page must still surface as a status, so backoff stays reachable."""
    response = httpx.Response(429, content=b"x" * 100)
    async with client_for(responding(response)) as client:
        with pytest.raises(UpstreamHttpError) as failure:
            await fetch_bounded_json(client, UPSTREAM_URL, TINY_BOUNDS)
    assert failure.value.status == 429


async def test_a_non_json_content_type_is_a_payload_error() -> None:
    """An HTML error page served with a 200 is a payload failure, not a successful parse."""
    response = httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"})
    async with client_for(responding(response)) as client:
        with pytest.raises(UpstreamPayloadError, match="not JSON"):
            await fetch_bounded_json(client, UPSTREAM_URL, SMALL_BOUNDS)


async def test_a_missing_content_type_is_accepted() -> None:
    """The TypeScript only checks the header when it is present; an absent header must not fail the parse."""
    async with client_for(responding(httpx.Response(200, content=b'{"ok":true}'))) as client:
        assert await fetch_bounded_json(client, UPSTREAM_URL, SMALL_BOUNDS) == {"ok": True}


async def test_a_json_content_type_with_a_charset_is_accepted() -> None:
    """Content-type matching is a substring check, so a charset parameter must not reject a JSON body."""
    response = httpx.Response(200, content=b'{"ok":true}', headers={"content-type": "application/json; charset=utf-8"})
    async with client_for(responding(response)) as client:
        assert await fetch_bounded_json(client, UPSTREAM_URL, SMALL_BOUNDS) == {"ok": True}


# --- the byte cap ---------------------------------------------------------------------------------


async def test_an_oversized_declared_length_is_rejected() -> None:
    """A content-length beyond the cap is refused before the body is read at all."""
    async with client_for(responding(httpx.Response(200, content=b"x" * 100))) as client:
        result = await fetch_bounded(client, UPSTREAM_URL, TINY_BOUNDS)
    assert isinstance(result.payload_error, UpstreamPayloadError)
    assert result.text == ""
    assert result.status == 200


async def test_an_oversized_streamed_body_is_rejected_mid_stream() -> None:
    """A chunked body that declares no length is still capped while it streams."""
    response = httpx.Response(200, content=chunked_body(b"x" * 8, 4))
    async with client_for(responding(response)) as client:
        with pytest.raises(UpstreamPayloadError, match="byte limit"):
            await fetch_bounded_text(client, UPSTREAM_URL, TINY_BOUNDS)


async def test_an_unreadable_declared_length_is_a_payload_error() -> None:
    """A non-numeric content-length is upstream garbage, not a body worth reading."""
    response = httpx.Response(200, content=chunked_body(b"x", 1), headers={"content-length": "not-a-number"})
    async with client_for(responding(response)) as client:
        result = await fetch_bounded(client, UPSTREAM_URL, SMALL_BOUNDS)
    assert isinstance(result.payload_error, UpstreamPayloadError)


async def test_a_body_exactly_at_the_cap_is_accepted() -> None:
    """The cap is inclusive, so a payload of exactly max_bytes must not be rejected."""
    async with client_for(responding(httpx.Response(200, content=b"x" * 10))) as client:
        assert await fetch_bounded_text(client, UPSTREAM_URL, TINY_BOUNDS) == "x" * 10


# --- fetch_bounded never raises on status or body -------------------------------------------------


async def test_fetch_bounded_reports_a_server_error_without_raising() -> None:
    """The core fetch reports status and body separately so status-based handling stays reachable."""
    async with client_for(responding(httpx.Response(503, content=b"unavailable"))) as client:
        result = await fetch_bounded(client, UPSTREAM_URL, SMALL_BOUNDS)
    assert result.status == 503
    assert result.ok is False
    assert result.payload_error is None
    assert result.text == "unavailable"


@pytest.mark.parametrize(("status", "expected"), [(200, True), (299, True), (300, False), (199, False)])
def test_bounded_response_ok_covers_only_the_2xx_range(status: int, expected: bool) -> None:
    """`ok` is a 2xx test, matching the Response.ok it replaces."""
    assert BoundedResponse(status=status, content_type=None, text="", payload_error=None).ok is expected


async def test_fetch_bounded_text_raises_on_a_non_2xx_status() -> None:
    """The text entry point applies the same status-first rule as the JSON one."""
    async with client_for(responding(httpx.Response(500, content=b"boom"))) as client:
        with pytest.raises(UpstreamHttpError) as failure:
            await fetch_bounded_text(client, UPSTREAM_URL, SMALL_BOUNDS)
    assert failure.value.status == 500


async def test_fetch_bounded_text_returns_a_csv_body() -> None:
    """FIRMS answers with CSV, which must come back untouched."""
    body = b"latitude,longitude\n47.838,-113.2649\n"
    async with client_for(responding(httpx.Response(200, content=body))) as client:
        assert await fetch_bounded_text(client, UPSTREAM_URL, SMALL_BOUNDS) == body.decode()


# --- transport failures ---------------------------------------------------------------------------


async def test_a_timeout_becomes_a_typed_timeout_error() -> None:
    """A slow upstream is its own failure class, not a payload or status failure."""
    async with client_for(raising(httpx.ReadTimeout("too slow"))) as client:
        with pytest.raises(UpstreamTimeoutError, match="timed out"):
            await fetch_bounded(client, UPSTREAM_URL, SMALL_BOUNDS)


async def test_a_transport_failure_never_echoes_the_url() -> None:
    """FIRMS request URLs embed an API key, so a transport error must not carry the URL into a log."""
    async with client_for(raising(httpx.ConnectError("connection refused"))) as client:
        with pytest.raises(UpstreamTransportError) as failure:
            await fetch_bounded(client, UPSTREAM_URL, SMALL_BOUNDS)

    message = str(failure.value)
    assert "secret-api-key" not in message
    assert "upstream.test" not in message
    assert "ConnectError" in message


# --- client construction ---------------------------------------------------------------------------


async def test_upstream_client_applies_the_caller_bounds_as_its_default_timeout() -> None:
    """One client per run carries the caller's timeout so every request inherits the same bound."""
    async with upstream_client(UpstreamBounds(max_bytes=1024, timeout_seconds=2.5)) as client:
        assert client.timeout.read == 2.5
        assert client.timeout.connect == 2.5


class _FlakyTransport(httpx.AsyncBaseTransport):
    """Fails with a transport error for the first `failures` calls, then answers normally."""

    def __init__(self, failures: int, error: Exception | None = None) -> None:
        self.failures = failures
        self.calls = 0
        self.error = error or httpx.ConnectError("connection refused")

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002 - httpx's own signature
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})


@pytest.mark.asyncio
async def test_a_transient_connect_error_is_retried_rather_than_failing_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The measured fault: 169 of 298 FIRMS windows and 46 of 95 streamflow windows died on
    # ConnectError during one walk, costing 2023-12 through 2026-06 of fire history entirely.
    # The same days succeed on a later attempt, so the fault is the local connection path.
    monkeypatch.setattr("agri_data_service.ingest.http.TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    transport = _FlakyTransport(failures=2)

    async with httpx.AsyncClient(transport=transport) as client:
        payload = await fetch_bounded_json(client, "https://example.test/x", UpstreamBounds(1024, 5.0))

    assert payload == {"ok": True}
    assert transport.calls == 3


@pytest.mark.asyncio
async def test_a_persistent_transport_fault_still_raises_the_same_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Retrying must not swallow a real outage: a caller that exhausts the attempts sees exactly
    # what it saw before the retry existed, only later.
    monkeypatch.setattr("agri_data_service.ingest.http.TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    transport = _FlakyTransport(failures=99)

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UpstreamTransportError):
            await fetch_bounded_json(client, "https://example.test/x", UpstreamBounds(1024, 5.0))

    assert transport.calls == TRANSPORT_RETRY_ATTEMPTS


@pytest.mark.asyncio
async def test_a_timeout_keeps_its_own_type_after_the_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agri_data_service.ingest.http.TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    transport = _FlakyTransport(failures=99, error=httpx.ConnectTimeout("slow"))

    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(UpstreamTimeoutError):
            await fetch_bounded_json(client, "https://example.test/x", UpstreamBounds(1024, 5.0))


@pytest.mark.asyncio
async def test_a_non_2xx_status_is_answered_once_and_never_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agri_data_service.ingest.http.TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={}, headers={"content-type": "application/json"})

    # A status is the upstream's considered answer. Retrying it here would hammer a service that
    # has already replied AND would pre-empt the per-source 429/503 backoff policies.
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_bounded_json(client, "https://example.test/x", UpstreamBounds(1024, 5.0))

    assert calls["n"] == 1
