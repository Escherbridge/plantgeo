"""Contract tests for the GloFAS flood lane: product inheritance, accounting, nulls, and bounded values."""


from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.historical_glofas import (
    GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS,
    GLOFAS_PRODUCTS,
    GlofasFloodCapture,
    GlofasFloodFetchError,
    HistoricalGlofasFloodPlan,
    cache_historical_glofas_result,
    fetch_glofas_flood_chunk,
    historical_glofas_release_manifest,
    initialize_historical_glofas_checkpoint,
    load_cached_historical_glofas_result,
    parse_glofas_flood_payload,
    record_historical_glofas_result,
    rederive_historical_glofas_checkpoint_state,
    require_accounted_glofas_result,
)
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.execution.historical_backfill import AnalysisGridCell
    from agri_data_service.execution.historical_glofas import GlofasFloodChunk, GlofasFloodChunkResult

WINDOW_END = date(2026, 4, 30)
WINDOW_START = WINDOW_END.replace(year=WINDOW_END.year - 4)
RETRIEVED_AT = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
# 0.05-degree v4 nodes, far enough apart that no two cells share one native grid point.
CELL_LATITUDES = (44.0, 44.25, 44.5, 44.75)
CELL_LONGITUDE = -116.0

SOURCE = {
    "key": "open-meteo-glofas-flood",
    "name": "Open-Meteo GloFAS flood (redistributed Copernicus EMS reanalysis)",
    "owner": "Open-Meteo",
    "purpose": "test",
    "base_url": "https://flood-api.open-meteo.com/v1/flood",
    "license_name": "CC-BY 4.0 (Open-Meteo) over Copernicus Emergency Management Service GloFAS",
    "license_url": "https://open-meteo.com/en/license",
    "citation": "Open-Meteo is an intermediary redistributor of GloFAS.",
    "retention_days": None,
    "reviewed_at": "2026-08-06T00:00:00Z",
    "reviewed_by": "test",
}


def _cell(latitude: float) -> dict[str, object]:
    return {
        "cell_key": f"glofas-v4-0p05deg:{latitude:.4f}:{CELL_LONGITUDE:.4f}",
        "latitude": latitude,
        "longitude": CELL_LONGITUDE,
    }


def _plan(
    *,
    model: str = "consolidated_v4",
    parameters: tuple[str, ...] = ("river_discharge",),
    chunk_cell_count: int = 2,
    latitudes: tuple[float, ...] = CELL_LATITUDES,
) -> HistoricalGlofasFloodPlan:
    product = GLOFAS_PRODUCTS[model]
    return HistoricalGlofasFloodPlan.model_validate(
        {
            "schema_version": product.schema_version,
            "source": SOURCE,
            "window": {"start_date": WINDOW_START.isoformat(), "end_date": WINDOW_END.isoformat()},
            "model": model,
            "grid_name": "pnw-flood-0p25deg",
            "grid_resolution_m": 27830,
            "native_grid_name": product.native_grid_name,
            "native_grid_degrees": product.native_grid_degrees,
            "native_grid_resolution_m": product.native_grid_resolution_m,
            "support_key": product.support_key,
            "cells": [_cell(latitude) for latitude in latitudes],
            "chunk_cell_count": chunk_cell_count,
            "parameters": sorted(parameters),
            "transform_version": "open-meteo-glofas-daily-river-discharge-v1",
            "release_set_key": "glofas-test-release",
            "release_set_as_of": "2026-08-07T23:59:59Z",
            "description": "test plan",
        }
    )


def _window_days(plan: HistoricalGlofasFloodPlan) -> list[str]:
    days: list[str] = []
    current = plan.window.start_date
    while current <= plan.window.end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _location(
    plan: HistoricalGlofasFloodPlan,
    cell: AnalysisGridCell,
    index: int,
    *,
    values: dict[str, list[float | None]] | None = None,
) -> dict[str, object]:
    days = _window_days(plan)
    daily: dict[str, object] = {"time": days}
    for parameter in plan.parameters:
        daily[parameter] = (values or {}).get(parameter, [12.5] * len(days))
    step = plan.product.native_grid_degrees
    location: dict[str, object] = {
        "latitude": round(round(cell.latitude / step) * step, 6),
        "longitude": round(round(cell.longitude / step) * step, 6),
        "timezone": "GMT",
        "utc_offset_seconds": 0,
        "daily": daily,
    }
    if index:
        location["location_id"] = index
    return location


def _payload(
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
    *,
    values: dict[str, dict[str, list[float | None]]] | None = None,
) -> bytes:
    return canonical_json_bytes(
        [
            _location(plan, cell, index, values=(values or {}).get(cell.cell_key))
            for index, cell in enumerate(chunk.cells)
        ]
    )


