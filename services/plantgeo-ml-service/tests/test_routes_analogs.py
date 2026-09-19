"""`GET /api/v1/ml/analogs`: one signal cell's p10/p50/p90 by horizon, from one origin day."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pytest
from serving_harness import MOMENT, body_of, build_serving, mount, request_with, signal_forecast_frame

from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import write_and_publish_forecast_rows, write_run_receipt
from plantgeo_ml_service.pipeline.monte_carlo_daily import ANALOG_ENSEMBLE_ARTIFACT_PREFIX
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.routes import read_analog_ensemble
from plantgeo_ml_service.planes.wire import ARTIFACT_ABSENT_NO_ARTIFACT, CLAIM_TIER
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM

if TYPE_CHECKING:
    from pathlib import Path

    from serving_harness import ServingHarness

ORIGIN: Final = date(2026, 9, 10)
PUBLISHED_HORIZON_DAYS: Final = 4
CELL_ID: Final = "signal-cell-0001"
CELL_LONGITUDE: Final = -120.125
CELL_LATITUDE: Final = 46.375
RUN_ID: Final = "e" * 64
ARTIFACT_SHA: Final = "f" * 64

HTTP_OK: Final = 200


@pytest.fixture
def analog_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServingHarness:
    """A signal lane holding four published horizon days for one cell, issued from ORIGIN."""
    built = build_serving(tmp_path)
    days = tuple(ORIGIN + timedelta(days=step) for step in range(1, PUBLISHED_HORIZON_DAYS + 1))
    frame = signal_forecast_frame(
        issued_on=ORIGIN, days=days, cell_id=CELL_ID, longitude=CELL_LONGITUDE, latitude=CELL_LATITUDE
    )
    receipt = write_run_receipt(built.store, {"lane": SIGNAL_STREAM}, layer=SIGNAL_STREAM, forecast_run_id=RUN_ID)
    write_and_publish_forecast_rows(
        built.store,
        built.pointers,
        frame,
        layer=SIGNAL_STREAM,
        run_id=RUN_ID,
        issued_on=ORIGIN,
        source_receipt=receipt,
        source_ceiling=ORIGIN + timedelta(days=PUBLISHED_HORIZON_DAYS),
        moment=MOMENT,
    )
    mount(built, monkeypatch)
    return built


async def test_one_cell_answers_one_series_with_three_quantiles_per_horizon(analog_lane: ServingHarness) -> None:
    assert analog_lane.objects

    response = await read_analog_ensemble(request_with(cell_id=CELL_ID, origin=ORIGIN.isoformat()))
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["error"] is None
    assert len(body["series"]) == 1
    steps = body["series"][0]["steps"]
    assert [step["horizon_days"] for step in steps] == list(range(1, PUBLISHED_HORIZON_DAYS + 1))
    assert all(step["p10"] is not None and step["p50"] is not None and step["p90"] is not None for step in steps)


async def test_the_answer_names_the_origin_and_the_claim_tier(analog_lane: ServingHarness) -> None:
    assert analog_lane.objects

    body = body_of(await read_analog_ensemble(request_with(cell_id=CELL_ID, origin=ORIGIN.isoformat())))

    assert body["origin"] == ORIGIN.isoformat()
    assert body["issued_on"] == ORIGIN.isoformat()
    assert body["claim_tier"] == CLAIM_TIER


async def test_a_lane_with_no_published_artifact_says_so_rather_than_naming_an_empty_digest(
    analog_lane: ServingHarness,
) -> None:
    assert analog_lane.objects

    body = body_of(await read_analog_ensemble(request_with(cell_id=CELL_ID, origin=ORIGIN.isoformat())))

    assert body["artifact_sha256"] is None
    assert body["artifact_absent_reason"] == ARTIFACT_ABSENT_NO_ARTIFACT


async def test_the_published_artifact_is_what_the_answer_names(analog_lane: ServingHarness) -> None:
    analog_lane.store.put_immutable(
        f"{ANALOG_ENSEMBLE_ARTIFACT_PREFIX}{ARTIFACT_SHA}.json",
        b"{}",
        content_type=JSON_CONTENT_TYPE,
    )

    body = body_of(await read_analog_ensemble(request_with(cell_id=CELL_ID, origin=ORIGIN.isoformat())))

    assert body["artifact_sha256"] == ARTIFACT_SHA
    assert body["artifact_absent_reason"] is None


async def test_a_cell_the_run_never_reached_is_a_typed_refusal(analog_lane: ServingHarness) -> None:
    assert analog_lane.objects

    response = await read_analog_ensemble(request_with(cell_id="signal-cell-9999", origin=ORIGIN.isoformat()))
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["error"]["code"] == refusals.CELL_SERIES_ABSENT


async def test_an_origin_nothing_was_issued_from_refuses_for_the_whole_window(
    analog_lane: ServingHarness,
) -> None:
    assert analog_lane.objects

    body = body_of(await read_analog_ensemble(request_with(cell_id=CELL_ID, origin="2026-08-01")))

    assert body["error"]["code"] == refusals.FORECAST_WINDOW_UNWRITTEN
