"""Bounded upstream HTTP: a byte cap, a timeout, and typed failures that keep a 404 distinguishable."""

from __future__ import annotations

import asyncio
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


class UpstreamPayloadTooLargeError(UpstreamPayloadError):
    """Raised for the oversized case alone, carrying the cap it broke and the size that broke it.

    A SUBCLASS, never a replacement: every existing `except UpstreamPayloadError` still catches this,
    `upstream_retry.py::is_retryable_failure` still declines to retry it (the message never matches
    `BUSY_MESSAGE_PATTERN`), and the message still opens with the exact string production logged --
    so an operator's existing filter keeps matching. What is new is that a caller which wants to
    ADAPT to the refusal rather than fail on it can now read the numbers instead of parsing prose.
    See ingest/AGENTS.md "arcgis.py: page-size halving, and the record that fits in no page".
    """

    def __init__(
        self,
        *,
        limit_bytes: int,
        declared_bytes: int | None = None,
        observed_bytes: int | None = None,
    ) -> None:
        """Record the cap and whichever of the declared or observed size proved it was broken."""
        evidence = f"declared {declared_bytes}" if declared_bytes is not None else f"read {observed_bytes}"
        super().__init__(f"upstream response exceeded the byte limit ({evidence} bytes against {limit_bytes})")
        self.limit_bytes = limit_bytes
        self.declared_bytes = declared_bytes
        self.observed_bytes = observed_bytes

    @property
    def transferred_bytes(self) -> int:
        """Bytes actually pulled off the wire before the cap tripped; zero when `content-length` refused it first."""
        return self.observed_bytes or 0

    @property
    def size_bytes(self) -> int | None:
        """The best available measure of how big the body was, declared first because it is the whole size."""
        return self.declared_bytes if self.declared_bytes is not None else self.observed_bytes


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
    # Bytes read off the wire for this response, whether or not the body survived the cap. Defaulted
    # so every existing construction (and every test that builds one by hand) still type-checks; the
    # only caller that needs a real number is a paged walk budgeting its own total transfer.
    byte_count: int = 0

    @property
    def ok(self) -> bool:
        """True for a 2xx status."""
        return SUCCESS_STATUS_MINIMUM <= self.status < SUCCESS_STATUS_MAXIMUM


@dataclass(frozen=True, slots=True)
class SizedJson:
    """One parsed JSON body beside what it cost to read, for a caller budgeting a whole walk's transfer."""

    payload: object
    byte_count: int


# How many times a transport-level failure is re-attempted, and the base delay between attempts.
#
# Measured 2026-08-07 over the first full archive walk: 169 of 298 FIRMS windows and 46 of 95
# streamflow windows failed, almost entirely `ConnectError` with a couple of `getaddrinfo failed`.
# The same days succeed on a later attempt, so what failed was the local connection and DNS path
# under sustained load, not the upstream. Left unhandled that cost 2.5 years of fire history --
# 2023-12 through 2026-06 had no detections at all, which read as sparse fire seasons rather than
# as lost work.
#
# Retrying HERE rather than only in the shell driver is what makes every source benefit: the
# forward cron jobs hit the same transient faults and had no retry at all, they simply reported a
# failed run. Only GETs go through this module, so a re-attempt is always safe.
TRANSPORT_RETRY_ATTEMPTS: Final = 3
TRANSPORT_RETRY_BASE_SECONDS: Final = 2.0

# Ceiling on simultaneous sockets per client. httpx defaults to 100, which is precisely how a
# tiled fetch exhausts the local connection table; every caller here fans out over at most a
# handful of tiles at a time, so this is a bound on the failure mode rather than on throughput.
MAX_UPSTREAM_CONNECTIONS: Final = 10
MAX_KEEPALIVE_CONNECTIONS: Final = 5


