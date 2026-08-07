"""Contract tests for the CAMS air-quality lane: two-axis chunking, hourly reduction, and coverage honesty."""


from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_cams import (
    CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS,
    CAMS_DAILY_STATISTICS,
    CAMS_MINIMUM_OBSERVED_HOURS_PER_DAY,
    CAMS_PRODUCTS,
    QUALITY_FLAG_ACCEPTED,
    QUALITY_FLAG_INSUFFICIENT_HOURS,
    QUALITY_FLAG_SOURCE_MISSING,
    CamsAirQualityCapture,
    CamsAirQualityFetchError,
    HistoricalCamsAirQualityPlan,
    cache_historical_cams_result,
    fetch_cams_air_quality_chunk,
    historical_cams_release_manifest,
    initialize_historical_cams_checkpoint,
    load_cached_historical_cams_result,
    parse_cams_air_quality_payload,
    record_historical_cams_result,
    require_accounted_cams_result,
)
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError
from agri_data_service.ingest.open_meteo_air_quality import HOURS_PER_DAY

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.execution.historical_backfill import AnalysisGridCell
    from agri_data_service.execution.historical_cams import CamsAirQualityChunk, CamsAirQualityChunkResult

WINDOW_END = date(2026, 4, 30)
WINDOW_START = WINDOW_END.replace(year=WINDOW_END.year - 4)
RETRIEVED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
# A release manifest is a sha256 hex digest of the ordered receipt set.
MANIFEST_CHECKSUM_LENGTH = 64
# 0.4-degree cams_global nodes: 0.8 degrees apart so no two cells share one native grid point.
CELL_LATITUDES = (44.0, 44.8, 45.6, 46.4)
CELL_LONGITUDE = -116.0

SOURCE = {
    "key": "open-meteo-cams-air-quality",
    "name": "Open-Meteo CAMS air quality (redistributed Copernicus atmosphere analysis)",
    "owner": "Open-Meteo",
    "purpose": "test",
    "base_url": "https://air-quality-api.open-meteo.com/v1/air-quality",
    "license_name": "CC-BY 4.0 (Open-Meteo) over Copernicus Atmosphere Monitoring Service",
    "license_url": "https://open-meteo.com/en/license",
    "citation": "Open-Meteo is an intermediary redistributor of CAMS.",
    "retention_days": None,
    "reviewed_at": "2026-08-06T00:00:00Z",
    "reviewed_by": "test",
}


def _cell(latitude: float) -> dict[str, object]:
    return {
        "cell_key": f"cams-global-0p4deg:{latitude:.4f}:{CELL_LONGITUDE:.4f}",
        "latitude": latitude,
        "longitude": CELL_LONGITUDE,
    }


def _plan(
    *,
    domain: str = "cams_global",
    parameters: tuple[str, ...] = ("pm10", "pm2_5"),
    chunk_cell_count: int = 2,
    chunk_day_count: int = 31,
    latitudes: tuple[float, ...] = CELL_LATITUDES,
) -> HistoricalCamsAirQualityPlan:
    product = CAMS_PRODUCTS[domain]
    return HistoricalCamsAirQualityPlan.model_validate(
        {
            "schema_version": product.schema_version,
            "source": SOURCE,
            "window": {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()},
            "domain": domain,
            "grid_name": "pnw-air-quality-0p5deg",
            "grid_resolution_m": 55000,
            "native_grid_name": product.native_grid_name,
            "native_grid_degrees": product.native_grid_degrees,
            "native_grid_resolution_m": product.native_grid_resolution_m,
            "support_key": product.support_key,
            "cells": [_cell(latitude) for latitude in latitudes],
            "chunk_cell_count": chunk_cell_count,
            "chunk_day_count": chunk_day_count,
            "parameters": sorted(parameters),
            "transform_version": "open-meteo-cams-hourly-to-daily-v1",
            "release_set_key": "cams-test-release",
            "release_set_as_of": "2026-08-07T23:59:59Z",
            "description": "test plan",
        }
    )


def _chunk_hours(chunk: CamsAirQualityChunk) -> list[str]:
    return [
        f"{(chunk.start_date + timedelta(days=offset)).isoformat()}T{hour:02d}:00"
        for offset in range(chunk.day_count)
        for hour in range(HOURS_PER_DAY)
    ]


