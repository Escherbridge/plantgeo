"""Capture tests use fake HTTP only and never publish environmental objects."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.scripts import load_scripts_module

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

CAPTURE = load_scripts_module("capture_sensor_evidence.py", "plantgeo_capture_sensor_evidence")
STATION = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [-120, 45]},
    "properties": {"stationIdentifier": "KAAA", "name": "Station", "provider": "ASOS"},
}
LEXICAL_TIME = (datetime.now(UTC) - timedelta(hours=1)).astimezone(timezone(timedelta(hours=-6))).isoformat()
OBSERVATION = {
    "type": "Feature",
    "properties": {
        "station": "https://api.weather.gov/stations/KAAA",
        "timestamp": LEXICAL_TIME,
        "temperature": {"value": 12.0},
    },
}


def response(features: list[object], following: str | None = None) -> bytes:
    payload: dict[str, object] = {"type": "FeatureCollection", "features": features}
    if following is not None:
        payload["pagination"] = {"next": following}
    return json.dumps(payload, indent=2).encode()


async def run_capture(
    handler: Callable[[httpx.Request], httpx.Response | Awaitable[httpx.Response]], limits: Any = None
) -> tuple[dict[str, Any], bytes]:
    now = datetime.now(UTC)
    scope = CAPTURE.Scope("-125,42,-110,50", ("WA",), ("ASOS",), (now - timedelta(days=1)).isoformat(), now.isoformat())
    sink = io.BytesIO()
    with tarfile.open(fileobj=sink, mode="w:gz") as archive:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            manifest = await CAPTURE.Capture(client, archive, scope, limits or CAPTURE.Limits()).run()
    return manifest, sink.getvalue()


@pytest.mark.asyncio
async def test_pagination_preserves_original_bytes_times_and_station_scope() -> None:
    raw = response([OBSERVATION])
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        if request.url.path == "/stations":
            body = response([STATION])
        elif "cursor" not in request.url.params:
            body = response([OBSERVATION], str(request.url) + "&cursor=second")
        else:
            body = raw
        return httpx.Response(200, stream=httpx.ByteStream(body))

    manifest, artifact = await run_capture(handler)
    assert manifest["source_complete"] is True
    assert manifest["upstream_population_complete"] is False
    assert len(urls) == len(manifest["requests"])
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as archive:
        receipt = manifest["requests"][-1]
        source = archive.extractfile(receipt["body_path"])
        assert source is not None
        assert source.read() == raw
        assert receipt["sha256"] == hashlib.sha256(raw).hexdigest()
        assert LEXICAL_TIME.encode() in raw
        assert datetime.fromisoformat(receipt["retrieved_at"]).tzinfo is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/stations/KAAA/observations?cursor=x",
        "https://api.weather.gov/stations/KBBB/observations?cursor=x",
        "https://api.weather.gov:443/stations/KAAA/observations?cursor=x",
        "https://api.weather.gov/stations/KAAA/observations?start=wrong",
        "https://api.weather.gov/stations/KAAA/observations?cursor=x&limit=999",
    ],
)
def test_pagination_cannot_change_owner_or_scope(url: str) -> None:
    with pytest.raises(CAPTURE.CaptureError):
        CAPTURE.validate_url(
            url,
            "https://api.weather.gov/stations/KAAA/observations?start=2026-09-09T00:00:00Z&end=2026-09-10T00:00:00Z&limit=500",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["wrong_station", "http_error", "pagination_cycle", "byte_cap", "request_cap", "record_cap"]
)
async def test_incomplete_evidence_never_becomes_complete(failure: str) -> None:
    limits = CAPTURE.Limits()
    if failure == "byte_cap":
        limits = replace(limits, page_bytes=8)
    elif failure == "request_cap":
        limits = replace(limits, requests=1)
    elif failure == "record_cap":
        limits = replace(limits, records=1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stations":
            return httpx.Response(200, stream=httpx.ByteStream(response([STATION])))
        observation = OBSERVATION
        if failure == "wrong_station":
            observation = {"type": "Feature", "properties": {"stationId": "KBBB", "timestamp": LEXICAL_TIME}}
        body = response([observation, observation], str(request.url) if failure == "pagination_cycle" else None)
        return httpx.Response(429 if failure == "http_error" else 200, stream=httpx.ByteStream(body))

    manifest, _ = await run_capture(handler, limits)
    assert manifest["source_complete"] is False
    assert manifest["counts"]["requests"] <= limits.requests
    assert manifest["counts"]["bytes"] <= limits.total_bytes
    assert all(item["byte_count"] <= limits.page_bytes for item in manifest["requests"])


def test_caps_refuse_infinite_time_and_unbounded_workers() -> None:
    for limits in (replace(CAPTURE.Limits(), seconds=float("inf")), replace(CAPTURE.Limits(), concurrency=9)):
        with pytest.raises(CAPTURE.CaptureError):
            CAPTURE.validate_limits(limits)


@pytest.mark.asyncio
async def test_station_cap_and_empty_next_page_remain_incomplete() -> None:
    second = {**STATION, "properties": {**STATION["properties"], "stationIdentifier": "KBBB"}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stations":
            body = response([STATION, second])
        else:
            body = response([], str(request.url) + "&cursor=empty")
        return httpx.Response(200, stream=httpx.ByteStream(body))

    manifest, _ = await run_capture(handler, replace(CAPTURE.Limits(), stations=1))
    assert manifest["source_complete"] is False
    assert manifest["roster"]["station_cap_applied"] is True
    assert manifest["roster"]["stations"][0]["station_identifier"] == "KAAA"
    assert "empty page" in manifest["stations"][0]["errors"][0]


@pytest.mark.asyncio
async def test_station_walks_obey_concurrency_and_join_before_return() -> None:
    workers = 2
    second = {**STATION, "properties": {**STATION["properties"], "stationIdentifier": "KBBB"}}
    gate = asyncio.Event()
    active = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        if request.url.path == "/stations":
            return httpx.Response(200, stream=httpx.ByteStream(response([STATION, second])))
        active += 1
        peak = max(peak, active)
        if active == workers:
            gate.set()
        try:
            await asyncio.wait_for(gate.wait(), timeout=5)
            return httpx.Response(200, stream=httpx.ByteStream(response([])))
        finally:
            active -= 1

    manifest, _ = await run_capture(handler, replace(CAPTURE.Limits(), concurrency=workers))
    assert peak == workers
    assert active == 0
    assert manifest["source_complete"] is True


@pytest.mark.parametrize("observed", ["2026-09-04T23:59:59Z", "2026-09-07T00:00:00Z", "2026-09-07T02:00:00+01:00"])
def test_observations_outside_request_window_are_refused(observed: str) -> None:
    scope = CAPTURE.Scope("-125,42,-110,50", ("WA",), ("ASOS",), "2026-09-05T00:00:00Z", "2026-09-07T00:00:00Z")
    feature = {"properties": {"station": "https://api.weather.gov/stations/KAAA", "timestamp": observed}}
    with pytest.raises(CAPTURE.CaptureError, match="outside its requested window"):
        CAPTURE.validate_observations([feature], "KAAA", scope)
    feature["properties"]["timestamp"] = "2026-09-04T18:00:00-06:00"
    CAPTURE.validate_observations([feature], "KAAA", scope)
    assert feature["properties"]["timestamp"] == "2026-09-04T18:00:00-06:00"


@pytest.mark.parametrize(
    "query", ["cursor=x&start=", "cursor=x&unknown=", "cursor=x&cursor=", "cursor=x&start=2026-09-05T00:00:00Z&start="]
)
def test_blank_pagination_parameters_cannot_hide_scope_drift(query: str) -> None:
    initial = "https://api.weather.gov/stations/KAAA/observations?start=2026-09-05T00:00:00Z&end=2026-09-07T00:00:00Z"
    with pytest.raises(CAPTURE.CaptureError):
        CAPTURE.validate_url("https://api.weather.gov/stations/KAAA/observations?" + query, initial)


def test_roster_rejects_non_point_geometry() -> None:
    station = {**STATION, "geometry": {"type": "LineString", "coordinates": [-120, 45]}}
    with pytest.raises(CAPTURE.CaptureError, match="geometry"):
        CAPTURE.validate_roster([station])
