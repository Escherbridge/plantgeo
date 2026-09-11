"""Transaction-backed evaluation-only forecast workflows.

See ``execution/AGENTS.md`` section "Forecast command workflows".
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.vegetation_ndvi_forecast import (
    METHOD_NAME as VEGETATION_METHOD_NAME,
)
from agri_data_service.execution.vegetation_ndvi_forecast import (
    PURPOSE_HOLDOUT_EVALUATION,
    SimulationRequest,
)
from agri_data_service.execution.vegetation_ndvi_plane import (
    ErrorMetrics,
    HoldoutEvaluation,
    IterationOutcome,
    RegistrationSummary,
    all_requested_cells_materialised,
    load_governed_history,
    load_governed_plane,
    load_license_snapshots,
    load_outcome_rows,
    load_series_identities,
    pin_determinism,
    reconcile_actuals,
    register_governed_plane,
    release_holds_claimed_corpus,
    select_candidate_cell_keys,
    simulate_cells,
    summarize_holdout,
)

if TYPE_CHECKING:
    import uuid

    from agri_data_service.execution.vegetation_ndvi_forecast import SeasonalHistory

SessionScopeFactory = Callable[[str], AbstractAsyncContextManager[AsyncSession]]

_MATERIALIZE_FORECAST_ITERATION = text(load_query_sql("execution/materialize_forecast_iteration.sql"))
_FORECAST_ITERATION_SUMMARY = text(load_query_sql("execution/forecast_iteration_summary.sql"))
_RECONCILE_FORECAST_ITERATION_ACTUALS_COMMAND = text(
    load_query_sql("execution/reconcile_forecast_iteration_actuals_command.sql")
)
_FORECAST_ITERATION_OUTCOME_TOTALS = text(load_query_sql("execution/forecast_iteration_outcome_totals.sql"))

VEGETATION_HORIZON_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("horizon_1_to_7_days", 1, 7),
    ("horizon_8_to_14_days", 8, 14),
    ("horizon_15_to_30_days", 15, 30),
)


async def refresh_ml_daily(database_url: str, session_factory: SessionScopeFactory) -> int:
    """Refresh the serving materialization and return its resulting row count."""
    async with session_factory(database_url) as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        await session.execute(text("SELECT agri.refresh_forecast_ml_daily_serving()"))
        result = await session.execute(text("SELECT count(*) FROM agri.mv_forecast_ml_daily_serving"))
        return int(result.scalar_one())


async def run_forecast_iteration(  # noqa: PLR0913
    *,
    database_url: str,
    session_factory: SessionScopeFactory,
    iteration_key: str,
    series_id: uuid.UUID,
    release_set_id: uuid.UUID,
    as_of_time: datetime,
    cutoff_time: datetime,
    history_start: datetime | None,
    horizon_days: int,
    simulation_count: int,
    seed: int,
    gap_policy: str,
    lower_bound: float | None,
    upper_bound: float | None,
) -> dict[str, Any]:
    """Materialize one deterministic evaluation iteration and return its receipt."""
    parameters = {
        "iteration_key": iteration_key,
        "series_id": series_id,
        "release_set_id": release_set_id,
        "as_of_time": as_of_time,
        "cutoff_time": cutoff_time,
        "history_start": history_start,
        "horizon_days": horizon_days,
        "simulation_count": simulation_count,
        "seed": seed,
        "gap_policy": gap_policy,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
    }
    async with session_factory(database_url) as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        call_result = await session.execute(_MATERIALIZE_FORECAST_ITERATION, parameters)
        iteration_id = call_result.scalar_one()
        summary_result = await session.execute(_FORECAST_ITERATION_SUMMARY, {"iteration_id": iteration_id})
        row = summary_result.mappings().one()
    return {
        "availability_mode": row["availability_mode"],
        "cutoff_time": row["cutoff_time"].isoformat(),
        "gap_policy": row["gap_policy"],
        "horizon_days": row["horizon_days"],
        "increment_count": row["increment_count"],
        "iteration_id": str(row["id"]),
        "iteration_key": row["iteration_key"],
        "method": row["method"],
        "purpose": row["purpose"],
        "receipt_checksum": row["receipt_checksum"],
        "recorded_at": row["recorded_at"].isoformat(),
        "release_set_id": str(row["release_set_id"]),
        "series_id": str(row["series_id"]),
        "simulation_count": row["simulation_count"],
        "simulation_seed": row["simulation_seed"],
        "state": row["status"],
        "training_day_count": row["training_day_count"],
        "value_count": row["value_count"],
    }


async def reconcile_forecast_actuals(
    *,
    database_url: str,
    session_factory: SessionScopeFactory,
    iteration_id: uuid.UUID,
    actual_release_set_id: uuid.UUID,
    as_of_time: datetime,
) -> dict[str, Any]:
    """Append governed actuals and return the iteration's updated outcome totals."""
    async with session_factory(database_url) as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        call_result = await session.execute(
            _RECONCILE_FORECAST_ITERATION_ACTUALS_COMMAND,
            {
                "iteration_id": iteration_id,
                "actual_release_set_id": actual_release_set_id,
                "as_of_time": as_of_time,
            },
        )
        inserted_count = int(call_result.scalar_one())
        result = await session.execute(_FORECAST_ITERATION_OUTCOME_TOTALS, {"iteration_id": iteration_id})
        row = result.mappings().one()
    return {
        "actual_count": row["actual_count"],
        "actual_release_set_id": str(actual_release_set_id),
        "as_of_time": as_of_time.isoformat(),
        "inserted_count": inserted_count,
        "interval_coverage": (float(row["interval_coverage"]) if row["interval_coverage"] is not None else None),
        "iteration_id": str(iteration_id),
        "mean_absolute_error": (float(row["mean_absolute_error"]) if row["mean_absolute_error"] is not None else None),
        "forecast_value_count": row["forecast_value_count"],
    }


