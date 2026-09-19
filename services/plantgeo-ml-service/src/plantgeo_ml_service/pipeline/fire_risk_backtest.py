"""Walk-forward, per-stratum evaluation of the fire-risk model against its two mandatory baselines.

Layer L3. The declared minimum lift, why the baselines are VPD-only and climatology, and why a
stratum that fails publishes refusals rather than scores live in `AGENTS-fire-risk.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import numpy as np
import polars as pl

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.method.ml.fire_risk_model import (
    DEFAULT_L2_PENALTY,
    StratumRule,
    brier_score,
    precision_recall_auc,
    predict,
    train_fire_risk_model,
)
from plantgeo_ml_service.pipeline.fire_risk_features import (
    FEATURE_SET_VERSION,
    FIRE_RISK_FEATURE_NAMES,
    SIGNAL_FEATURE_COLUMNS,
    VAPOR_PRESSURE_DEFICIT_SIGNAL,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import date, datetime

#: The single-variable screen the composite must beat. Vapour pressure deficit did most of the work
#: in the only measurement this lane has (AUC 0.697 against the composite's 0.725, in sample).
VAPOR_PRESSURE_DEFICIT_COLUMN: Final = SIGNAL_FEATURE_COLUMNS[VAPOR_PRESSURE_DEFICIT_SIGNAL]

#: The outcome column a backtest plane must carry: did the cell record a detection in the horizon
#: window. Occurrence, never severity: this lane predicts WHERE fire occurs and nothing about energy.
LABEL_COLUMN: Final = "burned"

#: How much per-stratum PR-AUC must exceed BOTH baselines before that stratum may publish a score.
#: Deliberately close to the measured in-sample margin over VPD alone (about 0.03), because a
#: composite that beats a single-variable screen by less than this is not worth a published claim.
MINIMUM_PR_AUC_LIFT: Final = 0.02

#: A calibrated model may not be WORSE calibrated than the baseline it beats on ranking.
MAXIMUM_BRIER_EXCESS: Final = 0.0

#: The seasonal half-window a cell's climatology rate is computed over, in days of the year.
CLIMATOLOGY_DAY_WINDOW: Final = 15

#: A fold needs enough of both classes for a precision-recall area to mean anything.
MINIMUM_FOLD_POSITIVES: Final = 10
MINIMUM_FOLD_ROWS: Final = 200

BACKTEST_SCHEMA_VERSION: Final = 1


class FireRiskBacktestError(RuntimeError):
    """Raised when a plane cannot be walked forward honestly: too few years, or a missing column."""


@dataclass(frozen=True, slots=True)
class FoldMetrics:
    """One held-out year of one stratum: the model and both baselines, scored on the same rows."""

    stratum: str
    held_out_year: int
    row_count: int
    positive_count: int
    model_precision_recall_auc: float
    model_brier: float
    vapor_pressure_deficit_precision_recall_auc: float
    vapor_pressure_deficit_brier: float
    climatology_precision_recall_auc: float
    climatology_brier: float

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "climatology_brier": self.climatology_brier,
            "climatology_precision_recall_auc": self.climatology_precision_recall_auc,
            "held_out_year": self.held_out_year,
            "model_brier": self.model_brier,
            "model_precision_recall_auc": self.model_precision_recall_auc,
            "positive_count": self.positive_count,
            "row_count": self.row_count,
            "stratum": self.stratum,
            "vapor_pressure_deficit_brier": self.vapor_pressure_deficit_brier,
            "vapor_pressure_deficit_precision_recall_auc": self.vapor_pressure_deficit_precision_recall_auc,
        }


@dataclass(frozen=True, slots=True)
class StratumVerdict:
    """One stratum's mean skill across its folds, and whether that clears the declared minimum lift."""

    stratum: str
    fold_count: int
    model_precision_recall_auc: float
    lift_over_vapor_pressure_deficit: float
    lift_over_climatology: float
    brier_excess_over_best_baseline: float
    cleared: bool
    basis: str

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "basis": self.basis,
            "brier_excess_over_best_baseline": self.brier_excess_over_best_baseline,
            "cleared": self.cleared,
            "fold_count": self.fold_count,
            "lift_over_climatology": self.lift_over_climatology,
            "lift_over_vapor_pressure_deficit": self.lift_over_vapor_pressure_deficit,
            "model_precision_recall_auc": self.model_precision_recall_auc,
            "stratum": self.stratum,
        }


