"""The recommendation models really fit, really cross-validate, and never fabricate an input."""

from __future__ import annotations

import json

import numpy as np
import pytest

from agri_data_service.method.ml.recommendation_models import (
    CLAIM_TIER,
    OUTCOME_UTILITY,
    SITE_CLIMATE_FEATURES,
    SITE_COVARIATE_FEATURES,
    RecommendationTrainingError,
    artifact_from_document,
    assemble_training_matrix,
    build_artifact,
    build_design_row,
    class_probabilities,
    design_feature_names,
    evaluate_leave_one_source_out,
    expected_utility,
    fit_logistic,
    fit_standardization,
    rank_subjects,
)

_SITE_CLIMATE = {
    "mean_annual_precipitation_mm": 300.0,
    "mean_annual_temperature_c": 11.0,
    "growing_season_frost_free_days": 160.0,
    "aridity_index": 0.31,
    "aridity": "semi_arid",
}
_SITE_COVARIATES = dict.fromkeys(SITE_COVARIATE_FEATURES, 1.0)


def _instance(  # noqa: PLR0913
    *,
    subject: str,
    outcome: str,
    doi: str,
    precipitation: float,
    day: str = "2025-06-01",
    slice_name: str = "species-trees-shrubs",
) -> dict[str, object]:
    return {
        "instance_id": f"{subject}-{day}",
        "observed_date": day,
        "feature_values": [
            {"feature_index": index + 1, "feature_name": name, "feature_value": 1.0 + index * 0.1}
            for index, name in enumerate(SITE_COVARIATE_FEATURES)
        ],
        "feature_checksum": "a" * 64,
        "envelope_match": {"site_climate": {**_SITE_CLIMATE, "observed_date": day}},
        "unexpressible_terms": [],
        "label_id": subject,
        "label_key": f"{subject}-label",
        "subject": subject,
        "subject_normalized": subject.lower(),
        "outcome": outcome,
        "condition_envelope": {
            "mean_annual_precipitation_mm": {"min": precipitation, "max": precipitation},
            "aridity": "semi_arid",
        },
        "confidence": "medium",
        "confidence_weight": 0.6,
        "harvest_slice": slice_name,
        "review_state": "agent_reviewed",
        "label_checksum": "b" * 64,
        "doi": doi,
        "title": f"A study of {subject}",
        "publication_year": 2022,
        "journal_or_publisher": "A journal",
        "source_key": f"doi:{doi}",
    }


def _matrix_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, (subject, outcome, precipitation) in enumerate(
        [
            ("alpha", "fit", 300.0),
            ("beta", "fit", 310.0),
            ("gamma", "marginal", 500.0),
            ("delta", "marginal", 520.0),
            ("epsilon", "unfit", 900.0),
            ("zeta", "unfit", 950.0),
            ("eta", "fit", 290.0),
            ("theta", "unfit", 1000.0),
        ]
    ):
        for day in ("2025-06-01", "2025-06-15", "2025-07-01"):
            rows.append(  # noqa: PERF401
                _instance(
                    subject=subject, outcome=outcome, doi=f"10.0000/{index}", precipitation=precipitation, day=day
                )
            )
    return rows


def test_a_missing_governed_value_yields_no_row_rather_than_a_default() -> None:
    incomplete = dict(_SITE_COVARIATES)
    incomplete[SITE_COVARIATE_FEATURES[0]] = None  # type: ignore[assignment]

    row = build_design_row(
        site_climate=_SITE_CLIMATE,
        site_covariates=incomplete,
        envelope={"mean_annual_precipitation_mm": 300},
        subject_normalized="alpha",
        subject_vocabulary=(),
    )

    assert row is None


def test_a_missing_climate_term_yields_no_row() -> None:
    climate = {**_SITE_CLIMATE, "mean_annual_precipitation_mm": None}

    row = build_design_row(
        site_climate=climate,
        site_covariates=_SITE_COVARIATES,
        envelope={"mean_annual_precipitation_mm": 300},
        subject_normalized="alpha",
        subject_vocabulary=(),
    )

    assert row is None


