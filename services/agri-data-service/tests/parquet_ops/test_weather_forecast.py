"""Reconcile local fixture artifacts, exact requests and transport parity without a network."""

from __future__ import annotations

import asyncio
import json
import math
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from agri_data_service.agent import weather_forecast as forecast_agent
from agri_data_service.agent.weather_forecast import weather_forecast_location
from agri_data_service.interface.http.weather_forecast import create_fixture_app
from agri_data_service.pipeline.direct.weather_forecast.adapter import LocalForecastStorage, publish_fixture
from agri_data_service.planes.weather_forecast import FieldRequest, LocationRequest, read_field, read_location
from agri_data_service.warehouse.schemas.weather_forecast import CALM_THRESHOLD, MAX_POINTS, wind_from_components

if TYPE_CHECKING:
    from pathlib import Path

RUN = "fixture-20260912T000000Z-v1"
NOW = datetime(2026, 9, 12, tzinfo=UTC)
NEXT_DAY = "2026-09-13T00:00:00Z"
END = "2026-09-15T00:00:00Z"
EXPECTED_HOURS = 48
HTTP_OK = 200
HTTP_BAD_REQUEST = 400


def query(*, start: str = NEXT_DAY, end: str = END, run_id: str = RUN, lat: float = 40) -> LocationRequest:
    return LocationRequest(run_id, lat, -105, start, end)


def answer(root: Path, request: LocationRequest | None = None) -> dict[str, object]:
    return read_location(root, request or query(), requested_zoom=13, now=NOW)


