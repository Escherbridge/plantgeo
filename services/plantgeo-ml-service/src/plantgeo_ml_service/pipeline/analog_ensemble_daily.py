"""The daily Analog Ensemble lane: analogs over signal covariates, written as `layer=signal/kind=forecast`.

Layer L3, spec FR-6 with FR-4 and FR-4a. Runs `method/ml/analog_ensemble.py` unchanged, one target
signal at a time per cell, and turns its p10/p50/p90 steps into partitions at every rung of the
ladder plus one availability publication. Why a zero-analog step is refused rather than published
flat, and what each propagated observed column means on a forecast row, live in
`AGENTS-forecast-lanes.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import numpy as np
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.method.ml.analog_ensemble import (
    DEFAULT_K_NEIGHBORS,
    DEFAULT_TEMPORAL_EXCLUSION_DAYS,
    METHOD_NAME,
    AnEnForecastStep,
    AnEnHyperparameters,
    generate_anen_forecast,
)
from plantgeo_ml_service.pipeline.covariate_vectors import (
    COVARIATE_SCHEMA_VERSION,
    COVARIATE_SIGNAL_ORDER,
    MIN_COVARIATE_HISTORY_DAYS,
    CovariateMatrix,
    SignalSeriesFacts,
    build_covariate_matrices,
    read_covariate_window,
)
from plantgeo_ml_service.pipeline.forecast_lane_bootstrap import (
    ForecastLaneError,
    forecast_issue_frontier,
    forecast_source_ceiling,
    write_and_publish_forecast_rows,
    write_run_receipt,
)
from plantgeo_ml_service.warehouse.streams import SIGNAL_STREAM, stream_schema

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from plantgeo_ml_service.pipeline.availability_publisher import PointerStore, PublicationReceipt
    from plantgeo_ml_service.pipeline.object_store import ObjectStore
    from plantgeo_ml_service.pipeline.observed_reader import ObservedReader

#: Spec FR-6: horizons 1 to 30, at three published quantiles.
ANALOG_ENSEMBLE_HORIZON_DAYS: Final = 30

LOW_QUANTILE: Final = 0.1
MEDIAN_QUANTILE: Final = 0.5
HIGH_QUANTILE: Final = 0.9
PUBLISHED_QUANTILES: Final[tuple[float, ...]] = (LOW_QUANTILE, MEDIAN_QUANTILE, HIGH_QUANTILE)

#: How much observed history one issue day reads to search analogs in. Four years, so every day of
#: year has three or more seasonal neighbours outside the exclusion window.
DEFAULT_ANALOG_HISTORY_DAYS: Final = 1_461

#: Forecast rows rest on ZERO observations. The observed column is carried at its honest value
#: rather than filled with the analog count, which would read as measurement support.
FORECAST_OBSERVATION_COUNT: Final = 0

#: A horizon step whose analog search found no successor. Named on the availability row of the day
#: it would have covered, so the hole is governed rather than silent (`layer-lanes.md` section 4).
NO_ANALOGS_REASON: Final = "no_analogs"


@dataclass(frozen=True, slots=True)
class ForecastRefusal:
    """One cell-signal this run declined to forecast, and the reason it declined."""

    layer: str
    cell_id: str
    series_name: str
    reason: str
    detail: str

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection carried in the run receipt."""
        return {
            "cell_id": self.cell_id,
            "detail": self.detail,
            "layer": self.layer,
            "reason": self.reason,
            "series_name": self.series_name,
        }


@dataclass(frozen=True, slots=True)
class AnalogEnsembleRunReceipt:
    """What one issue day's Analog Ensemble run produced, and what it refused."""

    layer: str
    issued_on: date
    forecast_run_id: str
    random_seed: int
    method_name: str
    hyperparameter_sha256: str
    artifact_sha256: str | None
    written_days: tuple[date, ...]
    absent_days: tuple[date, ...]
    row_count: int
    refusals: tuple[ForecastRefusal, ...]
    publication: PublicationReceipt | None


def forecast_run_identity(*, artifact_sha256: str, issued_on: date, random_seed: int) -> str:
    """Return the run identity every row of this run carries: a digest of what determined it."""
    return sha256_digest(
        canonical_json(
            {
                "artifact_sha256": artifact_sha256,
                "issued_on": issued_on.isoformat(),
                "method_name": METHOD_NAME,
                "random_seed": random_seed,
            }
        )
    )


