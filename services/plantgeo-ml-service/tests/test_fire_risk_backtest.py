"""The walk-forward gate: which strata clear the declared lift over BOTH baselines, and which cannot.

The plane is synthetic and deterministic by construction, not random: one stratum is separable by a
feature the VPD-only screen does not carry, and the other is predictable ONLY by the cell's own
climatology, which is exactly the shape that must never clear a publication gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from plantgeo_ml_service.pipeline.fire_risk_backtest import (
    LABEL_COLUMN,
    MINIMUM_PR_AUC_LIFT,
    BacktestReceipt,
    FireRiskBacktestError,
    StratumVerdict,
    run_walk_forward_backtest,
)
from plantgeo_ml_service.pipeline.fire_risk_features import (
    FIRE_RISK_FEATURE_NAMES,
    cyclical_day_of_year,
    photoperiod_seconds,
)

GENERATED_AT = datetime(2026, 9, 19, 0, 0, 0, tzinfo=UTC)
SEPARABLE_STRATUM = "open_canopy"
CLIMATOLOGY_ONLY_STRATUM = "closed_forest"

YEARS = (2023, 2024, 2025)
DAYS_PER_YEAR = 60
CELL_COUNT = 5
#: The first year trains nothing, so a walk-forward split over N years yields N-1 held-out folds.
HELD_OUT_FOLD_COUNT = len(YEARS) - 1
SEASON_START_MONTH = 6

CONSTANT_FEATURES = {
    "vapor_pressure_deficit": 2.0,
    "air_temperature_mean": 25.0,
    "relative_humidity": 30.0,
    "wind_speed": 3.0,
    "precipitation": 0.0,
    "surface_soil_water_content": 0.15,
    "root_zone_soil_water_content": 0.2,
    "normalized_difference_vegetation_index": 0.3,
    "detection_count_history": 1.0,
    "radiative_power_history": 10.0,
    "high_confidence_detection_history": 1.0,
    "prior_burn_envelope_overlap": 0.0,
    "regional_drought_category": 2.0,
}


def synthetic_plane() -> pl.DataFrame:
    """Return three seasons of two strata: one separable by soil temperature, one only by cell."""
    rows: list[dict[str, object]] = []
    for year in YEARS:
        for day_index in range(DAYS_PER_YEAR):
            valid_day = date(year, SEASON_START_MONTH, 1) + timedelta(days=day_index)
            for cell_index in range(CELL_COUNT):
                rows.append(_separable_row(valid_day, day_index, cell_index))
                rows.append(_climatology_row(valid_day, cell_index))
    return pl.DataFrame(rows)


def test_the_separable_stratum_clears_both_declared_minima() -> None:
    receipt = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT)

    assert receipt.cleared_strata == (SEPARABLE_STRATUM,)
    verdict = _verdict(receipt, SEPARABLE_STRATUM)
    assert verdict.lift_over_vapor_pressure_deficit >= MINIMUM_PR_AUC_LIFT
    assert verdict.lift_over_climatology >= MINIMUM_PR_AUC_LIFT
    assert verdict.brier_excess_over_best_baseline <= 0.0


def test_a_stratum_its_own_climatology_predicts_better_does_not_clear() -> None:
    """Beating a single-variable screen is not enough: the cell's seasonal rate is the other baseline."""
    receipt = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT)

    verdict = _verdict(receipt, CLIMATOLOGY_ONLY_STRATUM)
    assert verdict.cleared is False
    assert verdict.lift_over_climatology < 0.0
    assert CLIMATOLOGY_ONLY_STRATUM not in receipt.cleared_strata


def test_the_walk_holds_out_one_year_at_a_time() -> None:
    """The first year trains nothing, so three seasons give two held-out folds per stratum."""
    receipt = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT)

    held_out = sorted({fold.held_out_year for fold in receipt.folds})
    assert held_out == [YEARS[1], YEARS[2]]
    assert _verdict(receipt, SEPARABLE_STRATUM).fold_count == HELD_OUT_FOLD_COUNT


def test_the_receipt_is_byte_identical_for_the_same_plane_and_instant() -> None:
    """The receipt an artifact names must be reproducible, or the artifact names a moving target."""
    first = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT)
    second = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT)

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.sha256 == second.sha256


def test_the_receipt_states_the_declared_minima_it_judged_against() -> None:
    document = run_walk_forward_backtest(synthetic_plane(), generated_at=GENERATED_AT).to_wire()

    assert document["declared_minimum_precision_recall_auc_lift"] == MINIMUM_PR_AUC_LIFT
    assert document["declared_maximum_brier_excess"] == 0.0


def test_a_plane_missing_its_label_column_is_refused_by_name() -> None:
    plane = synthetic_plane().drop(LABEL_COLUMN)

    with pytest.raises(FireRiskBacktestError, match=LABEL_COLUMN):
        run_walk_forward_backtest(plane, generated_at=GENERATED_AT)


def test_an_empty_plane_is_a_refusal_not_a_zero_skill_verdict() -> None:
    with pytest.raises(FireRiskBacktestError):
        run_walk_forward_backtest(synthetic_plane().head(0), generated_at=GENERATED_AT)


def _separable_row(valid_day: date, day_index: int, cell_index: int) -> dict[str, object]:
    """One row whose outcome follows soil temperature, a feature the VPD-only baseline never sees."""
    burned = day_index % 2 == 0
    row = _base_row(valid_day, cell_index, stratum=SEPARABLE_STRATUM, burned=burned)
    row["soil_temperature"] = 30.0 if burned else 10.0
    return row


def _climatology_row(valid_day: date, cell_index: int) -> dict[str, object]:
    """One row whose outcome follows the CELL alone, so only a climatology baseline can predict it."""
    row = _base_row(valid_day, cell_index, stratum=CLIMATOLOGY_ONLY_STRATUM, burned=cell_index % 2 == 0)
    row["soil_temperature"] = 20.0
    return row


def _base_row(valid_day: date, cell_index: int, *, stratum: str, burned: bool) -> dict[str, object]:
    """Return the constant columns every synthetic row carries, plus its real seasonal features."""
    latitude = 46.0 + cell_index * 0.25
    sine, cosine = cyclical_day_of_year(valid_day)
    row: dict[str, object] = {
        "cell_longitude": -120.0 - cell_index * 0.25,
        "cell_latitude": latitude,
        "valid_day": valid_day,
        "stratum": stratum,
        LABEL_COLUMN: 1.0 if burned else 0.0,
        "day_of_year_sine": sine,
        "day_of_year_cosine": cosine,
        "photoperiod_seconds": photoperiod_seconds(latitude, valid_day),
    }
    row.update(CONSTANT_FEATURES)
    return row


def _verdict(receipt: BacktestReceipt, stratum: str) -> StratumVerdict:
    """Return one stratum's verdict out of a receipt, by name."""
    return next(candidate for candidate in receipt.verdicts if candidate.stratum == stratum)


def test_every_named_feature_is_present_in_the_synthetic_plane() -> None:
    """The plane must carry the whole feature set, or the walk is judging a different model."""
    assert set(FIRE_RISK_FEATURE_NAMES) <= set(synthetic_plane().columns)
