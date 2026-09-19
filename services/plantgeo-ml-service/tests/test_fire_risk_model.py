"""What the fire-risk estimator refuses, what it fits, and what its artifact round trips.

Every refusal here answers the same question: a row this model cannot honestly score must carry a
NAMED reason and a null probability, because a fabricated zero reads as "no risk here".
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from plantgeo_ml_service.method.ml.fire_risk_model import (
    ARTIFACT_MISSING,
    BACKTEST_LIFT_NOT_CLEARED,
    FEATURE_MISSING,
    FEATURE_SET_MISMATCH,
    OUT_OF_STRATUM,
    BacktestReference,
    CalibrationBin,
    FireRiskArtifact,
    FireRiskModelError,
    StratumRule,
    artifact_from_mapping,
    fit_calibration_bins,
    precision_recall_auc,
    predict,
    train_fire_risk_model,
)

FEATURE_NAMES = ("vapor_pressure_deficit", "soil_temperature")
FEATURE_SET_VERSION = "fire-risk-features-test"
OPEN_CANOPY = "open_canopy"
CLOSED_FOREST = "closed_forest"

STRATUM_TABLE = (
    StratumRule(stratum=OPEN_CANOPY, scoreable=True, basis="measured discrimination in steppe and transition"),
    StratumRule(stratum=CLOSED_FOREST, scoreable=False, basis="the index does not transfer to closed forest"),
)


def artifact(*, backtest: BacktestReference | None = None) -> FireRiskArtifact:
    """Return a small, fully specified artifact with a monotone two-step calibration."""
    return FireRiskArtifact(
        feature_set_version=FEATURE_SET_VERSION,
        feature_names=FEATURE_NAMES,
        coefficients=(0.8, 1.2),
        intercept=-0.5,
        feature_means=(2.0, 20.0),
        feature_standard_deviations=(0.5, 5.0),
        stratum_table=STRATUM_TABLE,
        calibration_bins=(
            CalibrationBin(upper_bound=0.5, probability=0.1),
            CalibrationBin(upper_bound=1.0, probability=0.8),
        ),
        trained_on_first_day="2024-04-01",
        trained_on_last_day="2025-10-31",
        l2_penalty=1.0,
        training_row_count=400,
        training_positive_count=120,
        backtest=backtest,
    )


def test_a_missing_artifact_refuses_every_row_and_scores_none() -> None:
    """An untrained lane is a typed refusal on every row, never a zero score on any of them."""
    batch = predict(
        None,
        np.array([[2.5, 26.0], [1.1, 12.0]]),
        strata=[OPEN_CANOPY, OPEN_CANOPY],
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
    )

    assert batch.refused_reason == (ARTIFACT_MISSING, ARTIFACT_MISSING)
    assert batch.probability == (None, None)
    assert batch.risk_score == (None, None)
    assert batch.scored_count == 0


def test_a_closed_forest_row_is_refused_out_of_stratum() -> None:
    batch = predict(
        artifact(),
        np.array([[2.5, 26.0], [2.5, 26.0]]),
        strata=[OPEN_CANOPY, CLOSED_FOREST],
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
    )

    assert batch.refused_reason == (None, OUT_OF_STRATUM)
    assert batch.probability[1] is None
    assert batch.risk_score[0] == pytest.approx(batch.probability[0] * 100.0)  # type: ignore[operator]  # scored


def test_a_row_missing_one_feature_is_refused_rather_than_imputed() -> None:
    batch = predict(
        artifact(),
        np.array([[2.5, 26.0], [np.nan, 26.0]]),
        strata=[OPEN_CANOPY, OPEN_CANOPY],
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
    )

    assert batch.refused_reason == (None, FEATURE_MISSING)


def test_a_withheld_stratum_is_refused_with_the_publication_gate_reason() -> None:
    """The FR-5 gate refuses by its own name, so a receipt separates it from an out-of-stratum cell."""
    batch = predict(
        artifact(),
        np.array([[2.5, 26.0]]),
        strata=[OPEN_CANOPY],
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
        withheld_strata=frozenset({OPEN_CANOPY}),
    )

    assert batch.refused_reason == (BACKTEST_LIFT_NOT_CLEARED,)


def test_a_feature_set_version_mismatch_refuses_the_whole_batch() -> None:
    batch = predict(
        artifact(),
        np.array([[2.5, 26.0]]),
        strata=[OPEN_CANOPY],
        feature_names=FEATURE_NAMES,
        feature_set_version="fire-risk-features-v999",
    )

    assert batch.refused_reason == (FEATURE_SET_MISMATCH,)


def test_a_feature_matrix_of_the_wrong_width_raises_rather_than_scoring() -> None:
    with pytest.raises(FireRiskModelError):
        predict(
            artifact(),
            np.array([[2.5]]),
            strata=[OPEN_CANOPY],
            feature_names=FEATURE_NAMES,
            feature_set_version=FEATURE_SET_VERSION,
        )


def test_the_artifact_round_trips_through_canonical_json_with_its_own_digest() -> None:
    reference = BacktestReference(
        key="ml/receipts/fire-risk/backtest.json", sha256="b" * 64, cleared_strata=(OPEN_CANOPY,)
    )
    original = artifact(backtest=reference)

    decoded = artifact_from_mapping(json.loads(original.to_canonical_json()))

    assert decoded == original
    assert decoded.sha256 == original.sha256
    assert decoded.scoreable_strata == frozenset({OPEN_CANOPY})


def test_an_artifact_document_whose_digest_disagrees_with_its_body_is_refused() -> None:
    document = json.loads(artifact().to_canonical_json())
    document["intercept"] = document["intercept"] + 1.0

    with pytest.raises(FireRiskModelError):
        artifact_from_mapping(document)


def test_an_artifact_written_under_another_schema_version_is_refused() -> None:
    document = json.loads(artifact().to_canonical_json())
    document["schema_version"] = 99

    with pytest.raises(FireRiskModelError):
        artifact_from_mapping(document)


def test_training_ranks_a_separable_set_the_right_way_round() -> None:
    """A fit that ranked burned cells below unburned ones would still produce probabilities."""
    class_size = 40
    temperatures = np.concatenate([np.linspace(5.0, 15.0, class_size), np.linspace(25.0, 35.0, class_size)])
    design = np.column_stack([np.full(class_size * 2, 2.0), temperatures])
    labels = np.concatenate([np.zeros(class_size), np.ones(class_size)])

    fitted = train_fire_risk_model(
        design,
        labels,
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
        stratum_table=STRATUM_TABLE,
        trained_on=("2024-04-01", "2024-10-31"),
    )
    batch = predict(
        fitted,
        np.array([[2.0, 33.0], [2.0, 7.0]]),
        strata=[OPEN_CANOPY, OPEN_CANOPY],
        feature_names=FEATURE_NAMES,
        feature_set_version=FEATURE_SET_VERSION,
    )

    assert fitted.training_positive_count == class_size
    assert batch.probability[0] >= batch.probability[1]  # type: ignore[operator]  # both rows scored


def test_a_single_class_training_set_is_refused() -> None:
    with pytest.raises(FireRiskModelError):
        train_fire_risk_model(
            np.array([[2.0, 20.0], [2.1, 21.0]]),
            np.array([0.0, 0.0]),
            feature_names=FEATURE_NAMES,
            feature_set_version=FEATURE_SET_VERSION,
            stratum_table=STRATUM_TABLE,
            trained_on=("2024-04-01", "2024-10-31"),
        )


def test_a_non_finite_training_value_is_refused_rather_than_zero_filled() -> None:
    with pytest.raises(FireRiskModelError):
        train_fire_risk_model(
            np.array([[2.0, np.nan], [2.1, 21.0]]),
            np.array([0.0, 1.0]),
            feature_names=FEATURE_NAMES,
            feature_set_version=FEATURE_SET_VERSION,
            stratum_table=STRATUM_TABLE,
            trained_on=("2024-04-01", "2024-10-31"),
        )


def test_calibration_bins_are_monotone_after_pooling_violators() -> None:
    raw = np.array([0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95])
    labels = np.array([0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 1.0, 1.0])

    bins = fit_calibration_bins(raw, labels, bin_count=5)

    probabilities = [step.probability for step in bins]
    assert probabilities == sorted(probabilities)
    assert bins[-1].upper_bound == 1.0


def test_a_perfect_ranking_scores_a_precision_recall_area_of_one() -> None:
    area = precision_recall_auc(np.array([0.9, 0.8, 0.2, 0.1]), np.array([1.0, 1.0, 0.0, 0.0]))

    assert area == pytest.approx(1.0)


def test_a_single_class_label_vector_has_no_precision_recall_area() -> None:
    with pytest.raises(FireRiskModelError):
        precision_recall_auc(np.array([0.9, 0.1]), np.array([1.0, 1.0]))