def default_hyperparameters() -> AnEnHyperparameters:
    """Return the pinned search knobs: a change here is a new artifact digest, not a tweak."""
    return AnEnHyperparameters(
        k_neighbors=DEFAULT_K_NEIGHBORS,
        temporal_exclusion_days=DEFAULT_TEMPORAL_EXCLUSION_DAYS,
        horizon_days=ANALOG_ENSEMBLE_HORIZON_DAYS,
    )


@dataclass(frozen=True, slots=True)
class AnalogEnsembleOptions:
    """The knobs one run may vary, so no public function here takes six positional parameters."""

    history_days: int = DEFAULT_ANALOG_HISTORY_DAYS
    signal_order: tuple[str, ...] = COVARIATE_SIGNAL_ORDER
    hyperparameters: AnEnHyperparameters | None = None
    artifact_sha256: str | None = None
    created_at: datetime | None = None

    def resolved_hyperparameters(self) -> AnEnHyperparameters:
        """Return the declared knobs, or the pinned defaults."""
        return self.hyperparameters if self.hyperparameters is not None else default_hyperparameters()

    def resolved_moment(self) -> datetime:
        """Return the instant every receipt of this run is stamped with."""
        return self.created_at if self.created_at is not None else datetime.now(tz=UTC)


def run_analog_ensemble_daily(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    reader: ObservedReader,
    *,
    issued_on: date,
    random_seed: int,
    options: AnalogEnsembleOptions | None = None,
) -> AnalogEnsembleRunReceipt:
    """Forecast every signal cell for horizons 1..30, write every rung, then publish availability."""
    settings = options if options is not None else AnalogEnsembleOptions()
    frame = read_covariate_window(
        reader,
        issued_on=issued_on,
        history_days=settings.history_days,
    )
    matrices = build_covariate_matrices(frame, issued_on=issued_on, signal_order=settings.signal_order)
    return write_analog_ensemble_run(
        store,
        pointers,
        matrices,
        issued_on=issued_on,
        random_seed=random_seed,
        options=settings,
    )


def write_analog_ensemble_run(  # noqa: PLR0913 - one keyword per run boundary is the contract
    store: ObjectStore,
    pointers: PointerStore,
    matrices: Sequence[CovariateMatrix],
    *,
    issued_on: date,
    random_seed: int,
    options: AnalogEnsembleOptions | None = None,
) -> AnalogEnsembleRunReceipt:
    """Turn built covariate matrices into published forecast partitions for one issue day."""
    settings = options if options is not None else AnalogEnsembleOptions()
    hyperparameters = settings.resolved_hyperparameters()
    moment = settings.resolved_moment()
    artifact_sha256 = settings.artifact_sha256 if settings.artifact_sha256 is not None else hyperparameters.checksum
    forecast_run_id = forecast_run_identity(
        artifact_sha256=artifact_sha256,
        issued_on=issued_on,
        random_seed=random_seed,
    )
    rows: list[dict[str, object]] = []
    refusals: list[ForecastRefusal] = []
    absences: dict[date, str] = {}
    identity = _RowIdentity(forecast_run_id=forecast_run_id, random_seed=random_seed, issued_at=moment)
    for matrix in matrices:
        for series_name in settings.signal_order:
            outcome = _forecast_one_series(
                matrix,
                series_name=series_name,
                hyperparameters=hyperparameters,
                identity=identity,
            )
            rows.extend(outcome.rows)
            if outcome.refusal is not None:
                refusals.append(outcome.refusal)
            absences.update(dict.fromkeys(outcome.unanalogged_days, NO_ANALOGS_REASON))
    return _publish_rows(
        store,
        pointers,
        rows,
        context=_RunContext(
            issued_on=issued_on,
            forecast_run_id=forecast_run_id,
            random_seed=random_seed,
            artifact_sha256=artifact_sha256,
            hyperparameters=hyperparameters,
            refusals=tuple(refusals),
            absence_reasons=absences,
            moment=moment,
        ),
    )


@dataclass(frozen=True, slots=True)
class _RowIdentity:
    """The run-level provenance values every produced row carries, and the instant it was issued."""

    forecast_run_id: str
    random_seed: int
    #: When the run was made. A forecast row's `newest_observed_at` is THIS, never the observed
    #: history's newest instant: borrowing that would make a projection look like a measurement
    #: taken at the time of the last observation (`layer-lanes.md` section 3).
    issued_at: datetime


