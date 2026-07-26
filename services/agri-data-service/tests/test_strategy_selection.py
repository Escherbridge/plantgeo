"""Deterministic local strategy-selection benchmark tests."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agri_data_service.execution.strategy_selection import (
    SelectionPolicy,
    load_strategy_label_bundle,
    train_strategy_models,
)

RELAXED_MIN_PAIRED_CLUSTERS = 12


def _episode(  # noqa: PLR0913
    *,
    episode_id: str,
    subject_id: str,
    strategy_id: str | None,
    arm: str,
    cohort: str,
    spatial_block: str,
    assigned_at: datetime,
    features: list[float],
    target: float,
) -> dict[str, object]:
    baseline_value = 10.0 + 0.2 * features[0] + 0.1 * features[1]
    return {
        "episode_id": episode_id,
        "subject_id": subject_id,
        "strategy_id": strategy_id,
        "arm": arm,
        "cohort": cohort,
        "spatial_block": spatial_block,
        "assigned_at": assigned_at.isoformat(),
        "baseline_start": (assigned_at - timedelta(days=30)).isoformat(),
        "baseline_end": (assigned_at - timedelta(days=1)).isoformat(),
        "outcome_start": assigned_at.isoformat(),
        "outcome_end": (assigned_at + timedelta(days=30)).isoformat(),
        "covariates_available_at": (assigned_at - timedelta(days=1)).isoformat(),
        "data_available_at": (assigned_at + timedelta(days=31)).isoformat(),
        "baseline_value": baseline_value,
        "outcome_value": baseline_value + target,
        "features": features,
    }


def _synthetic_payload() -> dict[str, object]:
    episodes: list[dict[str, object]] = []
    strategies = {"strategy-strong": 2.0, "strategy-weak": 0.8}
    blocks = ("block-a", "block-b", "block-c", "block-d")
    cohort_start = datetime(2022, 1, 15, tzinfo=UTC)
    sequence = 0
    for cohort_index in range(4):
        assigned_at = cohort_start + timedelta(days=90 * cohort_index)
        cohort = f"cohort-{cohort_index}"
        for block_index, block in enumerate(blocks):
            site_signal = float(block_index - 1.5)
            season = float(cohort_index)
            common_target = 0.2 * site_signal + 0.1 * season
            for offset in (-0.05, 0.05):
                sequence += 1
                episodes.append(
                    _episode(
                        episode_id=f"episode-{sequence:04d}",
                        subject_id=f"subject-{sequence:04d}",
                        strategy_id=None,
                        arm="control",
                        cohort=cohort,
                        spatial_block=block,
                        assigned_at=assigned_at,
                        features=[site_signal + offset, season],
                        target=common_target + 0.2 * offset,
                    )
                )
            for strategy_id, effect in strategies.items():
                sequence += 1
                episodes.append(
                    _episode(
                        episode_id=f"episode-{sequence:04d}",
                        subject_id=f"subject-{sequence:04d}",
                        strategy_id=strategy_id,
                        arm="treatment",
                        cohort=cohort,
                        spatial_block=block,
                        assigned_at=assigned_at,
                        features=[site_signal, season],
                        target=common_target + effect,
                    )
                )
    return {
        "schema_version": "strategy_labels_v1",
        "label_release_checksum": "b" * 64,
        "as_of_time": (cohort_start + timedelta(days=90 * 3 + 32)).isoformat(),
        "outcome": {
            "key": "soil_moisture_change_30d",
            "unit": "fraction",
            "benefit_direction": "increase",
            "smallest_meaningful_effect": 0.1,
        },
        "feature_names": ["site_signal", "season"],
        "episodes": episodes,
    }


def _write_payload(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _relaxed_policy() -> SelectionPolicy:
    return SelectionPolicy(
        min_treated=12,
        min_controls=24,
        min_spatial_blocks=4,
        min_cohorts=4,
        min_clusters=12,
        min_fold_training_per_arm=2,
        min_oof_fraction=0.70,
        propensity_low=0.01,
        propensity_high=0.99,
        min_overlap_fraction=0.90,
        max_stabilized_weight=100.0,
        min_effective_sample_size=5.0,
        min_effective_sample_fraction=0.20,
        max_weighted_smd=0.25,
        max_model_disagreement=0.35,
        min_paired_contrast_clusters=RELAXED_MIN_PAIRED_CLUSTERS,
        ridge_penalty=0.001,
        logistic_penalty=0.001,
    )


def test_label_bundle_rejects_unknown_keys(tmp_path: Path) -> None:
    payload = _synthetic_payload()
    payload["unexpected"] = True
    with pytest.raises(ValueError, match="must contain exactly"):
        load_strategy_label_bundle(_write_payload(tmp_path / "unknown.json", payload))

    payload = _synthetic_payload()
    episodes = payload["episodes"]
    assert isinstance(episodes, list)
    first = episodes[0]
    assert isinstance(first, dict)
    first["unexpected"] = True
    with pytest.raises(ValueError, match="must contain exactly"):
        load_strategy_label_bundle(_write_payload(tmp_path / "episode-unknown.json", payload))

    payload = _synthetic_payload()
    payload["label_release_checksum"] = "not-a-checksum"
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        load_strategy_label_bundle(_write_payload(tmp_path / "bad-label-checksum.json", payload))


def test_label_bundle_rejects_naive_times_and_future_covariates(tmp_path: Path) -> None:
    naive_payload = _synthetic_payload()
    naive_episodes = naive_payload["episodes"]
    assert isinstance(naive_episodes, list)
    naive_first = naive_episodes[0]
    assert isinstance(naive_first, dict)
    naive_first["assigned_at"] = "2022-01-15T00:00:00"
    with pytest.raises(ValueError, match="must include a UTC offset"):
        load_strategy_label_bundle(_write_payload(tmp_path / "naive.json", naive_payload))

    future_payload = _synthetic_payload()
    future_episodes = future_payload["episodes"]
    assert isinstance(future_episodes, list)
    future_first = future_episodes[0]
    assert isinstance(future_first, dict)
    future_first["covariates_available_at"] = "2022-01-16T00:00:00+00:00"
    with pytest.raises(ValueError, match="available no later than assignment"):
        load_strategy_label_bundle(_write_payload(tmp_path / "future.json", future_payload))


def test_label_bundle_rejects_outcomes_after_as_of_and_mixed_cohort_times(
    tmp_path: Path,
) -> None:
    future_outcome_payload = _synthetic_payload()
    future_outcome_payload["as_of_time"] = "2022-01-16T00:00:00+00:00"
    with pytest.raises(ValueError, match="available no later than as_of_time"):
        load_strategy_label_bundle(
            _write_payload(tmp_path / "future-outcome.json", future_outcome_payload)
        )

    mixed_cohort_payload = _synthetic_payload()
    mixed_episodes = mixed_cohort_payload["episodes"]
    assert isinstance(mixed_episodes, list)
    later_episode = mixed_episodes[-1]
    assert isinstance(later_episode, dict)
    later_episode["cohort"] = "cohort-0"
    with pytest.raises(ValueError, match="one assignment time"):
        load_strategy_label_bundle(
            _write_payload(tmp_path / "mixed-cohort.json", mixed_cohort_payload)
        )


def test_expanding_folds_exclude_outcomes_unavailable_at_test_origin(
    tmp_path: Path,
) -> None:
    payload = _synthetic_payload()
    episodes = payload["episodes"]
    assert isinstance(episodes, list)
    delayed = episodes[0]
    assert isinstance(delayed, dict)
    delayed_episode_id = delayed["episode_id"]
    delayed["data_available_at"] = "2022-04-16T00:00:00+00:00"
    bundle = load_strategy_label_bundle(_write_payload(tmp_path / "delayed.json", payload))

    artifact = train_strategy_models(bundle, policy=_relaxed_policy())

    assert artifact.decision_state == "ranked"
    for result in artifact.strategies:
        folds = result.model_parameters["folds"]
        assert isinstance(folds, list)
        april_folds = [
            fold
            for fold in folds
            if isinstance(fold, dict)
            and fold["test_assignment_origin"] == "2022-04-15T00:00:00+00:00"
        ]
        assert april_folds
        assert all(
            delayed_episode_id not in fold["training_episode_ids"]
            for fold in april_folds
        )


def test_default_policy_abstains_when_support_is_insufficient(tmp_path: Path) -> None:
    bundle = load_strategy_label_bundle(
        _write_payload(tmp_path / "labels.json", _synthetic_payload())
    )
    artifact = train_strategy_models(bundle)

    assert artifact.decision_state == "abstained"
    assert artifact.selected_strategy_id is None
    assert "insufficient_treated_units" in artifact.abstention_reasons
    assert "insufficient_control_units" in artifact.abstention_reasons
    assert all(result.state == "abstained" for result in artifact.strategies)
    assert all(result.estimates == () for result in artifact.strategies)


def test_training_artifact_json_and_checksum_are_deterministic(tmp_path: Path) -> None:
    bundle_path = _write_payload(tmp_path / "labels.json", _synthetic_payload())
    first_bundle = load_strategy_label_bundle(bundle_path)
    second_bundle = load_strategy_label_bundle(bundle_path)

    first = train_strategy_models(first_bundle, policy=_relaxed_policy())
    second = train_strategy_models(second_bundle, policy=_relaxed_policy())

    assert first.to_json() == second.to_json()
    assert first.checksum == second.checksum
    assert first.label_checksum == second.label_checksum == "b" * 64
    assert first.label_bundle_checksum == second.label_bundle_checksum == first_bundle.checksum
    assert " " not in first.to_json()
    payload = json.loads(first.to_json())
    assert payload["schema_version"] == "strategy_training_artifact_v1"
    assert payload["artifact_scope"] == "research_evaluation_only"
    assert payload["effect_claim_authorized"] is False
    assert payload["publication_authorized"] is False


def test_label_bundle_checksum_binds_exact_trimmed_utf8_json(tmp_path: Path) -> None:
    payload = _synthetic_payload()
    compact_json = json.dumps(payload, separators=(",", ":"))
    bundle_path = tmp_path / "labels.json"
    bundle_path.write_bytes(f"\r\n  {compact_json}\t\r\n".encode())

    original = load_strategy_label_bundle(bundle_path)

    assert original.checksum == hashlib.sha256(compact_json.encode()).hexdigest()

    episodes = payload["episodes"]
    assert isinstance(episodes, list)
    first = episodes[0]
    assert isinstance(first, dict)
    first["outcome_value"] = float(first["outcome_value"]) + 0.01
    modified_json = json.dumps(payload, separators=(",", ":"))
    bundle_path.write_text(modified_json, encoding="utf-8")
    modified = load_strategy_label_bundle(bundle_path)

    assert modified.label_release_checksum == original.label_release_checksum
    assert modified.checksum != original.checksum


def test_relaxed_synthetic_benchmark_runs_all_estimators_and_ranks_stronger_strategy(
    tmp_path: Path,
) -> None:
    bundle = load_strategy_label_bundle(
        _write_payload(tmp_path / "labels.json", _synthetic_payload())
    )
    artifact = train_strategy_models(bundle, policy=_relaxed_policy())

    assert artifact.decision_state == "ranked"
    assert artifact.selected_strategy_id == "strategy-strong"
    assert artifact.abstention_reasons == ()
    assert artifact.artifact_scope == "research_evaluation_only"
    assert artifact.effect_claim_authorized is False
    assert artifact.publication_authorized is False
    assert artifact.selection_contrast is not None
    assert artifact.selection_contrast.best_strategy_id == "strategy-strong"
    assert artifact.selection_contrast.comparator_strategy_id == "strategy-weak"
    assert (
        artifact.selection_contrast.paired_cluster_count
        >= RELAXED_MIN_PAIRED_CLUSTERS
    )
    assert artifact.selection_contrast.interval_low is not None
    assert artifact.selection_contrast.interval_low > 0
    assert artifact.selection_contrast.passed is True
    assert {result.strategy_id for result in artifact.strategies} == {
        "strategy-strong",
        "strategy-weak",
    }
    for result in artifact.strategies:
        assert result.state == "eligible"
        assert {estimate.estimator for estimate in result.estimates} == {
            "matched_did",
            "aipw",
            "dr_learner",
            "t_learner",
        }
        assert result.conservative_benefit is not None

    by_strategy = {result.strategy_id: result for result in artifact.strategies}
    strong = by_strategy["strategy-strong"]
    weak = by_strategy["strategy-weak"]
    assert strong.conservative_benefit is not None
    assert weak.conservative_benefit is not None
    assert strong.conservative_benefit > weak.conservative_benefit


def test_ranking_abstains_without_a_positive_paired_strategy_contrast(
    tmp_path: Path,
) -> None:
    payload = _synthetic_payload()
    episodes = payload["episodes"]
    assert isinstance(episodes, list)
    for episode in episodes:
        assert isinstance(episode, dict)
        if episode["strategy_id"] == "strategy-strong":
            episode["outcome_value"] = float(episode["outcome_value"]) - 1.2
    bundle = load_strategy_label_bundle(_write_payload(tmp_path / "tied.json", payload))

    artifact = train_strategy_models(bundle, policy=_relaxed_policy())

    assert artifact.decision_state == "abstained"
    assert artifact.selected_strategy_id is None
    assert "paired_strategy_contrast_not_positive" in artifact.abstention_reasons
    assert artifact.selection_contrast is not None
    assert artifact.selection_contrast.passed is False
    assert artifact.selection_contrast.interval_low is not None
    assert artifact.selection_contrast.interval_low <= 0
