"""Split-conformal recalibration of a finalized forecast-iteration method's bands.

Reads recorded forecast-iteration horizon values and their actuals through
`select_forecast_iteration_residuals.sql`, splits them by ORIGIN (cutoff_time) into a
calibration fold and a disjoint held-out fold, and wires the pure split-conformal module
(`agri_data_service.method.ml.conformal_calibration`) over real rows.

Read-only by construction: this module issues no INSERT/UPDATE/DELETE anywhere. A
recalibration report is evaluation evidence -- a decision record -- not a new governed
artifact, so nothing here writes a receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.method.ml.conformal_calibration import (
    DEFAULT_NOMINAL_COVERAGE,
    ConformalCalibrationResult,
    ResidualObservation,
    calibrate_intervals,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_RESIDUALS = text(load_query_sql("execution/select_forecast_iteration_residuals.sql"))

MODULE_NAME: Final = "conformal_recalibration"


async def load_residual_observations(
    session: AsyncSession,
    *,
    method: str,
    as_of_time: datetime,
    series_id: str | None = None,
) -> tuple[ResidualObservation, ...]:
    """Read every admissible (iteration, horizon-step) residual row for `method`, availability-gated."""
    await apply_statement_timeout(session)
    rows = (
        await session.execute(
            _SELECT_RESIDUALS,
            {"method": method, "series_id": series_id, "as_of_time": as_of_time},
        )
    ).mappings()
    return tuple(
        ResidualObservation(
            iteration_key=str(row["iteration_key"]),
            origin_cutoff_time=row["cutoff_time"].astimezone(UTC).isoformat(),
            target_date=row["valid_time"].astimezone(UTC).isoformat(),
            predicted_low=float(row["low_value"]),
            predicted_median=float(row["median_value"]),
            predicted_high=float(row["high_value"]),
            actual_value=float(row["actual_value"]),
            recorded_at=row["actual_recorded_at"].astimezone(UTC).isoformat(),
        )
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class RecalibrationSplit:
    """The origin (cutoff_time) boundary separating the calibration fold from the held-out fold."""

    calibration_cutoff_before: datetime  # exclusive upper bound of the calibration fold
    held_out_cutoff_at_or_after: datetime  # inclusive lower bound of the held-out fold

    def __post_init__(self) -> None:
        """Refuse a split whose held-out fold could start before its calibration fold ends."""
        if self.held_out_cutoff_at_or_after < self.calibration_cutoff_before:
            raise ValueError("held_out_cutoff_at_or_after must not precede calibration_cutoff_before")


def split_by_origin(
    observations: Sequence[ResidualObservation], split: RecalibrationSplit
) -> tuple[tuple[ResidualObservation, ...], tuple[ResidualObservation, ...]]:
    """Partition residuals by their own origin (cutoff_time), never by individual residual row.

    Splitting by origin -- not by row -- is what keeps every horizon step of one iteration in a
    single fold; letting two horizon steps of the SAME origin land in different folds would let
    the calibration fold "see" part of a held-out origin's own forecast. A row whose origin falls
    strictly between the two bounds (a deliberate buffer, when the caller sets one) belongs to
    neither fold rather than defaulting into one.
    """
    calibration: list[ResidualObservation] = []
    held_out: list[ResidualObservation] = []
    for observation in observations:
        cutoff = datetime.fromisoformat(observation.origin_cutoff_time)
        if cutoff < split.calibration_cutoff_before:
            calibration.append(observation)
        elif cutoff >= split.held_out_cutoff_at_or_after:
            held_out.append(observation)
    return tuple(calibration), tuple(held_out)


@dataclass(frozen=True, slots=True)
class RecalibrationReport:
    """One method's before/after conformal recalibration, over real recorded residuals."""

    method: str
    series_id: str | None
    as_of_time: str
    split: RecalibrationSplit
    result: ConformalCalibrationResult
    distinct_calibration_origin_count: int
    distinct_held_out_origin_count: int

    def to_summary(self) -> dict[str, object]:
        """Render the decision record: the split, the sample sizes, and the calibration result."""
        return {
            "method": self.method,
            "series_id": self.series_id,
            "as_of_time": self.as_of_time,
            "calibration_cutoff_before": self.split.calibration_cutoff_before.isoformat(),
            "held_out_cutoff_at_or_after": self.split.held_out_cutoff_at_or_after.isoformat(),
            "distinct_calibration_origin_count": self.distinct_calibration_origin_count,
            "distinct_held_out_origin_count": self.distinct_held_out_origin_count,
            **self.result.to_summary(),
        }


async def run_recalibration(  # noqa: PLR0913 - keyword-only recalibration knobs are the contract
    session: AsyncSession,
    *,
    method: str,
    split: RecalibrationSplit,
    as_of_time: datetime,
    series_id: str | None = None,
    nominal_coverage: float = DEFAULT_NOMINAL_COVERAGE,
) -> RecalibrationReport:
    """Read, split by origin, and calibrate. Read-only: the caller need not roll back anything.

    Propagates `conformal_calibration.EmptyConformalFoldError` uncaught when `split` leaves either
    fold empty (e.g. a boundary past the newest recorded origin) -- there is no guard here because
    `calibrate_intervals` already refuses rather than reporting a fabricated 0.0 coverage; see that
    function's docstring.
    """
    observations = await load_residual_observations(session, method=method, as_of_time=as_of_time, series_id=series_id)
    calibration_fold, held_out_fold = split_by_origin(observations, split)
    result = calibrate_intervals(
        calibration_fold, held_out_fold, as_of_time=as_of_time, nominal_coverage=nominal_coverage
    )
    return RecalibrationReport(
        method=method,
        series_id=series_id,
        as_of_time=as_of_time.isoformat(),
        split=split,
        result=result,
        distinct_calibration_origin_count=len({observation.origin_cutoff_time for observation in calibration_fold}),
        distinct_held_out_origin_count=len({observation.origin_cutoff_time for observation in held_out_fold}),
    )