def _resolved_as_of_time(as_of_time: datetime | None, cutoff_day: date) -> datetime:
    resolved = as_of_time if as_of_time is not None else datetime.now(tz=UTC)
    if resolved > datetime.now(tz=UTC):
        raise ValueError("as-of boundary cannot be in the future")
    if resolved < datetime.combine(cutoff_day, datetime.min.time(), tzinfo=UTC):
        raise ValueError("as-of boundary cannot precede the cutoff day")
    return resolved


def _error_metrics_payload(metrics: ErrorMetrics) -> dict[str, Any]:
    return {
        "label": metrics.label,
        "point_count": metrics.point_count,
        "mae": (round(metrics.mean_absolute_error, 6) if metrics.point_count else None),
        "rmse": (round(metrics.root_mean_squared_error, 6) if metrics.point_count else None),
        "bias": (round(metrics.bias, 6) if metrics.point_count else None),
    }


def _skill_score(method: ErrorMetrics, baseline: ErrorMetrics) -> float | None:
    if not method.point_count or not baseline.point_count or baseline.root_mean_squared_error == 0.0:
        return None
    return round(1.0 - method.root_mean_squared_error / baseline.root_mean_squared_error, 6)


def _registration_payload(summary: RegistrationSummary) -> dict[str, Any]:
    return {
        "corpus_cell_count": summary.plane.corpus_cell_count,
        "corpus_cell_day_count": summary.plane.corpus_cell_day_count,
        "corpus_source_row_count": summary.plane.corpus_row_count,
        "data_source_id": str(summary.plane.data_source_id),
        "first_observed_day": summary.plane.first_observed_day.isoformat(),
        "last_observed_day": summary.plane.last_observed_day.isoformat(),
        "observation_rows_inserted": summary.observation_count,
        "release_cell_day_count": summary.materialisation.observation_count,
        "release_series_count": summary.materialisation.series_count,
        "release_first_observed_day": (
            None
            if summary.materialisation.first_observed_day is None
            else summary.materialisation.first_observed_day.isoformat()
        ),
        "release_last_observed_day": (
            None
            if summary.materialisation.last_observed_day is None
            else summary.materialisation.last_observed_day.isoformat()
        ),
        "release_matches_claimed_corpus": release_holds_claimed_corpus(
            materialisation=summary.materialisation,
            plane=summary.plane,
        ),
        "requested_cells_materialised": summary.selection.series_count,
        "requested_cell_days_materialised": summary.selection.observation_count,
        "all_requested_cells_materialised": all_requested_cells_materialised(
            selection=summary.selection,
            requested_cell_count=summary.requested_cell_count,
        ),
        "payload_checksum": summary.plane.payload_checksum,
        "release_manifest_checksum": summary.plane.release_manifest_checksum,
        "release_set_id": str(summary.plane.release_set_id),
        "requested_cell_count": summary.requested_cell_count,
        "series_rows_inserted": summary.series_count,
        "source_release_id": str(summary.plane.source_release_id),
        "spatial_cell_rows_inserted": summary.spatial_cell_count,
    }


