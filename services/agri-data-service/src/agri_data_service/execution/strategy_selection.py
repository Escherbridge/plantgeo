"""Local, evaluation-only intervention-effect benchmark."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

_MAX_BUNDLE_BYTES = 16_000_000
_EPSILON = 1e-8
_CONVERGENCE_TOLERANCE = 1e-8
_MIN_CLUSTER_COUNT = 2
_MIN_STRATEGY_CANDIDATES = 2
_SHA256_HEX_LENGTH = 64
_Z_95 = 1.959963984540054


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _checksum(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _strict_keys(value: dict[str, object], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} must contain exactly {sorted(expected)}")


@dataclass(frozen=True)
class StrategyOutcome:
    """Versioned outcome semantics shared by every strategy comparison."""

    key: str
    unit: str
    benefit_direction: Literal["increase", "decrease"]
    smallest_meaningful_effect: float


@dataclass(frozen=True)
class StrategyEpisode:
    """One independent treated or eligible-control intervention unit."""

    episode_id: str
    subject_id: str
    strategy_id: str | None
    arm: Literal["treatment", "control"]
    cohort: str
    spatial_block: str
    assigned_at: datetime
    baseline_start: datetime
    baseline_end: datetime
    outcome_start: datetime
    outcome_end: datetime
    covariates_available_at: datetime
    data_available_at: datetime
    baseline_value: float
    outcome_value: float
    features: tuple[float, ...]

    @property
    def target(self) -> float:
        return self.outcome_value - self.baseline_value


@dataclass(frozen=True)
class StrategyLabelBundle:
    """Strict external label bundle accepted by the local benchmark."""

    schema_version: Literal["strategy_labels_v1"]
    label_release_checksum: str
    checksum: str
    as_of_time: datetime
    outcome: StrategyOutcome
    feature_names: tuple[str, ...]
    episodes: tuple[StrategyEpisode, ...]

@dataclass(frozen=True)
class SelectionPolicy:
    """Overrideable causal-support gates; defaults are deliberately strict."""

    min_treated: int = 100
    min_controls: int = 200
    min_spatial_blocks: int = 8
    min_cohorts: int = 4
    min_clusters: int = 20
    min_fold_training_per_arm: int = 2
    min_oof_fraction: float = 0.6
    propensity_low: float = 0.10
    propensity_high: float = 0.90
    min_overlap_fraction: float = 0.90
    max_stabilized_weight: float = 10.0
    min_effective_sample_size: float = 50.0
    min_effective_sample_fraction: float = 0.25
    max_weighted_smd: float = 0.10
    max_model_disagreement: float = 0.25
    min_paired_contrast_clusters: int = 20
    ridge_penalty: float = 1.0
    logistic_penalty: float = 1.0

    @property
    def checksum(self) -> str:
        return _checksum(asdict(self))


@dataclass(frozen=True)
class EffectEstimate:
    """One estimator's held-out average treatment effect."""

    estimator: Literal["matched_did", "aipw", "dr_learner", "t_learner"]
    effect: float
    standard_error: float
    interval_low: float
    interval_high: float


@dataclass(frozen=True)
class ClusterEffect:
    """One out-of-fold AIPW effect aggregated to an independent cluster."""

    cluster: str
    effect: float


@dataclass(frozen=True)
class StrategyModelResult:
    """Per-strategy estimates, diagnostics, and gate result."""

    strategy_id: str
    state: Literal["eligible", "abstained"]
    abstention_reasons: tuple[str, ...]
    estimates: tuple[EffectEstimate, ...]
    conservative_benefit: float | None
    diagnostics: dict[str, float | int]
    model_parameters: dict[str, object]
    oof_cluster_effects: tuple[ClusterEffect, ...]


@dataclass(frozen=True)
class SelectionContrast:
    """Paired out-of-fold contrast required to separate two strategies."""

    estimand: Literal["paired_oof_cluster_aipw_benefit_difference"]
    best_strategy_id: str
    comparator_strategy_id: str
    paired_cluster_count: int
    effect_difference: float | None
    standard_error: float | None
    interval_low: float | None
    interval_high: float | None
    passed: bool


