"""Evaluation-only rolling-origin ridge forecaster math logic.

Layer L1 (method/ml): Pure domain computation, no I/O, no SQLAlchemy.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import numpy as np

if TYPE_CHECKING:
    from datetime import date

SCHEMA_VERSION: Final = "agri_covariates_v1"
TARGET_SIGNAL_NAME: Final = "wind_speed"
DEFAULT_RIDGE_ALPHA: Final = 10.0
DEFAULT_HORIZON_COUNT: Final = 30
DEFAULT_CALIBRATION_DAYS: Final = 180
DEFAULT_ORIGIN_COUNT: Final = 1
LOWER_QUANTILE: Final = 0.10
UPPER_QUANTILE: Final = 0.90
NON_NEGATIVE_FLOOR: Final = 0.0
HORIZON_ORIGIN_OFFSET: Final = 0
PERSISTENCE_LOOKBACK_DAYS: Final = 14
MAPE_MINIMUM_DENOMINATOR: Final = 0.1


class OriginNotEvaluableError(ValueError):
    """Raised when one rolling origin cannot be scored."""


def canonical_json(payload: dict[str, object]) -> str:
    """Render a checksum input deterministically."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_digest(payload: dict[str, object]) -> str:
    """SHA-256 over canonical_json."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FeatureCoverage:
    candidate_day_count: int
    feature_complete_day_count: int
    excluded_day_count: int
    target_missing_day_count: int
    usable_day_count: int
    blocking_features: tuple[tuple[str, int], ...]
    manifest_day_count: int | None

    @property
    def feature_complete_fraction(self) -> float:
        if self.candidate_day_count == 0:
            return 0.0
        return self.feature_complete_day_count / self.candidate_day_count


@dataclass(frozen=True)
class CovariateMatrix:
    dates: tuple[date, ...]
    feature_names: tuple[str, ...]
    values: np.ndarray
    complete: np.ndarray
    manifest: dict[str, object]
    blocking_features: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RidgeModel:
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    coefficients: np.ndarray
    target_mean: float


@dataclass(frozen=True)
class ForecastEvaluation:
    evaluated_count: int
    mean_absolute_error: float
    root_mean_squared_error: float
    interval_coverage: float | None
    bias: float = 0.0
    mean_absolute_percentage_error: float | None = None


def fit_ridge(features: np.ndarray, targets: np.ndarray, *, alpha: float = DEFAULT_RIDGE_ALPHA) -> RidgeModel:
    feature_mean = features.mean(axis=0)
    feature_scale = features.std(axis=0)
    feature_scale[feature_scale == 0.0] = 1.0
    standardized = (features - feature_mean) / feature_scale
    target_mean = float(targets.mean())
    centered = targets - target_mean
    gram = standardized.T @ standardized + alpha * np.eye(standardized.shape[1])
    coefficients = np.linalg.solve(gram, standardized.T @ centered)
    return RidgeModel(feature_mean, feature_scale, coefficients, target_mean)


def predict(model: RidgeModel, features: np.ndarray) -> np.ndarray:
    standardized = (features - model.feature_mean) / model.feature_scale
    predictions: np.ndarray = np.maximum(standardized @ model.coefficients + model.target_mean, NON_NEGATIVE_FLOOR)
    return predictions


def evaluate(
    predictions: np.ndarray,
    actuals: np.ndarray,
    *,
    lower: np.ndarray | None = None,
    upper: np.ndarray | None = None,
) -> ForecastEvaluation:
    errors = predictions - actuals
    coverage: float | None = None
    if lower is not None and upper is not None:
        coverage = float(np.mean((actuals >= lower) & (actuals <= upper)))
    percentage_error: float | None = None
    if actuals.size and bool(np.all(np.abs(actuals) >= MAPE_MINIMUM_DENOMINATOR)):
        percentage_error = float(np.mean(np.abs(errors / actuals)))
    return ForecastEvaluation(
        evaluated_count=int(actuals.size),
        mean_absolute_error=float(np.mean(np.abs(errors))),
        root_mean_squared_error=float(np.sqrt(np.mean(errors**2))),
        interval_coverage=coverage,
        bias=float(np.mean(errors)) if errors.size else 0.0,
        mean_absolute_percentage_error=percentage_error,
    )
