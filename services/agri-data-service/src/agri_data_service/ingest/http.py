"""Bounded upstream HTTP: a byte cap, a timeout, typed failures, and the GL-2 per-host source meter.

See `ingest/AGENTS.md` "http.py: the GL-2 source-usage meter" for the counter contract, the
`PLANTGEO_UPSTREAM_TELEMETRY` fail-open switch, and the WQ-5 User-Agent. Metering writes into
`foundation.observability.usage._host_counters`, the module-level accumulator that module's own
docstring names as this slice's hook point -- every write goes through this module alone so the
counter shapes in both files never drift apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import os
import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from agri_data_service.foundation.observability import bootstrap, events, usage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator, Mapping

logger = structlog.get_logger()

HTTP_NOT_FOUND: Final = 404
HTTP_UNAUTHORIZED: Final = 401
HTTP_FORBIDDEN: Final = 403
HTTP_TOO_MANY_REQUESTS: Final = 429
HTTP_SERVER_ERROR_MINIMUM: Final = 500

SUCCESS_STATUS_MINIMUM: Final = 200
SUCCESS_STATUS_MAXIMUM: Final = 300
REDIRECT_STATUS_MINIMUM: Final = 300
CLIENT_ERROR_STATUS_MINIMUM: Final = 400
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

# --- GL-2 source-usage meter (design §2.1, §2.5; ingest/AGENTS.md "http.py: the GL-2 source-usage
# meter") --------------------------------------------------------------------------------------
#
# Unrecognised (or missing) means ON here, deliberately the opposite sense of the value check below:
# unset is the common case and must cost nothing to spell correctly, while a garbled value must fail
# TOWARD the safe state -- which for a rollout switch is OFF (today's byte-for-byte legacy client),
# not on (design SOF2-10, "a garbled switch resolves to the legacy state").
UPSTREAM_TELEMETRY_ENV_VAR: Final = "PLANTGEO_UPSTREAM_TELEMETRY"
UPSTREAM_CONTACT_ENV_VAR: Final = "PLANTGEO_UPSTREAM_CONTACT"
_RAILWAY_GIT_COMMIT_SHA_ENV_VAR: Final = "RAILWAY_GIT_COMMIT_SHA"
_UNKNOWN_COMMIT_SHA: Final = "unknown"
_COMMIT_SHA_DIGITS: Final = 7
_USER_AGENT_PRODUCT: Final = "plantgeo-agri-data-service"

# The header-value character band a WQ-5 User-Agent is sanitised down to (design §2.1: the resolver
# must never fail a send). Anything outside printable ASCII -- an accented contact name, an embedded
# CR/LF -- is dropped rather than passed through, because httpx/h11 raise on either (a non-ASCII
# header value at client construction, a CR/LF header on every send).
_ASCII_PRINTABLE_MINIMUM: Final = 0x20
_ASCII_PRINTABLE_MAXIMUM: Final = 0x7E

# design §1.8: "Failed and retried requests: 20 per host per process, then counted" -- past the cap
# the per-host counters below still update every time, only the log LINE stops.
MAX_LOGGED_SOURCE_EVENTS_PER_HOST: Final = 20


def upstream_telemetry_enabled() -> bool:
    """Whether the meter and the WQ-5 User-Agent are attached to a new client; see `ingest/AGENTS.md`."""
    raw = os.environ.get(UPSTREAM_TELEMETRY_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().casefold() in bootstrap._SWITCH_ON_VALUES


def _commit_sha7() -> str:
    sha = os.environ.get(_RAILWAY_GIT_COMMIT_SHA_ENV_VAR, "").strip()
    return sha[:_COMMIT_SHA_DIGITS] if sha else _UNKNOWN_COMMIT_SHA


def _package_version() -> str:
    """Resolve the installed package version lazily, inside `default_user_agent`'s own fail-open try.

    A module-level `from agri_data_service import __version__ as _PACKAGE_VERSION` reads as a
    lowercase dunder imported under a non-lowercase name (ruff N812) and, more to the point, gives
    the resolver nothing to catch if it ever fails: importing the package's own root here instead
    keeps the lookup inside `default_user_agent`'s `try`.
    """
    import agri_data_service  # noqa: PLC0415 - deliberately lazy, so a failure here is inside the caller's try

    return agri_data_service.__version__


def _sanitize_contact(raw: str) -> str:
    """Drop every character outside printable ASCII from an operator-supplied contact string.

    `PLANTGEO_UPSTREAM_CONTACT` becomes a literal HTTP header value with no further validation
    upstream of this call. An accented character raises `UnicodeEncodeError` inside the httpx client
    constructor; an embedded CR/LF is a header-injection shape that makes h11 raise
    `LocalProtocolError` on every send. Both would turn a cosmetic contact string into every source
    lane failing every request -- exactly what the fail-open design rule (§2.1) forbids.
    """
    return "".join(
        character for character in raw if _ASCII_PRINTABLE_MINIMUM <= ord(character) <= _ASCII_PRINTABLE_MAXIMUM
    )


def default_user_agent() -> str:
    """The WQ-5 identifying User-Agent every metered client sends, failing open; see `ingest/AGENTS.md`."""
    try:
        agent = f"{_USER_AGENT_PRODUCT}/{_package_version()}+{_commit_sha7()}"
        contact = _sanitize_contact(os.environ.get(UPSTREAM_CONTACT_ENV_VAR, "").strip())
        return f"{agent} (+{contact})" if contact else agent
    except Exception as error:  # the identifying header must never fail the send it labels
        _note_meter_error(error)
        return _USER_AGENT_PRODUCT


_meter_error_count = 0
_meter_error_logged = False


def meter_error_count() -> int:
    """This process's count of fail-open metering faults; a test seam, never part of the usage line."""
    return _meter_error_count