@dataclass(frozen=True, slots=True)
class BacktestReceipt:
    """The document a published artifact must name: every fold, every verdict, the declared minima."""

    feature_set_version: str
    generated_at: datetime
    folds: tuple[FoldMetrics, ...]
    verdicts: tuple[StratumVerdict, ...]
    schema_version: int = BACKTEST_SCHEMA_VERSION

    @property
    def cleared_strata(self) -> tuple[str, ...]:
        """Return the strata this receipt permits publishing, in order."""
        return tuple(verdict.stratum for verdict in self.verdicts if verdict.cleared)

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON document, without its own digest."""
        return {
            "cleared_strata": list(self.cleared_strata),
            "declared_maximum_brier_excess": MAXIMUM_BRIER_EXCESS,
            "declared_minimum_precision_recall_auc_lift": MINIMUM_PR_AUC_LIFT,
            "feature_set_version": self.feature_set_version,
            "folds": [fold.to_wire() for fold in self.folds],
            "generated_at": self.generated_at.isoformat(),
            "schema_version": self.schema_version,
            "verdicts": [verdict.to_wire() for verdict in self.verdicts],
        }

    def to_canonical_json(self) -> str:
        """Return the receipt document plus its own digest, which is what is stored."""
        document = self.to_wire()
        document["sha256"] = self.sha256
        return canonical_json(document)

    @property
    def sha256(self) -> str:
        """Return the digest of the receipt's content, computed without the digest field itself."""
        return sha256_digest(canonical_json(self.to_wire()))


def run_walk_forward_backtest(
    plane: pl.DataFrame,
    *,
    generated_at: datetime,
    feature_names: Sequence[str] = FIRE_RISK_FEATURE_NAMES,
    feature_set_version: str = FEATURE_SET_VERSION,
    l2_penalty: float = DEFAULT_L2_PENALTY,
) -> BacktestReceipt:
    """Walk the plane forward one year at a time, scoring each stratum against both baselines."""
    _refuse_unusable_plane(plane, feature_names)
    prepared = _with_climatology_keys(plane)
    folds: list[FoldMetrics] = []
    for stratum in sorted(prepared.get_column("stratum").unique().to_list()):
        stratum_rows = prepared.filter(pl.col("stratum") == stratum)
        folds.extend(
            _stratum_folds(
                stratum_rows,
                stratum=stratum,
                feature_names=tuple(feature_names),
                feature_set_version=feature_set_version,
                l2_penalty=l2_penalty,
            )
        )
    return BacktestReceipt(
        feature_set_version=feature_set_version,
        generated_at=generated_at,
        folds=tuple(folds),
        verdicts=tuple(_verdicts(tuple(folds))),
    )


def climatology_rate(training: pl.DataFrame, evaluation: pl.DataFrame) -> np.ndarray:
    """Return each evaluation row's cell-and-season ignition rate, learned from the training years.

    The rate is the mean outcome of the same cell within a seasonal half-window of the same day of
    the year. A cell the training years never held falls back to the training set's own base rate,
    which is the honest answer for a cell with no history rather than a zero.
    """
    # `Series.mean()` is typed for every dtype the stubs know; this column is float64.
    base_rate = float(training.get_column(LABEL_COLUMN).mean() or 0.0)  # type: ignore[arg-type]
    rates: list[float] = []
    training_cells = training.select("cell_longitude", "cell_latitude", "day_of_year", LABEL_COLUMN)
    for row in evaluation.iter_rows(named=True):
        window = training_cells.filter(
            (pl.col("cell_longitude") == row["cell_longitude"])
            & (pl.col("cell_latitude") == row["cell_latitude"])
            & (_seasonal_distance(pl.col("day_of_year"), row["day_of_year"]) <= CLIMATOLOGY_DAY_WINDOW)
        )
        window_mean = float(window.get_column(LABEL_COLUMN).mean() or 0.0)  # type: ignore[arg-type]  # float64 column
        rates.append(base_rate if window.height == 0 else window_mean)
    return np.asarray(rates, dtype=float)


