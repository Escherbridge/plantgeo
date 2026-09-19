"""`GET /api/v1/ml/fire-risk`: one scored cell, one refused cell, and the days that state nothing."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

import polars as pl
import pyarrow as pa
import pytest
from serving_harness import MOMENT, body_of, build_serving, mount, publish_lane, request_with

from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import write_forecast_day
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.routes import read_fire_risk
from plantgeo_ml_service.planes.wire import CLAIM_TIER, EVALUATION_DISCLAIMER, OUTCOME_ABSENT, OUTCOME_CONTENT
from plantgeo_ml_service.warehouse.streams import FIRE_RISK_STREAM, POINT_QUANTILE, stream_schema

if TYPE_CHECKING:
    from pathlib import Path

    from serving_harness import ServingHarness

VALID_DAY: Final = date(2026, 9, 20)
ISSUED_ON: Final = date(2026, 9, 10)
ARTIFACT_SHA: Final = "b" * 64
RUN_ID: Final = "c" * 64

SCORED_LONGITUDE: Final = -120.125
SCORED_LATITUDE: Final = 46.375
REFUSED_LONGITUDE: Final = -120.135

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400


def _frame() -> pl.DataFrame:
    """Two cells for one valid day: one scored, one refused for being out of stratum."""
    schema = stream_schema(FIRE_RISK_STREAM, "forecast")
    rows = [
        {
            "cell_longitude": SCORED_LONGITUDE,
            "cell_latitude": SCORED_LATITUDE,
            "valid_day": VALID_DAY,
            "probability": 0.25,
            "risk_score": 2.5,
            "stratum": "shrubland",
            "refused_reason": None,
            "model_artifact_sha256": ARTIFACT_SHA,
            "forecast_run_id": RUN_ID,
            "random_seed": 0,
            "ensemble_size": 1,
            "horizon_days": 10,
            "issued_on": ISSUED_ON,
            "quantile": POINT_QUANTILE,
        },
        {
            "cell_longitude": REFUSED_LONGITUDE,
            "cell_latitude": SCORED_LATITUDE,
            "valid_day": VALID_DAY,
            "probability": None,
            "risk_score": None,
            "stratum": "water",
            "refused_reason": "out_of_stratum",
            "model_artifact_sha256": ARTIFACT_SHA,
            "forecast_run_id": RUN_ID,
            "random_seed": 0,
            "ensemble_size": 1,
            "horizon_days": 10,
            "issued_on": ISSUED_ON,
            "quantile": POINT_QUANTILE,
        },
    ]
    return pl.from_arrow(pa.Table.from_pylist(rows, schema=schema.arrow_schema))


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServingHarness:
    """A bucket holding one completed fire-risk day, PUBLISHED, with the blueprint pointed at it."""
    built = build_serving(tmp_path)
    written = write_forecast_day(
        built.store, _frame(), layer=FIRE_RISK_STREAM, day=VALID_DAY, run_id=RUN_ID, completed_at=MOMENT
    )
    publish_lane(built, written, layer=FIRE_RISK_STREAM, source_ceiling=VALID_DAY)
    mount(built, monkeypatch)
    return built


async def test_a_scored_cell_answers_with_its_probability_and_its_artifact(harness: ServingHarness) -> None:
    assert harness.objects

    response = await read_fire_risk(
        request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat())
    )
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["error"] is None
    assert body["outcome"] == OUTCOME_CONTENT
    assert body["probability"] == pytest.approx(0.25)
    assert body["stratum"] == "shrubland"
    assert body["refused_reason"] is None


async def test_every_answer_carries_the_artifact_the_issue_day_and_the_claim_tier(harness: ServingHarness) -> None:
    """FR-8: a fire-risk number with no artifact and no tier beside it is an unattributable claim."""
    assert harness.objects

    body = body_of(
        await read_fire_risk(
            request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat())
        )
    )

    assert body["artifact_sha256"] == ARTIFACT_SHA
    assert body["artifact_absent_reason"] is None
    assert body["issued_on"] == ISSUED_ON.isoformat()
    assert body["claim_tier"] == CLAIM_TIER == "evaluation_only"
    assert body["disclaimer"] == EVALUATION_DISCLAIMER


async def test_a_refused_cell_answers_its_reason_and_never_a_zero(harness: ServingHarness) -> None:
    """A fabricated zero would read as "no risk here", which is the claim the FR-5 gate prevents."""
    assert harness.objects

    body = body_of(
        await read_fire_risk(
            request_with(lon=str(REFUSED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat())
        )
    )

    assert body["probability"] is None
    assert body["risk_score"] is None
    assert body["refused_reason"] == "out_of_stratum"


async def test_a_day_outside_the_published_generation_is_a_typed_refusal_rather_than_an_empty_answer(
    harness: ServingHarness,
) -> None:
    """FR-4a: the pointer decides, and it is consulted BEFORE the day's prefix is listed."""
    assert harness.objects

    response = await read_fire_risk(request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day="2026-09-21"))
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["outcome"] == OUTCOME_ABSENT
    assert body["error"]["code"] == refusals.AVAILABILITY_DAY_NOT_COVERED
    assert body["claim_tier"] == CLAIM_TIER


