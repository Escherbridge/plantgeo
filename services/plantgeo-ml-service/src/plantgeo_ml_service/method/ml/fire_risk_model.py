"""L2-penalised logistic fire-risk occurrence model, its calibration, and its canonical-JSON artifact.

Layer L1: numpy and `foundation` only. What the model claims, why a missing artifact refuses every
row instead of scoring zero, and why the stratum table travels inside the artifact live in
`../AGENTS.md` and in `pipeline/AGENTS-fire-risk.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import numpy as np

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: The artifact document's own version. A reader that does not know this number refuses the artifact.
FIRE_RISK_ARTIFACT_SCHEMA_VERSION: Final = 1

#: A calibrated probability is reported on 0..100 so a map legend never shows a bare probability.
RISK_SCORE_SCALE: Final = 100.0

#: Why one row carries no score. A refused row is never a zero: a fabricated zero reads as
#: "no risk here", which is the one false claim this lane exists to avoid.
RefusedReason = Literal[
    "artifact_missing",
    "out_of_stratum",
    "feature_missing",
    "feature_set_mismatch",
    "backtest_lift_not_cleared",
]

ARTIFACT_MISSING: Final[RefusedReason] = "artifact_missing"
OUT_OF_STRATUM: Final[RefusedReason] = "out_of_stratum"
FEATURE_MISSING: Final[RefusedReason] = "feature_missing"
FEATURE_SET_MISMATCH: Final[RefusedReason] = "feature_set_mismatch"
BACKTEST_LIFT_NOT_CLEARED: Final[RefusedReason] = "backtest_lift_not_cleared"

#: A column whose training spread is below this is standardised against 1.0 rather than its own
#: deviation, so a constant column contributes nothing instead of dividing by nearly zero.
MINIMUM_STANDARD_DEVIATION: Final = 1e-9

DEFAULT_L2_PENALTY: Final = 1.0
DEFAULT_MAX_ITERATIONS: Final = 50
DEFAULT_CONVERGENCE_TOLERANCE: Final = 1e-8

#: Histogram width for the calibration bins fitted by pool-adjacent-violators.
DEFAULT_CALIBRATION_BIN_COUNT: Final = 20

#: A training feature matrix is one row per sample, one column per named feature: exactly rank 2.
FEATURE_MATRIX_NDIM: Final = 2

#: Probabilities are clipped before a log or a division, so a saturated fit stays finite.
_PROBABILITY_FLOOR: Final = 1e-12


class FireRiskModelError(ValueError):
    """Raised when a fit cannot be performed, or an artifact document cannot be admitted as written."""


@dataclass(frozen=True, slots=True)
class StratumRule:
    """Whether one stratum may be scored at all, and the evidence sentence behind that decision."""

    stratum: str
    scoreable: bool
    basis: str

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {"basis": self.basis, "scoreable": self.scoreable, "stratum": self.stratum}


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One monotone calibration step: raw scores at or below `upper_bound` map to `probability`."""

    upper_bound: float
    probability: float

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {"probability": self.probability, "upper_bound": self.upper_bound}


