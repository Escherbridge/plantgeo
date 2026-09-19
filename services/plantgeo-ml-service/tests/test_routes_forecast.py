"""`GET /api/v1/ml/forecast-summary` on both serving paths, and the horizon it MEASURES.

The two paths exist because two lanes mean two different things by "tomorrow": a `kind=forecast`
lane partitions its future by valid day, and `weather-forecast` carries its future inside one
`kind=observed` issue file as `valid_time` rows (`layer-lanes.md` section 2, amended 2026-09-19).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import pytest
from serving_harness import MOMENT, body_of, build_serving, mount, request_with, signal_forecast_frame
from test_weather_forecast_daily import ISSUE_DATE, NEIGHBOURING_CELLS, PUBLISHED_AT, ReplayForecastSource

from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import write_and_publish_forecast_rows, write_run_receipt
from plantgeo_ml_service.pipeline.weather_forecast_daily import run_weather_forecast_daily
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.forecast_reads import ARTIFACT_COLUMN, _claim_for
from plantgeo_ml_service.planes.routes import read_forecast
from plantgeo_ml_service.planes.wire import (
    ARTIFACT_ABSENT_NOT_MODEL_BACKED,
    ARTIFACT_ABSENT_PROVENANCE_CONFLICTED,
    CLAIM_TIER,
    SERVING_PATH_FORECAST_KIND,
    SERVING_PATH_RELEASE_SERIES,
)
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM
from plantgeo_ml_service.warehouse.weather_forecast import WEATHER_FORECAST_STREAM

if TYPE_CHECKING:
    from pathlib import Path

    from serving_harness import ServingHarness

ISSUE_DAY: Final = date(2026, 9, 10)

#: The horizon the lane DECLARES, which is what `source_ceiling - issue_day` would report.
DECLARED_HORIZON_DAYS: Final = 30

#: The horizon this run actually PUBLISHED. The gap between the two is the whole point of the test.
PUBLISHED_HORIZON_DAYS: Final = 12

CELL_ID: Final = "signal-cell-0001"
CELL_LONGITUDE: Final = -120.125
CELL_LATITUDE: Final = 46.375
RUN_ID: Final = "d" * 64
QUANTILE_COUNT: Final = 3

WEATHER_LONGITUDE: Final = NEIGHBOURING_CELLS[0].longitude
WEATHER_LATITUDE: Final = NEIGHBOURING_CELLS[0].latitude
WEATHER_VALID_DAY: Final = ISSUE_DATE + timedelta(days=1)

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400


@pytest.fixture
def signal_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServingHarness:
    """A signal lane that published 12 of its 30 declared horizon days, absenting the other 18."""
    built = build_serving(tmp_path)
    published_days = tuple(ISSUE_DAY + timedelta(days=step) for step in range(1, PUBLISHED_HORIZON_DAYS + 1))
    frame = signal_forecast_frame(
        issued_on=ISSUE_DAY,
        days=published_days,
        cell_id=CELL_ID,
        longitude=CELL_LONGITUDE,
        latitude=CELL_LATITUDE,
    )
    receipt = write_run_receipt(built.store, {"lane": SIGNAL_STREAM}, layer=SIGNAL_STREAM, forecast_run_id=RUN_ID)
    write_and_publish_forecast_rows(
        built.store,
        built.pointers,
        frame,
        layer=SIGNAL_STREAM,
        run_id=RUN_ID,
        issued_on=ISSUE_DAY,
        source_receipt=receipt,
        source_ceiling=ISSUE_DAY + timedelta(days=DECLARED_HORIZON_DAYS),
        moment=MOMENT,
    )
    mount(built, monkeypatch)
    return built


@pytest.fixture
def weather_lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServingHarness:
    """A weather-forecast lane holding one published provider issue, replayed from the probe."""
    built = build_serving(tmp_path)
    run_weather_forecast_daily(
        built.store,
        ISSUE_DATE,
        NEIGHBOURING_CELLS,
        source=ReplayForecastSource(),
        pointers=built.pointers,
        forecast_days=2,
        now=PUBLISHED_AT,
    )
    mount(built, monkeypatch)
    return built


async def test_a_forecast_kind_lane_answers_its_rows_and_names_its_path(signal_lane: ServingHarness) -> None:
    assert signal_lane.objects

    response = await read_forecast(
        request_with(
            layer=SIGNAL_STREAM,
            lon=str(CELL_LONGITUDE),
            lat=str(CELL_LATITUDE),
            day=(ISSUE_DAY + timedelta(days=3)).isoformat(),
        )
    )
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["error"] is None
    assert body["serving_path"] == SERVING_PATH_FORECAST_KIND
    assert len(body["rows"]) == QUANTILE_COUNT
    assert body["claim_tier"] == CLAIM_TIER


async def test_a_partly_published_run_reports_the_horizon_it_reached_not_the_one_it_declared(
    signal_lane: ServingHarness,
) -> None:
    """The measured horizon is the whole reason this field exists: `source_ceiling - issue_day` is
    30 every single day, so it can never report a run that fell short."""
    assert signal_lane.objects

    body = body_of(
        await read_forecast(
            request_with(
                layer=SIGNAL_STREAM,
                lon=str(CELL_LONGITUDE),
                lat=str(CELL_LATITUDE),
                day=(ISSUE_DAY + timedelta(days=3)).isoformat(),
            )
        )
    )

    assert body["published_horizon_days"] == PUBLISHED_HORIZON_DAYS
    assert body["issue_day"] == ISSUE_DAY.isoformat()
    assert body["source_ceiling"] == (ISSUE_DAY + timedelta(days=DECLARED_HORIZON_DAYS)).isoformat()
    assert body["published_horizon_days"] < DECLARED_HORIZON_DAYS


async def test_a_day_past_the_published_generation_is_a_typed_refusal(signal_lane: ServingHarness) -> None:
    """The partitions for day 20 exist as governed absences; the answer is still a refusal."""
    assert signal_lane.objects

    body = body_of(
        await read_forecast(
            request_with(
                layer=SIGNAL_STREAM,
                lon=str(CELL_LONGITUDE),
                lat=str(CELL_LATITUDE),
                day=(ISSUE_DAY + timedelta(days=DECLARED_HORIZON_DAYS + 5)).isoformat(),
            )
        )
    )

    assert body["error"]["code"] == refusals.AVAILABILITY_DAY_NOT_COVERED


async def test_a_release_series_lane_answers_from_its_issue_file_and_names_its_generation(
    weather_lane: ServingHarness,
) -> None:
    """The absence of a `kind=forecast` partition is normal for this lane, not a refusal."""
    assert weather_lane.objects

    response = await read_forecast(
        request_with(
            layer=WEATHER_FORECAST_STREAM,
            lon=str(WEATHER_LONGITUDE),
            lat=str(WEATHER_LATITUDE),
            day=WEATHER_VALID_DAY.isoformat(),
        )
    )
    body = body_of(response)

    assert response.status == HTTP_OK
    assert body["error"] is None
    assert body["serving_path"] == SERVING_PATH_RELEASE_SERIES
    assert body["generation_key"].startswith(f"layer={WEATHER_FORECAST_STREAM}/kind=observed/availability/")
    assert body["rows"]


async def test_a_release_series_answer_states_its_issue_day_horizon_and_ceiling(
    weather_lane: ServingHarness,
) -> None:
    assert weather_lane.objects

    body = body_of(
        await read_forecast(
            request_with(
                layer=WEATHER_FORECAST_STREAM,
                lon=str(WEATHER_LONGITUDE),
                lat=str(WEATHER_LATITUDE),
                day=WEATHER_VALID_DAY.isoformat(),
            )
        )
    )

    assert body["issue_day"] == ISSUE_DATE.isoformat()
    assert body["published_horizon_days"] == (WEATHER_VALID_DAY - ISSUE_DATE).days
    assert body["source_ceiling"] is not None
    assert body["valid_day"] == WEATHER_VALID_DAY.isoformat()


async def test_every_row_of_a_release_series_answer_falls_on_the_requested_utc_day(
    weather_lane: ServingHarness,
) -> None:
    """The named-day rule: the filter converts to UTC explicitly, never through a session zone."""
    assert weather_lane.objects

    body = body_of(
        await read_forecast(
            request_with(
                layer=WEATHER_FORECAST_STREAM,
                lon=str(WEATHER_LONGITUDE),
                lat=str(WEATHER_LATITUDE),
                day=WEATHER_VALID_DAY.isoformat(),
            )
        )
    )

    assert {str(row["valid_time"])[:10] for row in body["rows"]} == {WEATHER_VALID_DAY.isoformat()}


def test_two_artifacts_in_one_cell_are_a_provenance_conflict_not_an_unmodelled_lane() -> None:
    """M7: the lane plainly IS model-backed; "we cannot tell you which model" is its own fault."""
    rows = (
        {ARTIFACT_COLUMN: "a" * 64, "issued_on": ISSUE_DAY},
        {ARTIFACT_COLUMN: "b" * 64, "issued_on": ISSUE_DAY},
    )

    claim = _claim_for(rows, issued_on=ISSUE_DAY)

    assert claim.artifact_sha256 is None
    assert claim.artifact_absent_reason == ARTIFACT_ABSENT_PROVENANCE_CONFLICTED
    assert claim.artifact_absent_reason != ARTIFACT_ABSENT_NOT_MODEL_BACKED


def test_rows_with_no_artifact_column_at_all_still_read_as_an_unmodelled_lane() -> None:
    claim = _claim_for(({"issued_on": ISSUE_DAY},), issued_on=ISSUE_DAY)

    assert claim.artifact_absent_reason == ARTIFACT_ABSENT_NOT_MODEL_BACKED


async def test_a_lane_this_service_pins_no_schema_for_is_rejected(signal_lane: ServingHarness) -> None:
    assert signal_lane.objects

    response = await read_forecast(request_with(layer="not-a-lane", lon="-120.0", lat="46.0", day="2026-09-13"))

    assert response.status == HTTP_BAD_REQUEST
    assert body_of(response)["error"]["code"] == refusals.LANE_UNKNOWN