@dataclass(frozen=True, slots=True)
class _RunContext:
    """Everything the publication step needs that is not a row."""

    issued_on: date
    forecast_run_id: str
    random_seed: int
    artifact_sha256: str
    hyperparameters: AnEnHyperparameters
    refusals: tuple[ForecastRefusal, ...]
    #: Days some series wanted to forecast and could not, with the reason. Only days that end up
    #: with NO rows at all become governed absences; a day another cell filled is published.
    absence_reasons: Mapping[date, str]
    moment: datetime


@dataclass(frozen=True, slots=True)
class _SeriesOutcome:
    """What one cell-signal produced: its rows, why it produced none, and which days it could not fill."""

    rows: tuple[dict[str, object], ...] = ()
    refusal: ForecastRefusal | None = None
    #: Valid days this series had NO analog successor for. They become governed absences with the
    #: reason NO_ANALOGS_REASON unless another series filled the same day.
    unanalogged_days: tuple[date, ...] = ()


def _forecast_one_series(
    matrix: CovariateMatrix,
    *,
    series_name: str,
    hyperparameters: AnEnHyperparameters,
    identity: _RowIdentity,
) -> _SeriesOutcome:
    """Run the analog search for one cell-signal, returning its rows or the reason it produced none."""
    facts = matrix.facts_by_signal.get(series_name)
    target_series = matrix.raw_series_by_signal.get(series_name)
    if facts is None or target_series is None:
        return _SeriesOutcome(
            refusal=_refusal(matrix, series_name, "series_absent", "the cell carries no rows for this signal")
        )
    if matrix.day_count <= hyperparameters.horizon_days:
        return _SeriesOutcome(
            refusal=_refusal(
                matrix,
                series_name,
                "insufficient_history",
                f"{matrix.day_count} complete covariate days do not exceed the "
                f"{hyperparameters.horizon_days}-day horizon",
            )
        )
    anchor_day = matrix.observed_days[-1]
    try:
        result = generate_anen_forecast(
            query_date=anchor_day,
            query_vector=matrix.query_vector(anchor_day),
            history_matrix=np.asarray(matrix.standardized_values),
            history_dates=list(matrix.observed_days),
            target_series=dict(target_series),
            hyperparams=hyperparameters,
        )
    except ValueError as error:
        return _SeriesOutcome(refusal=_refusal(matrix, series_name, "analog_search_refused", str(error)))
    # A step with no analog successor publishes NOTHING for its day rather than a flat persistence
    # value; the day it would have covered is carried out as a governed absence instead.
    unanalogged = tuple(step.valid_day for step in result.steps if step.analog_count == 0)
    produced = tuple(
        row
        for step in result.steps
        if step.analog_count > 0
        for row in _rows_for_step(
            matrix,
            facts=facts,
            step=step,
            anchor_day=anchor_day,
            identity=identity,
        )
    )
    if not produced:
        return _SeriesOutcome(
            refusal=_refusal(
                matrix,
                series_name,
                "no_analog_successors",
                "no horizon step had an analog successor, so every quantile would be a flat persistence value",
            ),
            unanalogged_days=unanalogged,
        )
    return _SeriesOutcome(rows=produced, unanalogged_days=unanalogged)


def _rows_for_step(
    matrix: CovariateMatrix,
    *,
    facts: SignalSeriesFacts,
    step: AnEnForecastStep,
    anchor_day: date,
    identity: _RowIdentity,
) -> tuple[dict[str, object], ...]:
    """Project one horizon step into one row per published quantile, in the signal forecast schema."""
    values = {
        LOW_QUANTILE: step.low_value,
        MEDIAN_QUANTILE: step.median_value,
        HIGH_QUANTILE: step.high_value,
    }
    return tuple(
        {
            "support_key": facts.support_key,
            "signal_name": facts.signal_name,
            "normalized_unit": facts.normalized_unit,
            "cell_id": matrix.cell_id,
            "observed_day": step.valid_day,
            "normalized_value": values[quantile],
            "observation_count": FORECAST_OBSERVATION_COUNT,
            # The run's own instant, never the history's: see `_RowIdentity.issued_at`.
            "newest_observed_at": identity.issued_at,
            "coverage_fraction": None,
            "allowed_client_exposure": facts.allowed_client_exposure,
            "cell_longitude": matrix.cell_longitude,
            "cell_latitude": matrix.cell_latitude,
            "forecast_run_id": identity.forecast_run_id,
            "random_seed": identity.random_seed,
            "ensemble_size": step.analog_count,
            "horizon_days": step.horizon_step,
            "issued_on": anchor_day,
            "quantile": quantile,
        }
        for quantile in PUBLISHED_QUANTILES
    )


