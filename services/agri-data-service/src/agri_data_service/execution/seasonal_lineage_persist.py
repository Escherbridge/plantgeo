"""Write the evaluation-only lineage plane from a scored benchmark, in a disposable database.

Two derived-signal generations are written, and the second is a genuine feedback signal in this
track's sense: a residual that becomes available only once its source actual has been recorded. The
parent forecast's cutoff and valid time both precede the residual's own cutoff, which is what the
edge constraints check.

This module writes **no** receipt, publication, forecast value or serving row, and the plane it
writes has no foreign key that could reach one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.seasonal_row_types import require_int, require_uuid
from agri_data_service.method.ml.seasonal_evaluation import summarize

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from pathlib import Path
    from uuid import UUID

    from agri_data_service.execution.seasonal_benchmark import BenchmarkResults
    from agri_data_service.method.ml.seasonal_evaluation import ScoredOrigin

_INSERT_SIGNAL_DEFINITION: Final = text(load_query_sql("execution/seasonal_insert_signal_definition.sql"))
_INSERT_DERIVED_SIGNAL_VALUE: Final = text(load_query_sql("execution/seasonal_insert_derived_signal_value.sql"))
_INSERT_LINEAGE_EDGE: Final = text(load_query_sql("execution/seasonal_insert_lineage_edge.sql"))
_INSERT_CANDIDATE_EVALUATION: Final = text(load_query_sql("execution/seasonal_insert_candidate_evaluation.sql"))
_INSERT_CANDIDATE_EVALUATION_ORIGIN: Final = text(
    load_query_sql("execution/seasonal_insert_candidate_evaluation_origin.sql")
)
_SELECT_SIGNAL_DEFINITION: Final = text(
    "SELECT id FROM agri.forecast_signal_definition WHERE signal_key = :signal_key AND signal_version = :signal_version"
)

POINT_FORECAST_SIGNAL_KEY: Final = "seasonal_point_forecast"
RESIDUAL_FEEDBACK_SIGNAL_KEY: Final = "seasonal_residual_feedback"
SIGNAL_VERSION: Final = "v1"
MAX_DEPENDENCY_DEPTH: Final = 4
SIMULATION_SEED: Final = 20260814


class SeasonalLineagePersistError(RuntimeError):
    """The lineage plane could not be written from the scored benchmark."""


@dataclass(frozen=True)
class SignalDefinitionSpec:
    """The reviewed identity of one derived-signal definition/version."""

    signal_key: str
    unit: str
    spatial_support_key: str
    recipe_key: str
    parent_schema: tuple[str, ...]


@dataclass(frozen=True)
class DerivedValueSpec:
    """One derived value's identity, times and provenance, before it is written."""

    definition_id: UUID
    series_key: str
    lineage_depth: int
    origin_cutoff_time: datetime
    valid_time: datetime
    availability_time: datetime
    signal_value: float | None
    input_release_checksum: str
    recipe_key: str


@dataclass(frozen=True)
class PersistedLineage:
    """What one persistence pass wrote."""

    point_forecast_definition_id: UUID
    residual_feedback_definition_id: UUID
    point_forecast_value_count: int
    residual_value_count: int
    lineage_edge_count: int
    evaluation_count: int
    evaluation_origin_count: int


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def recipe_checksum(recipe_key: str, *, parameters: str = "") -> str:
    """Deterministic digest of a recipe identity; the same inputs always yield the same digest."""
    return hashlib.sha256(f"seasonal-recipe-v1|{recipe_key}|{parameters}".encode()).hexdigest()


def origin_checksum(
    evaluation_key: str,
    origin: datetime,
    scored_step_count: int,
    mean_absolute_error: float,
) -> str:
    """Deterministic digest binding a per-origin metric row to its evaluation and origin."""
    preimage = "|".join(
        (
            "seasonal_candidate_evaluation_origin_v1",
            evaluation_key,
            origin.astimezone(UTC).isoformat(),
            str(scored_step_count),
            repr(mean_absolute_error),
        )
    )
    return hashlib.sha256(preimage.encode()).hexdigest()


async def _ensure_definition(session: AsyncSession, spec: SignalDefinitionSpec) -> UUID:
    signal_key = spec.signal_key
    recipe_key = spec.recipe_key
    digest = recipe_checksum(recipe_key)
    await session.execute(
        _INSERT_SIGNAL_DEFINITION,
        {
            "signal_key": signal_key,
            "signal_version": SIGNAL_VERSION,
            "unit": spec.unit,
            "spatial_support_key": spec.spatial_support_key,
            "temporal_grain": timedelta(days=1),
            "recipe_key": recipe_key,
            "recipe_checksum": digest,
            "parent_schema": json.dumps(list(spec.parent_schema), sort_keys=True),
            "max_dependency_depth": MAX_DEPENDENCY_DEPTH,
            "definition_checksum": recipe_checksum(recipe_key, parameters=signal_key),
        },
    )
    result = await session.execute(
        _SELECT_SIGNAL_DEFINITION, {"signal_key": signal_key, "signal_version": SIGNAL_VERSION}
    )
    row = result.mappings().first()
    if row is None:
        raise SeasonalLineagePersistError(f"signal definition {signal_key} was neither inserted nor found")
    return require_uuid(row["id"], "id")