def _iteration_outcome_payload(outcomes: tuple[IterationOutcome, ...]) -> dict[str, Any]:
    refusals: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.skipped_reason_code is not None:
            refusals[outcome.skipped_reason_code] = refusals.get(outcome.skipped_reason_code, 0) + 1
    written = tuple(outcome for outcome in outcomes if outcome.iteration_id is not None)
    return {
        "candidate_series_count": len(outcomes),
        "iteration_count": len(written),
        "iteration_value_count": sum(outcome.value_count for outcome in written),
        "refusals_by_reason": dict(sorted(refusals.items())),
        "training_day_count_min": min((outcome.training_day_count for outcome in written), default=None),
        "training_day_count_max": max((outcome.training_day_count for outcome in written), default=None),
    }


def _holdout_payload(evaluation: HoldoutEvaluation) -> dict[str, Any]:
    return {
        "cutoff_days": [day.isoformat() for day in evaluation.cutoff_days],
        "interval_coverage_fraction": (
            round(evaluation.interval_coverage_fraction, 6) if evaluation.reconciled_actual_count else None
        ),
        "iteration_count": evaluation.iteration_count,
        "metrics_by_horizon_bucket": {
            name: _error_metrics_payload(metrics) for name, metrics in evaluation.metrics_by_horizon_bucket
        },
        "method_metrics": _error_metrics_payload(evaluation.method_metrics),
        "reconciled_actual_count": evaluation.reconciled_actual_count,
        "baseline_climatology_metrics": _error_metrics_payload(evaluation.climatology_metrics),
        "baseline_persistence_metrics": _error_metrics_payload(evaluation.persistence_metrics),
        "skill_versus_climatology": _skill_score(evaluation.method_metrics, evaluation.climatology_metrics),
        "skill_versus_persistence": _skill_score(evaluation.method_metrics, evaluation.persistence_metrics),
    }


async def register_vegetation_plane(
    *,
    database_url: str,
    session_factory: SessionScopeFactory,
    cutoff_day: date,
    cell_limit: int,
    cell_keys: tuple[str, ...],
) -> dict[str, Any]:
    """Register the governed NDVI plane and return its materialization receipt."""
    async with session_factory(database_url) as session, session.begin():
        await pin_determinism(session)
        selected = cell_keys or await select_candidate_cell_keys(session, cutoff_day=cutoff_day, cell_limit=cell_limit)
        summary = await register_governed_plane(session, cutoff_day=cutoff_day, cell_keys=selected)
    payload = _registration_payload(summary)
    payload["cutoff_day"] = cutoff_day.isoformat()
    return payload


async def simulate_vegetation(  # noqa: PLR0913
    *,
    database_url: str,
    session_factory: SessionScopeFactory,
    cutoff_day: date,
    release_cutoff_day: date,
    request: SimulationRequest,
    purpose: str,
    cell_keys: tuple[str, ...],
    as_of_time: datetime | None,
) -> dict[str, Any]:
    """Write deterministic vegetation simulations and return their outcome summary."""
    if cutoff_day > release_cutoff_day:
        raise ValueError("simulation cutoff day cannot follow the governed release-set cutoff day")
    resolved_as_of = _resolved_as_of_time(as_of_time, cutoff_day)
    async with session_factory(database_url) as session, session.begin():
        await pin_determinism(session)
        plane = await load_governed_plane(session, cutoff_day=release_cutoff_day)
        identities = await load_series_identities(session, cell_keys=cell_keys or None)
        if not identities:
            raise ValueError("no registered NDVI series match the requested cells")
        governed_history = await load_governed_history(
            session,
            release_set_id=plane.release_set_id,
            as_of_time=resolved_as_of,
            cutoff_day=cutoff_day,
        )
        license_snapshots = await load_license_snapshots(
            session,
            release_set_id=plane.release_set_id,
            as_of_time=resolved_as_of,
            cutoff_day=cutoff_day,
        )
        outcomes, _histories = await simulate_cells(
            session,
            plane=plane,
            identities=identities,
            governed_history=governed_history,
            license_snapshots=license_snapshots,
            purpose=purpose,
            as_of_time=resolved_as_of,
            cutoff_day=cutoff_day,
            request=request,
        )
    payload = _iteration_outcome_payload(outcomes)
    payload.update(
        {
            "as_of_time": resolved_as_of.isoformat(),
            "cutoff_day": cutoff_day.isoformat(),
            "horizon_days": request.horizon_days,
            "method": VEGETATION_METHOD_NAME,
            "purpose": purpose,
            "release_set_id": str(plane.release_set_id),
            "seed": request.seed,
            "simulation_count": request.simulation_count,
        }
    )
    return payload


