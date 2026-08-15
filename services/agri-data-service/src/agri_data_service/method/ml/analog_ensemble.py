"""Analog Ensemble (AnEn) k-NN Forecaster.

Implements the Analog Ensemble method (Delle Monache et al. 2013; Junk et al. 2015):
- Neighbor search over pinned covariate vectors
- Temporal exclusion window around query origin to eliminate leakage
- Feature-weighted distance metric
- Empirical quantiles (low/p10, median/p50, high/p90) from analog successors
- Analog-space bias correction from availability-gated residuals
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

import numpy as np
from sklearn.neighbors import NearestNeighbors

from agri_data_service.foundation.canonical import canonical_json, sha256_digest, validate_finite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

METHOD_NAME: Final = "analog_ensemble_v1"
DEFAULT_K_NEIGHBORS: Final = 20
DEFAULT_TEMPORAL_EXCLUSION_DAYS: Final = 30
DEFAULT_HORIZON_DAYS: Final = 30


@dataclass(frozen=True)
class AnEnHyperparameters:
    k_neighbors: int = DEFAULT_K_NEIGHBORS
    temporal_exclusion_days: int = DEFAULT_TEMPORAL_EXCLUSION_DAYS
    horizon_days: int = DEFAULT_HORIZON_DAYS
    feature_weights: tuple[float, ...] = ()

    @property
    def checksum(self) -> str:
        return sha256_digest(canonical_json(asdict(self)))


@dataclass(frozen=True)
class AnEnForecastStep:
    horizon_step: int
    valid_day: date
    low_value: float
    median_value: float
    high_value: float
    analog_count: int
    mean_bias_correction: float


@dataclass(frozen=True)
class AnEnForecastResult:
    method_name: str
    query_origin_day: date
    hyperparameters: AnEnHyperparameters
    steps: tuple[AnEnForecastStep, ...]
    governed_data_reference: str
    artifact_checksum: str


def find_analogs(  # noqa: PLR0913 - one parameter per search knob is the contract
    query_vector: np.ndarray,
    history_matrix: np.ndarray,
    history_dates: Sequence[date],
    query_date: date,
    horizon_days: int,
    k: int = DEFAULT_K_NEIGHBORS,
    exclusion_days: int = DEFAULT_TEMPORAL_EXCLUSION_DAYS,
    feature_weights: np.ndarray | None = None,
) -> tuple[np.ndarray, tuple[date, ...]]:
    """Search for k nearest analogs in historical covariate space subject to temporal exclusion.

    `horizon_days` is a STRUCTURAL leakage guard, not an optional knob: an analog's entire
    `horizon_days`-long successor path (`analog_date + 1 .. analog_date + horizon_days`) must lie
    strictly before `query_date`, so `analog_date` itself must be strictly before
    `query_date - horizon_days`. This holds regardless of `exclusion_days` -- a caller cannot pass
    an `exclusion_days` small enough to defeat it, unlike a guard applied only by a caller (see
    `execution/analog_ensemble_model.py`'s `eligible_analog_pool`, which pre-filters its own pool
    to this same boundary and now overlaps this one rather than being the only guard). The
    symmetric `exclusion_days` window is applied ON TOP, for tie-avoidance among the already
    horizon-safe candidates -- it is the published Delle Monache et al. 2013 archive-method window
    and is preserved for that reason, not as the leakage guard itself.
    """
    if history_matrix.shape[0] == 0:
        raise ValueError("Cannot search analogs in empty history matrix")
    if horizon_days < 0:
        raise ValueError("horizon_days must not be negative")

    horizon_boundary = query_date - timedelta(days=horizon_days)
    horizon_safe_mask = np.array([d < horizon_boundary for d in history_dates], dtype=bool)
    # Apply temporal exclusion mask: exclude dates within +/- exclusion_days of query_date
    exclusion_mask = np.array([abs((d - query_date).days) > exclusion_days for d in history_dates], dtype=bool)
    valid_mask = horizon_safe_mask & exclusion_mask

    if not np.any(valid_mask):
        raise ValueError(
            f"No historical dates remain after applying the {horizon_days}-day horizon leakage "
            f"guard and the {exclusion_days}-day temporal exclusion window"
        )

    valid_indices = np.flatnonzero(valid_mask)
    valid_history = history_matrix[valid_mask]
    valid_dates = [history_dates[int(idx)] for idx in valid_indices]

    # Apply feature weighting if provided
    weights = feature_weights if feature_weights is not None else np.ones(history_matrix.shape[1])
    weighted_query = (query_vector * weights).reshape(1, -1)
    weighted_history = valid_history * weights

    n_neighbors = min(k, len(valid_dates))
    nbrs = NearestNeighbors(n_neighbors=n_neighbors, algorithm="brute", metric="euclidean")
    nbrs.fit(weighted_history)

    distances, indices = nbrs.kneighbors(weighted_query)
    selected_dates = tuple(valid_dates[i] for i in indices[0])

    return distances[0], selected_dates


def generate_anen_forecast(  # noqa: PLR0913 - one parameter per forecast input is the contract
    query_date: date,
    query_vector: np.ndarray,
    history_matrix: np.ndarray,
    history_dates: Sequence[date],
    target_series: Mapping[date, float],
    recorded_residuals: Mapping[date, float] | None = None,
    hyperparams: AnEnHyperparameters | None = None,
) -> AnEnForecastResult:
    """Generate AnEn forecast for horizon_days from query_date using k-NN analogs."""
    params = hyperparams or AnEnHyperparameters()
    weights = np.array(params.feature_weights) if params.feature_weights else None

    _distances, analog_dates = find_analogs(
        query_vector=query_vector,
        history_matrix=history_matrix,
        history_dates=history_dates,
        query_date=query_date,
        horizon_days=params.horizon_days,
        k=params.k_neighbors,
        exclusion_days=params.temporal_exclusion_days,
        feature_weights=weights,
    )

    steps: list[AnEnForecastStep] = []
    residuals_map = recorded_residuals or {}

    for step in range(1, params.horizon_days + 1):
        valid_day = query_date + timedelta(days=step)
        analog_successors: list[float] = []
        bias_terms: list[float] = []

        for analog_date in analog_dates:
            target_date = analog_date + timedelta(days=step)
            val = target_series.get(target_date)
            if val is not None and math.isfinite(val):
                analog_successors.append(val)
                res = residuals_map.get(analog_date)
                if res is not None and math.isfinite(res):
                    bias_terms.append(res)

        if not analog_successors:
            # Fallback to latest target if no analog successor exists
            fallback = target_series.get(query_date, 0.0)
            low_val, med_val, high_val = fallback, fallback, fallback
            bias_corr = 0.0
        else:
            bias_corr = float(np.mean(bias_terms)) if bias_terms else 0.0
            adjusted = np.array(analog_successors) - bias_corr
            low_val = float(np.percentile(adjusted, 10))
            med_val = float(np.percentile(adjusted, 50))
            high_val = float(np.percentile(adjusted, 90))

        steps.append(
            AnEnForecastStep(
                horizon_step=step,
                valid_day=valid_day,
                low_value=validate_finite(low_val, "low_value"),
                median_value=validate_finite(med_val, "median_value"),
                high_value=validate_finite(high_val, "high_value"),
                analog_count=len(analog_successors),
                mean_bias_correction=bias_corr,
            )
        )

    governed_ref = f"anen_history_{query_date.isoformat()}_{len(history_dates)}_rows"
    result_dict = {
        "method_name": METHOD_NAME,
        "query_origin_day": query_date.isoformat(),
        "hyperparameters": asdict(params),
        "governed_data_reference": governed_ref,
        "step_count": len(steps),
    }
    artifact_hash = sha256_digest(canonical_json(result_dict))

    return AnEnForecastResult(
        method_name=METHOD_NAME,
        query_origin_day=query_date,
        hyperparameters=params,
        steps=tuple(steps),
        governed_data_reference=governed_ref,
        artifact_checksum=artifact_hash,
    )