@asynccontextmanager
async def upstream_client(bounds: UpstreamBounds) -> AsyncIterator[httpx.AsyncClient]:
    """Yield one client whose default timeout is the caller's bound and whose redirects are capped."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(bounds.timeout_seconds),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        limits=httpx.Limits(
            max_connections=MAX_UPSTREAM_CONNECTIONS,
            max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        ),
    ) as client:
        yield client


async def _read_bounded_body(
    response: httpx.Response,
    bounds: UpstreamBounds,
) -> tuple[bytes, int, UpstreamPayloadError | None]:
    """Read a response body under the byte cap, rejecting an oversized declaration before reading anything.

    The middle element is how many bytes actually crossed the wire -- zero when `content-length`
    refused the body before a single chunk was read, which is the distinction a caller budgeting a
    whole walk's transfer needs and cannot recover afterwards.
    """
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError:
            return b"", 0, UpstreamPayloadError("upstream declared an unreadable content length")
        if declared_bytes < 0 or declared_bytes > bounds.max_bytes:
            return b"", 0, UpstreamPayloadTooLargeError(limit_bytes=bounds.max_bytes, declared_bytes=declared_bytes)

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in response.aiter_bytes():
        total_bytes += len(chunk)
        if total_bytes > bounds.max_bytes:
            refusal = UpstreamPayloadTooLargeError(limit_bytes=bounds.max_bytes, observed_bytes=total_bytes)
            return b"", total_bytes, refusal
        chunks.append(chunk)
    return b"".join(chunks), total_bytes, None


async def fetch_bounded(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> BoundedResponse:
    """Fetch a URL under a byte cap and timeout, never raising on a non-2xx status or an unreadable body.

    A transport fault is re-attempted before it becomes a failure; a non-2xx status is not. The
    distinction is deliberate: a connect error or a timeout says nothing about the request, whereas
    a status is the upstream's considered answer and retrying it would hammer a service that has
    already replied. Status handling stays entirely with the caller, which is what lets a source
    treat 429 and 503 with its own backoff policy.

    The raised exception types are unchanged, so a caller that exhausts the retries sees exactly
    what it saw before -- only later, and only after the fault proved persistent.
    """
    last_error: httpx.HTTPError | None = None
    for attempt in range(1, TRANSPORT_RETRY_ATTEMPTS + 1):
        try:
            async with client.stream(
                "GET", url, headers=dict(headers or {}), timeout=bounds.timeout_seconds
            ) as response:
                body, byte_count, payload_error = await _read_bounded_body(response, bounds)
                return BoundedResponse(
                    status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    text=body.decode("utf-8", errors="replace"),
                    payload_error=payload_error,
                    byte_count=byte_count,
                )
        except httpx.HTTPError as error:
            last_error = error
            if attempt == TRANSPORT_RETRY_ATTEMPTS:
                break
            # Exponential, because a connection table that is full drains on its own and the point
            # is to stop adding to it. Nothing is retried after the body has begun streaming: a
            # partial read raises inside _read_bounded_body as an UpstreamPayloadError, which is
            # returned rather than raised and so never reaches here.
            await asyncio.sleep(TRANSPORT_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    if isinstance(last_error, httpx.TimeoutException):
        raise UpstreamTimeoutError("upstream request timed out") from last_error
    raise UpstreamTransportError(f"upstream request failed ({last_error.__class__.__name__})") from last_error


async def fetch_bounded_json_sized(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> SizedJson:
    """Fetch and parse JSON, reporting what the body cost as well as what it said.

    Identical in every observable way to `fetch_bounded_json`, which delegates here: same order of
    raises (status before body, so a 429 answering with a huge error page stays reachable for
    backoff), same exception types. The only addition is the byte count, which a paged walk needs to
    hold a whole run inside a transfer budget rather than only each request inside a per-request cap.
    """
    response = await fetch_bounded(client, url, bounds, headers)
    if not response.ok:
        raise UpstreamHttpError(response.status)
    if response.payload_error is not None:
        raise response.payload_error
    if response.content_type is not None and "json" not in response.content_type.lower():
        raise UpstreamPayloadError("upstream response was not JSON")
    try:
        return SizedJson(payload=json.loads(response.text), byte_count=response.byte_count)
    except ValueError as error:
        raise UpstreamPayloadError("upstream response contained invalid JSON") from error


async def fetch_bounded_json(
    client: httpx.AsyncClient,
    url: str,
    bounds: UpstreamBounds,
    headers: Mapping[str, str] | None = None,
) -> object:
    """Fetch and parse JSON, raising the status failure before the body failure so backoff stays reachable."""
    return (await fetch_bounded_json_sized(client, url, bounds, headers)).payload


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