async def evaluate_vegetation(  # noqa: PLR0913
    *,
    database_url: str,
    session_factory: SessionScopeFactory,
    release_cutoff_day: date,
    holdout_cutoff_days: tuple[date, ...],
    request: SimulationRequest,
    cell_keys: tuple[str, ...],
    as_of_time: datetime | None,
) -> dict[str, Any]:
    """Run time-honest holdouts and return method and baseline metrics."""
    ordered_cutoffs = tuple(sorted(set(holdout_cutoff_days)))
    if any(cutoff >= release_cutoff_day for cutoff in ordered_cutoffs):
        raise ValueError("every holdout cutoff day must precede the governed release-set cutoff day")
    resolved_as_of = _resolved_as_of_time(as_of_time, max(ordered_cutoffs))
    async with session_factory(database_url) as session, session.begin():
        await pin_determinism(session)
        plane = await load_governed_plane(session, cutoff_day=release_cutoff_day)
        identities = await load_series_identities(session, cell_keys=cell_keys or None)
        if not identities:
            raise ValueError("no registered NDVI series match the requested cells")
        governed_history = await load_governed_history(
            session,
            release_set_id=plane.release_set_id,
            as_of_time=resolved_as_of,
            cutoff_day=max(ordered_cutoffs),
        )
        license_snapshots = await load_license_snapshots(
            session,
            release_set_id=plane.release_set_id,
            as_of_time=resolved_as_of,
            cutoff_day=max(ordered_cutoffs),
        )
        histories_by_cutoff: dict[date, dict[uuid.UUID, SeasonalHistory]] = {}
        iteration_ids: list[uuid.UUID] = []
        refusals: dict[str, int] = {}
        for cutoff_day in ordered_cutoffs:
            outcomes, histories = await simulate_cells(
                session,
                plane=plane,
                identities=identities,
                governed_history=governed_history,
                license_snapshots=license_snapshots,
                purpose=PURPOSE_HOLDOUT_EVALUATION,
                as_of_time=resolved_as_of,
                cutoff_day=cutoff_day,
                request=request,
            )
            histories_by_cutoff[cutoff_day] = histories
            iteration_ids.extend(outcome.iteration_id for outcome in outcomes if outcome.iteration_id is not None)
            for outcome in outcomes:
                if outcome.skipped_reason_code is not None:
                    refusals[outcome.skipped_reason_code] = refusals.get(outcome.skipped_reason_code, 0) + 1
        reconciled = await reconcile_actuals(
            session,
            iteration_ids=tuple(iteration_ids),
            release_set_id=plane.release_set_id,
            as_of_time=resolved_as_of,
        )
        outcome_rows = await load_outcome_rows(session, iteration_ids=tuple(iteration_ids))
        evaluation = summarize_holdout(
            cutoff_days=ordered_cutoffs,
            iteration_count=len(iteration_ids),
            outcome_rows=outcome_rows,
            histories_by_cutoff=histories_by_cutoff,
            horizon_buckets=VEGETATION_HORIZON_BUCKETS,
        )
    payload = _holdout_payload(evaluation)
    payload.update(
        {
            "as_of_time": resolved_as_of.isoformat(),
            "availability_mode": "retrospective_pinned_release",
            "horizon_days": request.horizon_days,
            "inserted_actual_count": reconciled,
            "method": VEGETATION_METHOD_NAME,
            "refusals_by_reason": dict(sorted(refusals.items())),
            "release_cutoff_day": release_cutoff_day.isoformat(),
            "release_set_id": str(plane.release_set_id),
            "seed": request.seed,
            "simulation_count": request.simulation_count,
        }
    )
    return payload
