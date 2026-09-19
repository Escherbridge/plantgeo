"""One daily turn over a synthetic observed day: every lane reported, and no lane stopping another."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

import pytest
from test_forecast_lane_bootstrap import MOMENT, build_harness
from test_monte_carlo_daily import FIRE_OPTIONS, ISSUED_ON, write_observed_fire_history

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.pipeline.predict_daily import (
    FORECAST_CELLS_UNCONFIGURED,
    NO_FIRE_RISK_ARTIFACT,
    PredictDailyReceipt,
    every_lane,
    run_predict_daily,
)
from plantgeo_ml_service.warehouse.streams import FIRE_DETECTIONS_STREAM, FIRE_RISK_STREAM, VEGETATION_STREAM
from plantgeo_ml_service.warehouse.weather_forecast import WEATHER_FORECAST_STREAM

if TYPE_CHECKING:
    from pathlib import Path

    from test_forecast_lane_bootstrap import ForecastHarness

SCRATCH_PREFIX: Final = "ml/scratch/2026-09-19/"


def _turn(harness: ForecastHarness, **overrides: object) -> PredictDailyReceipt:
    """Run one turn over the seeded bucket, defaulting to the whole lane list."""
    return run_predict_daily(
        harness.store,
        issued_on=ISSUED_ON,
        reader=harness.reader,
        pointers=harness.pointers,
        monte_carlo_options=FIRE_OPTIONS,
        completed_at=MOMENT,
        **overrides,  # type: ignore[arg-type]  # each override is one named keyword of the same call
    )


@pytest.fixture
def seeded(tmp_path: Path) -> ForecastHarness:
    """A bucket holding sixty governed observed days of `fire-detections` and nothing else."""
    harness = build_harness(tmp_path)
    write_observed_fire_history(harness)
    return harness


def test_the_receipt_names_every_lane_the_turn_runs(seeded: ForecastHarness) -> None:
    """A lane missing from the receipt is indistinguishable from a lane nobody attempted."""
    receipt = _turn(seeded)

    assert tuple(outcome.lane for outcome in receipt.outcomes) == every_lane()


def test_one_lane_refusing_does_not_stop_the_lanes_after_it(seeded: ForecastHarness) -> None:
    """`vegetation` has no observed history here and runs between two lanes that must still run."""
    receipt = _turn(seeded)

    by_lane = {outcome.lane: outcome for outcome in receipt.outcomes}
    assert by_lane[FIRE_DETECTIONS_STREAM].status == "written"
    assert by_lane[VEGETATION_STREAM].status == "refused"
    # The last lane still REACHED its own step and reported its own typed reason; this harness
    # configures no cell inventory, so that reason is `forecast_cells_unconfigured`.
    assert by_lane[WEATHER_FORECAST_STREAM].detail == FORECAST_CELLS_UNCONFIGURED


def test_a_lane_with_no_artifact_is_skipped_by_name_rather_than_scored(seeded: ForecastHarness) -> None:
    """FR-5: a run with no artifact can only refuse every row, so the turn does not attempt one."""
    receipt = _turn(seeded)

    fire_risk = next(outcome for outcome in receipt.outcomes if outcome.lane == FIRE_RISK_STREAM)
    assert fire_risk.status == "skipped"
    assert fire_risk.detail == NO_FIRE_RISK_ARTIFACT


def test_a_deployment_with_no_cell_inventory_refuses_the_weather_lane_and_never_calls_it_skipped(
    seeded: ForecastHarness,
) -> None:
    """A lane nobody configured will never run; "skipped" reads like a decision somebody made."""
    receipt = _turn(seeded)

    weather = next(outcome for outcome in receipt.outcomes if outcome.lane == WEATHER_FORECAST_STREAM)
    assert weather.detail == FORECAST_CELLS_UNCONFIGURED
    assert weather.status == "refused"


def test_the_turn_writes_exactly_one_receipt_whose_digest_covers_its_own_document(
    seeded: ForecastHarness,
) -> None:
    receipt = _turn(seeded)

    stored = seeded.store.read_object(receipt.relative_path)
    assert stored is not None
    document = json.loads(stored.decode("utf-8"))
    assert document.pop("sha256") == receipt.sha256
    assert sha256_digest(canonical_json(document)) == receipt.sha256


def test_a_turn_where_every_lane_refused_still_produces_a_receipt(seeded: ForecastHarness) -> None:
    """A bounded turn is a completed turn (owner 2026-09-04); the CLI exits 0 on this receipt."""
    receipt = _turn(seeded, lanes=[VEGETATION_STREAM])

    assert receipt.every_lane_refused
    assert seeded.store.read_object(receipt.relative_path) is not None


def test_a_dry_run_writes_under_the_scratch_root_and_never_the_published_lane(
    seeded: ForecastHarness,
) -> None:
    receipt = _turn(seeded, dry_run_prefix=SCRATCH_PREFIX)

    assert receipt.scratch_run
    assert receipt.prefix == SCRATCH_PREFIX
    written = {key for key in seeded.objects if key.startswith(f"layer={FIRE_DETECTIONS_STREAM}/kind=forecast")}
    assert not written
    assert any(key.startswith(SCRATCH_PREFIX) for key in seeded.objects)


def test_a_lane_slug_no_lane_answers_to_is_refused_rather_than_silently_skipped(
    seeded: ForecastHarness,
) -> None:
    with pytest.raises(ValueError, match="no lane named"):
        _turn(seeded, lanes=["not-a-lane"])