def _location(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    cell: AnalysisGridCell,
    index: int,
    *,
    values: dict[str, list[float | None]] | None = None,
) -> dict[str, object]:
    hours = _chunk_hours(chunk)
    hourly: dict[str, object] = {"time": hours}
    for parameter in plan.parameters:
        hourly[parameter] = (values or {}).get(parameter, [5.0] * len(hours))
    step = plan.product.native_grid_degrees
    location: dict[str, object] = {
        "latitude": round(round(cell.latitude / step) * step, 6),
        "longitude": round(round(cell.longitude / step) * step, 6),
        "timezone": "GMT",
        "utc_offset_seconds": 0,
        "hourly": hourly,
    }
    if index:
        location["location_id"] = index
    return location


def _payload(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    *,
    values: dict[str, dict[str, list[float | None]]] | None = None,
) -> bytes:
    return canonical_json_bytes(
        [
            _location(plan, chunk, cell, index, values=(values or {}).get(cell.cell_key))
            for index, cell in enumerate(chunk.cells)
        ]
    )


def _parse(
    plan: HistoricalCamsAirQualityPlan,
    chunk: CamsAirQualityChunk,
    payload: bytes,
) -> CamsAirQualityChunkResult:
    return parse_cams_air_quality_payload(
        plan,
        chunk,
        payload,
        CamsAirQualityCapture(
            retrieved_at=RETRIEVED_AT,
            wire_payload_bytes=len(payload),
            wire_payload_checksum=hashlib.sha256(payload).hexdigest(),
        ),
    )


def test_every_signal_specification_declares_a_known_reduction_and_a_finite_range() -> None:
    for specification in CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS.values():
        assert specification.daily_statistic in CAMS_DAILY_STATISTICS
        assert specification.minimum < specification.maximum


def test_the_two_domains_declare_different_lattices_and_different_support_keys() -> None:
    europe = CAMS_PRODUCTS["cams_europe"]
    global_domain = CAMS_PRODUCTS["cams_global"]
    assert europe.support_key != global_domain.support_key
    assert europe.native_grid_degrees != global_domain.native_grid_degrees
    for product in CAMS_PRODUCTS.values():
        assert set(product.supported_parameters).issubset(CAMS_AIR_QUALITY_SIGNAL_SPECIFICATIONS)


def test_a_domain_specific_variable_is_refused_against_the_wrong_domain() -> None:
    with pytest.raises(ValueError, match="does not serve parameter"):
        _plan(domain="cams_global", parameters=("european_aqi", "pm10"))
    with pytest.raises(ValueError, match="does not serve parameter"):
        _plan(domain="cams_europe", parameters=("dust", "pm10"))


def test_chunks_are_cut_on_both_the_cell_and_the_day_axis() -> None:
    plan = _plan(chunk_cell_count=2, chunk_day_count=31)
    day_blocks = len(plan.day_blocks)
    cell_blocks = len(plan.cells) // 2

    assert len(plan.chunks) == cell_blocks * day_blocks
    assert [chunk.key for chunk in plan.chunks] == sorted(chunk.key for chunk in plan.chunks)
    assert sum(chunk.day_count for chunk in plan.chunks[:day_blocks]) == plan.window.day_count
    assert plan.chunks[0].start_date == plan.window.start_date
    assert plan.chunks[day_blocks - 1].end_date == plan.window.end_date


def test_cells_sharing_one_native_grid_point_are_refused() -> None:
    with pytest.raises(ValueError, match="share a native grid point"):
        _plan(latitudes=(44.0, 44.05))


def test_a_plan_that_restates_its_product_incorrectly_is_refused() -> None:
    with pytest.raises(ValueError, match="restates its product incorrectly"):
        HistoricalCamsAirQualityPlan.model_validate(
            {**_plan().model_dump(mode="json"), "native_grid_resolution_m": 11000}
        )


def test_a_dense_hourly_series_reduces_to_one_daily_value_per_day() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    result = _parse(plan, chunk, _payload(plan, chunk))

    assert len(result.observations) == len(chunk.cells) * len(plan.parameters) * chunk.day_count
    assert all(item.quality_flag == QUALITY_FLAG_ACCEPTED for item in result.observations)
    assert all(item.normalized_value == pytest.approx(5.0) for item in result.observations)
    assert all(item.status == "complete" for item in result.coverage)


def test_a_maximum_reduction_takes_the_daily_peak_not_the_mean() -> None:
    plan = _plan(parameters=("uv_index",))
    chunk = plan.chunks[0]
    hours = [1.0] * (chunk.day_count * HOURS_PER_DAY)
    hours[5] = 9.0
    result = _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"uv_index": hours}}))

    first_day = next(item for item in result.observations if item.cell_key == chunk.cells[0].cell_key)
    assert first_day.normalized_value == pytest.approx(9.0)
    assert first_day.signal_name == "ultraviolet_index"