# --- Internals -----------------------------------------------------------------------------------


def _stratum_folds(
    stratum_rows: pl.DataFrame,
    *,
    stratum: str,
    feature_names: tuple[str, ...],
    feature_set_version: str,
    l2_penalty: float,
) -> list[FoldMetrics]:
    """Score every year of one stratum that has a usable training history behind it."""
    years = sorted(stratum_rows.get_column("year").unique().to_list())
    folds: list[FoldMetrics] = []
    for held_out_year in years[1:]:
        training = stratum_rows.filter(pl.col("year") < held_out_year)
        evaluation = stratum_rows.filter(pl.col("year") == held_out_year)
        if not _fold_is_usable(training) or not _fold_is_usable(evaluation):
            continue
        folds.append(
            _scored_fold(
                training,
                evaluation,
                stratum=stratum,
                held_out_year=int(held_out_year),
                feature_names=feature_names,
                feature_set_version=feature_set_version,
                l2_penalty=l2_penalty,
            )
        )
    return folds


def _scored_fold(  # noqa: PLR0913 - one fold is two frames plus the four things that identify it
    training: pl.DataFrame,
    evaluation: pl.DataFrame,
    *,
    stratum: str,
    held_out_year: int,
    feature_names: tuple[str, ...],
    feature_set_version: str,
    l2_penalty: float,
) -> FoldMetrics:
    """Fit the model and the VPD-only baseline on the training years, score both on the held-out one."""
    rule = (StratumRule(stratum=stratum, scoreable=True, basis="under evaluation"),)
    labels = evaluation.get_column(LABEL_COLUMN).to_numpy().astype(float)
    model_probabilities = _fitted_probabilities(
        training,
        evaluation,
        feature_names=feature_names,
        feature_set_version=feature_set_version,
        stratum_table=rule,
        l2_penalty=l2_penalty,
    )
    baseline_probabilities = _fitted_probabilities(
        training,
        evaluation,
        feature_names=(VAPOR_PRESSURE_DEFICIT_COLUMN,),
        feature_set_version=feature_set_version,
        stratum_table=rule,
        l2_penalty=l2_penalty,
    )
    climatology = climatology_rate(training, evaluation)
    return FoldMetrics(
        stratum=stratum,
        held_out_year=held_out_year,
        row_count=evaluation.height,
        positive_count=int(labels.sum()),
        model_precision_recall_auc=precision_recall_auc(model_probabilities, labels),
        model_brier=brier_score(model_probabilities, labels),
        vapor_pressure_deficit_precision_recall_auc=precision_recall_auc(baseline_probabilities, labels),
        vapor_pressure_deficit_brier=brier_score(baseline_probabilities, labels),
        climatology_precision_recall_auc=precision_recall_auc(climatology, labels),
        climatology_brier=brier_score(climatology, labels),
    )


def _fitted_probabilities(  # noqa: PLR0913 - the fit contract travels with the two frames
    training: pl.DataFrame,
    evaluation: pl.DataFrame,
    *,
    feature_names: tuple[str, ...],
    feature_set_version: str,
    stratum_table: tuple[StratumRule, ...],
    l2_penalty: float,
) -> np.ndarray:
    """Fit on the training years and return the held-out year's calibrated probabilities."""
    artifact = train_fire_risk_model(
        training.select(feature_names).to_numpy().astype(float),
        training.get_column(LABEL_COLUMN).to_numpy().astype(float),
        feature_names=feature_names,
        feature_set_version=feature_set_version,
        stratum_table=stratum_table,
        trained_on=_training_span(training),
        l2_penalty=l2_penalty,
    )
    batch = predict(
        artifact,
        evaluation.select(feature_names).to_numpy().astype(float),
        strata=evaluation.get_column("stratum").to_list(),
        feature_names=feature_names,
        feature_set_version=feature_set_version,
    )
    return np.asarray([0.0 if value is None else value for value in batch.probability], dtype=float)