def _capture(payload: bytes) -> GlofasFloodCapture:
    return GlofasFloodCapture(
        retrieved_at=RETRIEVED_AT,
        wire_payload_bytes=len(payload),
        wire_payload_checksum=hashlib.sha256(payload).hexdigest(),
    )


def _parse(
    plan: HistoricalGlofasFloodPlan,
    chunk: GlofasFloodChunk,
    payload: bytes,
) -> GlofasFloodChunkResult:
    return parse_glofas_flood_payload(plan, chunk, payload, _capture(payload))


def test_every_product_declares_a_native_lattice_and_a_distinct_support_key() -> None:
    for model, product in GLOFAS_PRODUCTS.items():
        assert product.model == model
        assert product.native_grid_degrees > 0
        assert product.support_key.startswith("glofas-v")
        assert set(product.supported_parameters).issubset(GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS)
    # v3 and v4 are different lattices, so their support keys must never collide.
    assert GLOFAS_PRODUCTS["consolidated_v3"].support_key != GLOFAS_PRODUCTS["consolidated_v4"].support_key


def test_every_signal_specification_carries_a_finite_inclusive_range() -> None:
    for specification in GLOFAS_FLOOD_SIGNAL_SPECIFICATIONS.values():
        assert specification.minimum < specification.maximum
        assert specification.original_unit == specification.normalized_unit == "m^3/s"


def test_a_plan_that_restates_its_product_incorrectly_is_refused() -> None:
    product = GLOFAS_PRODUCTS["consolidated_v4"]
    with pytest.raises(ValueError, match="restates its product incorrectly"):
        HistoricalGlofasFloodPlan.model_validate(
            {
                **_plan().model_dump(mode="json"),
                "native_grid_degrees": 0.1,
                "support_key": product.support_key,
            }
        )


def test_a_reanalysis_plan_cannot_request_ensemble_statistics() -> None:
    with pytest.raises(ValueError, match="does not serve parameter"):
        _plan(model="consolidated_v4", parameters=("river_discharge", "river_discharge_mean"))
    # The same parameters are legitimate against a forecast product.
    assert _plan(model="forecast_v4", parameters=("river_discharge", "river_discharge_mean")).parameters == [
        "river_discharge",
        "river_discharge_mean",
    ]


def test_cells_sharing_one_native_grid_point_are_refused() -> None:
    with pytest.raises(ValueError, match="share a native grid point"):
        _plan(latitudes=(44.0, 44.001))


def test_an_unreviewed_model_is_refused() -> None:
    with pytest.raises(ValueError, match="model must be one of"):
        HistoricalGlofasFloodPlan.model_validate({**_plan().model_dump(mode="json"), "model": "forecast_v9"})


def test_parse_normalizes_every_reviewed_cell_signal_and_day() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    result = _parse(plan, chunk, _payload(plan, chunk))

    day_count = plan.window.day_count
    assert len(result.observations) == len(chunk.cells) * len(plan.parameters) * day_count
    assert all(item.is_observed for item in result.observations)
    assert all(item.quality_flag == "accepted" for item in result.observations)
    assert all(item.status == "complete" for item in result.coverage)
    assert len(result.grid_points) == len(chunk.cells)


def test_a_series_the_provider_modelled_nowhere_becomes_one_no_data_coverage_row() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    silent = chunk.cells[1].cell_key
    payload = _payload(
        plan,
        chunk,
        values={silent: {"river_discharge": [None] * plan.window.day_count}},
    )
    result = _parse(plan, chunk, payload)

    statuses = {item.cell_key: item.status for item in result.coverage}
    assert statuses[silent] == "no_data"
    # No per-day `is_observed=false` rows are written for a series with nothing behind it.
    assert {item.cell_key for item in result.observations} == {chunk.cells[0].cell_key}


def test_a_partially_modelled_series_keeps_one_row_per_day() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    day_count = plan.window.day_count
    gappy = chunk.cells[0].cell_key
    series: list[float | None] = [12.5] * day_count
    series[0] = None
    result = _parse(plan, chunk, _payload(plan, chunk, values={gappy: {"river_discharge": series}}))

    coverage = {item.cell_key: item for item in result.coverage}
    assert coverage[gappy].status == "partial"
    assert coverage[gappy].received_observation_count == day_count - 1
    rows = [item for item in result.observations if item.cell_key == gappy]
    assert len(rows) == day_count
    assert rows[0].quality_flag == "source_missing"
    assert rows[0].normalized_value is None


def test_a_value_outside_its_reviewed_physical_range_fails_the_whole_chunk() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    sentinel: list[float | None] = [12.5] * plan.window.day_count
    sentinel[10] = -999.0
    with pytest.raises(ValueError, match="outside its reviewed physical"):
        _parse(plan, chunk, _payload(plan, chunk, values={chunk.cells[0].cell_key: {"river_discharge": sentinel}}))