def _note_meter_error(error: Exception) -> None:
    """Count a metering fault and log it once per process (design: 'warn, once per process').

    Also bumps `usage.record_meter_error()` (o5a's granted extension, GL-2 review MEDIUM #2): THIS
    module's own `_meter_error_count` only serves its local `meter_error_count()` test seam, and was
    never read by the usage line that reports `meter_errors` to an operator -- that line is written
    from `usage.py`, so the count it reads must live there too.
    """
    global _meter_error_count, _meter_error_logged  # noqa: PLW0603 - the documented per-process fault counter
    _meter_error_count += 1
    usage.record_meter_error()
    if not _meter_error_logged:
        _meter_error_logged = True
        with contextlib.suppress(Exception):
            logger.warning(events.EVENT_SOURCE_METER_ERROR, error=type(error).__name__)


def _meter(action: Callable[[], None]) -> None:
    """Run one metering side effect; it must never fail the send it instruments (design §2.1)."""
    try:
        action()
    except Exception as error:  # every metering hook fails open, by design
        _note_meter_error(error)


_logged_source_event_counts: dict[str, int] = {}


def _should_log_source_event(host: str) -> bool:
    """True for the first `MAX_LOGGED_SOURCE_EVENTS_PER_HOST` failed/retried sends per host per process."""
    count = _logged_source_event_counts.get(host, 0)
    _logged_source_event_counts[host] = count + 1
    return count < MAX_LOGGED_SOURCE_EVENTS_PER_HOST


def _log_source_event(event: str, *, host: str, level: str, **fields: object) -> None:
    if not _should_log_source_event(host):
        return
    log = logger.error if level == "error" else logger.warning
    with contextlib.suppress(Exception):
        log(event, host=host, **fields)


def _increment_int(entry: dict[str, object], key: str, amount: int = 1) -> None:
    """Increment an integer counter inside a `dict[str, object]` without an unchecked `object` cast."""
    current = entry.get(key, 0)
    entry[key] = (current if isinstance(current, int) else 0) + amount


def _increment_float(entry: dict[str, object], key: str, amount: float) -> None:
    """Increment a float counter inside a `dict[str, object]` without an unchecked `object` cast."""
    current = entry.get(key, 0.0)
    entry[key] = (current if isinstance(current, (int, float)) else 0.0) + amount


def _int_field(entry: dict[str, object], key: str) -> int:
    """Read an integer counter back out of a `dict[str, object]`, narrowed rather than force-cast."""
    current = entry.get(key, 0)
    return current if isinstance(current, int) else 0


