"""Contract tests for the ensemble forecast lane: product inheritance, member accounting, quantile carriage."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.contracts import canonical_json_bytes
from agri_data_service.execution.ensemble_forecast import (
    ENSEMBLE_PRODUCTS,
    ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE,
    EnsembleForecastCapture,
    EnsembleForecastFetchError,
    EnsembleForecastPlan,
    StagedForecastValue,
    cache_ensemble_forecast_result,
    empirical_quantile,
    ensemble_forecast_release_manifest,
    ensemble_forecast_staged_document,
    ensemble_forecast_staged_document_path,
    fetch_ensemble_forecast_chunk,
    format_quantile_level_key,
    initialize_ensemble_forecast_checkpoint,
    load_cached_ensemble_forecast_result,
    parse_ensemble_forecast_payload,
    record_ensemble_forecast_result,
    rederive_ensemble_forecast_checkpoint_state,
    require_accounted_ensemble_forecast_result,
    require_consistent_quantile_carriage,
    require_valid_quantile_levels,
    staged_forecast_receipt_checksum,
    summarize_ensemble_members,
    write_ensemble_forecast_staged_document,
)
from agri_data_service.ingest.http import UpstreamHttpError
from agri_data_service.ingest.open_meteo import OpenMeteoRateLimitError

if TYPE_CHECKING:
    from pathlib import Path

    from agri_data_service.execution.ensemble_forecast import (
        EnsembleForecastChunk,
        EnsembleForecastChunkResult,
        StagedForecastReceipt,
    )

ISSUE_DATE = date(2026, 8, 6)
RETRIEVED_AT = datetime(2026, 8, 6, 1, 0, tzinfo=UTC)
# 0.5-degree gem_global nodes, far enough apart that no two cells share one native grid point.
CELL_LATITUDES = (44.0, 44.5)
CELL_LONGITUDE = -116.0
QUANTILE_LEVELS = (0.1, 0.5, 0.9)

SOURCE = {
    "key": "open-meteo-ensemble-forecast",
    "name": "Open-Meteo Ensemble Forecast",
    "owner": "Open-Meteo",
    "purpose": "test",
    "base_url": "https://ensemble-api.open-meteo.com/v1/ensemble",
    "license_name": "CC-BY 4.0 (Open-Meteo) over the originating national weather services",
    "license_url": "https://open-meteo.com/en/license",
    "citation": "Open-Meteo is an intermediary redistributor of ensemble model output.",
    "retention_days": None,
    "reviewed_at": "2026-08-06T00:00:00Z",
    "reviewed_by": "test",
}


def _cell(latitude: float) -> dict[str, object]:
    return {
        "cell_key": f"gem-global-0p5deg:{latitude:.4f}:{CELL_LONGITUDE:.4f}",
        "latitude": latitude,
        "longitude": CELL_LONGITUDE,
    }


def _plan(  # noqa: PLR0913
    *,
    model: str = "gem_global",
    parameters: tuple[str, ...] = ("temperature_2m",),
    horizon_days: int = 1,
    chunk_cell_count: int = 2,
    quantile_levels: tuple[float, ...] = QUANTILE_LEVELS,
    overrides: dict[str, object] | None = None,
) -> EnsembleForecastPlan:
    product = ENSEMBLE_PRODUCTS[model]
    payload: dict[str, object] = {
        "schema_version": product.schema_version,
        "source": SOURCE,
        "model": model,
        "issue_date": ISSUE_DATE.isoformat(),
        "horizon_days": horizon_days,
        "grid_name": "pnw-ensemble-0p5deg",
        "grid_resolution_m": 55_000,
        "native_grid_name": product.native_grid_name,
        "native_grid_degrees": product.native_grid_degrees,
        "native_grid_resolution_m": product.native_grid_resolution_m,
        "support_key": product.support_key,
        "member_count": product.member_count,
        "cells": [_cell(latitude) for latitude in CELL_LATITUDES],
        "chunk_cell_count": chunk_cell_count,
        "parameters": sorted(parameters),
        "quantile_levels": sorted(quantile_levels),
        "transform_version": "open-meteo-ensemble-quantile-v1",
        "release_set_key": "ensemble-test-release",
        "release_set_as_of": "2026-08-06T01:00:00Z",
        "description": "test plan",
    }
    payload.update(overrides or {})
    return EnsembleForecastPlan.model_validate(payload)


def _payload(
    plan: EnsembleForecastPlan,
    chunk: EnsembleForecastChunk,
    *,
    member_values: dict[tuple[int, int], float | None] | None = None,
    drop_members: int = 0,
) -> bytes:
    """Build a canonical multi-location document; `member_values` overrides one (member, step) cell."""
    overrides = member_values or {}
    times = [
        (plan.issue_time + timedelta(hours=step)).strftime("%Y-%m-%dT%H:%M") for step in range(plan.step_count)
    ]
    locations: list[dict[str, object]] = []
    for index, cell in enumerate(chunk.cells):
        hourly: dict[str, object] = {"time": times}
        for parameter in plan.parameters:
            for member in range(1, plan.member_count + 1 - drop_members):
                hourly[f"{parameter}_member{member:02d}"] = [
                    overrides.get((member, step), float(member) + 0.25 * step) for step in range(plan.step_count)
                ]
        location: dict[str, object] = {
            "latitude": cell.latitude,
            "longitude": cell.longitude,
            "timezone": "GMT",
            "utc_offset_seconds": 0,
            "hourly": hourly,
        }
        if index:
            location["location_id"] = index
        locations.append(location)
    return canonical_json_bytes(locations)


def _capture() -> EnsembleForecastCapture:
    return EnsembleForecastCapture(
        retrieved_at=RETRIEVED_AT,
        wire_payload_bytes=1,
        wire_payload_checksum="0" * 64,
    )


def _parse(plan: EnsembleForecastPlan, payload: bytes) -> EnsembleForecastChunkResult:
    return parse_ensemble_forecast_payload(plan, plan.chunks[0], payload, _capture())


def _receipt(result: EnsembleForecastChunkResult) -> StagedForecastReceipt:
    return result.receipts[0]


def test_a_plan_inherits_its_lattice_member_count_and_schema_from_its_product() -> None:
    plan = _plan()
    product = ENSEMBLE_PRODUCTS["gem_global"]

    assert plan.product == product
    assert plan.member_count == product.member_count
    assert plan.support_key == product.support_key
    assert plan.step_count == 24
    assert plan.valid_to == plan.issue_time + timedelta(hours=24)


def test_restating_a_products_member_count_incorrectly_is_a_plan_error() -> None:
    with pytest.raises(ValueError, match="restates its product incorrectly"):
        _plan(overrides={"member_count": 30})


def test_an_unreviewed_model_is_a_plan_error() -> None:
    with pytest.raises(ValueError, match="model must be one of"):
        _plan(overrides={"model": "icon_seamless"})


def test_a_variable_the_lane_does_not_govern_is_a_plan_error() -> None:
    with pytest.raises(ValueError, match="unsupported ensemble forecast parameter"):
        _plan(overrides={"parameters": ["cape"]})


def test_quantile_levels_must_satisfy_the_receipt_check() -> None:
    with pytest.raises(ValueError, match="must include the median"):
        _plan(quantile_levels=(0.1, 0.9))
    with pytest.raises(ValueError, match="must lie in"):
        require_valid_quantile_levels([0.5, 1.5])
    with pytest.raises(ValueError, match="must be distinct"):
        require_valid_quantile_levels([0.5, 0.5])
    assert require_valid_quantile_levels([0.1, 0.5, 0.9]) == [0.1, 0.5, 0.9]


def test_the_canonical_level_key_is_the_only_renderer() -> None:
    assert format_quantile_level_key(0.1) == "0.1"
    assert format_quantile_level_key(0.5) == "0.5"
    assert format_quantile_level_key(0.25) == "0.25"
    assert format_quantile_level_key(1.0) == "1"


def test_empirical_quantiles_interpolate_and_stay_monotone_in_level() -> None:
    assert empirical_quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert empirical_quantile([5.0], 0.9) == 5.0
    summarized = summarize_ensemble_members([5.0, 1.0, 3.0, 2.0, 4.0], [0.1, 0.5, 0.9])
    assert list(summarized) == ["0.1", "0.5", "0.9"]
    assert summarized["0.1"] <= summarized["0.5"] <= summarized["0.9"]


def test_a_parsed_chunk_stages_one_receipt_per_cell_and_variable() -> None:
    plan = _plan()
    result = _parse(plan, _payload(plan, plan.chunks[0]))

    assert len(result.receipts) == len(plan.cells)
    assert result.failures == ()
    receipt = _receipt(result)
    assert receipt.expected_value_count == plan.step_count
    assert len(receipt.values) == plan.step_count
    assert receipt.quantile_levels == list(QUANTILE_LEVELS)
    assert receipt.member_count == plan.member_count
    assert receipt.signal_name == "air_temperature_2m"
    assert receipt.series_key.startswith("open-meteo-ensemble-forecast:gem_global:gem-global-0p5deg:")


def test_every_staged_value_carries_the_median_as_its_point_value() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))

    first = receipt.values[0]
    assert first.horizon_step == 1
    assert first.valid_time == plan.issue_time
    assert set(first.quantile_values) == {"0.1", "0.5", "0.9"}
    assert first.point_value == first.quantile_values["0.5"]
    assert first.p10_value == first.quantile_values["0.1"]
    assert first.p50_value == first.quantile_values["0.5"]
    assert first.p90_value == first.quantile_values["0.9"]


def test_horizon_steps_are_one_based_and_track_the_hourly_axis() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))

    assert [value.horizon_step for value in receipt.values] == list(range(1, plan.step_count + 1))
    assert receipt.values[-1].valid_time == plan.issue_time + timedelta(hours=plan.step_count - 1)


def test_one_null_member_drops_that_step_rather_than_summarizing_a_smaller_ensemble() -> None:
    plan = _plan()
    payload = _payload(plan, plan.chunks[0], member_values={(3, 5): None})

    receipt = _receipt(_parse(plan, payload))

    assert len(receipt.values) == plan.step_count - 1
    assert 6 not in {value.horizon_step for value in receipt.values}
    assert receipt.quality_summary["incomplete_step_count"] == 1
    assert receipt.quality_summary["complete_step_count"] == plan.step_count - 1
    assert receipt.expected_value_count == plan.step_count


def test_a_series_too_sparse_to_summarize_is_an_explicit_failure_not_a_thin_receipt() -> None:
    plan = _plan()
    holes = {(3, step): None for step in range(plan.step_count // 2)}
    payload = _payload(plan, plan.chunks[0], member_values=holes)

    result = _parse(plan, payload)

    assert result.receipts == ()
    assert len(result.failures) == len(plan.cells)
    failure = result.failures[0]
    assert failure.reason == "insufficient_member_coverage"
    assert failure.expected_step_count == plan.step_count
    assert failure.complete_step_count == plan.step_count // 2


def test_a_missing_member_series_fails_the_whole_chunk() -> None:
    plan = _plan()
    payload = _payload(plan, plan.chunks[0], drop_members=1)

    with pytest.raises(ValueError, match="member series, not the reviewed"):
        _parse(plan, payload)


def test_an_extra_member_series_fails_the_whole_chunk() -> None:
    plan = _plan()
    document = json.loads(_payload(plan, plan.chunks[0]))
    extra = plan.member_count + 1
    document[0]["hourly"][f"temperature_2m_member{extra:02d}"] = [1.0] * plan.step_count

    with pytest.raises(ValueError, match="member series, not the reviewed"):
        _parse(plan, canonical_json_bytes(document))


def test_a_value_outside_its_reviewed_physical_range_fails_the_chunk() -> None:
    plan = _plan()
    payload = _payload(plan, plan.chunks[0], member_values={(2, 0): -999.0})

    with pytest.raises(ValueError, match="outside its reviewed physical"):
        _parse(plan, payload)


def test_a_time_axis_that_is_not_the_reviewed_horizon_fails_the_chunk() -> None:
    plan = _plan()
    document = json.loads(_payload(plan, plan.chunks[0]))
    document[0]["hourly"]["time"][3] = "2027-01-01T00:00"

    with pytest.raises(ValueError, match="time axis does not match"):
        _parse(plan, canonical_json_bytes(document))


def test_a_response_off_the_gmt_time_base_fails_the_chunk() -> None:
    plan = _plan()
    document = json.loads(_payload(plan, plan.chunks[0]))
    document[0]["utc_offset_seconds"] = -25200

    with pytest.raises(ValueError, match="GMT time base"):
        _parse(plan, canonical_json_bytes(document))


def test_a_grid_point_outside_the_reviewed_cell_is_refused() -> None:
    plan = _plan()
    document = json.loads(_payload(plan, plan.chunks[0]))
    document[0]["latitude"] = 40.0

    with pytest.raises(ValueError, match="outside the reviewed cell"):
        _parse(plan, canonical_json_bytes(document))


def test_quantile_keys_that_disagree_with_the_declared_levels_are_refused() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(
        update={
            "values": [
                receipt.values[0].model_copy(update={"quantile_values": {"0.5": receipt.values[0].point_value}}),
                *receipt.values[1:],
            ]
        }
    )

    with pytest.raises(ValueError, match="do not match the receipt's declared quantile levels"):
        require_consistent_quantile_carriage(tampered)


def test_a_point_value_that_is_not_the_median_is_refused() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(
        update={"values": [receipt.values[0].model_copy(update={"point_value": 999.0}), *receipt.values[1:]]}
    )

    with pytest.raises(ValueError, match="not the declared ensemble median"):
        require_consistent_quantile_carriage(tampered)


def test_a_band_mirror_that_disagrees_with_its_quantile_object_is_refused() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(
        update={"values": [receipt.values[0].model_copy(update={"p10_value": None}), *receipt.values[1:]]}
    )

    with pytest.raises(ValueError, match="band mirror disagrees"):
        require_consistent_quantile_carriage(tampered)


def test_a_band_mirror_for_an_undeclared_level_is_refused() -> None:
    plan = _plan(quantile_levels=(0.5,))
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(
        update={"values": [receipt.values[0].model_copy(update={"p90_value": 1.0}), *receipt.values[1:]]}
    )

    assert receipt.values[0].p10_value is None
    with pytest.raises(ValueError, match="undeclared quantile level"):
        require_consistent_quantile_carriage(tampered)


def test_an_out_of_order_band_pair_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="p10 exceeds p50"):
        StagedForecastValue(
            valid_time=RETRIEVED_AT,
            horizon_step=1,
            point_value=1.0,
            p10_value=2.0,
            p50_value=1.0,
            quantile_values={"0.5": 1.0},
        )


def test_the_staged_document_carries_the_exact_insert_payload_and_the_blocked_state() -> None:
    plan = _plan()
    result = _parse(plan, _payload(plan, plan.chunks[0]))

    document = ensemble_forecast_staged_document(plan, result.receipts)

    assert document["plan_checksum"] == plan.plan_checksum
    assert document["warehouse_persistence"] == ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE
    receipts = document["receipts"]
    assert len(receipts) == len(plan.cells)
    assert [item["series_key"] for item in receipts] == sorted(item["series_key"] for item in receipts)
    first = receipts[0]
    assert first["receipt_checksum"] == staged_forecast_receipt_checksum(result.receipts[0])
    assert set(first["values"][0]["quantile_values"]) == {"0.1", "0.5", "0.9"}
    assert first["expected_value_count"] == plan.step_count


def test_the_staged_document_is_written_atomically_and_digested(tmp_path: Path) -> None:
    plan = _plan()
    result = _parse(plan, _payload(plan, plan.chunks[0]))
    path = ensemble_forecast_staged_document_path(tmp_path, plan)

    checksum = write_ensemble_forecast_staged_document(path, ensemble_forecast_staged_document(plan, result.receipts))

    assert path.exists()
    assert len(checksum) == 64
    reloaded = json.loads(path.read_bytes())
    assert reloaded["warehouse_persistence"] == ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE


def test_a_checkpoint_advances_only_on_an_accounted_chunk_and_rederives_its_state() -> None:
    plan = _plan(chunk_cell_count=1)
    checkpoint = initialize_ensemble_forecast_checkpoint(plan, updated_at=RETRIEVED_AT)
    assert checkpoint.state == "initialized"

    first = parse_ensemble_forecast_payload(plan, plan.chunks[0], _payload(plan, plan.chunks[0]), _capture())
    checkpoint = record_ensemble_forecast_result(plan, checkpoint, first, updated_at=RETRIEVED_AT)
    assert checkpoint.state == "running"

    second = parse_ensemble_forecast_payload(plan, plan.chunks[1], _payload(plan, plan.chunks[1]), _capture())
    checkpoint = record_ensemble_forecast_result(plan, checkpoint, second, updated_at=RETRIEVED_AT)
    assert checkpoint.state == "validated"
    assert sum(receipt.staged_receipt_count for receipt in checkpoint.receipts) == len(plan.cells)

    blocked = checkpoint.model_copy(update={"state": "blocked", "reason": "quota"})
    revived = rederive_ensemble_forecast_checkpoint_state(plan, blocked)
    assert revived.state == "validated"
    assert revived.reason == "quota"


def test_a_release_manifest_requires_a_complete_validated_checkpoint() -> None:
    plan = _plan(chunk_cell_count=1)
    checkpoint = initialize_ensemble_forecast_checkpoint(plan, updated_at=RETRIEVED_AT)
    first = parse_ensemble_forecast_payload(plan, plan.chunks[0], _payload(plan, plan.chunks[0]), _capture())
    checkpoint = record_ensemble_forecast_result(plan, checkpoint, first, updated_at=RETRIEVED_AT)

    with pytest.raises(ValueError, match="complete validated checkpoint is required"):
        ensemble_forecast_release_manifest(plan, checkpoint)

    second = parse_ensemble_forecast_payload(plan, plan.chunks[1], _payload(plan, plan.chunks[1]), _capture())
    checkpoint = record_ensemble_forecast_result(plan, checkpoint, second, updated_at=RETRIEVED_AT)
    assert len(ensemble_forecast_release_manifest(plan, checkpoint)) == 64


def test_a_cached_chunk_reparses_to_the_same_receipts_without_the_network(tmp_path: Path) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    result = _parse(plan, _payload(plan, chunk))

    cache_ensemble_forecast_result(tmp_path, plan, result)
    reloaded = load_cached_ensemble_forecast_result(tmp_path, plan, chunk)

    assert reloaded is not None
    assert reloaded.payload_checksum == result.payload_checksum
    assert reloaded.receipts == result.receipts
    assert reloaded.request_base_url == result.request_base_url


def test_the_cache_refuses_to_rebind_a_chunk_to_different_source_content(tmp_path: Path) -> None:
    plan = _plan()
    chunk = plan.chunks[0]
    cache_ensemble_forecast_result(tmp_path, plan, _parse(plan, _payload(plan, chunk)))
    different = _parse(plan, _payload(plan, chunk, member_values={(1, 0): 7.5}))

    with pytest.raises(ValueError, match="already binds this chunk"):
        cache_ensemble_forecast_result(tmp_path, plan, different)


# --- transport failure path -------------------------------------------------------------------
#
# The retry/backoff loop itself lives in execution/open_meteo_lane.py and is covered scope by scope
# in tests/test_open_meteo_lane.py. What these prove is the WIRING: that this lane really routes its
# fetch through that loop, surfaces its own error type, and never swallows an exhausted quota.

FETCH_TARGET = "agri_data_service.execution.ensemble_forecast.fetch_ensemble_hourly"


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
    with pytest.raises(EnsembleForecastFetchError) as raised:
        await fetch_ensemble_forecast_chunk(plan, chunk, client=None, sleep=record_wait)

    assert attempts == 1
    assert waits == []
    assert raised.value.chunk_key == chunk.key
    assert "ensemble forecast chunk" in str(raised.value)


@pytest.mark.asyncio
async def test_a_minutely_quota_is_retried_then_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _plan()
    waits: list[float] = []

    async def refuse(_client: object, _url: str) -> str:
        raise OpenMeteoRateLimitError("minute", "Minutely API request limit exceeded.")

    async def record_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(FETCH_TARGET, refuse)
    with pytest.raises(EnsembleForecastFetchError):
        await fetch_ensemble_forecast_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

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
    with pytest.raises(EnsembleForecastFetchError) as raised:
        await fetch_ensemble_forecast_chunk(plan, plan.chunks[0], client=None, sleep=record_wait)

    assert waits == [15.0, 30.0, 45.0]
    assert raised.value.attempts == 4


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
    result = await fetch_ensemble_forecast_chunk(
        plan, chunk, client=None, retrieved_at=RETRIEVED_AT, sleep=no_wait
    )

    assert calls == 2
    require_accounted_ensemble_forecast_result(plan, result)
    assert result.chunk_key == chunk.key


# --- issue time vs. retrieval -----------------------------------------------------------------
#
# `issue_date` is plan-declared and the provider always answers with its CURRENT model runs, so
# nothing in the payload itself says when the content became available. These pin the three rules
# that keep a staged receipt from claiming to have been issued before its data existed.


def _capture_at(retrieved_at: datetime) -> EnsembleForecastCapture:
    return EnsembleForecastCapture(
        retrieved_at=retrieved_at,
        wire_payload_bytes=1,
        wire_payload_checksum="0" * 64,
    )


def test_a_plan_whose_issue_time_postdates_its_retrieval_is_refused() -> None:
    plan = _plan()
    payload = _payload(plan, plan.chunks[0])

    with pytest.raises(ValueError, match="issue time later than its retrieval"):
        parse_ensemble_forecast_payload(
            plan, plan.chunks[0], payload, _capture_at(plan.issue_time - timedelta(hours=1))
        )


def test_every_staged_receipt_records_when_its_data_became_available() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))

    # The plan-declared issue instant and the retrieval instant are DIFFERENT facts, both carried.
    assert receipt.issue_time == plan.issue_time
    assert receipt.data_available_at == RETRIEVED_AT
    assert receipt.data_available_at > receipt.issue_time


def test_a_horizon_step_whose_hour_already_elapsed_is_flagged_not_silently_staged() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))

    # RETRIEVED_AT is 01:00Z on the issue day, so only step 1 (valid 00:00Z) is already history.
    elapsed = [value.horizon_step for value in receipt.values if value.is_elapsed_at_retrieval]
    assert elapsed == [1]
    assert receipt.quality_summary["elapsed_step_count"] == 1
    assert receipt.values[1].valid_time == RETRIEVED_AT
    assert receipt.values[1].is_elapsed_at_retrieval is False


def test_a_forged_elapsed_flag_is_refused_before_it_can_be_checksummed() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(
        update={
            "values": [
                receipt.values[0].model_copy(update={"is_elapsed_at_retrieval": False}),
                *receipt.values[1:],
            ]
        }
    )

    with pytest.raises(ValueError, match="whether its hour had elapsed at retrieval"):
        staged_forecast_receipt_checksum(tampered)


def test_a_receipt_issued_after_its_data_became_available_is_refused() -> None:
    plan = _plan()
    receipt = _receipt(_parse(plan, _payload(plan, plan.chunks[0])))
    tampered = receipt.model_copy(update={"data_available_at": receipt.issue_time - timedelta(hours=1)})

    with pytest.raises(ValueError, match="issue time later than its data became available"):
        require_consistent_quantile_carriage(tampered)
