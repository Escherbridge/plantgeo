"""Bounded upstream HTTP: a byte cap, a timeout, and typed failures that keep a 404 distinguishable."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

HTTP_NOT_FOUND: Final = 404
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR_MINIMUM: Final = 500

SUCCESS_STATUS_MINIMUM: Final = 200
SUCCESS_STATUS_MAXIMUM: Final = 300
MAX_REDIRECTS: Final = 3


class UpstreamError(Exception):
    """Base class for every bounded-upstream failure."""


class UpstreamHttpError(UpstreamError):
    """Raised for a non-2xx upstream status; carries the status so 404 and 429 stay distinguishable."""

    def __init__(self, status: int) -> None:
        """Record the status the upstream answered with."""
        super().__init__(f"upstream request failed with status {status}")
        self.status = status


class UpstreamPayloadError(UpstreamError):
    """Raised when an upstream body is oversized, absent, mistyped, or unparseable."""


class UpstreamTimeoutError(UpstreamError):
    """Raised when an upstream did not answer within the bounded timeout."""


class UpstreamTransportError(UpstreamError):
    """Raised when a request never completed; its message never carries the URL, which may hold an API key."""


@dataclass(frozen=True, slots=True)
class UpstreamBounds:
    """The response-size ceiling and request timeout one upstream is allowed to consume."""

    max_bytes: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class BoundedResponse:
    """A completed bounded fetch; a body failure is reported rather than raised so status handling stays reachable."""

    status: int
    content_type: str | None
    text: str
    payload_error: UpstreamPayloadError | None

    @property
    def ok(self) -> bool:
        """True for a 2xx status."""
        return SUCCESS_STATUS_MINIMUM <= self.status < SUCCESS_STATUS_MAXIMUM


@asynccontextmanager
async def upstream_client(bounds: UpstreamBounds) -> AsyncIterator[httpx.AsyncClient]:
    """Yield one client whose default timeout is the caller's bound and whose redirects are capped."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(bounds.timeout_seconds),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
    ) as client:
        yield client


async def _read_bounded_body(
    response: httpx.Response,
    bounds: UpstreamBounds,
) -> tuple[bytes, UpstreamPayloadError | None]:
    """Read a response body under the byte cap, rejecting an oversized declaration before reading anything."""
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError:
            return b"", UpstreamPayloadError("upstream declared an unreadable content length")
        if declared_bytes < 0 or declared_bytes > bounds.max_bytes:
            return b"", UpstreamPayloadError("upstream response exceeded the byte limit")

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in response.aiter_bytes():
        total_bytes += len(chunk)
        if total_bytes > bounds.max_bytes:
            return b"", UpstreamPayloadError("upstream response exceeded the byte limit")
        chunks.append(chunk)
    return b"".join(chunks), None


async def fetch_bounded(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> BoundedResponse:
    """Fetch a URL under a byte cap and timeout, never raising on a non-2xx status or an unreadable body."""
    try:
        async with client.stream("GET", url, headers=dict(headers or {}), timeout=bounds.timeout_seconds) as response:
            body, payload_error = await _read_bounded_body(response, bounds)
            return BoundedResponse(
                status=response.status_code,
                content_type=response.headers.get("content-type"),
                text=body.decode("utf-8", errors="replace"),
                payload_error=payload_error,
            )
    except httpx.TimeoutException as error:
        raise UpstreamTimeoutError("upstream request timed out") from error
    except httpx.HTTPError as error:
        raise UpstreamTransportError(f"upstream request failed ({error.__class__.__name__})") from error


async def fetch_bounded_json(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> object:
    """Fetch and parse JSON, raising the status failure before the body failure so backoff stays reachable."""
    response = await fetch_bounded(client, url, bounds, headers)
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("upstream response was not JSON")
    try:
        return json.loads(response.text)
    except ValueError as error:
        raise UpstreamPayloadError("upstream response contained invalid JSON") from error


async def fetch_bounded_text(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> str:
    """Fetch a text or CSV body without allowing unbounded buffering."""
    response = await fetch_bounded(client, url, bounds, headers)
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    return response.text