async def _insert_value(session: AsyncSession, spec: DerivedValueSpec) -> int:
    result = await session.execute(
        _INSERT_DERIVED_SIGNAL_VALUE,
        {
            "signal_definition_id": spec.definition_id,
            "max_dependency_depth": MAX_DEPENDENCY_DEPTH,
            "series_key": spec.series_key,
            "lineage_depth": spec.lineage_depth,
            "origin_cutoff_time": spec.origin_cutoff_time,
            "valid_time": spec.valid_time,
            "availability_time": spec.availability_time,
            "signal_value": spec.signal_value,
            "known_missing_inputs": "[]" if spec.signal_value is not None else '["scored actual absent"]',
            "input_release_checksum": spec.input_release_checksum,
            "recipe_checksum": recipe_checksum(spec.recipe_key),
        },
    )
    row = result.mappings().first()
    if row is None:
        raise SeasonalLineagePersistError("derived signal value insert returned no row")
    return require_int(row["id"], "id")


async def persist_scored_origins(
    session: AsyncSession,
    scored_origins: Sequence[ScoredOrigin],
    *,
    input_release_checksum: str,
) -> PersistedLineage:
    """Write the point forecasts, their later-available residual feedback, and the edges between."""
    if not scored_origins:
        raise SeasonalLineagePersistError("no scored origin to persist; refusing to write an empty plane")
    point_definition = await _ensure_definition(
        session,
        SignalDefinitionSpec(
            signal_key=POINT_FORECAST_SIGNAL_KEY,
            unit="native",
            spatial_support_key="surface",
            recipe_key="seasonal_point_forecast_recipe",
            parent_schema=(),
        ),
    )
    residual_definition = await _ensure_definition(
        session,
        SignalDefinitionSpec(
            signal_key=RESIDUAL_FEEDBACK_SIGNAL_KEY,
            unit="native",
            spatial_support_key="surface",
            recipe_key="seasonal_residual_feedback_recipe",
            parent_schema=(POINT_FORECAST_SIGNAL_KEY,),
        ),
    )

    point_count = 0
    residual_count = 0
    edge_count = 0
    for scored in scored_origins:
        origin_time = _midnight(scored.origin)
        for target_day, median, actual in zip(scored.target_days, scored.median, scored.actual, strict=True):
            valid_time = _midnight(target_day)
            # The residual can first be computed the day after its target day, which is also the
            # earliest instant it may be read; the parent forecast was available at its own origin.
            residual_available = valid_time + timedelta(days=1)
            parent_id = await _insert_value(
                session,
                DerivedValueSpec(
                    definition_id=point_definition,
                    series_key=scored.series_key,
                    lineage_depth=0,
                    origin_cutoff_time=origin_time,
                    valid_time=valid_time,
                    availability_time=origin_time,
                    signal_value=median,
                    input_release_checksum=input_release_checksum,
                    recipe_key="seasonal_point_forecast_recipe",
                ),
            )
            child_id = await _insert_value(
                session,
                DerivedValueSpec(
                    definition_id=residual_definition,
                    series_key=scored.series_key,
                    lineage_depth=1,
                    origin_cutoff_time=residual_available,
                    valid_time=valid_time,
                    availability_time=residual_available,
                    signal_value=actual - median,
                    input_release_checksum=input_release_checksum,
                    recipe_key="seasonal_residual_feedback_recipe",
                ),
            )
            await session.execute(
                _INSERT_LINEAGE_EDGE,
                {"child_value_id": child_id, "parent_value_id": parent_id, "parent_role": "base_forecast"},
            )
            point_count += 1
            residual_count += 1
            edge_count += 1

    return PersistedLineage(
        point_forecast_definition_id=point_definition,
        residual_feedback_definition_id=residual_definition,
        point_forecast_value_count=point_count,
        residual_value_count=residual_count,
        lineage_edge_count=edge_count,
        evaluation_count=0,
        evaluation_origin_count=0,
    )