def test_a_grid_point_outside_the_reviewed_cell_is_refused() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = [
        _location(plan, cell, index) for index, cell in enumerate(chunk.cells)
    ]
    document[0]["latitude"] = 45.0
    with pytest.raises(ValueError, match="outside the reviewed cell"):
        _parse(plan, chunk, canonical_json_bytes(document))


def test_a_response_off_the_reviewed_gmt_time_base_is_refused() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = [_location(plan, cell, index) for index, cell in enumerate(chunk.cells)]
    document[0]["utc_offset_seconds"] = 3600
    with pytest.raises(ValueError, match="GMT time base"):
        _parse(plan, chunk, canonical_json_bytes(document))


def test_out_of_order_locations_are_refused() -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    document = [_location(plan, cell, index) for index, cell in enumerate(chunk.cells)]
    document[1]["location_id"] = 7
    with pytest.raises(ValueError, match="not in the requested order"):
        _parse(plan, chunk, canonical_json_bytes(document))


def test_the_raw_cache_round_trips_and_refuses_conflicting_content(tmp_path: Path) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    result = _parse(plan, chunk, _payload(plan, chunk))
    receipt = cache_historical_glofas_result(tmp_path, plan, result)

    assert receipt.chunk_key == chunk.key
    reloaded = load_cached_historical_glofas_result(tmp_path, plan, chunk)
    assert reloaded is not None
    assert reloaded.payload_checksum == result.payload_checksum
    assert len(reloaded.observations) == len(result.observations)

    other = _parse(
        plan,
        chunk,
        _payload(plan, chunk, values={chunk.cells[0].cell_key: {"river_discharge": [9.0] * plan.window.day_count}}),
    )
    with pytest.raises(ValueError, match="different source content"):
        cache_historical_glofas_result(tmp_path, plan, other)


def test_a_complete_checkpoint_validates_and_yields_a_release_manifest() -> None:
    plan = _plan(chunk_cell_count=4)
    chunk = plan.chunks[0]
    checkpoint = record_historical_glofas_result(
        plan, initialize_historical_glofas_checkpoint(plan), _parse(plan, chunk, _payload(plan, chunk))
    )

    assert checkpoint.state == "validated"
    assert historical_glofas_release_manifest(plan, checkpoint)


def test_a_blocked_checkpoint_whose_chunks_are_all_receipted_is_rederived_as_validated() -> None:
    plan = _plan(chunk_cell_count=4)
    chunk = plan.chunks[0]
    checkpoint = record_historical_glofas_result(
        plan, initialize_historical_glofas_checkpoint(plan), _parse(plan, chunk, _payload(plan, chunk))
    )
    stalled = checkpoint.model_copy(update={"state": "blocked", "reason": "quota"})

    rederived = rederive_historical_glofas_checkpoint_state(plan, stalled)

    assert rederived.state == "validated"
    # The reason is the evidence of the last stop and survives; only `state` gates a resume.
    assert rederived.reason == "quota"


def test_an_incomplete_checkpoint_has_no_release_manifest() -> None:
    plan = _plan(chunk_cell_count=2)
    chunk = plan.chunks[0]
    checkpoint = record_historical_glofas_result(
        plan, initialize_historical_glofas_checkpoint(plan), _parse(plan, chunk, _payload(plan, chunk))
    )

    assert checkpoint.state == "running"
    with pytest.raises(ValueError, match="complete validated checkpoint"):
        historical_glofas_release_manifest(plan, checkpoint)


# --- transport failure path -------------------------------------------------------------------
#
# The retry/backoff loop itself lives in execution/open_meteo_lane.py and is covered scope by scope
# in tests/test_open_meteo_lane.py. What these prove is the WIRING: that this lane really routes its
# fetch through that loop, surfaces its own error type, and never swallows an exhausted quota.

FETCH_TARGET = "agri_data_service.execution.historical_glofas.fetch_flood_daily"


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
    with pytest.raises(GlofasFloodFetchError) as raised:
        await fetch_glofas_flood_chunk(plan, chunk, client=None, sleep=record_wait)

    assert attempts == 1
    assert waits == []
    assert raised.value.chunk_key == chunk.key
    assert "GloFAS flood chunk" in str(raised.value)


@pytest.mark.asyncio
async def test_a_minutely_quota_is_retried_then_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(FETCH_TARGET, refuse)
    with pytest.raises(GlofasFloodFetchError):
        await fetch_glofas_flood_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

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
    with pytest.raises(GlofasFloodFetchError) as raised:
        await fetch_glofas_flood_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

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
    result = await fetch_glofas_flood_chunk(
        plan, chunk, client=None, retrieved_at=RETRIEVED_AT, sleep=no_wait
    )

    assert calls == 2  # noqa: PLR2004
    require_accounted_glofas_result(plan, result)
    assert result.chunk_key == chunk.key