def _refusal(matrix: CovariateMatrix, series_name: str, reason: str, detail: str) -> ForecastRefusal:
    """Build one governed refusal receipt entry; nothing here fabricates a row in its place."""
    return ForecastRefusal(
        layer=SIGNAL_STREAM,
        cell_id=matrix.cell_id,
        series_name=series_name,
        reason=reason,
        detail=detail,
    )


def _publish_rows(
    store: ObjectStore,
    pointers: PointerStore,
    rows: Sequence[Mapping[str, object]],
    *,
    context: _RunContext,
) -> AnalogEnsembleRunReceipt:
    """Write every valid day of the run at every rung, bootstrap the lane if new, then publish."""
    source_receipt = write_run_receipt(
        store,
        _run_receipt_payload(rows, context=context),
        layer=SIGNAL_STREAM,
        forecast_run_id=context.forecast_run_id,
    )
    # An all-refusal run still publishes: every day of the horizon becomes a governed absence, so a
    # run that forecast nothing is distinguishable from a run that never happened.
    base = _forecast_frame(rows)
    ceiling = forecast_source_ceiling(
        SIGNAL_STREAM,
        issued_on=context.issued_on,
        horizon_days=context.hyperparameters.horizon_days,
    )
    published = write_and_publish_forecast_rows(
        store,
        pointers,
        base,
        layer=SIGNAL_STREAM,
        run_id=context.forecast_run_id,
        issued_on=forecast_issue_frontier(SIGNAL_STREAM, issued_on=context.issued_on),
        source_receipt=source_receipt,
        source_ceiling=ceiling,
        moment=context.moment,
        absence_reasons=context.absence_reasons,
    )
    return AnalogEnsembleRunReceipt(
        layer=SIGNAL_STREAM,
        issued_on=context.issued_on,
        forecast_run_id=context.forecast_run_id,
        random_seed=context.random_seed,
        method_name=METHOD_NAME,
        hyperparameter_sha256=context.hyperparameters.checksum,
        artifact_sha256=context.artifact_sha256,
        written_days=published.written_days,
        absent_days=published.absent_days,
        row_count=published.row_count,
        refusals=context.refusals,
        publication=published.publication,
    )


def _forecast_frame(rows: Sequence[Mapping[str, object]]) -> pl.DataFrame:
    """Build the run's rows under the pinned forecast contract, so nothing is typed by inference."""
    schema = stream_schema(SIGNAL_STREAM, "forecast")
    table = pa.Table.from_pylist([dict(row) for row in rows], schema=schema.arrow_schema)
    return pl.from_arrow(table)  # type: ignore[return-value]  # a Table always yields a DataFrame


def _run_receipt_payload(rows: Sequence[Mapping[str, object]], *, context: _RunContext) -> dict[str, object]:
    """Render the immutable run receipt every availability row of this run names as its source."""
    return {
        "artifact_sha256": context.artifact_sha256,
        "covariate_schema_version": COVARIATE_SCHEMA_VERSION,
        "ensemble_method": METHOD_NAME,
        "forecast_run_id": context.forecast_run_id,
        "horizon_days": context.hyperparameters.horizon_days,
        "hyperparameter_sha256": context.hyperparameters.checksum,
        "issued_on": context.issued_on.isoformat(),
        "lane": SIGNAL_STREAM,
        "minimum_history_days": MIN_COVARIATE_HISTORY_DAYS,
        "random_seed": context.random_seed,
        "refusals": [refusal.to_wire() for refusal in context.refusals],
        "row_count": len(rows),
    }


__all__ = [
    "ANALOG_ENSEMBLE_HORIZON_DAYS",
    "DEFAULT_ANALOG_HISTORY_DAYS",
    "PUBLISHED_QUANTILES",
    "AnalogEnsembleOptions",
    "AnalogEnsembleRunReceipt",
    "ForecastLaneError",
    "ForecastRefusal",
    "default_hyperparameters",
    "forecast_run_identity",
    "run_analog_ensemble_daily",
    "write_analog_ensemble_run",
]