@dataclass(frozen=True, slots=True)
class BacktestReference:
    """The receipt object a published artifact must name, and the strata that receipt cleared."""

    key: str
    sha256: str
    cleared_strata: tuple[str, ...]

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {"cleared_strata": list(self.cleared_strata), "key": self.key, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class FireRiskArtifact:
    """Everything needed to score a feature row, and nothing that needs an estimator object."""

    feature_set_version: str
    feature_names: tuple[str, ...]
    coefficients: tuple[float, ...]
    intercept: float
    feature_means: tuple[float, ...]
    feature_standard_deviations: tuple[float, ...]
    stratum_table: tuple[StratumRule, ...]
    calibration_bins: tuple[CalibrationBin, ...]
    trained_on_first_day: str
    trained_on_last_day: str
    l2_penalty: float
    training_row_count: int
    training_positive_count: int
    backtest: BacktestReference | None = None
    schema_version: int = FIRE_RISK_ARTIFACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        widths = {len(self.feature_names), len(self.coefficients), len(self.feature_means)}
        widths.add(len(self.feature_standard_deviations))
        if len(widths) != 1 or not self.feature_names:
            raise FireRiskModelError("an artifact's names, coefficients and moments must all be the same width")
        if not self.stratum_table:
            raise FireRiskModelError("an artifact without a stratum table could not refuse anything")

    @property
    def scoreable_strata(self) -> frozenset[str]:
        """Return the strata this artifact claims skill in."""
        return frozenset(rule.stratum for rule in self.stratum_table if rule.scoreable)

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON document, without its own digest."""
        return {
            "backtest": self.backtest.to_wire() if self.backtest is not None else None,
            "calibration_bins": [calibration_bin.to_wire() for calibration_bin in self.calibration_bins],
            "coefficients": list(self.coefficients),
            "feature_means": list(self.feature_means),
            "feature_names": list(self.feature_names),
            "feature_set_version": self.feature_set_version,
            "feature_standard_deviations": list(self.feature_standard_deviations),
            "intercept": self.intercept,
            "l2_penalty": self.l2_penalty,
            "schema_version": self.schema_version,
            "stratum_table": [rule.to_wire() for rule in self.stratum_table],
            "trained_on_first_day": self.trained_on_first_day,
            "trained_on_last_day": self.trained_on_last_day,
            "training_positive_count": self.training_positive_count,
            "training_row_count": self.training_row_count,
        }

    def to_canonical_json(self) -> str:
        """Return the artifact document plus its own digest, which is what is stored."""
        document = self.to_wire()
        document["sha256"] = self.sha256
        return canonical_json(document)

    @property
    def sha256(self) -> str:
        """Return the digest of the artifact's content, computed without the digest field itself."""
        return sha256_digest(canonical_json(self.to_wire()))


def artifact_from_mapping(document: Mapping[str, Any]) -> FireRiskArtifact:
    """Decode one stored artifact document, refusing a version or a digest this reader cannot honour."""
    version = document.get("schema_version")
    if version != FIRE_RISK_ARTIFACT_SCHEMA_VERSION:
        raise FireRiskModelError(
            f"artifact schema version {version!r} is not {FIRE_RISK_ARTIFACT_SCHEMA_VERSION}; an artifact "
            "written under another version is refused rather than read as this shape"
        )
    backtest = document.get("backtest")
    artifact = FireRiskArtifact(
        feature_set_version=str(document["feature_set_version"]),
        feature_names=tuple(str(name) for name in document["feature_names"]),
        coefficients=tuple(float(value) for value in document["coefficients"]),
        intercept=float(document["intercept"]),
        feature_means=tuple(float(value) for value in document["feature_means"]),
        feature_standard_deviations=tuple(float(value) for value in document["feature_standard_deviations"]),
        stratum_table=tuple(
            StratumRule(stratum=str(rule["stratum"]), scoreable=bool(rule["scoreable"]), basis=str(rule["basis"]))
            for rule in document["stratum_table"]
        ),
        calibration_bins=tuple(
            CalibrationBin(upper_bound=float(step["upper_bound"]), probability=float(step["probability"]))
            for step in document["calibration_bins"]
        ),
        trained_on_first_day=str(document["trained_on_first_day"]),
        trained_on_last_day=str(document["trained_on_last_day"]),
        l2_penalty=float(document["l2_penalty"]),
        training_row_count=int(document["training_row_count"]),
        training_positive_count=int(document["training_positive_count"]),
        backtest=_backtest_from_mapping(backtest),
    )
    stored_digest = document.get("sha256")
    if stored_digest is not None and stored_digest != artifact.sha256:
        raise FireRiskModelError("the stored artifact digest does not match the document it was read from")
    return artifact


@dataclass(frozen=True, slots=True)
class FireRiskPredictionBatch:
    """One scored batch: a probability or a refusal for every row handed in, in the same order."""

    probability: tuple[float | None, ...]
    risk_score: tuple[float | None, ...]
    stratum: tuple[str, ...]
    refused_reason: tuple[RefusedReason | None, ...]
    artifact_sha256: str

    @property
    def scored_count(self) -> int:
        """Return how many rows carry a probability."""
        return sum(1 for value in self.probability if value is not None)


def predict(  # noqa: PLR0913 - the artifact, the matrix and the three contract fields are one call
    artifact: FireRiskArtifact | None,
    feature_matrix: np.ndarray,
    *,
    strata: Sequence[str],
    feature_names: Sequence[str],
    feature_set_version: str,
    withheld_strata: frozenset[str] = frozenset(),
) -> FireRiskPredictionBatch:
    """Score one feature matrix, refusing by name wherever a row or this artifact cannot be scored."""
    row_count = len(strata)
    if artifact is None:
        return _refused_batch(strata, ARTIFACT_MISSING, artifact_sha256="")
    if tuple(feature_names) != artifact.feature_names or feature_set_version != artifact.feature_set_version:
        return _refused_batch(strata, FEATURE_SET_MISMATCH, artifact_sha256=artifact.sha256)
    if feature_matrix.shape != (row_count, len(artifact.feature_names)):
        raise FireRiskModelError(
            f"a feature matrix of {feature_matrix.shape} does not match {row_count} rows of "
            f"{len(artifact.feature_names)} named features"
        )
    raw = _raw_probabilities(artifact, feature_matrix)
    calibrated = _calibrated(artifact, raw)
    finite_rows = np.all(np.isfinite(feature_matrix), axis=1)
    probabilities: list[float | None] = []
    scores: list[float | None] = []
    reasons: list[RefusedReason | None] = []
    for index, stratum in enumerate(strata):
        reason = _row_refusal(
            stratum, finite=bool(finite_rows[index]), artifact=artifact, withheld_strata=withheld_strata
        )
        reasons.append(reason)
        probabilities.append(None if reason is not None else float(calibrated[index]))
        scores.append(None if reason is not None else float(calibrated[index]) * RISK_SCORE_SCALE)
    return FireRiskPredictionBatch(
        probability=tuple(probabilities),
        risk_score=tuple(scores),
        stratum=tuple(strata),
        refused_reason=tuple(reasons),
        artifact_sha256=artifact.sha256,
    )


def train_fire_risk_model(  # noqa: PLR0913 - a fit declares its own contract, it does not read one
    feature_matrix: np.ndarray,
    labels: np.ndarray,
    *,
    feature_names: Sequence[str],
    feature_set_version: str,
    stratum_table: Sequence[StratumRule],
    trained_on: tuple[str, str],
    l2_penalty: float = DEFAULT_L2_PENALTY,
    calibration_bin_count: int = DEFAULT_CALIBRATION_BIN_COUNT,
) -> FireRiskArtifact:
    """Fit the standardised ridge logistic model and its calibration, returning the artifact."""
    design, labels_vector = _validated_training_inputs(feature_matrix, labels, feature_names)
    means = design.mean(axis=0)
    deviations = np.where(design.std(axis=0) < MINIMUM_STANDARD_DEVIATION, 1.0, design.std(axis=0))
    standardised = (design - means) / deviations
    coefficients, intercept = _fit_ridge_logistic(standardised, labels_vector, l2_penalty=l2_penalty)
    raw = _sigmoid(standardised @ coefficients + intercept)
    bins = fit_calibration_bins(raw, labels_vector, bin_count=calibration_bin_count)
    return FireRiskArtifact(
        feature_set_version=feature_set_version,
        feature_names=tuple(feature_names),
        coefficients=tuple(float(value) for value in coefficients),
        intercept=float(intercept),
        feature_means=tuple(float(value) for value in means),
        feature_standard_deviations=tuple(float(value) for value in deviations),
        stratum_table=tuple(stratum_table),
        calibration_bins=bins,
        trained_on_first_day=trained_on[0],
        trained_on_last_day=trained_on[1],
        l2_penalty=float(l2_penalty),
        training_row_count=int(design.shape[0]),
        training_positive_count=int(labels_vector.sum()),
    )


def fit_calibration_bins(
    raw_probabilities: np.ndarray, labels: np.ndarray, *, bin_count: int = DEFAULT_CALIBRATION_BIN_COUNT
) -> tuple[CalibrationBin, ...]:
    """Fit monotone histogram calibration: equal-width bins, then pool adjacent violators."""
    if bin_count < 1:
        raise FireRiskModelError("calibration needs at least one bin")
    edges = np.linspace(0.0, 1.0, bin_count + 1)
    assignments = np.clip(np.digitize(raw_probabilities, edges[1:-1], right=True), 0, bin_count - 1)
    observed: list[float] = []
    weights: list[float] = []
    upper_bounds: list[float] = []
    for index in range(bin_count):
        members = labels[assignments == index]
        if members.size == 0:
            continue
        observed.append(float(members.mean()))
        weights.append(float(members.size))
        upper_bounds.append(float(edges[index + 1]))
    if not observed:
        return (CalibrationBin(upper_bound=1.0, probability=0.0),)
    pooled = _pool_adjacent_violators(np.asarray(observed), np.asarray(weights))
    steps = [
        CalibrationBin(upper_bound=bound, probability=float(value))
        for bound, value in zip(upper_bounds, pooled, strict=True)
    ]
    steps[-1] = CalibrationBin(upper_bound=1.0, probability=steps[-1].probability)
    return tuple(steps)


def calibrate(artifact: FireRiskArtifact, raw_probability: float) -> float:
    """Map one raw probability through the artifact's monotone bins."""
    for step in artifact.calibration_bins:
        if raw_probability <= step.upper_bound:
            return step.probability
    return artifact.calibration_bins[-1].probability


def brier_score(probabilities: np.ndarray, labels: np.ndarray) -> float:
    """Return the mean squared error of a probabilistic forecast."""
    return float(np.mean((np.asarray(probabilities, dtype=float) - np.asarray(labels, dtype=float)) ** 2))


def precision_recall_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Return the area under the precision-recall curve by the step (interpolation-free) rule."""
    score_values = np.asarray(scores, dtype=float)
    label_values = np.asarray(labels, dtype=float)
    positives = float(label_values.sum())
    if positives == 0.0 or positives == float(label_values.size):
        raise FireRiskModelError("precision-recall area needs both a positive and a negative row")
    order = np.lexsort((np.arange(score_values.size), -score_values))
    ordered_labels = label_values[order]
    true_positives = np.cumsum(ordered_labels)
    predicted = np.arange(1, ordered_labels.size + 1, dtype=float)
    precision = true_positives / predicted
    recall = true_positives / positives
    recall_steps = np.diff(np.concatenate(([0.0], recall)))
    return float(np.sum(precision * recall_steps))


# --- Internals -----------------------------------------------------------------------------------


def _backtest_from_mapping(document: object) -> BacktestReference | None:
    """Decode the optional backtest reference, refusing a shape that is present but unusable."""
    if document is None:
        return None
    if not isinstance(document, dict):
        raise FireRiskModelError("an artifact's backtest reference must be an object or absent")
    return BacktestReference(
        key=str(document["key"]),
        sha256=str(document["sha256"]),
        cleared_strata=tuple(str(name) for name in document["cleared_strata"]),
    )


def _refused_batch(strata: Sequence[str], reason: RefusedReason, *, artifact_sha256: str) -> FireRiskPredictionBatch:
    """Return one batch where every row carries the same refusal, and none carries a score."""
    return FireRiskPredictionBatch(
        probability=tuple(None for _row in strata),
        risk_score=tuple(None for _row in strata),
        stratum=tuple(strata),
        refused_reason=tuple(reason for _row in strata),
        artifact_sha256=artifact_sha256,
    )


def _row_refusal(
    stratum: str, *, finite: bool, artifact: FireRiskArtifact, withheld_strata: frozenset[str]
) -> RefusedReason | None:
    """Return why one row may not be scored, or None when it may."""
    if stratum in withheld_strata:
        return BACKTEST_LIFT_NOT_CLEARED
    if stratum not in artifact.scoreable_strata:
        return OUT_OF_STRATUM
    if not finite:
        return FEATURE_MISSING
    return None


def _raw_probabilities(artifact: FireRiskArtifact, feature_matrix: np.ndarray) -> np.ndarray:
    """Standardise with the artifact's own moments and apply the fitted linear predictor."""
    means = np.asarray(artifact.feature_means, dtype=float)
    deviations = np.asarray(artifact.feature_standard_deviations, dtype=float)
    coefficients = np.asarray(artifact.coefficients, dtype=float)
    filled = np.nan_to_num(np.asarray(feature_matrix, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    standardised = (filled - means) / deviations
    return _sigmoid(standardised @ coefficients + artifact.intercept)


def _calibrated(artifact: FireRiskArtifact, raw: np.ndarray) -> np.ndarray:
    """Apply the artifact's monotone bins to every raw probability."""
    return np.asarray([calibrate(artifact, float(value)) for value in raw], dtype=float)


def _validated_training_inputs(
    feature_matrix: np.ndarray, labels: np.ndarray, feature_names: Sequence[str]
) -> tuple[np.ndarray, np.ndarray]:
    """Refuse a training set that cannot produce an honest fit, by naming which property failed."""
    design = np.asarray(feature_matrix, dtype=float)
    labels_vector = np.asarray(labels, dtype=float)
    if design.ndim != FEATURE_MATRIX_NDIM or design.shape[1] != len(feature_names):
        raise FireRiskModelError("the training matrix must be two-dimensional and one column per named feature")
    if design.shape[0] != labels_vector.size:
        raise FireRiskModelError("the training matrix and the label vector disagree on the row count")
    if not np.all(np.isfinite(design)):
        raise FireRiskModelError("the training matrix carries a non-finite value; impute upstream or drop the row")
    positives = float(labels_vector.sum())
    if positives == 0.0 or positives == float(labels_vector.size):
        raise FireRiskModelError("a fire-occurrence fit needs both burned and unburned rows")
    return design, labels_vector


def _fit_ridge_logistic(
    standardised: np.ndarray,
    labels: np.ndarray,
    *,
    l2_penalty: float,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    tolerance: float = DEFAULT_CONVERGENCE_TOLERANCE,
) -> tuple[np.ndarray, float]:
    """Iteratively reweighted least squares with an L2 penalty that never touches the intercept."""
    if l2_penalty <= 0.0:
        raise FireRiskModelError("the L2 penalty is what keeps the Hessian invertible; it must be positive")
    design = np.column_stack([np.ones(standardised.shape[0]), standardised])
    penalty = np.eye(design.shape[1]) * (2.0 * l2_penalty)
    penalty[0, 0] = 0.0
    parameters = np.zeros(design.shape[1])
    for _iteration in range(max_iterations):
        probabilities = _sigmoid(design @ parameters)
        weights = np.clip(probabilities * (1.0 - probabilities), _PROBABILITY_FLOOR, None)
        gradient = design.T @ (labels - probabilities) - penalty @ parameters
        hessian = design.T @ (design * weights[:, None]) + penalty
        step = np.linalg.solve(hessian, gradient)
        parameters = parameters + step
        if float(np.max(np.abs(step))) < tolerance:
            break
    return parameters[1:], float(parameters[0])


def _pool_adjacent_violators(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Return the weighted isotonic (non-decreasing) fit of `values`. Pool adjacent violators."""
    pooled_values = list(values.astype(float))
    pooled_weights = list(weights.astype(float))
    block_sizes = [1] * len(pooled_values)
    index = 0
    while index < len(pooled_values) - 1:
        if pooled_values[index] <= pooled_values[index + 1]:
            index += 1
            continue
        total_weight = pooled_weights[index] + pooled_weights[index + 1]
        merged = pooled_values[index] * pooled_weights[index] + pooled_values[index + 1] * pooled_weights[index + 1]
        pooled_values[index] = merged / total_weight
        pooled_weights[index] = total_weight
        block_sizes[index] += block_sizes[index + 1]
        del pooled_values[index + 1], pooled_weights[index + 1], block_sizes[index + 1]
        index = max(index - 1, 0)
    expanded: list[float] = []
    for value, size in zip(pooled_values, block_sizes, strict=True):
        expanded.extend([value] * size)
    return np.asarray(expanded, dtype=float)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    """Return the numerically stable logistic transform of a linear predictor."""
    clipped = np.clip(values, -500.0, 500.0)
    # numpy's stubs resolve this arithmetic to `Any`; the runtime type is always an ndarray.
    return cast("np.ndarray", 1.0 / (1.0 + np.exp(-clipped)))
