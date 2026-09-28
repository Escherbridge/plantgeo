"""The GL-2 source-usage meter in `ingest/http.py`: per-host counters, fail-open, telemetry off, WQ-5.

See `ingest/AGENTS.md` "http.py: the GL-2 source-usage meter" for the contract these tests pin, and
`foundation/observability/AGENTS.md` "Usage line" for how `usage._host_counters` (mutated here)
reaches the written usage line.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest
import structlog.testing

from agri_data_service.foundation.observability import usage
from agri_data_service.ingest import http
from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamTransportError,
    default_user_agent,
    fetch_bounded,
    fetch_bounded_json,
    upstream_client,
    upstream_sync_client,
    upstream_telemetry_enabled,
)
from agri_data_service.pipeline.direct.soil.source import SoilSourceCache, _instrumented

if TYPE_CHECKING:
    from collections.abc import Callable

BOUNDS = UpstreamBounds(max_bytes=1024, timeout_seconds=5.0)


@pytest.fixture(autouse=True)
def _clean_meter_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts with an empty shared counter dict and a fresh fail-open/log-cap state."""
    monkeypatch.setattr(usage, "_host_counters", {})
    monkeypatch.setattr(http, "_logged_source_event_counts", {})
    monkeypatch.setattr(http, "_meter_error_count", 0)
    monkeypatch.setattr(http, "_meter_error_logged", False)
    monkeypatch.delenv("PLANTGEO_UPSTREAM_TELEMETRY", raising=False)
    monkeypatch.delenv("PLANTGEO_UPSTREAM_CONTACT", raising=False)
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)


def _responding(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return response

    return handler


def _sequence(responses: list[httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return remaining.pop(0)

    return handler


def _raising(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise error

    return handler


# --- telemetry default, and the fail-safe garbled-value rule --------------------------------------


def test_telemetry_defaults_on_and_recognises_the_full_switch_synonym_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design SOF2-10: on {1, true, yes, on, enabled}, off {0, false, no, off, disabled, none}, trimmed/insensitive.

    Unlike GL-1's `bootstrap.parse_switch`, an UNRECOGNISED value here resolves to OFF, not to the
    default -- this switch's safe state on a garbled input is the legacy client (design: "a garbled
    switch resolves to the legacy state"), which is why `banana` joins the off column below rather
    than falling back to on.
    """
    assert upstream_telemetry_enabled() is True
    for on_synonym in ("on", "ON ", " On", "1", "true", "TRUE", "yes", "enabled"):
        monkeypatch.setenv("PLANTGEO_UPSTREAM_TELEMETRY", on_synonym)
        assert upstream_telemetry_enabled() is True, on_synonym
    for off_or_garbled in ("off", "0", "false", "no", "disabled", "none", "banana", ""):
        monkeypatch.setenv("PLANTGEO_UPSTREAM_TELEMETRY", off_or_garbled)
        assert upstream_telemetry_enabled() is False, off_or_garbled


# --- per-host request/response counters ------------------------------------------------------------


async def test_two_independent_fetches_each_count_their_own_http_request() -> None:
    """Two separate `fetch_bounded` calls on one client are two sends, each bucketed by its OWN status.

    Renamed from `..._including_a_retried_status`: neither call here actually retries anything
    (`fetch_bounded` never retries a non-2xx status, only a transport fault) -- the two responses are
    two independent fetches, which is the property this test actually pins.
    """
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_sequence([httpx.Response(503), httpx.Response(200, json={})]))
    ) as client:
        await fetch_bounded(client, "https://waterservices.usgs.gov/x", BOUNDS)
        await fetch_bounded(client, "https://waterservices.usgs.gov/y", BOUNDS)

    entry = usage._host_counters["waterservices.usgs.gov"]
    assert entry["http_requests"] == 2
    assert entry["http_5xx"] == 1
    assert entry["http_2xx"] == 1
    assert entry["provider"] == "usgs-water-data"
    assert entry["pool"] == "usgs-water-data"


async def test_a_source_event_log_never_carries_a_url_or_query_string() -> None:
    """S-12: `plantgeo_source_request_failed` carries host/status/attempt/elapsed, never the URL an API key rides in."""
    async with upstream_client(BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(429)))) as client:
        with structlog.testing.capture_logs() as captured:
            await fetch_bounded(client, "https://example.test/x?apikey=SECRET123", BOUNDS)

    assert captured, "expected the 429 to emit a plantgeo_source_request_failed log line"
    for record in captured:
        rendered = " ".join(str(value) for value in record.values())
        assert "SECRET123" not in rendered
        assert "?" not in rendered


async def test_a_redirect_hop_counts_as_its_own_http_request() -> None:
    """`http_requests` is a WIRE count: a followed redirect is two sends, not one logical fetch."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/end"})
        return httpx.Response(200, json={})

    async with upstream_client(BOUNDS, transport=httpx.MockTransport(handler)) as client:
        await fetch_bounded(client, "https://example.test/start", BOUNDS)

    entry = usage._host_counters["example.test"]
    assert entry["http_requests"] == 2
    assert entry["http_3xx"] == 1
    assert entry["http_2xx"] == 1


async def test_every_transport_retry_attempt_counts_as_its_own_http_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_raising(httpx.ConnectError("refused")))
    ) as client:
        with pytest.raises(UpstreamTransportError):
            await fetch_bounded(client, "https://example.test/x", BOUNDS)

    assert usage._host_counters["example.test"]["http_requests"] == http.TRANSPORT_RETRY_ATTEMPTS