def _host_entry(host: str) -> dict[str, object]:
    """Get-or-create this process's per-host counters in `usage._host_counters` (the GL-2 hook point).

    Populated fields match design §2.1's table: the send/status counters, `transport_failures`,
    `bytes_in`, `backoff_seconds`, `weighted_calls_metered`, `last_send_outcome`/`last_send_at`, plus
    `provider`/`pool` from `usage.provider_for_host` so the report (o4) never has to re-resolve a host.
    """
    entry = usage._host_counters.get(host)
    if entry is None:
        resolution = usage.provider_for_host(host)
        entry = {
            "provider": resolution.provider if resolution else None,
            "pool": resolution.pool if resolution else None,
            "http_requests": 0,
            "http_2xx": 0,
            "http_3xx": 0,
            "http_4xx": 0,
            "http_429": 0,
            "http_5xx": 0,
            "transport_failures": 0,
            "bytes_in": 0,
            "backoff_seconds": 0.0,
            "weighted_calls_metered": 0.0,
            "last_send_outcome": None,
            "last_send_at": None,
        }
        usage._host_counters[host] = entry
    return entry


def _status_bucket(status: int) -> str:
    """One of the five closed HTTP status buckets design §2.1 counts."""
    if status == HTTP_TOO_MANY_REQUESTS:
        return "http_429"
    if status >= HTTP_SERVER_ERROR_MINIMUM:
        return "http_5xx"
    if status >= CLIENT_ERROR_STATUS_MINIMUM:
        return "http_4xx"
    if status >= REDIRECT_STATUS_MINIMUM:
        return "http_3xx"
    return "http_2xx"


def _send_outcome(status: int) -> str:
    """The `last_send_outcome` label for a status -- the R3 evidence rule's vocabulary, not the bucket key."""
    if status == HTTP_TOO_MANY_REQUESTS:
        return "429"
    if status >= HTTP_SERVER_ERROR_MINIMUM:
        return "5xx"
    if status >= CLIENT_ERROR_STATUS_MINIMUM:
        return "4xx"
    if status >= REDIRECT_STATUS_MINIMUM:
        return "3xx"
    return "2xx"


def _open_meteo_weight_for_send(host: str, url: str) -> float:
    """The Open-Meteo weight of one send, or 0 for every other host (design §2.1's weighted grain)."""
    if "open-meteo.com" not in host.casefold():
        return 0.0
    return usage.open_meteo_weight_for_url(url)


_SENT_AT_EXTENSION_KEY: Final = "plantgeo_sent_at"

# Set only by `fetch_bounded`, which is the sole caller that knows a real per-request retry attempt
# number; absent (None) for any other caller of a metered client, e.g. G0's own direct `client.get`.
_ATTEMPT_EXTENSION_KEY: Final = "plantgeo_attempt"


def _record_request_sent(request: httpx.Request) -> None:
    host = request.url.host
    if not host:
        return
    request.extensions[_SENT_AT_EXTENSION_KEY] = time.monotonic()
    entry = _host_entry(host)
    _increment_int(entry, "http_requests")


def _record_response(response: httpx.Response) -> None:
    host = response.request.url.host
    if not host:
        return
    entry = _host_entry(host)
    status = response.status_code
    _increment_int(entry, _status_bucket(status))
    outcome = _send_outcome(status)
    entry["last_send_outcome"] = outcome
    entry["last_send_at"] = time.time()

    sent_at = response.request.extensions.get(_SENT_AT_EXTENSION_KEY)
    elapsed_ms = round((time.monotonic() - sent_at) * 1000, 1) if isinstance(sent_at, float) else None
    raw_attempt = response.request.extensions.get(_ATTEMPT_EXTENSION_KEY)
    attempt = raw_attempt if isinstance(raw_attempt, int) else None
    is_auth_failure = status in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN)
    # Logged BEFORE the Open-Meteo weight below: a weight computation that raises (an unexpected
    # query shape) must never cost this response its failure event, which is the line the source-abuse
    # audit actually reads.
    if outcome in ("429", "5xx") or is_auth_failure:
        _log_source_event(
            events.EVENT_SOURCE_REQUEST_FAILED,
            host=host,
            level="error" if is_auth_failure else "warn",
            status=status,
            attempt=attempt,
            elapsed_ms=elapsed_ms,
        )

    weight = _open_meteo_weight_for_send(host, str(response.request.url))
    if weight:
        _increment_float(entry, "weighted_calls_metered", weight)