def test_the_design_row_matches_its_pinned_column_names() -> None:
    names = design_feature_names(subject_vocabulary=("alpha", "beta"))
    row = build_design_row(
        site_climate=_SITE_CLIMATE,
        site_covariates=_SITE_COVARIATES,
        envelope={"mean_annual_precipitation_mm": {"min": 250, "max": 350}, "aridity": "semi_arid"},
        subject_normalized="alpha",
        subject_vocabulary=("alpha", "beta"),
    )

    assert row is not None
    assert len(row) == len(names)
    assert names[: len(SITE_CLIMATE_FEATURES)] == tuple(f"site__{name}" for name in SITE_CLIMATE_FEATURES)
    assert row[names.index("subject__alpha")] == 1.0
    assert row[names.index("subject__beta")] == 0.0


def test_an_unstated_envelope_term_is_flagged_rather_than_imputed() -> None:
    names = design_feature_names(subject_vocabulary=())
    row = build_design_row(
        site_climate=_SITE_CLIMATE,
        site_covariates=_SITE_COVARIATES,
        envelope={"mean_annual_precipitation_mm": 300},
        subject_normalized="alpha",
        subject_vocabulary=(),
    )

    assert row is not None
    assert row[names.index("envelope__mean_annual_precipitation_mm__stated")] == 1.0
    assert row[names.index("envelope__elevation_m__stated")] == 0.0
    assert row[names.index("envelope__elevation_m__center")] == 0.0


def test_model_a_carries_no_subject_identity_column() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")

    assert matrix.subject_vocabulary == ()
    assert not any(name.startswith("subject__") for name in matrix.feature_names)


def test_model_b_carries_a_subject_identity_column_per_strategy() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="strategy_selection")

    assert len(matrix.subject_vocabulary) == 8  # noqa: PLR2004
    assert sum(1 for name in matrix.feature_names if name.startswith("subject__")) == 8  # noqa: PLR2004


def test_effective_sample_size_is_labels_not_rows() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")

    assert matrix.row_count == 24  # noqa: PLR2004
    assert matrix.label_count == 8  # noqa: PLR2004
    assert matrix.source_count == 8  # noqa: PLR2004


def test_the_fit_is_a_real_estimator_and_the_artifact_reproduces_its_probabilities() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")
    standardization = fit_standardization(matrix.features)
    model = fit_logistic(standardization.apply(matrix.features), matrix.targets, matrix.weights)
    artifact = build_artifact(
        matrix,
        model,
        standardization,
        model_kind="species_fit",
        feature_schema_version="agri_covariates_v1",
        label_release_key="release",
        label_release_checksum="c" * 64,
        label_review_tier="agent_reviewed_pending_owner_signature",
        regularization_strength=1.0,
    )

    estimator_probabilities = model.predict_proba(standardization.apply(matrix.features[:1]))[0]
    artifact_probabilities = class_probabilities(artifact, matrix.features[0])

    for label, value in zip(model.classes_, estimator_probabilities, strict=True):
        assert artifact_probabilities[str(label)] == pytest.approx(float(value), abs=1e-9)


def test_the_artifact_is_canonical_json_and_survives_a_round_trip() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")
    standardization = fit_standardization(matrix.features)
    model = fit_logistic(standardization.apply(matrix.features), matrix.targets, matrix.weights)
    artifact = build_artifact(
        matrix,
        model,
        standardization,
        model_kind="species_fit",
        feature_schema_version="agri_covariates_v1",
        label_release_key="release",
        label_release_checksum="c" * 64,
        label_review_tier="agent_reviewed_pending_owner_signature",
        regularization_strength=1.0,
    )

    document = json.loads(json.dumps(artifact.to_document()))
    restored = artifact_from_document(document)

    assert restored.checksum == artifact.checksum
    assert document["publication_authorized"] is False
    assert document["claim_tier"] == CLAIM_TIER
    assert all(isinstance(value, float) for row in document["coefficients"] for value in row)


def test_cross_validation_groups_by_source_and_reports_its_deviation() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")

    metrics = evaluate_leave_one_source_out(matrix, model_kind="species_fit", regularization_strength=1.0)

    assert metrics.scheme == "grouped_leave_one_source_out"
    assert metrics.fold_count == 8  # noqa: PLR2004
    assert metrics.effective_sample_size == 8  # noqa: PLR2004
    assert 0.0 <= metrics.accuracy <= 1.0
    assert any("Spatially-blocked" in deviation for deviation in metrics.deviations)
    assert len(metrics.per_source) == 8  # noqa: PLR2004
    assert all("source" in entry for entry in metrics.per_source)


def test_cross_validation_never_scores_a_row_its_own_source_trained_on() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")

    metrics = evaluate_leave_one_source_out(matrix, model_kind="species_fit", regularization_strength=1.0)

    assert metrics.scored_row_count == matrix.row_count
    for entry in metrics.per_source:
        assert entry["held_out_rows"] == 3  # noqa: PLR2004