@pytest.mark.parametrize(
    ("status", "bucket"),
    [(200, "http_2xx"), (301, "http_3xx"), (404, "http_4xx"), (429, "http_429"), (503, "http_5xx")],
)
async def test_every_status_bucket_is_counted_under_its_own_key(status: int, bucket: str) -> None:
    async with upstream_client(BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(status)))) as client:
        await fetch_bounded(client, "https://example.test/x", BOUNDS)

    entry = usage._host_counters["example.test"]
    assert entry[bucket] == 1


async def test_a_transport_failure_is_counted_apart_from_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "TRANSPORT_RETRY_BASE_SECONDS", 0.0)
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_raising(httpx.ConnectError("refused")))
    ) as client:
        with pytest.raises(UpstreamTransportError):
            await fetch_bounded(client, "https://example.test/x", BOUNDS)

    entry = usage._host_counters["example.test"]
    assert entry["transport_failures"] == http.TRANSPORT_RETRY_ATTEMPTS
    assert entry["http_5xx"] == 0
    assert entry["last_send_outcome"] == "transport"


async def test_bytes_in_is_the_actual_body_size_read_off_the_wire() -> None:
    body = b"x" * 37
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, content=body)))
    ) as client:
        await fetch_bounded(client, "https://example.test/x", BOUNDS)

    assert usage._host_counters["example.test"]["bytes_in"] == len(body)


async def test_last_send_outcome_is_recorded() -> None:
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_sequence([httpx.Response(429), httpx.Response(200, json={})]))
    ) as client:
        await fetch_bounded(client, "https://example.test/x", BOUNDS)
        entry = usage._host_counters["example.test"]
        assert entry["last_send_outcome"] == "429"
        assert isinstance(entry["last_send_at"], float)

        await fetch_bounded(client, "https://example.test/y", BOUNDS)
        assert entry["last_send_outcome"] == "2xx"


# --- Open-Meteo weight, only on the weighted host --------------------------------------------------


async def test_weighted_calls_metered_only_accrues_on_an_open_meteo_host() -> None:
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={})))
    ) as client:
        await fetch_bounded(
            client,
            "https://archive-api.open-meteo.com/v1/archive?latitude=1,2&start_date=2026-01-01&end_date=2026-01-01",
            BOUNDS,
        )
        await fetch_bounded(client, "https://example.test/x", BOUNDS)

    open_meteo_entry = usage._host_counters["archive-api.open-meteo.com"]
    assert open_meteo_entry["weighted_calls_metered"] > 0
    assert usage._host_counters["example.test"]["weighted_calls_metered"] == 0.0


# --- fail-open: a raising hook must never fail the send --------------------------------------------


async def test_raising_meter_never_fails_the_send(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_host_entry(_host: str) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(http, "_host_entry", _broken_host_entry)

    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={"ok": True})))
    ) as client:
        payload = await fetch_bounded_json(client, "https://example.test/x", BOUNDS)

    assert payload == {"ok": True}
    assert http.meter_error_count() >= 1


# --- PLANTGEO_UPSTREAM_TELEMETRY=off is byte-for-byte legacy ---------------------------------------


def _has_no_wq5_user_agent(client: httpx.AsyncClient | httpx.Client) -> bool:
    """True unless the client's default `User-Agent` is the WQ-5 identifying string this module sets.

    Not a check for the header's ABSENCE: httpx itself always sets some default `User-Agent` (its own
    `python-httpx/<version>`) when the caller supplies none, so "no user-agent header at all" is never
    true either way. What "byte for byte legacy" means here is that THIS module never overrides it.
    """
    return not client.headers.get("user-agent", "").startswith("plantgeo-agri-data-service/")


async def test_telemetry_off_is_byte_for_byte_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_UPSTREAM_TELEMETRY", "off")
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={})))
    ) as client:
        assert client.event_hooks.get("request", []) == []
        assert client.event_hooks.get("response", []) == []
        assert _has_no_wq5_user_agent(client)
        await fetch_bounded(client, "https://example.test/x", BOUNDS)

    assert usage._host_counters == {}


def test_telemetry_off_is_byte_for_byte_legacy_for_the_sync_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_UPSTREAM_TELEMETRY", "off")
    with upstream_sync_client(transport=httpx.MockTransport(_responding(httpx.Response(200)))) as client:
        assert client.event_hooks.get("request", []) == []
        assert client.event_hooks.get("response", []) == []
        assert _has_no_wq5_user_agent(client)


async def test_telemetry_on_attaches_the_wq5_user_agent_by_default() -> None:
    async with upstream_client(BOUNDS) as client:
        assert client.headers.get("user-agent", "").startswith("plantgeo-agri-data-service/")