async def test_an_unpublished_lane_refuses_every_day_even_where_partitions_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing a partition does not publish it: a lane with no pointer selects no day at all."""
    built = build_serving(tmp_path)
    write_forecast_day(built.store, _frame(), layer=FIRE_RISK_STREAM, day=VALID_DAY, run_id=RUN_ID, completed_at=MOMENT)
    mount(built, monkeypatch)

    body = body_of(
        await read_fire_risk(
            request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat())
        )
    )

    assert body["error"]["code"] == refusals.AVAILABILITY_UNPUBLISHED


async def test_the_answer_wears_the_point_label_rather_than_an_ensemble_fraction(
    harness: ServingHarness,
) -> None:
    """`layer-lanes.md` section 3: a median of one draw encoded as 0.5 reads as an ensemble."""
    assert harness.objects

    body = body_of(
        await read_fire_risk(
            request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat())
        )
    )

    assert body["quantile"] == POINT_QUANTILE
    assert body["ensemble_size"] == 1


async def test_a_point_outside_the_lane_is_refused_rather_than_answered_from_the_nearest_cell(
    harness: ServingHarness,
) -> None:
    assert harness.objects

    body = body_of(await read_fire_risk(request_with(lon="-100.0", lat="20.0", day=VALID_DAY.isoformat())))

    assert body["error"]["code"] == refusals.CELL_NOT_COVERED


async def test_a_day_carrying_a_time_is_rejected_by_field_name(harness: ServingHarness) -> None:
    """The named-day rule: an instant is refused, never truncated onto the neighbouring day."""
    assert harness.objects

    response = await read_fire_risk(
        request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day="2026-09-20T00:00:00Z")
    )
    body = body_of(response)

    assert response.status == HTTP_BAD_REQUEST
    assert body["error"]["code"] == refusals.INVALID_REQUEST
    assert [entry["field"] for entry in body["error"]["fields"]] == ["day"]


async def test_a_non_finite_latitude_is_rejected_before_any_bound_check(harness: ServingHarness) -> None:
    """`nan` compares false against every bound, so a range check alone would let it through."""
    assert harness.objects

    response = await read_fire_risk(request_with(lon=str(SCORED_LONGITUDE), lat="nan", day=VALID_DAY.isoformat()))
    body = body_of(response)

    assert response.status == HTTP_BAD_REQUEST
    assert [entry["field"] for entry in body["error"]["fields"]] == ["lat"]


async def test_an_unknown_query_parameter_is_rejected_rather_than_ignored(harness: ServingHarness) -> None:
    assert harness.objects

    response = await read_fire_risk(
        request_with(lon=str(SCORED_LONGITUDE), lat=str(SCORED_LATITUDE), day=VALID_DAY.isoformat(), zoom="9")
    )

    assert response.status == HTTP_BAD_REQUEST
    assert "zoom" in {entry["field"] for entry in body_of(response)["error"]["fields"]}