def test_a_day_below_the_hour_floor_is_unobserved_but_still_written() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    thin_hours = CAMS_MINIMUM_OBSERVED_HOURS_PER_DAY - 1
    series: list[float | None] = [
        5.0 if (index % HOURS_PER_DAY) < thin_hours else None
        for index in range(chunk.day_count * HOURS_PER_DAY)
    ]
    result = _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"pm10": series}}))

    rows = [item for item in result.observations if item.cell_key == chunk.cells[0].cell_key]
    coverage = next(item for item in result.coverage if item.cell_key == chunk.cells[0].cell_key)
    # The provider did publish here, so this is a transform failure, never "the provider modelled nothing".
    assert coverage.status == "failed"
    assert coverage.received_observation_count == 0
    assert len(rows) == chunk.day_count
    assert all(item.quality_flag == QUALITY_FLAG_INSUFFICIENT_HOURS for item in rows)
    assert all(item.normalized_value is None for item in rows)
    assert result.insufficient_hour_day_count == chunk.day_count


def test_a_series_the_provider_published_nothing_for_is_one_no_data_row() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    empty: list[float | None] = [None] * (chunk.day_count * HOURS_PER_DAY)
    result = _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"pm10": empty}}))

    coverage = next(item for item in result.coverage if item.cell_key == chunk.cells[0].cell_key)
    assert coverage.status == "no_data"
    assert not [item for item in result.observations if item.cell_key == chunk.cells[0].cell_key]


def test_a_mixed_series_keeps_one_row_per_day_and_names_each_days_reason() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    series: list[float | None] = [5.0] * (chunk.day_count * HOURS_PER_DAY)
    series[HOURS_PER_DAY : 2 * HOURS_PER_DAY] = [None] * HOURS_PER_DAY
    series[2 * HOURS_PER_DAY : 3 * HOURS_PER_DAY] = [None] * (HOURS_PER_DAY - 1) + [7.0]
    result = _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"pm10": series}}))

    rows = [item for item in result.observations if item.cell_key == chunk.cells[0].cell_key]
    coverage = next(item for item in result.coverage if item.cell_key == chunk.cells[0].cell_key)
    assert coverage.status == "partial"
    assert coverage.received_observation_count == chunk.day_count - 2
    assert rows[0].quality_flag == QUALITY_FLAG_ACCEPTED
    assert rows[1].quality_flag == QUALITY_FLAG_SOURCE_MISSING
    assert rows[2].quality_flag == QUALITY_FLAG_INSUFFICIENT_HOURS


def test_an_hourly_value_outside_its_reviewed_range_fails_the_whole_chunk() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    series: list[float | None] = [5.0] * (chunk.day_count * HOURS_PER_DAY)
    series[3] = -999.0
    with pytest.raises(ValueError, match="outside its reviewed physical"):
        _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"pm10": series}}))


def test_a_sparse_or_misordered_hourly_axis_is_refused() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    document = [_location(plan, chunk, cell, index) for index, cell in enumerate(chunk.cells)]
    hourly = document[0]["hourly"]
    assert isinstance(hourly, dict)
    hours = list(hourly["time"])
    hours[1], hours[2] = hours[2], hours[1]
    hourly["time"] = hours
    with pytest.raises(ValueError, match="hourly time axis does not match"):
        _parse(plan, chunk, canonical_json_bytes(document))


def test_a_truncated_hourly_axis_is_refused() -> None:
    plan = _plan(parameters=("pm10",))
    chunk = plan.chunks[0]
    document = [_location(plan, chunk, cell, index) for index, cell in enumerate(chunk.cells)]
    hourly = document[0]["hourly"]
    assert isinstance(hourly, dict)
    hourly["time"] = list(hourly["time"])[:-1]
    with pytest.raises(ValueError, match="hourly time axis does not match"):
        _parse(plan, chunk, canonical_json_bytes(document))


def test_the_raw_cache_round_trips_one_chunk(tmp_path: Path) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    result = _parse(plan, chunk, _payload(plan, chunk))
    receipt = cache_historical_cams_result(tmp_path, plan, result)

    assert receipt.chunk_key == chunk.key
    reloaded = load_cached_historical_cams_result(tmp_path, plan, chunk)
    assert reloaded is not None
    assert reloaded.payload_checksum == result.payload_checksum
    assert len(reloaded.observations) == len(result.observations)