async def _async_request_hook(request: httpx.Request) -> None:
    _meter(lambda: _record_request_sent(request))


async def _async_response_hook(response: httpx.Response) -> None:
    _meter(lambda: _record_response(response))


def _sync_request_hook(request: httpx.Request) -> None:
    _meter(lambda: _record_request_sent(request))


def _sync_response_hook(response: httpx.Response) -> None:
    _meter(lambda: _record_response(response))


def _event_hooks(*, sync: bool) -> dict[str, list[object]]:
    if sync:
        return {"request": [_sync_request_hook], "response": [_sync_response_hook]}
    return {"request": [_async_request_hook], "response": [_async_response_hook]}


def _record_bytes_in(host: str, byte_count: int) -> None:
    _increment_int(_host_entry(host), "bytes_in", byte_count)


def _record_transport_failure(host: str) -> None:
    entry = _host_entry(host)
    _increment_int(entry, "transport_failures")
    entry["last_send_outcome"] = "transport"
    entry["last_send_at"] = time.time()


def _record_backoff(host: str, seconds: float) -> None:
    _increment_float(_host_entry(host), "backoff_seconds", seconds)


def record_backoff_seconds(host: str, seconds: float) -> None:
    """Fail-open public seam for a caller outside this module to count its OWN retry ladder's backoff.

    `fetch_bounded`'s transport-retry sleep already counts itself through `_record_backoff` directly;
    this exists for `upstream_retry.py::retry_upstream`, whose ArcGIS/USGS ladder sleeps between a
    429/5xx retry and today counted nothing (design §2.1 lists "retry_upstream" as one of
    `backoff_seconds`' three origins). `upstream_retry.py` has no reads/owns claim on this module's
    private counter state, so this is the one function it calls instead of reaching into `_meter`/
    `_record_backoff` itself.
    """
    _meter(functools.partial(_record_backoff, host, seconds))


def _client_is_metered(client: httpx.AsyncClient) -> bool:
    """Whether THIS client was actually built with telemetry on, never re-reading the environment.

    `fetch_bounded` and `_read_bounded_body` gate every metering call they make directly (as opposed
    to through an attached hook) on this, rather than on a fresh `upstream_telemetry_enabled()` call --
    a raw client built by a caller that never went through `upstream_client` at all, or a client built
    while telemetry was on and then handed to code that outlives an env flip, must stay exactly as
    inert or as metered as the hooks actually attached to it, matching `test_telemetry_off_is_byte_for_byte_legacy`.
    """
    return _async_response_hook in client.event_hooks.get("response", [])


def _client_kwargs(
    *,
    transport: object | None,
    telemetry_on: bool,
    sync: bool,
) -> dict[str, Any]:
    """Build only the keyword arguments a metered client factory actually needs to pass.

    Telemetry off (or no test transport supplied) means the keyword is OMITTED entirely, not passed
    as `None` -- `httpx.AsyncClient(transport=None, event_hooks=None, headers=None, ...)` is not "the
    same call" a caller wrapping or patching the constructor sees (a test double that supplies its own
    `transport=` collides with one already present in the forwarded kwargs), and byte-for-byte legacy
    means the pre-GL-2 call shape exactly, not merely its observable behaviour.
    """
    kwargs: dict[str, Any] = {}
    if transport is not None:
        kwargs["transport"] = transport
    if telemetry_on:
        kwargs["event_hooks"] = _event_hooks(sync=sync)
        kwargs["headers"] = {"User-Agent": default_user_agent()}
    return kwargs


