"""Contract tests for the shared Open-Meteo lane scaffold: backoff scope, canonicalization, cache verification."""

# ruff: noqa: PLR2004

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.open_meteo_lane import (
    MAX_FETCH_ATTEMPTS,
    RATE_LIMIT_BACKOFF_SECONDS,
    TRANSPORT_BACKOFF_SECONDS,
    OpenMeteoLane,
    OpenMeteoLaneCapture,
    OpenMeteoLaneFetchError,
    canonical_location_document,
    derived_checkpoint_state,
    fetch_lane_capture,
    require_complete_raw_cache_pair,
    verified_cached_payload,
    write_raw_cache_pair,
)
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError
from agri_data_service.ingest.open_meteo_endpoint import OpenMeteoProductRequest
from agri_data_service.ingest.open_meteo_flood import OPEN_METEO_FLOOD_BASE_URL, OPEN_METEO_FLOOD_ENDPOINT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

TEST_LANE = OpenMeteoLane(
    label="test lane",
    cache_directory_name="test-lane",
    endpoint=OPEN_METEO_FLOOD_ENDPOINT,
)

REQUEST = OpenMeteoProductRequest(
    base_url=OPEN_METEO_FLOOD_BASE_URL,
    request_url=f"{OPEN_METEO_FLOOD_BASE_URL}?latitude=44&longitude=-116",
)


class LaneFetchError(OpenMeteoLaneFetchError):
    """A stand-in for a real lane's fetch error, so the shared loop is exercised through one."""

    def __init__(self, chunk_key: str, cause: object, attempts: int) -> None:
        """Bind this stand-in to the test lane's label."""
        super().__init__(TEST_LANE.label, chunk_key, cause, attempts)  # type: ignore[arg-type]


def _document() -> bytes:
    return json.dumps(
        [{"latitude": 44.0, "longitude": -116.0, "generationtime_ms": 0.4231, "daily": {"time": ["2026-08-06"]}}]
    ).encode("utf-8")


async def _capture(
    refuse: Callable[[object, str], Awaitable[str]],
    recorded_waits: list[float],
) -> OpenMeteoLaneCapture:
    """Run the shared loop with a sleep that records instead of waiting, so backoff is observable."""

    async def record_wait(seconds: float) -> None:
        recorded_waits.append(seconds)

    return await fetch_lane_capture(
        TEST_LANE,
        "cells-0000",
        REQUEST,
        client=None,  # type: ignore[arg-type]
        fetch_text=refuse,
        error_factory=LaneFetchError,
        sleep=record_wait,
    )


def test_only_a_minutely_refusal_is_ever_waited_out() -> None:
    """An unrecognised 429 body is more likely a reworded wall than a blip, so it fails closed."""
    assert RATE_LIMIT_BACKOFF_SECONDS == {"minute": 70.0}
    for scope in ("hour", "day", "unknown"):
        assert scope not in RATE_LIMIT_BACKOFF_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["hour", "day", "unknown"])
async def test_an_unwaitable_quota_wall_fails_immediately_without_sleeping(scope: str) -> None:
    """Burning four requests and 360 s against an exhausted quota is the failure mode to avoid."""
    attempts = 0
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        nonlocal attempts
        attempts += 1
        raise OpenMeteoRateLimitError(scope, f"{scope} API request limit exceeded.")

    with pytest.raises(OpenMeteoLaneFetchError) as raised:
        await _capture(refuse, waits)

    assert attempts == 1
    assert waits == []
    assert raised.value.chunk_key == "cells-0000"
    assert raised.value.attempts == 1
    assert "test lane chunk cells-0000 failed after 1 attempt:" in str(raised.value)


@pytest.mark.asyncio
async def test_a_minutely_quota_is_retried_then_surfaced() -> None:
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")

    with pytest.raises(OpenMeteoLaneFetchError) as raised:
        await _capture(refuse, waits)

    assert waits == [70.0, 70.0, 70.0]
    assert raised.value.attempts == MAX_FETCH_ATTEMPTS