def test_one_chunk_of_many_leaves_the_checkpoint_running() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    checkpoint = record_historical_cams_result(
        plan, initialize_historical_cams_checkpoint(plan), _parse(plan, chunk, _payload(plan, chunk))
    )

    assert checkpoint.state == "running"
    assert checkpoint.receipts[0].day_count == chunk.day_count
    assert checkpoint.receipts[0].failed_series_count == 0


def test_a_release_manifest_requires_every_chunk_of_both_axes_in_plan_order() -> None:
    """Both axes, in the plan's own order, or there is no manifest.

    The plan deliberately cuts more than one cell block AND more than one day block. Receipts are
    folded into a lexicographically sorted set, so this passes only while `plan.chunks` is
    cell-major; swapping the two `for` clauses in the chunk comprehension leaves the checkpoint
    `running` forever and fails here, rather than at the end of a paid four-year fetch.
    """
    plan = _plan(parameters=("pm10",), chunk_cell_count=1, chunk_day_count=92, latitudes=CELL_LATITUDES[:2])
    cell_blocks = -(-len(plan.cells) // plan.chunk_cell_count)
    day_blocks = len(plan.day_blocks)
    assert cell_blocks > 1
    assert day_blocks > 1
    assert len(plan.chunks) == cell_blocks * day_blocks

    checkpoint = initialize_historical_cams_checkpoint(plan)
    for chunk in plan.chunks[:-1]:
        checkpoint = record_historical_cams_result(plan, checkpoint, _parse(plan, chunk, _payload(plan, chunk)))

    assert checkpoint.state == "running"
    with pytest.raises(ValueError, match="complete validated checkpoint is required"):
        historical_cams_release_manifest(plan, checkpoint)

    last = plan.chunks[-1]
    checkpoint = record_historical_cams_result(plan, checkpoint, _parse(plan, last, _payload(plan, last)))

    assert checkpoint.state == "validated"
    assert [receipt.chunk_key for receipt in checkpoint.receipts] == [chunk.key for chunk in plan.chunks]
    assert sum(receipt.day_count for receipt in checkpoint.receipts) == plan.window.day_count * cell_blocks
    assert len(historical_cams_release_manifest(plan, checkpoint)) == MANIFEST_CHECKSUM_LENGTH


# --- transport failure path -------------------------------------------------------------------
#
# The retry/backoff loop itself lives in execution/open_meteo_lane.py and is covered scope by scope
# in tests/test_open_meteo_lane.py. What these prove is the WIRING: that this lane really routes its
# fetch through that loop, surfaces its own error type, and never swallows an exhausted quota.

FETCH_TARGET = "agri_data_service.execution.historical_cams.fetch_air_quality_hourly"


@pytest.mark.asyncio
async def test_an_exhausted_daily_quota_surfaces_without_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    attempts = 0
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        nonlocal attempts
        attempts += 1
        raise OpenMeteoRateLimitError("day", "Daily API request limit exceeded. Please try again tomorrow.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(FETCH_TARGET, refuse)
    with pytest.raises(CamsAirQualityFetchError) as raised:
        await fetch_cams_air_quality_chunk(plan, chunk, client=None, sleep=record_wait)

    assert attempts == 1
    assert waits == []
    assert raised.value.chunk_key == chunk.key
    assert "CAMS air-quality chunk" in str(raised.value)


@pytest.mark.asyncio
async def test_a_minutely_quota_is_retried_then_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(FETCH_TARGET, refuse)
    with pytest.raises(CamsAirQualityFetchError):
        await fetch_cams_air_quality_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

    assert waits == [70.0, 70.0, 70.0]


@pytest.mark.asyncio
async def test_a_transport_failure_is_retried_with_escalating_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise UpstreamHttpError(502)

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(FETCH_TARGET, refuse)
    with pytest.raises(CamsAirQualityFetchError) as raised:
        await fetch_cams_air_quality_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

    assert waits == [15.0, 30.0, 45.0]
    assert raised.value.attempts == 4  # noqa: PLR2004


@pytest.mark.asyncio
async def test_a_recovered_refusal_still_produces_an_accounted_result(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    body = _payload(plan, chunk).decode("utf-8")
    calls = 0

    async def flaky(_client: object, _url: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")
        return body

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(FETCH_TARGET, flaky)
    result = await fetch_cams_air_quality_chunk(
        plan, chunk, client=None, retrieved_at=RETRIEVED_AT, sleep=no_wait
    )

    assert calls == 2  # noqa: PLR2004
    require_accounted_cams_result(plan, result)
    assert result.chunk_key == chunk.key