def _verdicts(folds: tuple[FoldMetrics, ...]) -> list[StratumVerdict]:
    """Average each stratum's folds and decide whether it clears both declared minima."""
    verdicts: list[StratumVerdict] = []
    for stratum in sorted({fold.stratum for fold in folds}):
        stratum_folds = tuple(fold for fold in folds if fold.stratum == stratum)
        model_auc = _mean(fold.model_precision_recall_auc for fold in stratum_folds)
        vapor_lift = model_auc - _mean(fold.vapor_pressure_deficit_precision_recall_auc for fold in stratum_folds)
        climatology_lift = model_auc - _mean(fold.climatology_precision_recall_auc for fold in stratum_folds)
        brier_excess = _mean(fold.model_brier for fold in stratum_folds) - min(
            _mean(fold.vapor_pressure_deficit_brier for fold in stratum_folds),
            _mean(fold.climatology_brier for fold in stratum_folds),
        )
        cleared = (
            vapor_lift >= MINIMUM_PR_AUC_LIFT
            and climatology_lift >= MINIMUM_PR_AUC_LIFT
            and brier_excess <= MAXIMUM_BRIER_EXCESS
        )
        verdicts.append(
            StratumVerdict(
                stratum=stratum,
                fold_count=len(stratum_folds),
                model_precision_recall_auc=model_auc,
                lift_over_vapor_pressure_deficit=vapor_lift,
                lift_over_climatology=climatology_lift,
                brier_excess_over_best_baseline=brier_excess,
                cleared=cleared,
                basis=_verdict_sentence(cleared, len(stratum_folds)),
            )
        )
    return verdicts


def _verdict_sentence(cleared: bool, fold_count: int) -> str:
    """Return the one-line evidence sentence carried into the artifact's stratum table."""
    if cleared:
        return f"walk-forward over {fold_count} held-out year(s) cleared both declared minima"
    return f"walk-forward over {fold_count} held-out year(s) did not clear the declared minimum lift"


def _refuse_unusable_plane(plane: pl.DataFrame, feature_names: Sequence[str]) -> None:
    """Refuse a plane that lacks a column the walk needs, by naming the column."""
    required = {LABEL_COLUMN, "stratum", "valid_day", "cell_longitude", "cell_latitude", *feature_names}
    missing = tuple(sorted(required - set(plane.columns)))
    if missing:
        raise FireRiskBacktestError(f"the backtest plane is missing the column(s) {missing}")
    if plane.height == 0:
        raise FireRiskBacktestError("a backtest needs rows; an empty plane is a refusal, not a zero-skill verdict")


def _with_climatology_keys(plane: pl.DataFrame) -> pl.DataFrame:
    """Add the year and the day of the year the walk folds and the climatology baseline key on."""
    return plane.with_columns(
        pl.col("valid_day").dt.year().alias("year"),
        pl.col("valid_day").dt.ordinal_day().alias("day_of_year"),
    )


def _fold_is_usable(rows: pl.DataFrame) -> bool:
    """Return whether one fold holds enough rows and enough of both classes to be scored."""
    if rows.height < MINIMUM_FOLD_ROWS:
        return False
    positives = int(rows.get_column(LABEL_COLUMN).sum())
    return MINIMUM_FOLD_POSITIVES <= positives < rows.height


def _training_span(training: pl.DataFrame) -> tuple[str, str]:
    """Return the first and last day the training fold covers, as ISO text."""
    first: date = training.get_column("valid_day").min()  # type: ignore[assignment]  # a Date column yields a date
    last: date = training.get_column("valid_day").max()  # type: ignore[assignment]  # a Date column yields a date
    return first.isoformat(), last.isoformat()


def _seasonal_distance(day_of_year: pl.Expr, target: int) -> pl.Expr:
    """Return the circular distance in days between a column of days-of-year and one target day."""
    difference = (day_of_year - target).abs()
    return pl.min_horizontal(difference, 366 - difference)


def _mean(values: Iterable[float]) -> float:
    """Return the arithmetic mean of an iterable of floats."""
    materialised = list(values)
    return float(sum(materialised) / len(materialised))