def test_ranking_is_deterministic_and_carries_citations() -> None:
    matrix = assemble_training_matrix(_matrix_rows(), model_kind="species_fit")
    standardization = fit_standardization(matrix.features)
    model = fit_logistic(standardization.apply(matrix.features), matrix.targets, matrix.weights)
    artifact = build_artifact(
        matrix,
        model,
        standardization,
        model_kind="species_fit",
        feature_schema_version="agri_covariates_v1",
        label_release_key="release",
        label_release_checksum="c" * 64,
        label_review_tier="agent_reviewed_pending_owner_signature",
        regularization_strength=1.0,
    )

    first, skipped = rank_subjects(
        artifact,
        site_climate=_SITE_CLIMATE,
        site_covariates=_SITE_COVARIATES,
        wildfire_weight=0.0,
        water_weight=0.0,
    )
    second, _ = rank_subjects(
        artifact,
        site_climate=_SITE_CLIMATE,
        site_covariates=_SITE_COVARIATES,
        wildfire_weight=0.0,
        water_weight=0.0,
    )

    assert skipped == ()
    assert [item.subject for item in first] == [item.subject for item in second]
    assert [item.rank for item in first] == list(range(1, len(first) + 1))
    assert all(item.citations for item in first)
    assert all(item.to_payload()["claim_tier"] == CLAIM_TIER for item in first)


def test_objective_weights_only_move_candidates_with_that_evidence() -> None:
    rows = _matrix_rows()
    for row in rows:
        if row["subject"] == "alpha":
            row["harvest_slice"] = "strategy-water-harvesting"
    matrix = assemble_training_matrix(rows, model_kind="species_fit")
    standardization = fit_standardization(matrix.features)
    model = fit_logistic(standardization.apply(matrix.features), matrix.targets, matrix.weights)
    artifact = build_artifact(
        matrix,
        model,
        standardization,
        model_kind="species_fit",
        feature_schema_version="agri_covariates_v1",
        label_release_key="release",
        label_release_checksum="c" * 64,
        label_review_tier="agent_reviewed_pending_owner_signature",
        regularization_strength=1.0,
    )

    neutral, _ = rank_subjects(
        artifact, site_climate=_SITE_CLIMATE, site_covariates=_SITE_COVARIATES, wildfire_weight=0.0, water_weight=0.0
    )
    weighted, _ = rank_subjects(
        artifact, site_climate=_SITE_CLIMATE, site_covariates=_SITE_COVARIATES, wildfire_weight=0.0, water_weight=1.0
    )
    neutral_by_subject = {item.subject: item for item in neutral}
    weighted_by_subject = {item.subject: item for item in weighted}

    assert weighted_by_subject["alpha"].objective_adjusted_score > neutral_by_subject["alpha"].objective_adjusted_score
    assert weighted_by_subject["beta"].objective_adjusted_score == pytest.approx(
        neutral_by_subject["beta"].objective_adjusted_score
    )


def test_expected_utility_is_bounded_by_the_outcome_scale() -> None:
    assert expected_utility({"fit": 1.0}) == 1.0
    assert expected_utility({"unfit": 1.0}) == 0.0
    assert 0.0 < expected_utility({"fit": 0.5, "unfit": 0.5}) < 1.0
    assert set(OUTCOME_UTILITY) == {"fit", "marginal", "unfit", "effective", "mixed", "ineffective"}


def test_an_empty_instance_set_is_refused_rather_than_fitted() -> None:
    with pytest.raises(RecommendationTrainingError, match="no matched training instances"):
        assemble_training_matrix([], model_kind="species_fit")


def test_a_matrix_whose_every_row_is_incomplete_is_refused() -> None:
    rows = _matrix_rows()
    for row in rows:
        row["envelope_match"] = {"site_climate": {**_SITE_CLIMATE, "mean_annual_temperature_c": None}}

    with pytest.raises(RecommendationTrainingError, match="nothing was fabricated"):
        assemble_training_matrix(rows, model_kind="species_fit")


def test_standardization_survives_a_constant_column() -> None:
    features = np.array([[1.0, 5.0], [1.0, 7.0]])

    standardization = fit_standardization(features)

    assert standardization.scale[0] == 1.0
    assert np.isfinite(standardization.apply(features)).all()