@asynccontextmanager
async def upstream_client(
    bounds: UpstreamBounds,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield one metered client, timeout-bound and redirect-capped; see `ingest/AGENTS.md`."""
    kwargs = _client_kwargs(transport=transport, telemetry_on=upstream_telemetry_enabled(), sync=False)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(bounds.timeout_seconds),
        follow_redirects=True,
        max_redirects=MAX_REDIRECTS,
        limits=httpx.Limits(
            max_connections=MAX_UPSTREAM_CONNECTIONS,
            max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        ),
        **kwargs,
    ) as client:
        yield client


@contextmanager
def upstream_sync_client(
    *,
    follow_redirects: bool = True,
    trust_env: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> Iterator[httpx.Client]:
    """Yield one SYNCHRONOUS metered client, mirroring `upstream_client`; see `ingest/AGENTS.md`."""
    kwargs = _client_kwargs(transport=transport, telemetry_on=upstream_telemetry_enabled(), sync=True)
    with httpx.Client(follow_redirects=follow_redirects, trust_env=trust_env, **kwargs) as client:
        yield client


async def _read_bounded_body(
    response: httpx.Response,
    bounds: UpstreamBounds,
    *,
    metered: bool,
) -> tuple[bytes, int, UpstreamPayloadError | None]:
    """Read a response body under the byte cap, rejecting an oversized declaration before reading anything.

    The middle element is how many bytes actually crossed the wire -- zero when `content-length`
    refused the body before a single chunk was read, which is the distinction a caller budgeting a
    whole walk's transfer needs and cannot recover afterwards. `metered` comes from the caller's own
    `_client_is_metered` check (never a fresh env read here), so a telemetry-off client stays
    byte-for-byte inert through this function too.
    """
    host = response.url.host

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
    try:
        async for chunk in response.aiter_bytes():
            total_bytes += len(chunk)
            if total_bytes > bounds.max_bytes:
                return (
                    b"",
                    total_bytes,
                    UpstreamPayloadTooLargeError(limit_bytes=bounds.max_bytes, observed_bytes=total_bytes),
                )
            chunks.append(chunk)
    finally:
        # In a `finally` rather than only at each return: a mid-stream `httpx.ReadError`/`ReadTimeout`
        # from `aiter_bytes` used to skip this entirely, under-reporting a transfer that was re-fetched
        # on retry as if it cost nothing.
        if metered and host:
            _meter(functools.partial(_record_bytes_in, host, total_bytes))
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
    host = httpx.URL(url).host
    # Checked once against the CLIENT actually passed in, not the environment: a client this module
    # built with telemetry off carries none of our hooks, and every metering call below stays inert
    # to match (test_telemetry_off_is_byte_for_byte_legacy) -- re-reading the env var here would
    # instead meter a raw client whose caller never asked for it, or silently stop mid-call on a flip.
    metered = _client_is_metered(client)
    last_error: httpx.HTTPError | None = None
    for attempt in range(1, TRANSPORT_RETRY_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            async with client.stream(
                "GET",
                url,
                headers=dict(headers or {}),
                timeout=bounds.timeout_seconds,
                extensions={_ATTEMPT_EXTENSION_KEY: attempt},
            ) as response:
                body, byte_count, payload_error = await _read_bounded_body(response, bounds, metered=metered)
                return BoundedResponse(
                    status=response.status_code,
                    content_type=response.headers.get("content-type"),
                    text=body.decode("utf-8", errors="replace"),
                    payload_error=payload_error,
                    byte_count=byte_count,
                )
        except httpx.HTTPError as error:
            last_error = error
            elapsed_ms = round((time.monotonic() - started) * 1000, 1)
            if metered and host:
                _meter(functools.partial(_record_transport_failure, host))
            if attempt == TRANSPORT_RETRY_ATTEMPTS:
                break
            # Exponential, because a connection table that is full drains on its own and the point
            # is to stop adding to it. Nothing is retried after the body has begun streaming: a
            # partial read raises inside _read_bounded_body as an UpstreamPayloadError, which is
            # returned rather than raised and so never reaches here.
            delay = TRANSPORT_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
            if metered and host:
                _meter(functools.partial(_record_backoff, host, delay))
                _log_source_event(
                    events.EVENT_SOURCE_REQUEST_RETRY,
                    host=host,
                    level="warn",
                    error_type=type(error).__name__,
                    attempt=attempt,
                    elapsed_ms=elapsed_ms,
                )
            await asyncio.sleep(delay)

    if metered and host:
        _log_source_event(
            events.EVENT_SOURCE_REQUEST_FAILED,
            host=host,
            level="warn",
            error_type=type(last_error).__name__ if last_error is not None else "unknown",
            status=None,
            attempt=TRANSPORT_RETRY_ATTEMPTS,
            elapsed_ms=None,
        )
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
