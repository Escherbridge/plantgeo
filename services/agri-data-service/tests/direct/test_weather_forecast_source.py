"""Real Single Runs evidence and adversarial normalization checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.pipeline.direct.weather_forecast.source import (
    LICENCE,
    MODEL,
    OUTPUT_VARIABLES,
    PRODUCT,
    PROVIDER,
    SOURCE_BOUNDS,
    SOURCE_VARIABLES,
    STEP_COUNT,
    SingleRunRequest,
    fetch_single_run,
    normalize_single_run,
)
from agri_data_service.warehouse.weather_forecast.contracts import ForecastRun, SpatialSupport

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "weather_forecast"
FIXTURE = FIXTURE_ROOT / "ecmwf_ifs_20260908T0000_boise.json"
RECEIPT = FIXTURE_ROOT / "ecmwf_ifs_20260908T0000_boise.receipt.json"
INIT = datetime(2026, 9, 8, tzinfo=UTC)
FETCHED = datetime(2026, 9, 12, 1, 7, 51, 747385, tzinfo=UTC)
REQUEST = SingleRunRequest(latitude=43.615, longitude=-116.2023, model_init_at=INIT)
RETURNED_POINT = (43.620384, -116.15964)
SOURCE_MISSING_COUNT = 3


def _raw() -> str:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    return FIXTURE.read_bytes()[: receipt["bytes"]].decode("utf-8")


def _run(raw: str) -> ForecastRun:
    return ForecastRun(
        run_id="ecmwf-ifs-20260908T0000",
        product_id=PRODUCT,
        provider=PROVIDER,
        model=MODEL,
        model_version=None,
        model_init_at=INIT,
        provider_issued_at=None,
        fetched_at=FETCHED,
        admitted_at=FETCHED + timedelta(minutes=1),
        published_at=FETCHED + timedelta(minutes=2),
        licence=LICENCE,
        source_url=REQUEST.url,
        source_payload_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        support=SpatialSupport(kind="sampled_point"),
        variables=OUTPUT_VARIABLES,
    )


def test_real_capture_checksum_and_request_parameters() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == receipt["capture_file_sha256"]
    assert hashlib.sha256(_raw().encode()).hexdigest() == receipt["source_response_sha256"]
    assert parse_qs(urlparse(REQUEST.url).query) == parse_qs(urlparse(receipt["url"]).query)


def test_real_run_preserves_complete_inventory_samples_and_lead_zero_missingness() -> None:
    raw = _raw()
    series = normalize_single_run(raw, REQUEST, _run(raw))
    assert len(series.values) == STEP_COUNT * len(OUTPUT_VARIABLES)
    assert {(value.latitude, value.longitude) for value in series.values} == {RETURNED_POINT}
    assert len({value.sample_id for value in series.values}) == 1
    missing = [value for value in series.values if value.status == "missing"]
    assert len(missing) == SOURCE_MISSING_COUNT
    assert {value.variable for value in missing} == {"precipitation", "wind_gusts_10m", "weather_code"}
    assert all(value.valid_at == INIT and value.value is None for value in missing)
    assert all(
        value.interval_start == INIT - timedelta(hours=1) for value in missing if value.variable != "weather_code"
    )
    assert series.run.provider_issued_at is None
    assert max(value.lead_seconds for value in series.values) == (STEP_COUNT - 1) * 3600


def test_real_wind_components_preserve_speed_and_meteorological_direction() -> None:
    raw = _raw()
    values = {
        value.variable: value.value
        for value in normalize_single_run(raw, REQUEST, _run(raw)).values
        if value.valid_at == INIT
    }
    speed = values["wind_speed_10m"]
    u, v = values["wind_u_10m"], values["wind_v_10m"]
    assert speed is not None
    assert u is not None
    assert v is not None
    assert (u * u + v * v) ** 0.5 == pytest.approx(speed)
    assert u > 0
    assert v < 0


@pytest.mark.parametrize("field", ["wind_speed_10m", "wind_direction_10m"])
def test_partial_source_wind_missingness_never_creates_a_vector(field: str) -> None:
    payload = json.loads(_raw())
    payload["hourly"][field][1] = None
    raw = json.dumps(payload)
    values = normalize_single_run(raw, REQUEST, _run(raw)).values
    vectors = [
        value
        for value in values
        if value.valid_at == INIT + timedelta(hours=1) and value.variable in {"wind_u_10m", "wind_v_10m"}
    ]
    assert all(value.value is None and value.status == "missing" for value in vectors)


def _wrong_unit(payload: dict[str, Any]) -> None:
    payload["hourly_units"]["wind_speed_10m"] = "km/h"


def _shift_time(payload: dict[str, Any]) -> None:
    payload["hourly"]["time"][1] += 3600


def _truncate(payload: dict[str, Any]) -> None:
    payload["hourly"]["temperature_2m"].pop()


def _extra_field(payload: dict[str, Any]) -> None:
    payload["hourly"]["precipitation_probability"] = [0] * STEP_COUNT


def _boolean(payload: dict[str, Any]) -> None:
    payload["hourly"]["temperature_2m"][1] = True


def _available_preinit_interval(payload: dict[str, Any]) -> None:
    payload["hourly"]["precipitation"][0] = 0


def _bad_coordinate(payload: dict[str, Any]) -> None:
    payload["latitude"] = 91


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_wrong_unit, "units or variable inventory"),
        (_shift_time, "time axis must be contiguous"),
        (_truncate, "exactly 240 hourly values"),
        (_extra_field, "hourly variable inventory"),
        (_boolean, "must be finite numeric data"),
        (_available_preinit_interval, "available forecast intervals"),
        (_bad_coordinate, "latitude"),
    ],
)
def test_source_contract_drift_refuses_the_whole_run(mutate: Callable[[dict[str, Any]], None], message: str) -> None:
    payload = json.loads(_raw())
    mutate(payload)
    raw = json.dumps(payload)
    with pytest.raises(ValueError, match=message):
        normalize_single_run(raw, REQUEST, _run(raw))


def test_wrong_checksum_or_source_identity_refuses_replay() -> None:
    raw = _raw()
    with pytest.raises(ValueError, match="checksum"):
        normalize_single_run(raw + " ", REQUEST, _run(raw))
    other = SingleRunRequest(latitude=0, longitude=0, model_init_at=INIT)
    with pytest.raises(ValueError, match="provenance"):
        normalize_single_run(raw, other, _run(raw))


@pytest.mark.parametrize(
    ("invalid", "message"),
    [
        ('{"error": true}', "successful single location"),
        ('{"timezone":"GMT","timezone":"GMT"}', "duplicate JSON field"),
        ('{"value":NaN}', "nonfinite JSON constant"),
    ],
)
def test_error_duplicate_and_nonfinite_json_are_refused(invalid: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_single_run(invalid, REQUEST, _run(invalid))


def test_request_refuses_unprobed_nonmidnight_cycle() -> None:
    with pytest.raises(ValueError, match="midnight"):
        SingleRunRequest(latitude=0, longitude=0, model_init_at=INIT + timedelta(hours=6))


@pytest.mark.parametrize(
    "changed",
    [{"provider_issued_at": INIT}, {"model_version": "invented"}, {"licence": "public domain"}],
)
def test_caller_cannot_invent_unverified_source_metadata(changed: dict[str, object]) -> None:
    raw = _raw()
    run = ForecastRun.model_validate({**_run(raw).model_dump(), **changed})
    with pytest.raises(ValueError, match="provenance"):
        normalize_single_run(raw, REQUEST, run)


def test_fetch_obeys_one_location_and_source_bounds() -> None:
    raw = _raw()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.extensions["timeout"]["read"] == SOURCE_BOUNDS.timeout_seconds
        return httpx.Response(200, text=raw, headers={"content-type": "application/json"})

    async def exercise() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_single_run(client, REQUEST)

    assert asyncio.run(exercise()) == raw
    assert len(seen) == 1
    assert seen[0].url.params["hourly"] == ",".join(SOURCE_VARIABLES)


def test_rate_limit_is_typed_and_not_retried() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, json={"error": True}, headers={"Retry-After": "60"})

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_single_run(client, REQUEST)

    with pytest.raises(UpstreamHttpError) as caught:
        asyncio.run(exercise())
    assert caught.value.status == httpx.codes.TOO_MANY_REQUESTS
    assert len(seen) == 1


def test_oversized_response_refuses_before_normalization() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"{}",
            headers={"content-type": "application/json", "content-length": str(SOURCE_BOUNDS.max_bytes + 1)},
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await fetch_single_run(client, REQUEST)

    with pytest.raises(UpstreamPayloadError, match="byte limit"):
        asyncio.run(exercise())