async def persist_candidate_evaluations(
    session: AsyncSession,
    results: BenchmarkResults,
    *,
    baseline_by_series: dict[tuple[str, str], Sequence[ScoredOrigin]],
) -> tuple[int, int]:
    """Write one receipt per (series, candidate) and one metric row per scored origin."""
    evaluation_count = 0
    origin_count = 0
    for series_result in results.series_results:
        evaluation_key = f"{results.export_key}|{series_result.series_key}|{series_result.candidate_name}"
        metrics = {
            "development_mae": series_result.development.mae,
            "final_holdout_mae": series_result.final_holdout.mae,
            "final_holdout_rmse": series_result.final_holdout.rmse,
            "final_holdout_bias": series_result.final_holdout.bias,
            "final_holdout_interval_coverage": series_result.final_holdout.interval_coverage,
            "final_holdout_skill_versus_persistence": series_result.final_holdout_skill,
            "abstention_reasons": list(series_result.abstention.reasons),
        }
        decision = (
            "abstain" if series_result.abstention.must_abstain else _decision_for(results, series_result.candidate_name)
        )
        result = await session.execute(
            _INSERT_CANDIDATE_EVALUATION,
            {
                "evaluation_key": evaluation_key,
                "series_key": series_result.series_key,
                "candidate_family": series_result.candidate_name,
                "candidate_version": SIGNAL_VERSION,
                "hyperparameters": json.dumps({"horizon_steps": results.origin_plan.horizon_steps}, sort_keys=True),
                "simulation_seed": SIMULATION_SEED,
                "export_manifest_checksum": results.manifest_checksum,
                "horizon_steps": results.origin_plan.horizon_steps,
                "development_origin_count": series_result.abstention.development_origin_count,
                "final_holdout_origin_count": series_result.abstention.final_holdout_origin_count,
                "metrics": json.dumps(metrics, sort_keys=True),
                "decision": decision,
                "decision_reason": _reason_for(results, series_result.candidate_name),
            },
        )
        row = result.mappings().first()
        if row is None:
            raise SeasonalLineagePersistError(f"candidate evaluation {evaluation_key} returned no row")
        evaluation_id = require_uuid(row["id"], "id")
        evaluation_count += 1

        scored = baseline_by_series.get((series_result.series_key, series_result.candidate_name), ())
        for origin in scored:
            summary = summarize([origin])
            moment = _midnight(origin.origin)
            await session.execute(
                _INSERT_CANDIDATE_EVALUATION_ORIGIN,
                {
                    "evaluation_id": evaluation_id,
                    "origin_cutoff_time": moment,
                    "fold_kind": origin.fold_kind,
                    "scored_step_count": origin.scored_day_count,
                    "mean_absolute_error": summary.mae,
                    "root_mean_squared_error": summary.rmse,
                    "bias": summary.bias,
                    "interval_coverage_fraction": summary.interval_coverage,
                    "skill_versus_persistence": None,
                    "origin_checksum": origin_checksum(evaluation_key, moment, origin.scored_day_count, summary.mae),
                },
            )
            origin_count += 1
    return evaluation_count, origin_count


async def persist_benchmark(
    export_dir: Path,
    database_url: str,
    *,
    candidate_name: str,
    series_keys: Sequence[str] | None = None,
) -> dict[str, int | str]:
    """Score the frozen export, then write its receipts and one candidate's lineage in one transaction.

    The candidate whose derived signals are written is named explicitly rather than inferred from the
    decision, so persisting a *rejected* candidate as evidence stays possible: the plane records what
    was evaluated, and the decision is a column on the receipt, not a filter on what may be stored.
    """
    # Imported here, not at module scope: `seasonal_benchmark` imports this module's
    # `ScoredOrigin` types transitively, and a top-level import would close the cycle.
    from agri_data_service.execution.seasonal_benchmark import run_benchmark  # noqa: PLC0415

    results = run_benchmark(export_dir)
    selected = tuple(series_keys) if series_keys else results.targets
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            evaluation_count, origin_count = await persist_candidate_evaluations(
                session,
                results,
                baseline_by_series=dict(results.scored_by_series_candidate),
            )
            scored: list[ScoredOrigin] = []
            for series_key in selected:
                scored.extend(results.scored_by_series_candidate.get((series_key, candidate_name), ()))
            lineage = await persist_scored_origins(session, scored, input_release_checksum=results.manifest_checksum)
            await session.commit()
    finally:
        await engine.dispose()
    return {
        "export_key": results.export_key,
        "manifest_checksum": results.manifest_checksum,
        "candidate": candidate_name,
        "series_count": len(selected),
        "evaluation_receipts": evaluation_count,
        "evaluation_origin_rows": origin_count,
        "point_forecast_values": lineage.point_forecast_value_count,
        "residual_feedback_values": lineage.residual_value_count,
        "lineage_edges": lineage.lineage_edge_count,
    }


def _decision_for(results: BenchmarkResults, candidate_name: str) -> str:
    for decision in results.decisions:
        if decision.candidate_name == candidate_name:
            return decision.decision
    return "abstain"


def _reason_for(results: BenchmarkResults, candidate_name: str) -> str:
    for decision in results.decisions:
        if decision.candidate_name == candidate_name:
            return "; ".join(decision.failed_gates) or "all pre-registered gates cleared"
    return "candidate absent from the decision set"