@dataclass(frozen=True)
class StrategyTrainingArtifact:
    """Canonical, non-executable research artifact with no effect-claim authority."""

    schema_version: Literal["strategy_training_artifact_v1"]
    artifact_scope: Literal["research_evaluation_only"]
    effect_claim_authorized: Literal[False]
    publication_authorized: Literal[False]
    label_checksum: str
    label_bundle_checksum: str
    policy_checksum: str
    outcome: StrategyOutcome
    feature_names: tuple[str, ...]
    decision_state: Literal["ranked", "abstained"]
    selected_strategy_id: str | None
    abstention_reasons: tuple[str, ...]
    strategies: tuple[StrategyModelResult, ...]
    selection_contrast: SelectionContrast | None

    @property
    def checksum(self) -> str:
        return _checksum(self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return _canonical_json(self.to_payload())


def load_strategy_label_bundle(path: Path) -> StrategyLabelBundle:  # noqa: PLR0912
    """Load a bounded bundle and bind its exact trimmed UTF-8 JSON text."""
    source_bytes = path.read_bytes()
    if len(source_bytes) > _MAX_BUNDLE_BYTES:
        raise ValueError("strategy label bundle exceeds the 16 MB local limit")
    source_text = source_bytes.decode("utf-8").strip()
    raw = json.loads(source_text)
    if not isinstance(raw, dict):
        raise ValueError("strategy label bundle must be a JSON object")
    _strict_keys(
        raw,
        {
            "schema_version",
            "label_release_checksum",
            "as_of_time",
            "outcome",
            "feature_names",
            "episodes",
        },
        "bundle",
    )
    if raw["schema_version"] != "strategy_labels_v1":
        raise ValueError("unsupported strategy label bundle schema_version")
    label_release_checksum = _sha256_string(
        raw["label_release_checksum"],
        "label_release_checksum",
    )
    as_of_time = _aware_datetime(raw["as_of_time"], "as_of_time")

    outcome_raw = raw["outcome"]
    if not isinstance(outcome_raw, dict):
        raise ValueError("outcome must be an object")
    _strict_keys(
        outcome_raw,
        {"key", "unit", "benefit_direction", "smallest_meaningful_effect"},
        "outcome",
    )
    direction = outcome_raw["benefit_direction"]
    if direction not in {"increase", "decrease"}:
        raise ValueError("outcome.benefit_direction must be increase or decrease")
    outcome = StrategyOutcome(
        key=_nonempty_string(outcome_raw["key"], "outcome.key"),
        unit=_nonempty_string(outcome_raw["unit"], "outcome.unit"),
        benefit_direction=direction,
        smallest_meaningful_effect=_finite_float(
            outcome_raw["smallest_meaningful_effect"],
            "outcome.smallest_meaning_effect",
        ),
    )
    if outcome.smallest_meaningful_effect < 0:
        raise ValueError("outcome.smallest_meaningful_effect must be nonnegative")

    names_raw = raw["feature_names"]
    if not isinstance(names_raw, list) or not names_raw:
        raise ValueError("feature_names must be a nonempty array")
    feature_names = tuple(_nonempty_string(name, "feature_names[]") for name in names_raw)
    if len(set(feature_names)) != len(feature_names):
        raise ValueError("feature_names must be unique")

    episodes_raw = raw["episodes"]
    if not isinstance(episodes_raw, list) or not episodes_raw:
        raise ValueError("episodes must be a nonempty array")
    episodes = tuple(_parse_episode(value, len(feature_names)) for value in episodes_raw)
    if len({episode.episode_id for episode in episodes}) != len(episodes):
        raise ValueError("episode_id must be unique")
    if len({episode.subject_id for episode in episodes}) != len(episodes):
        raise ValueError("each subject_id may appear in only one independent episode")
    if any(episode.data_available_at > as_of_time for episode in episodes):
        raise ValueError("every outcome must be available no later than as_of_time")
    cohort_times: dict[str, set[datetime]] = {}
    for episode in episodes:
        cohort_times.setdefault(episode.cohort, set()).add(episode.assigned_at)
    if any(len(times) != 1 for times in cohort_times.values()):
        raise ValueError("each cohort must map to exactly one assignment time")
    return StrategyLabelBundle(
        schema_version="strategy_labels_v1",
        label_release_checksum=label_release_checksum,
        checksum=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        as_of_time=as_of_time,
        outcome=outcome,
        feature_names=feature_names,
        episodes=episodes,
    )


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def _sha256_string(value: object, field: str) -> str:
    parsed = _nonempty_string(value, field)
    if len(parsed) != _SHA256_HEX_LENGTH or any(
        character not in "0123456789abcdef" for character in parsed
    ):
        raise ValueError(f"{field} must be a lowercase SHA-256 checksum")
    return parsed


def _parse_episode(value: object, feature_count: int) -> StrategyEpisode:
    if not isinstance(value, dict):
        raise ValueError("episodes[] must be an object")
    expected = {
        "episode_id",
        "subject_id",
        "strategy_id",
        "arm",
        "cohort",
        "spatial_block",
        "assigned_at",
        "baseline_start",
        "baseline_end",
        "outcome_start",
        "outcome_end",
        "covariates_available_at",
        "data_available_at",
        "baseline_value",
        "outcome_value",
        "features",
    }
    _strict_keys(value, expected, "episodes[]")
    arm = value["arm"]
    if arm not in {"treatment", "control"}:
        raise ValueError("episodes[].arm must be treatment or control")
    raw_strategy = value["strategy_id"]
    strategy_id = None if raw_strategy is None else _nonempty_string(raw_strategy, "episodes[].strategy_id")
    if (arm == "treatment") != (strategy_id is not None):
        raise ValueError("treatment episodes require strategy_id and controls must omit it")
    raw_features = value["features"]
    if not isinstance(raw_features, list) or len(raw_features) != feature_count:
        raise ValueError("episodes[].features must match feature_names")

    assigned_at = _aware_datetime(value["assigned_at"], "episodes[].assigned_at")
    baseline_start = _aware_datetime(value["baseline_start"], "episodes[].baseline_start")
    baseline_end = _aware_datetime(value["baseline_end"], "episodes[].baseline_end")
    outcome_start = _aware_datetime(value["outcome_start"], "episodes[].outcome_start")
    outcome_end = _aware_datetime(value["outcome_end"], "episodes[].outcome_end")
    covariates_available_at = _aware_datetime(
        value["covariates_available_at"],
        "episodes[].covariates_available_at",
    )
    data_available_at = _aware_datetime(value["data_available_at"], "episodes[].data_available_at")
    if not (baseline_start < baseline_end <= assigned_at <= outcome_start < outcome_end <= data_available_at):
        raise ValueError("episode windows must be ordered, nonoverlapping, and mature before availability")
    if covariates_available_at > assigned_at:
        raise ValueError("predictive covariates must be available no later than assignment")

    return StrategyEpisode(
        episode_id=_nonempty_string(value["episode_id"], "episodes[].episode_id"),
        subject_id=_nonempty_string(value["subject_id"], "episodes[].subject_id"),
        strategy_id=strategy_id,
        arm=arm,
        cohort=_nonempty_string(value["cohort"], "episodes[].cohort"),
        spatial_block=_nonempty_string(value["spatial_block"], "episodes[].spatial_block"),
        assigned_at=assigned_at,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        outcome_start=outcome_start,
        outcome_end=outcome_end,
        covariates_available_at=covariates_available_at,
        data_available_at=data_available_at,
        baseline_value=_finite_float(value["baseline_value"], "episodes[].baseline_value"),
        outcome_value=_finite_float(value["outcome_value"], "episodes[].outcome_value"),
        features=tuple(_finite_float(item, "episodes[].features[]") for item in raw_features),
    )


@dataclass(frozen=True)
class _LinearFit:
    coefficients: NDArray[np.float64]
    center: NDArray[np.float64]
    scale: NDArray[np.float64]

    def predict(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        standardized = (features - self.center) / self.scale
        design = np.column_stack((np.ones(len(features)), standardized))
        return np.asarray(design @ self.coefficients, dtype=np.float64)

    def payload(self) -> dict[str, object]:
        return {
            "center": self.center.tolist(),
            "coefficients": self.coefficients.tolist(),
            "scale": self.scale.tolist(),
        }


def _design_fit(
    features: NDArray[np.float64],
    target: NDArray[np.float64],
    penalty: float,
) -> _LinearFit:
    center = np.mean(features, axis=0)
    scale = np.std(features, axis=0)
    scale = np.where(scale < _EPSILON, 1.0, scale)
    design = np.column_stack((np.ones(len(features)), (features - center) / scale))
    regularizer = np.eye(design.shape[1], dtype=np.float64) * penalty
    regularizer[0, 0] = 0
    coefficients = np.linalg.pinv(design.T @ design + regularizer) @ design.T @ target
    return _LinearFit(np.asarray(coefficients), center, scale)


def _propensity_fit(
    features: NDArray[np.float64],
    treatment: NDArray[np.float64],
    penalty: float,
) -> _LinearFit:
    center = np.mean(features, axis=0)
    scale = np.std(features, axis=0)
    scale = np.where(scale < _EPSILON, 1.0, scale)
    design = np.column_stack((np.ones(len(features)), (features - center) / scale))
    coefficients = np.zeros(design.shape[1], dtype=np.float64)
    regularizer = np.eye(design.shape[1], dtype=np.float64) * penalty
    regularizer[0, 0] = 0
    for _ in range(50):
        probability = np.clip(_sigmoid(design @ coefficients), _EPSILON, 1 - _EPSILON)
        weights = np.clip(probability * (1 - probability), _EPSILON, None)
        gradient = design.T @ (treatment - probability) - regularizer @ coefficients
        hessian = design.T @ (design * weights[:, None]) + regularizer
        step = np.linalg.pinv(hessian) @ gradient
        coefficients += step
        if float(np.max(np.abs(step))) < _CONVERGENCE_TOLERANCE:
            break
    return _LinearFit(coefficients, center, scale)


def _sigmoid(value: NDArray[np.float64]) -> NDArray[np.float64]:
    clipped = np.clip(value, -35, 35)
    return np.asarray(1 / (1 + np.exp(-clipped)), dtype=np.float64)


def _propensity_predict(model: _LinearFit, features: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.clip(_sigmoid(model.predict(features)), _EPSILON, 1 - _EPSILON)


def _folds(episodes: list[StrategyEpisode]) -> list[tuple[NDArray[np.int64], NDArray[np.int64]]]:
    cohort_origins = {
        episode.cohort: episode.assigned_at
        for episode in episodes
    }
    cohorts = sorted(cohort_origins, key=lambda cohort: (cohort_origins[cohort], cohort))
    blocks = sorted({episode.spatial_block for episode in episodes})
    result: list[tuple[NDArray[np.int64], NDArray[np.int64]]] = []
    for cohort in cohorts[1:]:
        test_origin = cohort_origins[cohort]
        for block in blocks:
            train = np.asarray(
                [
                    index
                    for index, episode in enumerate(episodes)
                    if episode.assigned_at < test_origin
                    and episode.data_available_at <= test_origin
                    and episode.spatial_block != block
                ],
                dtype=np.int64,
            )
            test = np.asarray(
                [
                    index
                    for index, episode in enumerate(episodes)
                    if episode.cohort == cohort and episode.spatial_block == block
                ],
                dtype=np.int64,
            )
            if len(train) and len(test):
                result.append((train, test))
    return result


def _effective_sample_size(weights: NDArray[np.float64]) -> float:
    denominator = float(np.sum(weights**2))
    if denominator <= _EPSILON:
        return 0.0
    return float(np.sum(weights) ** 2 / denominator)


def _weighted_smd(
    features: NDArray[np.float64],
    treatment: NDArray[np.float64],
    weights: NDArray[np.float64],
) -> float:
    maxima: list[float] = []
    for column in range(features.shape[1]):
        treated = treatment == 1
        control = ~treated
        treated_mean = float(np.average(features[treated, column], weights=weights[treated]))
        control_mean = float(np.average(features[control, column], weights=weights[control]))
        treated_var = float(
            np.average((features[treated, column] - treated_mean) ** 2, weights=weights[treated])
        )
        control_var = float(
            np.average((features[control, column] - control_mean) ** 2, weights=weights[control])
        )
        pooled = math.sqrt(max((treated_var + control_var) / 2, _EPSILON))
        maxima.append(abs(treated_mean - control_mean) / pooled)
    return max(maxima, default=0.0)


def _cluster_standard_error(values: NDArray[np.float64], clusters: list[str]) -> float:
    by_cluster: dict[str, list[float]] = {}
    for value, cluster in zip(values.tolist(), clusters, strict=True):
        by_cluster.setdefault(cluster, []).append(value)
    cluster_count = len(by_cluster)
    if cluster_count < _MIN_CLUSTER_COUNT:
        return math.inf
    centered_mean = float(np.mean(values))
    cluster_scores = np.asarray(
        [np.sum(np.asarray(group, dtype=np.float64) - centered_mean) for group in by_cluster.values()],
        dtype=np.float64,
    )
    variance = (
        cluster_count
        / (cluster_count - 1)
        * float(np.sum(cluster_scores**2))
        / len(values) ** 2
    )
    return math.sqrt(max(variance, 0.0))


def _cluster_effects(
    values: NDArray[np.float64],
    clusters: list[str],
) -> tuple[ClusterEffect, ...]:
    by_cluster: dict[str, list[float]] = {}
    for value, cluster in zip(values.tolist(), clusters, strict=True):
        by_cluster.setdefault(cluster, []).append(value)
    return tuple(
        ClusterEffect(cluster=cluster, effect=float(np.mean(by_cluster[cluster])))
        for cluster in sorted(by_cluster)
    )


def _estimate(
    name: Literal["matched_did", "aipw", "dr_learner", "t_learner"],
    effect: float,
    standard_error: float,
) -> EffectEstimate:
    return EffectEstimate(
        estimator=name,
        effect=float(effect),
        standard_error=float(standard_error),
        interval_low=float(effect - _Z_95 * standard_error),
        interval_high=float(effect + _Z_95 * standard_error),
    )


def _matched_did(
    episodes: list[StrategyEpisode],
    features: NDArray[np.float64],
    target: NDArray[np.float64],
    treatment: NDArray[np.float64],
) -> EffectEstimate | None:
    differences: list[float] = []
    clusters: list[str] = []
    control_indices = np.flatnonzero(treatment == 0)
    for treated_index in np.flatnonzero(treatment == 1):
        treated_episode = episodes[int(treated_index)]
        eligible = [
            int(index)
            for index in control_indices
            if episodes[int(index)].cohort == treated_episode.cohort
            and episodes[int(index)].spatial_block == treated_episode.spatial_block
        ]
        if not eligible:
            continue
        distances = [
            float(np.linalg.norm(features[int(treated_index)] - features[control_index]))
            for control_index in eligible
        ]
        control_index = eligible[int(np.argmin(distances))]
        differences.append(float(target[int(treated_index)] - target[control_index]))
        clusters.append(f"{treated_episode.cohort}|{treated_episode.spatial_block}")
    if not differences:
        return None
    values = np.asarray(differences, dtype=np.float64)
    return _estimate("matched_did", float(np.mean(values)), _cluster_standard_error(values, clusters))


def _cross_fitted_predictions(
    episodes: list[StrategyEpisode],
    features: NDArray[np.float64],
    target: NDArray[np.float64],
    treatment: NDArray[np.float64],
    policy: SelectionPolicy,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], NDArray[np.bool_], dict[str, object]]:
    propensity = np.full(len(episodes), np.nan, dtype=np.float64)
    outcome_treated = np.full(len(episodes), np.nan, dtype=np.float64)
    outcome_control = np.full(len(episodes), np.nan, dtype=np.float64)
    fold_payload: list[dict[str, object]] = []
    for train, test in _folds(episodes):
        train_treatment = treatment[train]
        if (
            int(np.sum(train_treatment == 1)) < policy.min_fold_training_per_arm
            or int(np.sum(train_treatment == 0)) < policy.min_fold_training_per_arm
        ):
            continue
        propensity_model = _propensity_fit(features[train], train_treatment, policy.logistic_penalty)
        treated_model = _design_fit(
            features[train][train_treatment == 1],
            target[train][train_treatment == 1],
            policy.ridge_penalty,
        )
        control_model = _design_fit(
            features[train][train_treatment == 0],
            target[train][train_treatment == 0],
            policy.ridge_penalty,
        )
        propensity[test] = _propensity_predict(propensity_model, features[test])
        outcome_treated[test] = treated_model.predict(features[test])
        outcome_control[test] = control_model.predict(features[test])
        fold_payload.append(
            {
                "control_outcome": control_model.payload(),
                "propensity": propensity_model.payload(),
                "training_episode_ids": [episodes[int(index)].episode_id for index in train],
                "test_assignment_origin": min(
                    episodes[int(index)].assigned_at for index in test
                ).isoformat(),
                "test_episode_ids": [episodes[int(index)].episode_id for index in test],
                "treated_outcome": treated_model.payload(),
            }
        )
    valid = np.isfinite(propensity) & np.isfinite(outcome_treated) & np.isfinite(outcome_control)
    return propensity, outcome_treated, outcome_control, valid, {"folds": fold_payload}


def _abstained_result(
    strategy_id: str,
    reasons: list[str],
    diagnostics: dict[str, float | int],
) -> StrategyModelResult:
    return StrategyModelResult(
        strategy_id=strategy_id,
        state="abstained",
        abstention_reasons=tuple(sorted(set(reasons))),
        estimates=(),
        conservative_benefit=None,
        diagnostics=diagnostics,
        model_parameters={},
        oof_cluster_effects=(),
    )


def _train_one_strategy(  # noqa: PLR0912, PLR0915
    bundle: StrategyLabelBundle,
    strategy_id: str,
    policy: SelectionPolicy,
) -> StrategyModelResult:
    episodes = [
        episode
        for episode in bundle.episodes
        if episode.arm == "control" or episode.strategy_id == strategy_id
    ]
    features = np.asarray([episode.features for episode in episodes], dtype=np.float64)
    benefit_sign = 1.0 if bundle.outcome.benefit_direction == "increase" else -1.0
    target = np.asarray([benefit_sign * episode.target for episode in episodes], dtype=np.float64)
    treatment = np.asarray([episode.arm == "treatment" for episode in episodes], dtype=np.float64)
    treated_count = int(np.sum(treatment == 1))
    control_count = int(np.sum(treatment == 0))
    block_count = len({episode.spatial_block for episode in episodes})
    cohort_count = len({episode.cohort for episode in episodes})
    cluster_count = len({(episode.cohort, episode.spatial_block) for episode in episodes})
    diagnostics: dict[str, float | int] = {
        "cluster_count": cluster_count,
        "cohort_count": cohort_count,
        "control_count": control_count,
        "spatial_block_count": block_count,
        "treated_count": treated_count,
    }
    reasons: list[str] = []
    if treated_count < policy.min_treated:
        reasons.append("insufficient_treated_units")
    if control_count < policy.min_controls:
        reasons.append("insufficient_control_units")
    if block_count < policy.min_spatial_blocks:
        reasons.append("insufficient_spatial_blocks")
    if cohort_count < policy.min_cohorts:
        reasons.append("insufficient_start_cohorts")
    if cluster_count < policy.min_clusters:
        reasons.append("insufficient_block_cohort_clusters")
    if reasons:
        return _abstained_result(strategy_id, reasons, diagnostics)

    matched = _matched_did(episodes, features, target, treatment)
    if matched is None:
        reasons.append("no_exact_block_cohort_matches")

    propensity, outcome_treated, outcome_control, valid, parameters = _cross_fitted_predictions(
        episodes,
        features,
        target,
        treatment,
        policy,
    )
    oof_fraction = float(np.mean(valid))
    diagnostics["oof_fraction"] = oof_fraction
    if oof_fraction < policy.min_oof_fraction:
        reasons.append("insufficient_time_spatial_oof_coverage")
    if reasons:
        return _abstained_result(strategy_id, reasons, diagnostics)

    oof_propensity = propensity[valid]
    oof_treatment = treatment[valid]
    oof_target = target[valid]
    oof_m1 = outcome_treated[valid]
    oof_m0 = outcome_control[valid]
    overlap = (oof_propensity >= policy.propensity_low) & (oof_propensity <= policy.propensity_high)
    treated_overlap = float(np.mean(overlap[oof_treatment == 1]))
    control_overlap = float(np.mean(overlap[oof_treatment == 0]))
    weights = np.where(
        oof_treatment == 1,
        np.mean(oof_treatment) / oof_propensity,
        (1 - np.mean(oof_treatment)) / (1 - oof_propensity),
    )
    treated_weights = weights[oof_treatment == 1]
    control_weights = weights[oof_treatment == 0]
    treated_ess = _effective_sample_size(treated_weights)
    control_ess = _effective_sample_size(control_weights)
    max_weight = float(np.max(weights))
    max_smd = _weighted_smd(features[valid], oof_treatment, weights)
    diagnostics.update(
        {
            "control_effective_sample_size": control_ess,
            "control_overlap_fraction": control_overlap,
            "max_stabilized_weight": max_weight,
            "max_weighted_smd": max_smd,
            "treated_effective_sample_size": treated_ess,
            "treated_overlap_fraction": treated_overlap,
        }
    )
    if min(treated_overlap, control_overlap) < policy.min_overlap_fraction:
        reasons.append("propensity_overlap_failed")
    if max_weight > policy.max_stabilized_weight:
        reasons.append("stabilized_weight_limit_exceeded")
    if treated_ess < max(policy.min_effective_sample_size, policy.min_effective_sample_fraction * treated_count):
        reasons.append("treated_effective_sample_size_failed")
    if control_ess < max(policy.min_effective_sample_size, policy.min_effective_sample_fraction * control_count):
        reasons.append("control_effective_sample_size_failed")
    if max_smd > policy.max_weighted_smd:
        reasons.append("weighted_balance_failed")
    if reasons:
        return _abstained_result(strategy_id, reasons, diagnostics)

    pseudo = (
        oof_m1
        - oof_m0
        + oof_treatment * (oof_target - oof_m1) / oof_propensity
        - (1 - oof_treatment) * (oof_target - oof_m0) / (1 - oof_propensity)
    )
    clusters = [
        f"{episode.cohort}|{episode.spatial_block}"
        for episode, keep in zip(episodes, valid.tolist(), strict=True)
        if keep
    ]
    aipw = _estimate("aipw", float(np.mean(pseudo)), _cluster_standard_error(pseudo, clusters))
    t_effects = oof_m1 - oof_m0
    t_learner = _estimate(
        "t_learner",
        float(np.mean(t_effects)),
        _cluster_standard_error(t_effects, clusters),
    )
    dr_model = _design_fit(features[valid], pseudo, policy.ridge_penalty)
    dr_effects = dr_model.predict(features[valid])
    dr_learner = _estimate(
        "dr_learner",
        float(np.mean(dr_effects)),
        _cluster_standard_error(dr_effects, clusters),
    )
    assert matched is not None
    estimates = (matched, aipw, dr_learner, t_learner)
    primary = (matched, aipw, dr_learner)
    directions = {math.copysign(1, estimate.effect) for estimate in primary if abs(estimate.effect) > _EPSILON}
    disagreement = max(estimate.effect for estimate in primary) - min(estimate.effect for estimate in primary)
    diagnostics["model_disagreement"] = disagreement
    if len(directions) > 1:
        reasons.append("causal_estimators_disagree_in_direction")
    if disagreement > max(policy.max_model_disagreement, bundle.outcome.smallest_meaningful_effect):
        reasons.append("causal_estimators_disagree_in_magnitude")

    conservative_benefit = aipw.interval_low
    if conservative_benefit <= bundle.outcome.smallest_meaningful_effect:
        reasons.append("conservative_effect_does_not_clear_policy")
    parameters["dr_learner"] = dr_model.payload()
    return StrategyModelResult(
        strategy_id=strategy_id,
        state="eligible" if not reasons else "abstained",
        abstention_reasons=tuple(sorted(set(reasons))),
        estimates=estimates,
        conservative_benefit=float(conservative_benefit),
        diagnostics=diagnostics,
        model_parameters=parameters,
        oof_cluster_effects=_cluster_effects(pseudo, clusters),
    )


def _aipw_effect(result: StrategyModelResult) -> float:
    estimate = next(
        (item for item in result.estimates if item.estimator == "aipw"),
        None,
    )
    return estimate.effect if estimate is not None else -math.inf


def _selection_contrast(
    best: StrategyModelResult,
    comparator: StrategyModelResult,
    policy: SelectionPolicy,
) -> SelectionContrast:
    best_effects = {item.cluster: item.effect for item in best.oof_cluster_effects}
    comparator_effects = {
        item.cluster: item.effect for item in comparator.oof_cluster_effects
    }
    paired_clusters = sorted(best_effects.keys() & comparator_effects.keys())
    differences = np.asarray(
        [best_effects[cluster] - comparator_effects[cluster] for cluster in paired_clusters],
        dtype=np.float64,
    )
    if len(differences) >= _MIN_CLUSTER_COUNT:
        effect_difference = float(np.mean(differences))
        standard_error = _cluster_standard_error(differences, paired_clusters)
        interval_low = effect_difference - _Z_95 * standard_error
        interval_high = effect_difference + _Z_95 * standard_error
    else:
        effect_difference = None
        standard_error = None
        interval_low = None
        interval_high = None
    return SelectionContrast(
        estimand="paired_oof_cluster_aipw_benefit_difference",
        best_strategy_id=best.strategy_id,
        comparator_strategy_id=comparator.strategy_id,
        paired_cluster_count=len(paired_clusters),
        effect_difference=effect_difference,
        standard_error=standard_error,
        interval_low=interval_low,
        interval_high=interval_high,
        passed=(
            len(paired_clusters) >= policy.min_paired_contrast_clusters
            and interval_low is not None
            and interval_low > 0
        ),
    )


def train_strategy_models(
    bundle: StrategyLabelBundle,
    policy: SelectionPolicy | None = None,
) -> StrategyTrainingArtifact:
    """Train comparable effect estimators and rank only when every gate passes."""
    active_policy = policy or SelectionPolicy()
    strategy_ids = sorted(
        {episode.strategy_id for episode in bundle.episodes if episode.strategy_id is not None}
    )
    if not strategy_ids:
        return StrategyTrainingArtifact(
            schema_version="strategy_training_artifact_v1",
            artifact_scope="research_evaluation_only",
            effect_claim_authorized=False,
            publication_authorized=False,
            label_checksum=bundle.label_release_checksum,
            label_bundle_checksum=bundle.checksum,
            policy_checksum=active_policy.checksum,
            outcome=bundle.outcome,
            feature_names=bundle.feature_names,
            decision_state="abstained",
            selected_strategy_id=None,
            abstention_reasons=("no_treated_strategies",),
            strategies=(),
            selection_contrast=None,
        )

    results = tuple(_train_one_strategy(bundle, strategy_id, active_policy) for strategy_id in strategy_ids)
    eligible = sorted(
        (result for result in results if result.state == "eligible"),
        key=lambda result: (_aipw_effect(result), result.strategy_id),
        reverse=True,
    )
    reasons: list[str] = []
    contrast: SelectionContrast | None = None
    if len(eligible) < _MIN_STRATEGY_CANDIDATES:
        reasons.append("fewer_than_two_eligible_strategies")
    if len(eligible) >= _MIN_STRATEGY_CANDIDATES:
        contrast = _selection_contrast(eligible[0], eligible[1], active_policy)
        if contrast.paired_cluster_count < active_policy.min_paired_contrast_clusters:
            reasons.append("insufficient_paired_strategy_contrast")
        elif not contrast.passed:
            reasons.append("paired_strategy_contrast_not_positive")
    if reasons:
        reasons.extend(
            reason
            for result in results
            if result.state == "abstained"
            for reason in result.abstention_reasons
        )
        return StrategyTrainingArtifact(
            schema_version="strategy_training_artifact_v1",
            artifact_scope="research_evaluation_only",
            effect_claim_authorized=False,
            publication_authorized=False,
            label_checksum=bundle.label_release_checksum,
            label_bundle_checksum=bundle.checksum,
            policy_checksum=active_policy.checksum,
            outcome=bundle.outcome,
            feature_names=bundle.feature_names,
            decision_state="abstained",
            selected_strategy_id=None,
            abstention_reasons=tuple(sorted(set(reasons))),
            strategies=results,
            selection_contrast=contrast,
        )
    return StrategyTrainingArtifact(
        schema_version="strategy_training_artifact_v1",
        artifact_scope="research_evaluation_only",
        effect_claim_authorized=False,
        publication_authorized=False,
        label_checksum=bundle.label_release_checksum,
        label_bundle_checksum=bundle.checksum,
        policy_checksum=active_policy.checksum,
        outcome=bundle.outcome,
        feature_names=bundle.feature_names,
        decision_state="ranked",
        selected_strategy_id=eligible[0].strategy_id,
        abstention_reasons=(),
        strategies=results,
        selection_contrast=contrast,
    )