@pytest.mark.asyncio
async def test_a_transport_failure_escalates_its_backoff_then_surfaces() -> None:
    """A transport blip is worth retrying, but not at a constant interval and not forever."""
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise UpstreamHttpError(503)

    with pytest.raises(OpenMeteoLaneFetchError) as raised:
        await _capture(refuse, waits)

    assert waits == [TRANSPORT_BACKOFF_SECONDS * 1, TRANSPORT_BACKOFF_SECONDS * 2, TRANSPORT_BACKOFF_SECONDS * 3]
    assert raised.value.attempts == MAX_FETCH_ATTEMPTS
    assert isinstance(raised.value.cause, UpstreamHttpError)


@pytest.mark.asyncio
async def test_a_recovered_refusal_still_yields_a_canonical_capture() -> None:
    waits: list[float] = []
    calls = 0
    body = _document().decode("utf-8")

    async def flaky(_client: object, _url: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")
        return body

    capture = await _capture(flaky, waits)

    assert calls == 2
    assert waits == [70.0]
    assert capture.request_base_url == OPEN_METEO_FLOOD_BASE_URL
    # The WIRE bytes are what the wire checksum covers; the canonical document is what is cached.
    assert capture.wire_payload_bytes == len(body.encode("utf-8"))
    assert capture.wire_payload_checksum == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert b"generationtime_ms" not in capture.canonical_payload


def test_the_canonicalizer_strips_only_the_providers_timing_metric() -> None:
    """One canonicalizer for every lane: a second nondeterministic field is one edit, not four."""
    canonical = canonical_location_document(TEST_LANE, _document())
    parsed = json.loads(canonical)

    assert "generationtime_ms" not in parsed[0]
    assert parsed[0]["daily"] == {"time": ["2026-08-06"]}
    # Two responses differing only in that field must checksum identically.
    other = json.loads(_document())
    other[0]["generationtime_ms"] = 91.7
    assert canonical_location_document(TEST_LANE, json.dumps(other).encode("utf-8")) == canonical


def test_the_canonicalizer_names_its_lane_when_it_refuses() -> None:
    with pytest.raises(ValueError, match="test lane response contained invalid JSON"):
        canonical_location_document(TEST_LANE, b"not json at all")
    with pytest.raises(ValueError, match="must be a JSON array"):
        canonical_location_document(TEST_LANE, b'{"latitude": 44.0}')


def test_a_half_written_raw_cache_is_refused_rather_than_reused(tmp_path: Path) -> None:
    payload_path = tmp_path / "cells-0000.json"
    receipt_path = tmp_path / "cells-0000.receipt.json"

    assert require_complete_raw_cache_pair(TEST_LANE, payload_path, receipt_path) is False

    write_raw_cache_pair(payload_path, receipt_path, b"payload", b"receipt")
    assert require_complete_raw_cache_pair(TEST_LANE, payload_path, receipt_path) is True

    receipt_path.unlink()
    with pytest.raises(ValueError, match="incomplete content or receipt"):
        require_complete_raw_cache_pair(TEST_LANE, payload_path, receipt_path)


def test_cached_bytes_are_proven_against_their_receipt_before_reuse(tmp_path: Path) -> None:
    payload_path = tmp_path / "cells-0000.json"
    payload = b"the document exactly as it was cached"
    payload_path.write_bytes(payload)
    checksum = hashlib.sha256(payload).hexdigest()

    assert (
        verified_cached_payload(TEST_LANE, payload_path, expected_bytes=len(payload), expected_checksum=checksum)
        == payload
    )

    payload_path.write_bytes(b"the document after somebody edited it")
    with pytest.raises(ValueError, match="raw cache payload does not match its receipt"):
        verified_cached_payload(TEST_LANE, payload_path, expected_bytes=len(payload), expected_checksum=checksum)


def test_checkpoint_state_is_rederived_from_receipt_completeness() -> None:
    """A recorded `blocked` must not outlive its cause, so state is derived rather than trusted."""
    assert derived_checkpoint_state(set(), ["cells-0000", "cells-0001"]) == "initialized"
    assert derived_checkpoint_state({"cells-0000"}, ["cells-0000", "cells-0001"]) == "running"
    assert derived_checkpoint_state({"cells-0000", "cells-0001"}, ["cells-0000", "cells-0001"]) == "validated"