def items(response: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", response[key])


def test_interruption_is_not_served_and_replay_completes_identically(tmp_path: Path) -> None:
    with pytest.raises(InterruptedError, match="stopped after immutable parts"):
        publish_fixture(tmp_path, interrupt_after_parts=True)
    before = (tmp_path / f"runs/{RUN}/values.parquet").read_bytes()
    assert answer(tmp_path)["status"] == "not-generated"
    assert not (tmp_path / "current.json").exists()
    first = publish_fixture(tmp_path)
    assert publish_fixture(tmp_path) == first
    assert (tmp_path / f"runs/{RUN}/values.parquet").read_bytes() == before
    assert answer(tmp_path)["status"] == "ready"


def test_real_parquet_selected_window_and_daily_vectors_are_conserved(tmp_path: Path) -> None:
    publish_fixture(tmp_path)
    result = answer(tmp_path)
    hourly, daily = items(result, "hourly"), items(result, "daily")
    assert len(hourly) == EXPECTED_HOURS
    assert hourly[0]["valid_time"] == NEXT_DAY
    assert hourly[-1]["interval_end"] == END
    assert all(day["complete"] for day in daily)
    assert all(day["precipitation_sum"] == pytest.approx(0.8) for day in daily)
    assert all(cast("float", day["wind_speed"]) < CALM_THRESHOLD for day in daily)
    assert all(day["wind_direction"] is None for day in daily)
    assert result["request"] == query().wire()


def test_missing_cloud_and_partial_day_never_become_zero_or_full_day_total(tmp_path: Path) -> None:
    publish_fixture(tmp_path)
    result = answer(tmp_path, query(start="2026-09-12T00:00:00Z", end="2026-09-12T12:00:00Z"))
    assert result["status"] == "partial"
    hourly = items(result, "hourly")
    assert cast("dict[str, object]", hourly[6]["values"])["cloud_cover"] is None
    assert cast("dict[str, object]", hourly[6]["missingness"])["cloud_cover"] == "synthetic-source-missing"
    assert items(result, "daily")[0]["precipitation_sum"] is None
    assert items(result, "daily")[0]["complete"] is False


@pytest.mark.parametrize(
    ("forecast_request", "status"),
    [
        (query(lat=40.00001), "outside-domain"),
        (query(start="2026-09-11T00:00:00Z", end=NEXT_DAY), "not-generated"),
        (query(start="2026-09-12T00:00:00Z"), "refused"),
        (query(run_id="../../private"), "refused"),
    ],
)
def test_outside_support_and_invalid_time_never_substitute(
    tmp_path: Path, forecast_request: LocationRequest, status: str
) -> None:
    publish_fixture(tmp_path)
    result = answer(tmp_path, forecast_request)
    assert result["status"] == status
    assert result["hourly"] == []


def test_supersession_preserves_pinned_run_and_refuses_stale(tmp_path: Path) -> None:
    publish_fixture(tmp_path)
    pinned = answer(tmp_path)
    publish_fixture(tmp_path, initialization="2026-09-13T00:00:00Z")
    publish_fixture(tmp_path)
    assert json.loads((tmp_path / "current.json").read_bytes())["run_id"] == "fixture-20260913T000000Z-v1"
    assert answer(tmp_path) == pinned
    assert (
        read_location(tmp_path, query(), requested_zoom=13, now=datetime(2026, 9, 15, tzinfo=UTC))["status"]
        == "stale-run"
    )


@pytest.mark.parametrize("artifact", ["values.parquet", "source.json", "manifest.json", "complete.json"])
def test_corrupt_publication_refuses_without_data(tmp_path: Path, artifact: str) -> None:
    publish_fixture(tmp_path)
    (tmp_path / f"runs/{RUN}/{artifact}").write_bytes(b"{}")
    result = answer(tmp_path)
    assert result["status"] == "refused"
    assert result["hourly"] == []


def test_immutable_conflict_and_pointer_cas_are_enforced(tmp_path: Path) -> None:
    storage = LocalForecastStorage(tmp_path)
    storage.put_immutable("sample", b"a", content_type="application/json")
    with pytest.raises(ValueError, match="Immutable artifact conflict"):
        storage.put_immutable("sample", b"b", content_type="application/json")
    assert not storage.compare_and_swap("sample", b"b", expected_etag=None, content_type="application/json")
    with pytest.raises(ValueError, match="escapes"):
        storage.read("../outside", max_bytes=10)


def test_field_keeps_only_sample_support_and_requested_hour(tmp_path: Path) -> None:
    publish_fixture(tmp_path)
    request = FieldRequest(RUN, NEXT_DAY, "precipitation", (-106, 39, -104, 41))
    result = read_field(tmp_path, request, requested_zoom=11, now=NOW)
    assert result["status"] == "ready"
    assert len(items(result, "points")) == MAX_POINTS
    assert all(point["valid_time"] == NEXT_DAY for point in items(result, "points"))
    assert "sampled_point" in str(result["support"])


@pytest.mark.parametrize(("u", "v", "direction"), [(0, -5, 0), (-5, 0, 90), (0, 5, 180), (5, 0, 270)])
def test_wind_components_keep_meteorological_cardinal_directions(u: float, v: float, direction: float) -> None:
    speed, actual = wind_from_components(u, v)
    assert speed == pytest.approx(5)
    assert actual == pytest.approx(direction)


def test_wind_vector_mean_crosses_north_without_scalar_bearing_average() -> None:
    bearings = (350, 10)
    u = sum(-5 * math.sin(math.radians(angle)) for angle in bearings) / len(bearings)
    v = sum(-5 * math.cos(math.radians(angle)) for angle in bearings) / len(bearings)
    _, direction = wind_from_components(u, v)
    assert direction is not None
    assert min(direction, 360 - direction) == pytest.approx(0, abs=1e-12)


async def test_http_and_agent_use_the_exact_reader_and_reject_duplicate_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(forecast_agent, "read_location", partial(read_location, now=NOW))
    publish_fixture(tmp_path)
    app = create_fixture_app(tmp_path)
    incoming: asyncio.Queue[dict[str, str]] = asyncio.Queue()
    outgoing: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    lifespan = asyncio.create_task(app({"type": "lifespan"}, incoming.get, outgoing.put))
    try:
        await incoming.put({"type": "lifespan.startup"})
        assert await asyncio.wait_for(outgoing.get(), timeout=10) == {"type": "lifespan.startup.complete"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
            response = await client.get("/weather-forecast/location", params=query().wire())
            assert response.status_code == HTTP_OK
            assert response.json()["status"] == "ready"
            expected = read_location(tmp_path, query(), requested_zoom=13, now=NOW)
            assert response.json() == expected
            assert await weather_forecast_location(tmp_path, query()) == expected
            rejected = await client.get("/weather-forecast/location?run_id=a&run_id=b&lat=40&lon=-105&start=x&end=y")
            assert rejected.status_code == HTTP_BAD_REQUEST
    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        assert await asyncio.wait_for(outgoing.get(), timeout=10) == {"type": "lifespan.shutdown.complete"}
        await asyncio.wait_for(lifespan, timeout=10)