# --- factories accept a test-only transport (both) --------------------------------------------------


async def test_factories_accept_a_transport() -> None:
    async_calls = {"n": 0}

    def async_handler(request: httpx.Request) -> httpx.Response:
        del request
        async_calls["n"] += 1
        return httpx.Response(200, json={})

    async with upstream_client(BOUNDS, transport=httpx.MockTransport(async_handler)) as client:
        await fetch_bounded(client, "https://example.test/x", BOUNDS)
    assert async_calls["n"] == 1

    sync_calls = {"n": 0}

    def sync_handler(request: httpx.Request) -> httpx.Response:
        del request
        sync_calls["n"] += 1
        return httpx.Response(200)

    with upstream_sync_client(transport=httpx.MockTransport(sync_handler)) as client:
        client.get("https://example.test/y")
    assert sync_calls["n"] == 1


# --- WQ-5 identification: default shape, contact, and the caller-header-wins rule ------------------


def test_default_user_agent_carries_the_version_and_short_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef0123456789")
    agent = default_user_agent()
    assert agent.startswith("plantgeo-agri-data-service/")
    assert agent.endswith("+abcdef0")
    assert "(+" not in agent


def test_default_user_agent_is_unknown_sha_when_unset() -> None:
    assert default_user_agent().endswith("+unknown")


def test_default_user_agent_appends_the_contact_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANTGEO_UPSTREAM_CONTACT", "ops@example.test")
    assert default_user_agent().endswith("(+ops@example.test)")


async def test_a_non_ascii_contact_is_sanitised_and_never_fails_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An accented contact used to raise `UnicodeEncodeError` inside the client constructor; it must not any more."""
    monkeypatch.setenv("PLANTGEO_UPSTREAM_CONTACT", "José <ops@example.test>")
    agent = default_user_agent()
    assert all(0x20 <= ord(character) <= 0x7E for character in agent)

    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={"ok": True})))
    ) as client:
        payload = await fetch_bounded_json(client, "https://example.test/x", BOUNDS)
    assert payload == {"ok": True}


async def test_a_crlf_contact_is_sanitised_and_never_fails_the_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An embedded CR/LF used to make h11 raise `LocalProtocolError` on every send; it must not any more."""
    monkeypatch.setenv("PLANTGEO_UPSTREAM_CONTACT", "ops@example.test\r\nX-Injected: y")
    agent = default_user_agent()
    assert "\r" not in agent
    assert "\n" not in agent

    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={"ok": True})))
    ) as client:
        payload = await fetch_bounded_json(client, "https://example.test/x", BOUNDS)
    assert payload == {"ok": True}


async def test_a_callers_own_user_agent_header_wins_over_the_default() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user-agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={})

    async with upstream_client(BOUNDS, transport=httpx.MockTransport(handler)) as client:
        await fetch_bounded(client, "https://example.test/x", BOUNDS, headers={"User-Agent": "custom/1"})

    assert captured["user-agent"] == "custom/1"


# --- the 20-per-host log cap (design §1.8): counters still update, only the LOG stops --------------


def test_retry_and_failure_logs_cap_at_twenty_per_host_then_are_still_counted() -> None:
    host = "capped.example.test"
    logged = [http._should_log_source_event(host) for _ in range(25)]
    assert logged == [True] * 20 + [False] * 5


# --- the SYNC client meters directly (not through `fetch_bounded`, which is async-only) --------------


def test_sync_client_meters_a_send_through_its_own_hooks() -> None:
    with upstream_sync_client(transport=httpx.MockTransport(_responding(httpx.Response(200, json={})))) as client:
        client.get("https://example.test/x")

    entry = usage._host_counters["example.test"]
    assert entry["http_requests"] == 1
    assert entry["http_2xx"] == 1


def test_sync_client_carries_the_wq5_user_agent_by_default() -> None:
    with upstream_sync_client() as client:
        assert client.headers.get("user-agent", "").startswith("plantgeo-agri-data-service/")


def test_sync_clients_raising_meter_never_fails_the_send(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken_host_entry(_host: str) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(http, "_host_entry", _broken_host_entry)
    with upstream_sync_client(transport=httpx.MockTransport(_responding(httpx.Response(200, content=b"ok")))) as client:
        response = client.get("https://example.test/x")

    assert response.status_code == 200
    assert http.meter_error_count() >= 1


# --- G0's own request hook coexists with this meter on the same client -------------------------------


async def test_g0s_own_hook_coexists_with_the_gl2_meter_on_the_same_client() -> None:
    """`pipeline/direct/soil/source.py::_instrumented` APPENDS a hook rather than replacing this module's."""
    cache = SoilSourceCache(request_budget=10)
    async with upstream_client(
        BOUNDS, transport=httpx.MockTransport(_responding(httpx.Response(200, json={})))
    ) as client:
        _instrumented(client, cache)
        await fetch_bounded(client, "https://example.test/x", BOUNDS)

    assert cache.http_requests == 1
    assert usage._host_counters["example.test"]["http_requests"] == 1
