"""Split-Conformal Self-Correction Calibration.

Fits a conformal margin over recorded `forecast_iteration_actual` residuals from one
origin fold, then scores before/after interval coverage on a DISJOINT held-out origin
fold -- the split is what makes this "split-conformal" rather than in-sample. Both folds
are availability-gated so no residual is used before its own actual was recorded.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final

import numpy as np

from agri_data_service.foundation.canonical import canonical_json, sha256_digest

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

DEFAULT_NOMINAL_COVERAGE: Final = 0.80


@dataclass(frozen=True)
class ResidualObservation:
    """One recorded forecast-iteration horizon value: its own nominal band, plus its actual."""

    iteration_key: str
    origin_cutoff_time: str  # ISO-8601 -- the iteration's own origin/cutoff time
    target_date: str
    predicted_low: float
    predicted_median: float
    predicted_high: float
    actual_value: float
    recorded_at: str  # ISO-8601 -- when the actual became available

    @property
    def residual(self) -> float:
        """Actual minus the iteration's own median forecast."""
        return self.actual_value - self.predicted_median

    @property
    def absolute_residual(self) -> float:
        return abs(self.residual)

    @property
    def nominal_interval_covered(self) -> bool:
        """Whether the actual fell inside the iteration's own recorded p10-p90 band."""
        return self.predicted_low <= self.actual_value <= self.predicted_high


def _availability_gated(
    observations: Sequence[ResidualObservation], *, as_of_time: datetime
) -> list[ResidualObservation]:
    """Keep only residuals whose actual was recorded at or before `as_of_time` -- the leakage gate."""
    return [
        observation
        for observation in observations
        if datetime.fromisoformat(observation.recorded_at.replace("Z", "+00:00")) <= as_of_time
    ]


def compute_conformal_margin(residuals: Sequence[float], alpha: float) -> float:
    """The finite-sample split-conformal quantile margin over |residual|, level ceil((n+1)(1-a))/n."""
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be strictly between 0 and 1")
    n = len(residuals)
    if n == 0:
        return 0.0
    absolute_residuals = np.abs(np.asarray(residuals, dtype=float))
    level = min(max(math.ceil((n + 1) * (1.0 - alpha)) / n, 0.0), 1.0)
    return float(np.quantile(absolute_residuals, level, method="higher"))


@dataclass(frozen=True)
class ConformalCalibrationResult:
    """Before/after interval coverage on a held-out fold, plus the margin fitted on a disjoint one."""

    nominal_coverage: float
    conformal_margin: float
    calibration_sample_count: int
    held_out_sample_count: int
    before_coverage: float
    after_coverage: float
    as_of_time: str
    digest: str

    def to_summary(self) -> dict[str, object]:
        """Render the result for a decision-record JSON payload."""
        return dict(asdict(self))


def _hit_rate(observations: Sequence[ResidualObservation], predicate: Callable[[ResidualObservation], bool]) -> float:
    """Share of `observations` satisfying `predicate`. Callers must not pass an empty sequence --
    `calibrate_intervals` refuses an empty fold before this is ever reached; see
    `EmptyConformalFoldError`."""
    return sum(1 for observation in observations if predicate(observation)) / len(observations)


class EmptyConformalFoldError(ValueError):
    """Raised when a calibration or held-out fold is empty after availability gating.

    A `ValueError` subclass rather than a bespoke base: `execution/analog_ensemble_cli.py`'s
    `forecast-recalibrate-ndvi` command already catches `ValueError` generically and reports it as
    a `click.ClickException`, so this refusal reaches the operator without that CLI needing to
    know this module exists. Before this guard, an empty fold silently produced margin/coverage
    0.0 with a signed digest -- indistinguishable from a genuine "0% coverage" measurement -- which
    is worse than refusing: see the inverted pinning test in tests/test_conformal_calibration.py.
    """


def calibrate_intervals(
    calibration_observations: Sequence[ResidualObservation],
    held_out_observations: Sequence[ResidualObservation],
    *,
    as_of_time: datetime,
    nominal_coverage: float = DEFAULT_NOMINAL_COVERAGE,
) -> ConformalCalibrationResult:
    """Fit a conformal margin on one origin fold; score before/after coverage on a DISJOINT fold.

    `before_coverage` reads each held-out row's OWN recorded p10-p90 band (the nominal,
    uncalibrated coverage). `after_coverage` re-scores the same held-out rows against
    `median +/- margin`, where `margin` is fit only from `calibration_observations`. A residual
    never both trains the margin and is scored by it. Both folds are independently
    availability-gated on `as_of_time`.

    Raises `EmptyConformalFoldError` when either fold is empty after gating: a margin fit from
    zero residuals, or a coverage rate scored over zero rows, is not a measurement -- it is a
    fabricated 0.0 wearing a real-looking digest. This propagates uncaught through
    `execution/conformal_recalibration.run_recalibration`, so a bad origin split reports a refusal
    rather than a report that looks like a real (if poor) result.
    """
    if not 0.0 < nominal_coverage < 1.0:
        raise ValueError("nominal_coverage must be strictly between 0 and 1")

    calibration_fold = _availability_gated(calibration_observations, as_of_time=as_of_time)
    held_out_fold = _availability_gated(held_out_observations, as_of_time=as_of_time)
    if not calibration_fold:
        raise EmptyConformalFoldError(
            f"calibration fold is empty after availability gating (0 of {len(calibration_observations)} "
            f"rows recorded at or before {as_of_time.isoformat()}); a conformal margin cannot be fit "
            "from zero residuals"
        )
    if not held_out_fold:
        raise EmptyConformalFoldError(
            f"held-out fold is empty after availability gating (0 of {len(held_out_observations)} rows "
            f"recorded at or before {as_of_time.isoformat()}); before/after coverage cannot be scored "
            "over zero rows"
        )

    alpha = 1.0 - nominal_coverage
    margin = compute_conformal_margin([observation.absolute_residual for observation in calibration_fold], alpha)

    before_coverage = _hit_rate(held_out_fold, lambda observation: observation.nominal_interval_covered)
    after_coverage = _hit_rate(held_out_fold, lambda observation: observation.absolute_residual <= margin)

    payload = {
        "as_of_time": as_of_time.isoformat(),
        "conformal_margin": margin,
        "nominal_coverage": nominal_coverage,
        "calibration_sample_count": len(calibration_fold),
        "held_out_sample_count": len(held_out_fold),
    }
    return ConformalCalibrationResult(
        nominal_coverage=nominal_coverage,
        conformal_margin=margin,
        calibration_sample_count=len(calibration_fold),
        held_out_sample_count=len(held_out_fold),
        before_coverage=before_coverage,
        after_coverage=after_coverage,
        as_of_time=as_of_time.isoformat(),
        digest=sha256_digest(canonical_json(payload)),
    )
