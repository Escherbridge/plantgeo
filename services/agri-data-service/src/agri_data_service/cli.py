"""CLI commands for migrations and reviewed seed data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, Self, cast

import click
import httpx
import structlog
from alembic.config import Config
from sqlalchemy import or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import (
    async_session,
    combined_local_engine,
    forecast_iteration_session,
    forecast_mv_refresh_session,
    ingest_session,
    local_source_loader_engine,
    local_source_loader_session,
)
from agri_data_service.db.maintenance import (
    MaintenanceBusyError,
    maintain_job_event_partitions,
)
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.contracts import ExpectedOutput

# Importing the covariate wind lane is also what REGISTERS its durable handler: `@job_handler`
# binds `execution.covariate_wind_train` into `JOB_HANDLERS` at import time, and a slice resolves
# a stored `job_definition.handler` token through that registry. See execution/AGENTS.md.
from agri_data_service.execution.covariate_wind_lane import (
    CovariateWindLaneContext,
    CovariateWindLanePlan,
    covariate_wind_lane_context,
    covariate_wind_targets,
    plan_covariate_wind_lane,
)
from agri_data_service.execution.covariate_wind_model import OriginNotEvaluableError
from agri_data_service.execution.covariate_wind_persist import (
    TRAINING_DEFINITION_NAME,
    ForecastTrainingPersistError,
    WindTrainingRequest,
    run_covariate_wind_training,
)
from agri_data_service.execution.coverage_census import (
    CoverageCensusError,
    census_contracts,
    contracts_for_keys,
    load_lane_cells,
)
from agri_data_service.execution.coverage_contract import contracts_for_source
from agri_data_service.execution.coverage_fill import (
    GAP_PROBE_CELL_COUNT,
    CoverageFillError,
    FillRefusal,
    coverage_fill_payload,
    decide_coverage_fill,
    gap_to_probe,
    probe_gap_window,
    record_governed_absence,
    signals_this_plan_can_fill,
    write_fill_plan,
)
from agri_data_service.execution.coverage_report import coverage_status_payload, render_census
from agri_data_service.execution.ensemble_forecast import (
    ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE,
    EnsembleForecastCheckpoint,
    EnsembleForecastChunk,
    EnsembleForecastChunkResult,
    EnsembleForecastFetchError,
    EnsembleForecastPlan,
    StagedForecastReceipt,
    cache_ensemble_forecast_result,
    ensemble_forecast_checkpoint_path,
    ensemble_forecast_plan_checksum,
    ensemble_forecast_release_manifest,
    ensemble_forecast_staged_document,
    ensemble_forecast_staged_document_path,
    initialize_ensemble_forecast_checkpoint,
    load_cached_ensemble_forecast_result,
    load_ensemble_forecast_checkpoint,
    record_ensemble_forecast_result,
    rederive_ensemble_forecast_checkpoint_state,
    run_ensemble_forecast_chunks,
    write_ensemble_forecast_checkpoint,
    write_ensemble_forecast_staged_document,
)
from agri_data_service.execution.historical_cams import (
    CamsAirQualityChunk,
    CamsAirQualityChunkResult,
    CamsAirQualityFetchError,
    HistoricalCamsAirQualityPlan,
    HistoricalCamsCheckpoint,
    cache_historical_cams_result,
    historical_cams_checkpoint_path,
    historical_cams_plan_checksum,
    historical_cams_release_manifest,
    initialize_historical_cams_checkpoint,
    load_cached_historical_cams_result,
    load_historical_cams_checkpoint,
    record_historical_cams_result,
    rederive_historical_cams_checkpoint_state,
    run_cams_air_quality_chunks,
    write_historical_cams_checkpoint,
)
from agri_data_service.execution.historical_era5 import (
    HistoricalEra5Checkpoint,
    HistoricalEra5Finalization,
    HistoricalEra5LandBackfillPlan,
    cache_historical_era5_result,
    fetch_era5_land_monthly,
    historical_era5_checkpoint_path,
    historical_era5_plan_checksum,
    historical_era5_release_manifest,
    initialize_historical_era5_checkpoint,
    load_cached_historical_era5_result,
    load_historical_era5_checkpoint,
    rebind_historical_era5_checkpoint_for_finalization,
    record_historical_era5_result,
    write_historical_era5_checkpoint,
    write_historical_era5_release_plan,
)
from agri_data_service.execution.historical_era5_parquet import (
    historical_era5_parquet_root,
    materialize_historical_era5_parquet,
)
from agri_data_service.execution.historical_export import (
    HistoricalPromotionUploader,
    LocalHistoricalPromotionExporter,
    load_historical_promotion_spool,
)
from agri_data_service.execution.historical_glofas import (
    GlofasFloodChunk,
    GlofasFloodChunkResult,
    GlofasFloodFetchError,
    HistoricalGlofasCheckpoint,
    HistoricalGlofasFloodPlan,
    cache_historical_glofas_result,
    historical_glofas_checkpoint_path,
    historical_glofas_plan_checksum,
    historical_glofas_release_manifest,
    initialize_historical_glofas_checkpoint,
    load_cached_historical_glofas_result,
    load_historical_glofas_checkpoint,
    record_historical_glofas_result,
    rederive_historical_glofas_checkpoint_state,
    run_glofas_flood_chunks,
    write_historical_glofas_checkpoint,
)
from agri_data_service.execution.historical_parquet import (
    historical_nasa_parquet_root,
    materialize_historical_nasa_parquet,
)
from agri_data_service.execution.historical_usdm import (
    HistoricalUsdmBackfillPlan,
    HistoricalUsdmCheckpoint,
    HistoricalUsdmFinalization,
    fetch_usdm_shapefile,
    historical_usdm_checkpoint_path,
    historical_usdm_plan_checksum,
    initialize_historical_usdm_checkpoint,
    load_historical_usdm_checkpoint,
    rebind_historical_usdm_checkpoint_for_finalization,
    record_historical_usdm_result,
    write_historical_usdm_checkpoint,
)
from agri_data_service.execution.historical_writer import (
    finalize_cams_release_set,
    finalize_era5_release_set,
    finalize_glofas_release_set,
    finalize_nasa_release_set,
    finalize_open_meteo_release_set,
    finalize_usdm_release_set,
    persist_cams_air_quality_chunk,
    persist_era5_land_month,
    persist_glofas_flood_chunk,
    persist_nasa_power_cell,
    persist_open_meteo_archive_chunk,
    persist_usdm_shapefile,
)
from agri_data_service.execution.local_store import LocalRunStore
from agri_data_service.execution.plan_continuation import (
    CONTINUATION_AS_OF_HORIZON_DAYS,
    FRONTIER_PROBE_CELL_COUNT,
    MINIMUM_CONTINUATION_ADVANCE_DAYS,
    PlanContinuationError,
    continuation_decision_payload,
    decide_continuation,
    declared_frontier,
    load_continuation_source,
    plan_staleness_payload,
    probe_provider_frontier,
    scan_plan_staleness,
    write_continuation_plan,
)
from agri_data_service.execution.publisher import BoundedPublisher, PublicationError
from agri_data_service.execution.source_ingestion import (
    SOURCE_INGESTION_CHECKPOINT_SCHEMA_VERSION,
    SourceIngestionCheckpoint,
    SourceIngestionPlan,
    checkpoint_path,
    load_and_validate_geojson,
    load_checkpoint,
    publish_source_release,
    release_set_manifest,
    source_ingestion_plan_checksum,
    write_checkpoint,
)
from agri_data_service.execution.strategy_label_mapping import (
    preflight_strategy_label_source_mapping,
)
from agri_data_service.execution.strategy_selection import (
    load_strategy_label_bundle,
    train_strategy_models,
)
from agri_data_service.execution.vegetation_ndvi_forecast import (
    METHOD_NAME as VEGETATION_METHOD_NAME,
)
from agri_data_service.execution.vegetation_ndvi_forecast import (
    PURPOSE_FORWARD_SIMULATION,
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
from agri_data_service.execution.weather_observations.era5_land import (
    HistoricalOpenMeteoArchivePlan,
    HistoricalOpenMeteoCheckpoint,
    OpenMeteoArchiveChunk,
    OpenMeteoArchiveChunkResult,
    OpenMeteoArchiveFetchError,
    cache_historical_open_meteo_result,
    historical_open_meteo_checkpoint_path,
    historical_open_meteo_plan_checksum,
    historical_open_meteo_release_manifest,
    initialize_historical_open_meteo_checkpoint,
    load_cached_historical_open_meteo_result,
    load_historical_open_meteo_checkpoint,
    open_meteo_observed_values_by_parameter,
    record_historical_open_meteo_result,
    rederive_historical_open_meteo_checkpoint_state,
    run_open_meteo_archive_chunks,
    unanswered_open_meteo_parameters,
    write_historical_open_meteo_checkpoint,
)
from agri_data_service.execution.weather_observations.nasa_power import (
    HistoricalNasaBackfillPlan,
    HistoricalNasaCheckpoint,
    HistoricalNasaFinalization,
    cache_historical_nasa_result,
    fetch_nasa_power_daily,
    historical_nasa_checkpoint_path,
    historical_nasa_plan_checksum,
    initialize_historical_nasa_checkpoint,
    load_cached_historical_nasa_result,
    load_historical_nasa_checkpoint,
    rebind_historical_nasa_checkpoint_for_finalization,
    record_historical_nasa_result,
    write_historical_nasa_checkpoint,
    write_historical_nasa_release_plan,
)
from agri_data_service.ingest.commands import register_ingest_commands
from agri_data_service.jobs import (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
    run_job_slice,
    shutdown_signal,
)
from agri_data_service.models.strategy import Strategy
from agri_data_service.pipeline.parquet.gap_fill import (
    DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS,
    GapFillSummary,
    LaneWatermarkReading,
    build_gap_census,
    gap_census_report,
    resolve_lane_watermarks,
    run_gap_fill,
)
from agri_data_service.pipeline.parquet.lane_registry import (
    LANE_REGISTRATIONS,
    LaneRegistryError,
    registered_lane_slugs,
    resolve_lanes,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteError
from agri_data_service.seed.strategies import STRATEGY_SEEDS
from alembic import command

logger = structlog.get_logger()
_RUN_PLAN_MAX_BYTES = 512_000
_SHA256_HEX_LENGTH = 64
_OPEN_METEO_PENDING_PREVIEW = 8
_MAX_RUN_PLAN_OUTPUTS = 1_000
_MAX_RUN_PLAN_KEYS = 10_000
_MAX_RUN_PLAN_KEY_LENGTH = 500
_GAP_FILL_FAILED_EXIT_CODE = 1

# Runtime query SQL lives in sql/cli/, loaded once per process; see src/agri_data_service/sql/AGENTS.md.
_MATERIALIZE_FORECAST_ITERATION = text(load_query_sql("cli/materialize_forecast_iteration.sql"))
_FORECAST_ITERATION_SUMMARY = text(load_query_sql("cli/forecast_iteration_summary.sql"))
_RECONCILE_FORECAST_ITERATION_ACTUALS = text(load_query_sql("cli/reconcile_forecast_iteration_actuals.sql"))
_FORECAST_ITERATION_OUTCOME_TOTALS = text(load_query_sql("cli/forecast_iteration_outcome_totals.sql"))

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from agri_data_service.execution.vegetation_ndvi_forecast import SeasonalHistory
    from agri_data_service.jobs import JobSliceSummary
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration


@click.group()
def cli() -> None:
    """Agri Data Service CLI."""


register_ingest_commands(cli)

from agri_data_service.execution.analog_ensemble_cli import (  # noqa: E402
    forecast_recalibrate_ndvi,
    forecast_train_anen,
)
from agri_data_service.execution.jobs_pulse_command import jobs_pulse  # noqa: E402
from agri_data_service.execution.recommendation_commands import (  # noqa: E402
    register_recommendation_commands,
)
from agri_data_service.execution.seasonal_command import (  # noqa: E402
    register_seasonal_commands,
)

cli.add_command(forecast_train_anen)
cli.add_command(forecast_recalibrate_ndvi)
cli.add_command(jobs_pulse)
register_recommendation_commands(cli)
register_seasonal_commands(cli)


@cli.command()
def seed() -> None:
    """Seed draft regenerative strategies."""
    asyncio.run(_seed())


async def _seed() -> None:
    async with async_session() as session:
        for data in STRATEGY_SEEDS:
            await session.execute(_strategy_seed_statement(data))
        await session.commit()

    click.echo(f"Seeded {len(STRATEGY_SEEDS)} draft strategies for evidence review.")


@cli.command("strategy-label-map-preflight")
@click.option(
    "--mapping-manifest",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.pass_context
def strategy_label_map_preflight(context: click.Context, mapping_manifest: Path) -> None:
    """Validate a declared external intervention-label source mapping."""
    try:
        result = preflight_strategy_label_source_mapping(mapping_manifest)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result.to_json())
    if not result.ready:
        context.exit(2)


@cli.command("strategy-train")
@click.option(
    "--label-bundle",
    type=click.Path(path_type=Path, exists=True, dir_okay=False, readable=True),
    required=True,
)
@click.option(
    "--output-artifact",
    type=click.Path(path_type=Path, dir_okay=False),
    required=True,
)
def strategy_train(label_bundle: Path, output_artifact: Path) -> None:
    """Train the local evaluation-only strategy benchmark."""
    try:
        bundle = load_strategy_label_bundle(label_bundle)
        artifact = train_strategy_models(bundle)
        _write_strategy_artifact_atomic(output_artifact, artifact.to_json())
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            {
                "artifact_checksum": artifact.checksum,
                "decision_state": artifact.decision_state,
                "label_bundle_checksum": artifact.label_bundle_checksum,
                "output_artifact": str(output_artifact),
                "selected_strategy_id": artifact.selected_strategy_id,
                "strategy_label_checksum": artifact.label_checksum,
            },
            sort_keys=True,
        )
    )


def _write_strategy_artifact_atomic(path: Path, payload: str) -> None:
    """Publish one canonical model artifact atomically within its target directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _strategy_seed_statement(data: dict[str, Any]) -> Any:
    """Reset review only when governed seed content actually changes."""
    draft_data = {
        **data,
        "review_state": "draft",
        "reviewed_at": None,
        "reviewed_by": None,
    }
    governed_content = {
        key: value
        for key, value in draft_data.items()
        if key not in {"slug", "review_state", "reviewed_at", "reviewed_by"}
    }
    content_changed = or_(*(getattr(Strategy, key).is_distinct_from(value) for key, value in governed_content.items()))
    return (
        insert(Strategy)
        .values(**draft_data)
        .on_conflict_do_update(
            index_elements=["slug"],
            set_={key: value for key, value in draft_data.items() if key != "slug"},
            where=content_changed,
        )
    )


def _alembic_config() -> Config:
    env_path = os.environ.get("AGRI_ALEMBIC_CONFIG")
    if env_path:
        return Config(env_path)
    curr = Path(__file__).resolve().parent
    default_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    while curr != curr.parent:
        candidate = curr / "alembic.ini"
        if candidate.is_file():
            default_path = candidate
            break
        curr = curr.parent
    return Config(str(default_path))


@cli.command("db-status")
def db_status() -> None:
    """Show the database's current Alembic revision."""
    command.current(_alembic_config(), verbose=True)


@cli.command("db-upgrade")
@click.argument("revision", default="head")
def db_upgrade(revision: str) -> None:
    """Upgrade through Alembic without application-owned DDL."""
    command.upgrade(_alembic_config(), revision)


@cli.command("forecast-refresh-ml-daily")
def forecast_refresh_ml_daily() -> None:
    """Explicitly refresh the reviewed ML daily serving materialization."""
    try:
        row_count = asyncio.run(_forecast_refresh_ml_daily())
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"forecast materialized-view refresh failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps({"state": "refreshed", "row_count": row_count}, sort_keys=True))


def _forecast_mv_refresh_database_url() -> str:
    """Resolve the async DSN `forecast-refresh-ml-daily` connects with, falling back through what
    this service already has configured rather than reading the environment a second time.

    `FORECAST_MV_REFRESH_DATABASE_URL` -> `DATABASE_URL` is `require_forecast_mv_refresh_database_url`'s
    own fallback (config.py, not owned by this pass). Neither is reliably set in the deployments this
    refresh has actually needed to run against, which -- per the design this fallback was added
    under -- is the likely reason `agri.mv_forecast_ml_daily_serving` shipped `relispopulated = false`
    and stayed that way: nothing ever supplied a DSN this command would accept. `DATABASE_URL_SYNC` IS
    always configured (every `db-upgrade` needs it -- see `plantgeo-alembic-targets-production` in this
    repo's memory), so it is a third, additional fallback. Its scheme is the SYNCHRONOUS driver
    (`postgresql://` / `postgresql+psycopg2://`); rewritten to `postgresql+asyncpg://` it is a valid
    DSN for the async session this command opens.
    """
    try:
        return settings.require_forecast_mv_refresh_database_url()
    except ValueError:
        pass
    sync_url = settings.database_url_sync.strip()
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError(
        "set FORECAST_MV_REFRESH_DATABASE_URL or DATABASE_URL, or make DATABASE_URL_SYNC a "
        "recognised synchronous PostgreSQL URL"
    )


async def _forecast_refresh_ml_daily() -> int:
    # No capability-role assumption since 20260808_0019 retired the family: the refresher
    # function and the matview it refreshes now belong to the owner credential that calls
    # them, so the refresh is an ordinary owner statement. See alembic/versions/20260808_0019.
    database_url = _forecast_mv_refresh_database_url()
    async with forecast_mv_refresh_session(database_url) as session, session.begin():
        # Non-concurrent REFRESH takes ACCESS EXCLUSIVE on the matview; the timeout is the
        # same bound every other CLI verb sets so a wedged refresh cannot hold that lock.
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        await session.execute(text("SELECT agri.refresh_forecast_ml_daily_serving()"))
        result = await session.execute(text("SELECT count(*) FROM agri.mv_forecast_ml_daily_serving"))
        return int(result.scalar_one())


def _forecast_cli_timestamp(value: str, option_name: str) -> datetime:
    """Parse one timezone-aware ISO-8601 CLI timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter("must be an ISO-8601 timestamp", param_hint=option_name) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise click.BadParameter("must include a UTC offset", param_hint=option_name)
    return parsed.astimezone(UTC)


@cli.command("forecast-run-iteration")
@click.option("--iteration-key", required=True, help="Stable idempotency key for this immutable evaluation.")
@click.option("--series-id", type=click.UUID, required=True)
@click.option("--release-set-id", type=click.UUID, required=True)
@click.option("--as-of-time", required=True, help="Timezone-aware governed data-availability boundary.")
@click.option("--cutoff-time", required=True, help="UTC day start that ends the training history.")
@click.option("--history-start", help="Optional UTC day start; defaults to the first governed observation.")
@click.option("--horizon-days", type=click.IntRange(1, 366), default=30, show_default=True)
@click.option("--simulation-count", type=click.IntRange(100, 10_000), default=1000, show_default=True)
@click.option("--seed", type=click.IntRange(-(2**63), 2**63 - 1), default=0, show_default=True)
@click.option(
    "--gap-policy",
    type=click.Choice(["strict", "locf"], case_sensitive=True),
    default="strict",
    show_default=True,
)
@click.option("--lower-bound", type=float)
@click.option("--upper-bound", type=float)
def forecast_run_iteration(  # noqa: PLR0913
    iteration_key: str,
    series_id: uuid.UUID,
    release_set_id: uuid.UUID,
    as_of_time: str,
    cutoff_time: str,
    history_start: str | None,
    horizon_days: int,
    simulation_count: int,
    seed: int,
    gap_policy: str,
    lower_bound: float | None,
    upper_bound: float | None,
) -> None:
    """Persist one deterministic evaluation-only daily bootstrap iteration."""
    if any(bound is not None and not math.isfinite(bound) for bound in (lower_bound, upper_bound)):
        raise click.BadParameter("must be finite", param_hint="lower-bound/upper-bound")
    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        raise click.BadParameter("must not exceed upper-bound", param_hint="lower-bound")
    try:
        summary = asyncio.run(
            _forecast_run_iteration(
                iteration_key=iteration_key,
                series_id=series_id,
                release_set_id=release_set_id,
                as_of_time=_forecast_cli_timestamp(as_of_time, "as-of-time"),
                cutoff_time=_forecast_cli_timestamp(cutoff_time, "cutoff-time"),
                history_start=(
                    _forecast_cli_timestamp(history_start, "history-start") if history_start is not None else None
                ),
                horizon_days=horizon_days,
                simulation_count=simulation_count,
                seed=seed,
                gap_policy=gap_policy,
                lower_bound=lower_bound,
                upper_bound=upper_bound,
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"forecast iteration failed ({exc.__class__.__name__})" if isinstance(exc, SQLAlchemyError) else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True))


async def _forecast_run_iteration(  # noqa: PLR0913
    *,
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
    database_url = settings.require_forecast_iteration_database_url()
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
    async with forecast_iteration_session(database_url) as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        call_result = await session.execute(_MATERIALIZE_FORECAST_ITERATION, parameters)
        iteration_id = call_result.scalar_one()
        summary_result = await session.execute(
            _FORECAST_ITERATION_SUMMARY,
            {"iteration_id": iteration_id},
        )
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


@cli.command("forecast-reconcile-actuals")
@click.option("--iteration-id", type=click.UUID, required=True)
@click.option(
    "--actual-release-set-id",
    type=click.UUID,
    required=True,
    help="Validated release set containing the later actual observations.",
)
@click.option("--as-of-time", required=True, help="Timezone-aware actual-availability boundary.")
def forecast_reconcile_actuals(
    iteration_id: uuid.UUID,
    actual_release_set_id: uuid.UUID,
    as_of_time: str,
) -> None:
    """Append governed actuals and outcome signals to one forecast iteration."""
    try:
        result = asyncio.run(
            _forecast_reconcile_actuals(
                iteration_id=iteration_id,
                actual_release_set_id=actual_release_set_id,
                as_of_time=_forecast_cli_timestamp(as_of_time, "as-of-time"),
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"forecast actual reconciliation failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(result, sort_keys=True))


async def _forecast_reconcile_actuals(
    *,
    iteration_id: uuid.UUID,
    actual_release_set_id: uuid.UUID,
    as_of_time: datetime,
) -> dict[str, Any]:
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        call_result = await session.execute(
            _RECONCILE_FORECAST_ITERATION_ACTUALS,
            {
                "iteration_id": iteration_id,
                "actual_release_set_id": actual_release_set_id,
                "as_of_time": as_of_time,
            },
        )
        inserted_count = int(call_result.scalar_one())
        result = await session.execute(
            _FORECAST_ITERATION_OUTCOME_TOTALS,
            {"iteration_id": iteration_id},
        )
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


def _forecast_cli_day(value: str, option_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise click.BadParameter("must be an ISO-8601 calendar date", param_hint=option_name) from exc


def _resolved_as_of_time(as_of_time: datetime | None, cutoff_day: date) -> datetime:
    """Resolve the governed availability boundary, refusing a future or pre-cutoff as-of."""
    resolved = as_of_time if as_of_time is not None else datetime.now(tz=UTC)
    if resolved > datetime.now(tz=UTC):
        raise ValueError("as-of boundary cannot be in the future")
    if resolved < datetime.combine(cutoff_day, datetime.min.time(), tzinfo=UTC):
        raise ValueError("as-of boundary cannot precede the cutoff day")
    return resolved


VEGETATION_HORIZON_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("horizon_1_to_7_days", 1, 7),
    ("horizon_8_to_14_days", 8, 14),
    ("horizon_15_to_30_days", 15, 30),
)


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
        "training_day_count_min": (min((outcome.training_day_count for outcome in written), default=None)),
        "training_day_count_max": (max((outcome.training_day_count for outcome in written), default=None)),
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


@cli.command("forecast-vegetation-register")
@click.option("--cutoff-day", required=True, help="Publisher-named UTC day that closes the governed NDVI corpus.")
@click.option("--cell-limit", type=click.IntRange(1, 2000), default=24, show_default=True)
@click.option("--cell-key", "cell_keys", multiple=True, help="Explicit vegetation cell keys; overrides sampling.")
def forecast_vegetation_register(cutoff_day: str, cell_limit: int, cell_keys: tuple[str, ...]) -> None:
    """Register the governed Sentinel-2 NDVI observation plane for a bounded cell selection."""
    try:
        summary = asyncio.run(
            _forecast_vegetation_register(
                cutoff_day=_forecast_cli_day(cutoff_day, "cutoff-day"),
                cell_limit=cell_limit,
                cell_keys=cell_keys,
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"vegetation NDVI plane registration failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True))


async def _forecast_vegetation_register(
    *,
    cutoff_day: date,
    cell_limit: int,
    cell_keys: tuple[str, ...],
) -> dict[str, Any]:
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session, session.begin():
        await pin_determinism(session)
        selected = cell_keys or await select_candidate_cell_keys(
            session,
            cutoff_day=cutoff_day,
            cell_limit=cell_limit,
        )
        summary = await register_governed_plane(session, cutoff_day=cutoff_day, cell_keys=selected)
    payload = _registration_payload(summary)
    payload["cutoff_day"] = cutoff_day.isoformat()
    return payload


@cli.command("forecast-vegetation-simulate")
@click.option("--cutoff-day", required=True, help="Publisher-named UTC day that ends the training history.")
@click.option("--release-cutoff-day", help="Governed release-set cutoff day; defaults to --cutoff-day.")
@click.option("--horizon-days", type=click.IntRange(1, 366), default=30, show_default=True)
@click.option("--simulation-count", type=click.IntRange(100, 10_000), default=1000, show_default=True)
@click.option("--seed", type=click.IntRange(0, 2**31 - 1), default=0, show_default=True)
@click.option(
    "--purpose",
    type=click.Choice([PURPOSE_FORWARD_SIMULATION, PURPOSE_HOLDOUT_EVALUATION], case_sensitive=True),
    default=PURPOSE_FORWARD_SIMULATION,
    show_default=True,
)
@click.option("--cell-key", "cell_keys", multiple=True, help="Restrict to explicit vegetation cell keys.")
@click.option(
    "--as-of-time",
    help="Timezone-aware governed availability boundary; pin it to reproduce a recorded iteration.",
)
def forecast_vegetation_simulate(  # noqa: PLR0913
    cutoff_day: str,
    release_cutoff_day: str | None,
    horizon_days: int,
    simulation_count: int,
    seed: int,
    purpose: str,
    cell_keys: tuple[str, ...],
    as_of_time: str | None,
) -> None:
    """Write one deterministic seasonal-anomaly Monte Carlo iteration per eligible NDVI cell."""
    try:
        summary = asyncio.run(
            _forecast_vegetation_simulate(
                cutoff_day=_forecast_cli_day(cutoff_day, "cutoff-day"),
                release_cutoff_day=(
                    _forecast_cli_day(release_cutoff_day, "release-cutoff-day")
                    if release_cutoff_day is not None
                    else _forecast_cli_day(cutoff_day, "cutoff-day")
                ),
                request=SimulationRequest(
                    horizon_days=horizon_days,
                    simulation_count=simulation_count,
                    seed=seed,
                ),
                purpose=purpose,
                cell_keys=cell_keys,
                as_of_time=(_forecast_cli_timestamp(as_of_time, "as-of-time") if as_of_time is not None else None),
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"vegetation NDVI simulation failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True))


async def _forecast_vegetation_simulate(  # noqa: PLR0913
    *,
    cutoff_day: date,
    release_cutoff_day: date,
    request: SimulationRequest,
    purpose: str,
    cell_keys: tuple[str, ...],
    as_of_time: datetime | None,
) -> dict[str, Any]:
    if cutoff_day > release_cutoff_day:
        raise ValueError("simulation cutoff day cannot follow the governed release-set cutoff day")
    resolved_as_of = _resolved_as_of_time(as_of_time, cutoff_day)
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session, session.begin():
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


@cli.command("forecast-vegetation-evaluate")
@click.option("--release-cutoff-day", required=True, help="Governed release-set cutoff day holding the actuals.")
@click.option(
    "--holdout-cutoff-day",
    "holdout_cutoff_days",
    multiple=True,
    required=True,
    help="Simulated historical cutoff day; repeatable.",
)
@click.option("--horizon-days", type=click.IntRange(1, 366), default=30, show_default=True)
@click.option("--simulation-count", type=click.IntRange(100, 10_000), default=1000, show_default=True)
@click.option("--seed", type=click.IntRange(0, 2**31 - 1), default=0, show_default=True)
@click.option("--cell-key", "cell_keys", multiple=True, help="Restrict to explicit vegetation cell keys.")
@click.option(
    "--as-of-time",
    help="Timezone-aware governed availability boundary; pin it to reproduce a recorded evaluation.",
)
def forecast_vegetation_evaluate(  # noqa: PLR0913
    release_cutoff_day: str,
    holdout_cutoff_days: tuple[str, ...],
    horizon_days: int,
    simulation_count: int,
    seed: int,
    cell_keys: tuple[str, ...],
    as_of_time: str | None,
) -> None:
    """Run time-honest holdout iterations and report method error against its trivial baselines."""
    try:
        summary = asyncio.run(
            _forecast_vegetation_evaluate(
                release_cutoff_day=_forecast_cli_day(release_cutoff_day, "release-cutoff-day"),
                holdout_cutoff_days=tuple(
                    _forecast_cli_day(value, "holdout-cutoff-day") for value in holdout_cutoff_days
                ),
                request=SimulationRequest(
                    horizon_days=horizon_days,
                    simulation_count=simulation_count,
                    seed=seed,
                ),
                cell_keys=cell_keys,
                as_of_time=(_forecast_cli_timestamp(as_of_time, "as-of-time") if as_of_time is not None else None),
            )
        )
    except (SQLAlchemyError, ValueError) as exc:
        reason = (
            f"vegetation NDVI holdout evaluation failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True))


async def _forecast_vegetation_evaluate(
    *,
    release_cutoff_day: date,
    holdout_cutoff_days: tuple[date, ...],
    request: SimulationRequest,
    cell_keys: tuple[str, ...],
    as_of_time: datetime | None,
) -> dict[str, Any]:
    ordered_cutoffs = tuple(sorted(set(holdout_cutoff_days)))
    if any(cutoff >= release_cutoff_day for cutoff in ordered_cutoffs):
        raise ValueError("every holdout cutoff day must precede the governed release-set cutoff day")
    resolved_as_of = _resolved_as_of_time(as_of_time, max(ordered_cutoffs))
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session, session.begin():
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


@cli.command("forecast-train-wind")
@click.option("--cell-id", type=click.UUID, required=True, help="Spatial cell whose covariate vectors train the fit.")
@click.option("--series-id", type=click.UUID, required=True, help="Forecast series the metrics are filed under.")
@click.option("--history-start", required=True, help="First covariate day to read, as YYYY-MM-DD.")
@click.option("--history-end", required=True, help="Last covariate day to read, as YYYY-MM-DD.")
@click.option("--origin-date", required=True, help="Newest rolling origin, as YYYY-MM-DD.")
@click.option(
    "--origins",
    "origin_count",
    type=click.IntRange(1, 60),
    default=1,
    show_default=True,
    help="Rolling origins to refit and score, walking back from --origin-date.",
)
@click.option(
    "--origin-stride-days",
    type=click.IntRange(1, 366),
    default=None,
    help="Days between rolling origins; defaults to --horizon-count so their target spans do not overlap.",
)
@click.option("--horizon-count", type=click.IntRange(1, 366), default=30, show_default=True)
@click.option("--calibration-days", type=click.IntRange(1, 3660), default=180, show_default=True)
@click.option("--alpha", type=float, default=10.0, show_default=True, help="Ridge penalty on standardized features.")
@click.option(
    "--as-of-time",
    default=None,
    help="Availability gate fed to p_as_of_time; defaults to now(). Pin it to reproduce a manifest checksum.",
)
@click.option(
    "--quality-policy-key",
    default=None,
    help="Existing agri.forecast_quality_policy the backtest run references. Required with --persist.",
)
@click.option(
    "--persist",
    is_flag=True,
    default=False,
    help="Write the training/backtest receipt chain. OFF by default; without it nothing is written.",
)
@click.option("--json", "as_json", is_flag=True, default=True, show_default=True, help="Emit one JSON line.")
def forecast_train_wind(  # noqa: PLR0913 - one parameter per click option, as this file's own verbs are
    cell_id: uuid.UUID,
    series_id: uuid.UUID,
    history_start: str,
    history_end: str,
    origin_date: str,
    origin_count: int,
    origin_stride_days: int | None,
    horizon_count: int,
    calibration_days: int,
    alpha: float,
    as_of_time: str | None,
    quality_policy_key: str | None,
    persist: bool,
    as_json: bool,
) -> None:
    """Fit and score the evaluation-only covariate wind ridge over rolling origins; optionally record it.

    READ-ONLY BY DEFAULT. Without `--persist` this opens no write transaction at all: it reads the
    pinned covariate vectors and the availability-gated target, refits the model at every origin,
    and prints one JSON line. That is the behaviour this module has always had.

    With `--persist` it additionally writes ONE governed training receipt chain -- a job run, a
    model artifact, a forecast model, a validated feature snapshot, a validated training run, a
    STAGED forecast run and one backtest metric row per origin -- through
    `agri.validate_forecast_feature_snapshot` and `agri.validate_forecast_training_run`, which
    re-derive every lineage check server-side. It writes no forecast receipt, no forecast value
    and no publication, so nothing it records can reach a serving surface.

    EVALUATION ONLY. The metrics prove the framework runs end to end. They are not an operational
    forecast and are not life-safety validated. Read `origin_count` before `evaluated_count`: the
    horizons scored from one origin are consecutive days of an autocorrelated variable.
    """
    if persist and not quality_policy_key:
        raise click.BadParameter(
            "--persist requires --quality-policy-key: a forecast run must reference a reviewed "
            "quality policy, and this lane will not invent one",
            param_hint="--quality-policy-key",
        )
    try:
        request = WindTrainingRequest(
            cell_id=str(cell_id),
            series_id=str(series_id),
            history_start=_forecast_cli_day(history_start, "history-start"),
            history_end=_forecast_cli_day(history_end, "history-end"),
            origin_date=_forecast_cli_day(origin_date, "origin-date"),
            as_of_time=(
                _forecast_cli_timestamp(as_of_time, "as-of-time") if as_of_time is not None else datetime.now(tz=UTC)
            ),
            horizon_count=horizon_count,
            calibration_days=calibration_days,
            origin_count=origin_count,
            origin_stride_days=origin_stride_days,
            alpha=alpha,
            quality_policy_key=quality_policy_key,
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    try:
        summary = asyncio.run(_forecast_train_wind(request, persist=persist))
    except (SQLAlchemyError, OriginNotEvaluableError, ForecastTrainingPersistError, ValueError) as exc:
        reason = (
            f"covariate wind training failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True) if as_json else json.dumps(summary, indent=2, sort_keys=True))


async def _forecast_train_wind(request: WindTrainingRequest, *, persist: bool) -> dict[str, Any]:
    """Open the evaluation-writer session, run the fit, and commit only when persistence was asked for."""
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session:
        report = await run_covariate_wind_training(session, request, persist=persist)
        if persist:
            await session.commit()
        else:
            # An explicit rollback rather than trusting the session's close: a read-only run must
            # leave nothing behind even if a future edit starts writing on a path it does not today.
            await session.rollback()
    return report.to_summary()


@cli.command("forecast-train-wind-plan")
@click.option(
    "--target",
    "targets",
    multiple=True,
    required=True,
    help="A cell and its series as CELL_UUID:SERIES_UUID; repeatable, one per trained entity.",
)
@click.option("--history-start", required=True, help="First covariate day each batch reads, as YYYY-MM-DD.")
@click.option("--history-end", required=True, help="Last covariate day each batch reads, as YYYY-MM-DD.")
@click.option("--newest-origin", required=True, help="Frontier origin the newest batch ends at, as YYYY-MM-DD.")
@click.option("--batches", "batch_count", type=click.IntRange(1, 200), default=1, show_default=True)
@click.option("--origins-per-batch", type=click.IntRange(1, 60), default=4, show_default=True)
@click.option("--origin-stride-days", type=click.IntRange(1, 366), default=None)
@click.option("--horizon-count", type=click.IntRange(1, 366), default=30, show_default=True)
@click.option("--calibration-days", type=click.IntRange(1, 3660), default=180, show_default=True)
@click.option("--alpha", type=float, default=10.0, show_default=True)
@click.option("--quality-policy-key", required=True, help="Existing agri.forecast_quality_policy each batch cites.")
@click.option(
    "--as-of-time",
    required=True,
    help="PINNED availability gate for the whole run. Not optional: an unpinned gate moves under a run in flight.",
)
def forecast_train_wind_plan(  # noqa: PLR0913 - one parameter per click option, as this file's own verbs are
    targets: tuple[str, ...],
    history_start: str,
    history_end: str,
    newest_origin: str,
    batch_count: int,
    origins_per_batch: int,
    origin_stride_days: int | None,
    horizon_count: int,
    calibration_days: int,
    alpha: float,
    quality_policy_key: str,
    as_of_time: str,
) -> None:
    """Declare the covariate wind training lane and fan its (cell, origin batch) shards out, idempotently.

    Safe to re-run. `open_job_run` inserts the run `ON CONFLICT (logical_run_key) DO NOTHING` and
    each shard `ON CONFLICT (job_run_id, shard_key) DO NOTHING`, and the shard keys come from the
    plan's own pinned grid, so replanning the same declared shape adds nothing and re-keys nothing.

    `agri-cli forecast-train-wind-run` is what then works the shards, one bounded slice per tick,
    on the same lease/fence/budget runtime the archive lanes use. Note it is NOT `jobs-run`: that
    verb opens the source-loader DSN, and a forecast receipt must be written through the
    evaluation-writer DSN instead. A `jobs-run` pointed at this definition fails loudly with an
    unbound-context error rather than writing through the wrong role.

    EXIT CODES -- always 0 unless the plan itself could not be written. A lane that owes every one
    of its batches the moment it is planned is the normal, healthy state.
    """
    try:
        plan = CovariateWindLanePlan(
            targets=covariate_wind_targets([_parse_wind_target(entry) for entry in targets]),
            history_start=_forecast_cli_day(history_start, "history-start"),
            history_end=_forecast_cli_day(history_end, "history-end"),
            newest_origin=_forecast_cli_day(newest_origin, "newest-origin"),
            as_of_time=_forecast_cli_timestamp(as_of_time, "as-of-time"),
            quality_policy_key=quality_policy_key,
            batch_count=batch_count,
            origins_per_batch=origins_per_batch,
            origin_stride_days=origin_stride_days,
            horizon_count=horizon_count,
            calibration_days=calibration_days,
            alpha=alpha,
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc
    try:
        summary = asyncio.run(_forecast_train_wind_plan(plan))
    except (SQLAlchemyError, JobRunError, JobSpecificationError, JobLedgerRowError) as exc:
        reason = (
            f"planning the covariate wind lane failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary, sort_keys=True))


def _parse_wind_target(entry: str) -> tuple[str, str]:
    """Read one `CELL_UUID:SERIES_UUID` pair, refusing anything that is not two UUIDs."""
    cell_text, separator, series_text = entry.partition(":")
    if not separator:
        raise click.BadParameter(f"{entry!r} must be CELL_UUID:SERIES_UUID", param_hint="--target")
    try:
        return str(uuid.UUID(cell_text.strip())), str(uuid.UUID(series_text.strip()))
    except ValueError as exc:
        raise click.BadParameter(f"{entry!r} must name two UUIDs", param_hint="--target") from exc


async def _forecast_train_wind_plan(plan: CovariateWindLanePlan) -> dict[str, Any]:
    """Open one session for the definition upsert and the fan-out, and commit them together."""
    database_url = settings.require_forecast_iteration_database_url()
    async with forecast_iteration_session(database_url) as session:
        await session.execute(text("SET LOCAL statement_timeout = '120s'"))
        opened = await plan_covariate_wind_lane(session, plan, requested_by="agri-cli forecast-train-wind-plan")
        await session.commit()
    return {
        "definition": TRAINING_DEFINITION_NAME,
        "run_key": opened.logical_run_key,
        "job_run_id": str(opened.job_run_id),
        "created": opened.created,
        "added_work_items": opened.added_work_items,
        "total_work_items": opened.total_work_items,
        "run_status": opened.status,
        "target_count": len(plan.targets),
        "batch_count": plan.batch_count,
        "origins_per_batch": plan.origins_per_batch,
        "origin_stride_days": plan.effective_stride_days,
        "newest_origin": plan.newest_origin.isoformat(),
        "as_of_time": plan.as_of_time.isoformat(),
    }


@cli.command("forecast-train-wind-run")
@click.option("--budget-seconds", type=float, default=None, help="Override the definition's own slice budget.")
@click.option("--worker-id", default=None, help="Label this lease owner; defaults to a per-process id.")
@click.pass_context
def forecast_train_wind_run(context: click.Context, budget_seconds: float | None, worker_id: str | None) -> None:
    """Run ONE bounded slice of the covariate wind training lane: claim, train, record, exit.

    This is the forecast lane's own slice runner and not `jobs-run`, for one reason that is not
    cosmetic: `jobs-run` opens `ingest_session()`, which is the source-loader DSN, and a forecast
    receipt must be written through the evaluation-writer DSN. Sharing the runner would mean
    writing governed forecast rows through the ingestion role.

    EXIT CODES, matching `jobs-run`:

      0 -- the slice ran. Work may remain, the budget may be spent, nothing may have been
           claimable: all three are healthy multi-tick states and none is an incident.
      1 -- a work item DEAD-LETTERED during this slice, or the slice itself raised.

    A dead-lettered batch means every attempt failed and no receipt exists for that (cell, origin
    batch) until someone requeues it. That is the one outcome that needs a human, and it stays
    visible as missing rather than being quietly marked done.
    """
    try:
        summary = asyncio.run(
            _forecast_train_wind_slice(
                worker_id=(worker_id or "").strip() or f"forecast-train-wind:{uuid.uuid4()}",
                budget_seconds=budget_seconds,
            )
        )
    except (
        JobDefinitionNotFoundError,
        JobLedgerRowError,
        JobRunError,
        JobSpecificationError,
        UnknownJobHandlerError,
        SQLAlchemyError,
    ) as exc:
        reason = (
            f"covariate wind training slice failed ({exc.__class__.__name__})"
            if isinstance(exc, SQLAlchemyError)
            else str(exc)
        )
        raise click.ClickException(reason) from exc
    click.echo(json.dumps(summary.to_summary(), sort_keys=True))
    if summary.dead_lettered:
        context.exit(1)


async def _forecast_train_wind_slice(*, worker_id: str, budget_seconds: float | None) -> JobSliceSummary:
    """Open one evaluation-writer session for the whole tick and drive one bounded slice through it.

    ONE session for the slice and never one per work item: `forecast_iteration_session` builds a
    new engine per `async with` and disposes it in its `finally`, so a per-shard binding would be
    a full handshake per batch. `shutdown_signal()` is installed HERE because this is the process
    boundary -- without it a SIGTERM strands the batch in hand behind a lease no living process
    owns. See jobs/AGENTS.md "Shutdown and heartbeat semantics".
    """
    database_url = settings.require_forecast_iteration_database_url()
    async with (
        forecast_iteration_session(database_url) as session,
        shutdown_signal() as stop,
        covariate_wind_lane_context(CovariateWindLaneContext(session=session)),
    ):
        return await run_job_slice(
            session,
            definition_name=TRAINING_DEFINITION_NAME,
            worker_id=worker_id,
            budget_seconds=budget_seconds,
            stop=stop,
        )


@cli.command("job-logs-maintain")
@click.option(
    "--retention-days",
    type=click.IntRange(1, 365),
    default=30,
    show_default=True,
)
@click.option(
    "--future-days",
    type=click.IntRange(1, 31),
    default=7,
    show_default=True,
)
def job_logs_maintain(retention_days: int, future_days: int) -> None:
    """Maintain UTC job-event partitions and the hot retention window."""
    asyncio.run(_job_logs_maintain(retention_days, future_days))


async def _job_logs_maintain(retention_days: int, future_days: int) -> None:
    try:
        async with combined_local_engine().begin() as connection:
            result = await maintain_job_event_partitions(
                connection,
                now=datetime.now().astimezone(),
                retention_days=retention_days,
                future_days=future_days,
            )
    except (MaintenanceBusyError, OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result.to_dict(), indent=2, sort_keys=True))


@cli.group("local")
def local_execution() -> None:
    """Manage local-only resumable model and forecast runs."""


@local_execution.command("init")
@click.option("--job-name", required=True)
@click.option("--job-version", required=True)
@click.option("--scheduled-for", required=True, help="Timezone-aware ISO-8601 timestamp.")
@click.option("--release-set-id", type=click.UUID, required=True)
@click.option("--release-set-manifest-checksum", required=True)
@click.option("--recipe-version")
@click.option("--model-version")
@click.option(
    "--run-plan",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
def local_init(  # noqa: PLR0913
    job_name: str,
    job_version: str,
    scheduled_for: str,
    release_set_id: uuid.UUID,
    release_set_manifest_checksum: str,
    recipe_version: str | None,
    model_version: str | None,
    run_plan: Path,
) -> None:
    """Create or resume a deterministic local run directory."""
    store = LocalRunStore(settings.local_execution_root)
    try:
        _require_sha256(release_set_manifest_checksum, "release-set-manifest-checksum")
        partitions, expected_shards, expected_outputs = _load_run_plan(run_plan)
        manifest = store.initialize(
            job_name=job_name,
            job_version=job_version,
            scheduled_for=_parse_datetime(scheduled_for),
            release_set_id=release_set_id,
            release_set_manifest_checksum=release_set_manifest_checksum,
            recipe_version=recipe_version,
            model_version=model_version,
            partitions=partitions,
            expected_shards=expected_shards,
            expected_outputs=expected_outputs,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            {
                "run_id": str(manifest.run_id),
                "logical_run_key": manifest.logical_run_key,
                "run_directory": str(store.run_directory(manifest.run_id)),
                "state": manifest.state,
                "algorithm_started": False,
            },
            indent=2,
        )
    )


@local_execution.command("status")
@click.argument("run_id", type=click.UUID)
def local_status(run_id: uuid.UUID) -> None:
    """Print the durable local manifest without starting work."""
    try:
        manifest = LocalRunStore(settings.local_execution_root).load(run_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(manifest.model_dump_json(indent=2))


@local_execution.command("checkpoint")
@click.argument("run_id", type=click.UUID)
@click.option("--shard-key", required=True)
@click.option("--cursor-file", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--progress", type=click.FloatRange(0, 1), required=True)
def local_checkpoint(
    run_id: uuid.UUID,
    shard_key: str,
    cursor_file: Path,
    progress: float,
) -> None:
    """Append a resumable cursor after a bounded unit of local work."""
    try:
        cursor = json.loads(cursor_file.read_text(encoding="utf-8"))
        if not isinstance(cursor, dict):
            raise ValueError("checkpoint cursor must be a JSON object")
        checkpoint = LocalRunStore(settings.local_execution_root).checkpoint(
            run_id,
            shard_key=shard_key,
            cursor=cursor,
            progress_fraction=progress,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(checkpoint.model_dump_json(indent=2))


@local_execution.command("interrupt")
@click.argument("run_id", type=click.UUID)
def local_interrupt(run_id: uuid.UUID) -> None:
    """Record a clean interruption so the same run can resume later."""
    try:
        manifest = LocalRunStore(settings.local_execution_root).mark_interrupted(run_id)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(manifest.model_dump_json(indent=2))


@local_execution.command("resume")
@click.argument("run_id", type=click.UUID)
@click.option("--shard-key", required=True)
def local_resume(run_id: uuid.UUID, shard_key: str) -> None:
    """Print the verified latest cursor for a shard."""
    try:
        cursor = LocalRunStore(settings.local_execution_root).resume_cursor(run_id, shard_key=shard_key)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(cursor, indent=2, sort_keys=True))


@local_execution.command("register-output")
@click.argument("run_id", type=click.UUID)
@click.option("--output-key", required=True)
@click.option("--kind", required=True)
@click.option("--artifact", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--validation-report", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--media-type", default="application/octet-stream", show_default=True)
@click.option("--row-count", type=click.IntRange(min=0))
def local_register_output(  # noqa: PLR0913
    run_id: uuid.UUID,
    output_key: str,
    kind: str,
    artifact: Path,
    validation_report: Path,
    media_type: str,
    row_count: int | None,
) -> None:
    """Freeze an artifact only after an explicit passing validation report."""
    try:
        output = LocalRunStore(settings.local_execution_root).register_output(
            run_id,
            output_key=output_key,
            kind=kind,
            artifact_path=artifact,
            validation_report_path=validation_report,
            media_type=media_type,
            row_count=row_count,
            max_validation_bytes=settings.local_publish_max_validation_bytes,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(output.model_dump_json(indent=2))


@local_execution.command("finalize")
@click.argument("run_id", type=click.UUID)
@click.option(
    "--run-validation-report",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
def local_finalize(run_id: uuid.UUID, run_validation_report: Path) -> None:
    """Freeze a complete run after exact coverage and run-level validation."""
    try:
        manifest = LocalRunStore(settings.local_execution_root).finalize_validation(
            run_id,
            run_validation_report_path=run_validation_report,
            max_validation_bytes=settings.local_publish_max_validation_bytes,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(manifest.model_dump_json(indent=2))


@local_execution.command("publish")
@click.argument("run_id", type=click.UUID)
@click.option("--product", required=True)
@click.option("--scope-key", required=True)
@click.option("--api-url", help="Override LOCAL_PUBLISH_API_URL; token remains environment-only.")
def local_publish(
    run_id: uuid.UUID,
    product: str,
    scope_key: str,
    api_url: str | None,
) -> None:
    """Resume bounded publication through the authenticated service API."""
    publish_url = api_url or settings.local_publish_api_url
    token = settings.local_publish_token
    if not publish_url or token is None:
        raise click.ClickException("publication is disabled; configure LOCAL_PUBLISH_API_URL and LOCAL_PUBLISH_TOKEN")
    publisher = BoundedPublisher(
        base_url=publish_url,
        token=token.get_secret_value(),
        max_artifact_bytes=settings.local_publish_max_artifact_bytes,
        max_validation_bytes=settings.local_publish_max_validation_bytes,
        max_outputs=settings.local_publish_max_outputs,
        max_run_artifact_bytes=settings.local_publish_max_run_artifact_bytes,
        max_run_validation_bytes=settings.local_publish_max_run_validation_bytes,
        retry_attempts=settings.local_publish_retry_attempts,
        retry_base_seconds=settings.local_publish_retry_base_seconds,
    )
    try:
        result = publisher.publish(
            LocalRunStore(settings.local_execution_root),
            run_id,
            product=product,
            scope_key=scope_key,
        )
    except (OSError, ValueError, PublicationError) as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        publisher.close()
    click.echo(json.dumps(result, indent=2, sort_keys=True))


@cli.command("source-ingest")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--payload", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def source_ingest(plan: Path, payload: Path) -> None:
    """Publish one local, reviewed GeoJSON source release to the warehouse."""
    asyncio.run(_source_ingest(plan, payload))


async def _source_ingest(plan_path: Path, payload_path: Path) -> None:
    checkpoint: SourceIngestionCheckpoint | None = None
    path: Path | None = None
    plan: SourceIngestionPlan | None = None
    checksum: str | None = None
    plan_checksum: str | None = None
    release_manifest_checksum: str | None = None
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = SourceIngestionPlan.model_validate_json(plan_path.read_bytes())
        payload, quality_summary = load_and_validate_geojson(payload_path)
        checksum = hashlib.sha256(payload).hexdigest()
        plan_checksum = source_ingestion_plan_checksum(plan)
        release_manifest_checksum = release_set_manifest(plan, checksum)
        path = checkpoint_path(settings.local_execution_root, plan, checksum)
        existing = load_checkpoint(path) if path.exists() else None
        if existing is not None:
            if (
                existing.schema_version != SOURCE_INGESTION_CHECKPOINT_SCHEMA_VERSION
                or existing.plan_checksum != plan_checksum
                or existing.release_set_manifest_checksum != release_manifest_checksum
            ):
                raise ValueError("existing checkpoint does not bind the reviewed source-ingestion plan")
            if existing.state == "published":
                click.echo(existing.model_dump_json(indent=2))
                return
        write_checkpoint(
            path,
            SourceIngestionCheckpoint(
                state="validated",
                source_key=plan.source.key,
                source_version=plan.release.source_version,
                payload_checksum=checksum,
                payload_bytes=len(payload),
                updated_at=datetime.now().astimezone(),
                plan_checksum=plan_checksum,
                release_set_manifest_checksum=release_manifest_checksum,
            ),
        )
        async with local_source_loader_session(loader_database_url) as session, session.begin():
            result = await publish_source_release(session, plan, payload, quality_summary)
        checkpoint = SourceIngestionCheckpoint(
            state="published",
            source_key=plan.source.key,
            source_version=plan.release.source_version,
            payload_checksum=checksum,
            payload_bytes=len(payload),
            updated_at=datetime.now().astimezone(),
            plan_checksum=plan_checksum,
            release_set_manifest_checksum=release_manifest_checksum,
            source_release_id=result.source_release_id,
            artifact_id=result.artifact_id,
            release_set_id=result.release_set_id,
        )
        write_checkpoint(path, checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        reason = _source_ingestion_failure_reason(exc)
        if (
            path is not None
            and plan is not None
            and checksum is not None
            and plan_checksum is not None
            and release_manifest_checksum is not None
        ):
            with suppress(OSError):
                write_checkpoint(
                    path,
                    SourceIngestionCheckpoint(
                        state="blocked",
                        source_key=plan.source.key,
                        source_version=plan.release.source_version,
                        payload_checksum=checksum,
                        payload_bytes=len(payload),
                        updated_at=datetime.now().astimezone(),
                        plan_checksum=plan_checksum,
                        release_set_manifest_checksum=release_manifest_checksum,
                        reason=reason,
                    ),
                )
        raise click.ClickException(reason) from exc
    if checkpoint is None or path is None:
        raise click.ClickException("source ingestion did not produce a checkpoint")
    click.echo(json.dumps({**checkpoint.model_dump(mode="json"), "checkpoint": str(path)}, indent=2, default=str))


@cli.command("source-ingest-status")
@click.argument("checkpoint", type=click.Path(path_type=Path, exists=True, dir_okay=False))
def source_ingest_status(checkpoint: Path) -> None:
    """Read a local source-ingestion checkpoint without touching the warehouse."""
    try:
        value = load_checkpoint(checkpoint)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(value.model_dump_json(indent=2))


@cli.command("historical-nasa-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_nasa_backfill(plan: Path) -> None:
    """Locally fetch, validate, and persist one reviewed NASA POWER backfill."""
    asyncio.run(_historical_nasa_backfill(plan))


async def _historical_nasa_backfill(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalNasaBackfillPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_nasa_checkpoint_path(settings.local_execution_root, plan)
        checkpoint = (
            load_historical_nasa_checkpoint(checkpoint_path_value)
            if checkpoint_path_value.exists()
            else initialize_historical_nasa_checkpoint(plan)
        )
        if checkpoint.plan_checksum != historical_nasa_plan_checksum(plan):
            raise ValueError("historical checkpoint does not bind the reviewed plan")
        if checkpoint.state == "blocked" and {receipt.cell_key for receipt in checkpoint.receipts} == {
            cell.cell_key for cell in plan.nasa.cells
        }:
            checkpoint = checkpoint.model_copy(
                update={"state": "validated", "updated_at": datetime.now().astimezone(), "reason": None}
            )
        write_historical_nasa_checkpoint(checkpoint_path_value, checkpoint)
        completed_cells = {receipt.cell_key for receipt in checkpoint.receipts}
        # One engine for the whole verb, one session and one transaction per cell: the per-cell
        # write boundary is unchanged, but the connect handshake is paid once instead of per cell.
        async with local_source_loader_engine(loader_database_url) as loader_session:
            for cell in plan.nasa.cells:
                if cell.cell_key in completed_cells:
                    continue
                result = load_cached_historical_nasa_result(settings.local_execution_root, plan, cell)
                if result is None:
                    result = await fetch_nasa_power_daily(plan.nasa, cell)
                    cache_historical_nasa_result(settings.local_execution_root, plan, result)
                async with loader_session() as session, session.begin():
                    await persist_nasa_power_cell(session, plan=plan, result=result)
                checkpoint = record_historical_nasa_result(plan, checkpoint, result)
                write_historical_nasa_checkpoint(checkpoint_path_value, checkpoint)
                completed_cells.add(cell.cell_key)
            if checkpoint.state != "validated":
                raise ValueError("historical backfill did not produce complete source-cell coverage")
            release_set = None
            if all(receipt.retrieved_at <= plan.release_set_as_of for receipt in checkpoint.receipts):
                async with loader_session() as session, session.begin():
                    release_set = await finalize_nasa_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError, httpx.HTTPError) as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        reason = _historical_nasa_failure_reason(exc)
        raise click.ClickException(reason) from exc
    click.echo(
        json.dumps(
            {
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "source_cell_count": len(checkpoint.receipts),
                "release_set_id": None if release_set is None else str(release_set.release_set_id),
                "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
                "release_set_idempotent": None if release_set is None else release_set.idempotent,
                "finalization_required": release_set is None,
            },
            indent=2,
        )
    )


@cli.command("historical-nasa-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_nasa_status(plan: Path) -> None:
    """Read the durable local NASA historical checkpoint without network or database access."""
    try:
        value = HistoricalNasaBackfillPlan.model_validate_json(plan.read_bytes())
        path = historical_nasa_checkpoint_path(settings.local_execution_root, value)
        checkpoint = load_historical_nasa_checkpoint(path)
        if checkpoint.plan_checksum != historical_nasa_plan_checksum(value):
            raise ValueError("historical checkpoint does not bind the reviewed plan")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(checkpoint.model_dump_json(indent=2))


@cli.command("historical-nasa-materialize-parquet")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_nasa_materialize_parquet(plan: Path) -> None:
    """Build one local daily-partitioned Parquet lake from complete cached NASA receipts."""
    try:
        value = HistoricalNasaBackfillPlan.model_validate_json(plan.read_bytes())
        checkpoint_path_value = historical_nasa_checkpoint_path(settings.local_execution_root, value)
        checkpoint = load_historical_nasa_checkpoint(checkpoint_path_value)
        manifest = materialize_historical_nasa_parquet(settings.local_execution_root, value, checkpoint)
    except (OSError, ValueError) as exc:
        raise click.ClickException(_historical_nasa_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "dataset_root": str(historical_nasa_parquet_root(settings.local_execution_root, value)),
                **manifest.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@cli.command("historical-nasa-finalize")
@click.option("--source-plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--finalization", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output-plan", type=click.Path(path_type=Path, dir_okay=False), required=True)
def historical_nasa_finalize(source_plan: Path, finalization: Path, output_plan: Path) -> None:
    """Finalize completed NASA POWER receipts under a later governed as-of time."""
    asyncio.run(_historical_nasa_finalize(source_plan, finalization, output_plan))


async def _historical_nasa_finalize(
    source_plan_path: Path,
    finalization_path: Path,
    output_plan_path: Path,
) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        source_plan = HistoricalNasaBackfillPlan.model_validate_json(source_plan_path.read_bytes())
        finalization = HistoricalNasaFinalization.model_validate_json(finalization_path.read_bytes())
        source_checkpoint_path = historical_nasa_checkpoint_path(settings.local_execution_root, source_plan)
        source_checkpoint = load_historical_nasa_checkpoint(source_checkpoint_path)
        release_plan, checkpoint = rebind_historical_nasa_checkpoint_for_finalization(
            source_plan,
            finalization,
            source_checkpoint,
            updated_at=datetime.now(UTC),
        )
        checkpoint_path_value = historical_nasa_checkpoint_path(settings.local_execution_root, release_plan)
        write_historical_nasa_checkpoint(checkpoint_path_value, checkpoint)
        async with local_source_loader_session(loader_database_url) as session, session.begin():
            release_set = await finalize_nasa_release_set(session, plan=release_plan, checkpoint=checkpoint)
        write_historical_nasa_release_plan(output_plan_path, release_plan)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_nasa_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "source_checkpoint": str(source_checkpoint_path),
                "checkpoint": str(checkpoint_path_value),
                "release_plan": str(output_plan_path),
                "state": checkpoint.state,
                "source_cell_count": len(checkpoint.receipts),
                "release_set_key": release_plan.release_set_key,
                "release_set_id": str(release_set.release_set_id),
                "release_set_manifest_checksum": release_set.manifest_checksum,
                "release_set_idempotent": release_set.idempotent,
            },
            indent=2,
        )
    )


@cli.command("historical-era5-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_era5_backfill(plan: Path) -> None:
    """Fetch, validate, and cache one reviewed ERA5-Land historical source plan."""
    asyncio.run(_historical_era5_backfill(plan))


async def _historical_era5_backfill(plan_path: Path) -> None:
    try:
        plan = HistoricalEra5LandBackfillPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_era5_checkpoint_path(settings.local_execution_root, plan)
        checkpoint = (
            load_historical_era5_checkpoint(checkpoint_path_value)
            if checkpoint_path_value.exists()
            else initialize_historical_era5_checkpoint(plan)
        )
        if checkpoint.plan_checksum != historical_era5_plan_checksum(plan):
            raise ValueError("ERA5 checkpoint does not bind the reviewed plan")
        expected_period_keys = {period.key for period in plan.periods}
        if (
            checkpoint.state == "blocked"
            and {receipt.period_key for receipt in checkpoint.receipts} == expected_period_keys
        ):
            checkpoint = checkpoint.model_copy(
                update={"state": "validated", "updated_at": datetime.now(UTC), "reason": None}
            )
        write_historical_era5_checkpoint(checkpoint_path_value, checkpoint)
        completed_period_keys = {receipt.period_key for receipt in checkpoint.receipts}
        for period in plan.periods:
            if period.key in completed_period_keys:
                continue
            # Off-thread: the cached replay parses a whole month's GRIB payload, and doing it inline
            # blocks the event loop for the entire parse on every period.
            result = await asyncio.to_thread(
                load_cached_historical_era5_result,
                settings.local_execution_root,
                plan,
                period,
                cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
            )
            if result is None:
                result = await fetch_era5_land_monthly(plan, period)
                cache_historical_era5_result(settings.local_execution_root, plan, result)
            checkpoint = record_historical_era5_result(plan, checkpoint, result)
            write_historical_era5_checkpoint(checkpoint_path_value, checkpoint)
            completed_period_keys.add(period.key)
        if checkpoint.state != "validated":
            raise ValueError("ERA5 backfill did not produce complete monthly source coverage")
    except Exception as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_era5_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_era5_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "source_month_count": len(checkpoint.receipts),
                "release_receipt_manifest_checksum": historical_era5_release_manifest(plan, checkpoint),
                "next_steps": ["historical-era5-persist", "historical-era5-materialize-parquet"],
            },
            indent=2,
        )
    )


@cli.command("historical-era5-persist")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_era5_persist(plan: Path) -> None:
    """Persist one complete cache-backed ERA5-Land source plan into the local warehouse."""
    asyncio.run(_historical_era5_persist(plan))


async def _historical_era5_persist(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalEra5LandBackfillPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_era5_checkpoint_path(settings.local_execution_root, plan)
        checkpoint = load_historical_era5_checkpoint(checkpoint_path_value)
        if checkpoint.plan_checksum != historical_era5_plan_checksum(plan) or checkpoint.state != "validated":
            raise ValueError("ERA5 persistence requires a complete validated matching checkpoint")
        for period in plan.periods:
            # Off-thread: see the note in the backfill verb -- the same month-sized parse, and here
            # it sits directly in front of an awaited warehouse write.
            result = await asyncio.to_thread(
                load_cached_historical_era5_result,
                settings.local_execution_root,
                plan,
                period,
                cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
            )
            if result is None:
                raise ValueError("ERA5 persistence requires every validated local raw archive")
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                await persist_era5_land_month(session, plan=plan, result=result)
        release_set = None
        if all(receipt.retrieved_at <= plan.release_set_as_of for receipt in checkpoint.receipts):
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                release_set = await finalize_era5_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_era5_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "source_month_count": len(checkpoint.receipts),
                "release_set_id": None if release_set is None else str(release_set.release_set_id),
                "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
                "release_set_idempotent": None if release_set is None else release_set.idempotent,
                "finalization_required": release_set is None,
            },
            indent=2,
        )
    )


class LaneChunk(Protocol):
    """One bounded unit of work a chunked lane fetches; `key` is the token its receipt cites."""

    @property
    def key(self) -> str: ...


class LaneReceipt(Protocol):
    """One chunk a lane already completed, as recorded in its checkpoint."""

    @property
    def chunk_key(self) -> str: ...


class LanePlan[ChunkT: LaneChunk](Protocol):
    """The reviewed-plan surface the shared chunked-lane driver reads."""

    @property
    def cells(self) -> Sequence[object]: ...

    @property
    def chunks(self) -> Sequence[ChunkT]: ...


class LaneCheckpoint(Protocol):
    """The durable resumable state a chunked lane rewrites after every recorded chunk."""

    @property
    def state(self) -> str: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def receipts(self) -> Sequence[LaneReceipt]: ...

    def model_copy(self, *, update: Mapping[str, Any]) -> Self: ...


class LaneChunkRunner[PlanT, ChunkT, ResultT](Protocol):
    """The bounded-concurrency wave runner a chunked lane exposes."""

    async def __call__(
        self,
        plan: PlanT,
        chunks: Sequence[ChunkT],
        *,
        concurrency: int,
    ) -> Sequence[ResultT | BaseException]: ...


@dataclass(frozen=True)
class ChunkedLane[ChunkT: LaneChunk, PlanT: LanePlan[Any], CheckpointT: LaneCheckpoint, ResultT]:
    """One chunked fetch lane's bindings, so status, resume, wave fetch and blocked-write exist once.

    Open-Meteo, GloFAS, CAMS and Ensemble ran four hand-copied versions of the methods below, which
    had already drifted; a fix to resume semantics now lands in one place. The per-lane differences
    that are real -- the failure-reason ladder, the payload keys, the error token -- stay per-lane
    fields rather than being unified away.

    Every field is filled by a factory called from inside a verb body, never at import, so the
    module-level name each one names is resolved at call time. That is what keeps a test's
    `monkeypatch.setattr(agri_data_service.cli, ...)` intercepting: an import-time binding would
    capture the original function and make the patch a silent no-op.

    The four type parameters are not decoration. They are what makes mypy refuse a cams `record_*`
    bound into the glofas lane -- exactly the copy-paste mistake this collapse exists to prevent.
    """

    error_token: str
    parse_plan: Callable[[bytes], PlanT]
    plan_checksum: Callable[[PlanT], str]
    checkpoint_path: Callable[[Path, PlanT], Path]
    initialize_checkpoint: Callable[[PlanT], CheckpointT]
    read_checkpoint: Callable[[Path], CheckpointT]
    rederive_checkpoint: Callable[[PlanT, CheckpointT], CheckpointT]
    write_checkpoint: Callable[[Path, CheckpointT], None]
    load_cached_result: Callable[[Path, PlanT, ChunkT], ResultT | None]
    cache_result: Callable[[Path, PlanT, ResultT], object]
    record_result: Callable[[PlanT, CheckpointT, ResultT], CheckpointT]
    run_chunks: LaneChunkRunner[PlanT, ChunkT, ResultT]
    release_manifest: Callable[[PlanT, CheckpointT], str]
    failure_reason: Callable[[BaseException], str]
    status_identity: Callable[[PlanT], dict[str, Any]]
    status_totals: Callable[[PlanT, CheckpointT], dict[str, Any]]
    backfill_extras: Callable[[PlanT, CheckpointT], dict[str, Any]]

    def load_checkpoint(self, plan: PlanT, checkpoint_path_value: Path) -> CheckpointT:
        """Load a plan-bound checkpoint, re-deriving `state` from receipts rather than trusting the file.

        A `blocked` checkpoint whose chunks are all receipted has nothing left to fetch, so trusting
        the stored value would strand it: nothing would ever move it off `blocked`.
        """
        if not checkpoint_path_value.exists():
            return self.initialize_checkpoint(plan)
        return self.rederive_checkpoint(plan, self.read_checkpoint(checkpoint_path_value))

    def write_blocked_checkpoint(self, path: Path, checkpoint: CheckpointT, exc: Exception) -> None:
        """Record why a run stopped so a resume starts from evidence rather than a rerun of everything."""
        with suppress(OSError, ValueError):
            self.write_checkpoint(
                path,
                checkpoint.model_copy(
                    update={
                        "state": "blocked",
                        "updated_at": datetime.now(UTC),
                        "reason": self.failure_reason(exc),
                    }
                ),
            )

    async def fetch_chunks(
        self,
        plan: PlanT,
        checkpoint: CheckpointT,
        checkpoint_path_value: Path,
        chunks: Sequence[ChunkT],
        concurrency: int,
    ) -> tuple[CheckpointT, list[dict[str, str]]]:
        """Reuse the local cache first, fetch the rest under bounded concurrency, and keep failures visible."""
        failures: list[dict[str, str]] = []
        pending: list[ChunkT] = []
        for chunk in chunks:
            cached = self.load_cached_result(settings.local_execution_root, plan, chunk)
            if cached is None:
                pending.append(chunk)
                continue
            checkpoint = self.record_result(plan, checkpoint, cached)
            self.write_checkpoint(checkpoint_path_value, checkpoint)
        # Harvested in waves of `concurrency` rather than one gather over everything, so an interrupted
        # long run keeps every chunk that already answered instead of discarding the whole batch.
        for start in range(0, len(pending), concurrency):
            wave = pending[start : start + concurrency]
            results = await self.run_chunks(plan, wave, concurrency=concurrency)
            for chunk, result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    failures.append({"chunk_key": chunk.key, "reason": self.failure_reason(result)})
                    continue
                self.cache_result(settings.local_execution_root, plan, result)
                checkpoint = self.record_result(plan, checkpoint, result)
                self.write_checkpoint(checkpoint_path_value, checkpoint)
            if failures:
                # A quota wall does not clear inside one run; stop rather than burn the remaining waves.
                break
        return checkpoint, failures

    def report_status(self, plan_path: Path) -> None:
        """Report which chunks are already cached and what a resume would still fetch."""
        try:
            plan = self.parse_plan(plan_path.read_bytes())
            checkpoint_path_value = self.checkpoint_path(settings.local_execution_root, plan)
            checkpoint = self.load_checkpoint(plan, checkpoint_path_value)
        except (OSError, ValueError) as exc:
            raise click.ClickException(self.failure_reason(exc)) from exc
        completed = {receipt.chunk_key for receipt in checkpoint.receipts}
        pending = [chunk.key for chunk in plan.chunks if chunk.key not in completed]
        click.echo(
            json.dumps(
                {
                    "plan_checksum": self.plan_checksum(plan),
                    "checkpoint": str(checkpoint_path_value),
                    "state": checkpoint.state,
                    "reason": checkpoint.reason,
                    **self.status_identity(plan),
                    "cell_count": len(plan.cells),
                    "chunk_count": len(plan.chunks),
                    "completed_chunk_count": len(completed),
                    "pending_chunk_count": len(pending),
                    "pending_chunks": pending[:_OPEN_METEO_PENDING_PREVIEW],
                    **self.status_totals(plan, checkpoint),
                },
                indent=2,
            )
        )

    async def run_backfill(self, plan_path: Path, max_chunks: int | None, concurrency: int) -> None:
        """Resume from the checkpoint, fetch a bounded batch, and never report a partial run as success."""
        failures: list[dict[str, str]] = []
        try:
            plan = self.parse_plan(plan_path.read_bytes())
            checkpoint_path_value = self.checkpoint_path(settings.local_execution_root, plan)
            checkpoint = self.load_checkpoint(plan, checkpoint_path_value)
            self.write_checkpoint(checkpoint_path_value, checkpoint)
            completed = {receipt.chunk_key for receipt in checkpoint.receipts}
            outstanding = [chunk for chunk in plan.chunks if chunk.key not in completed]
            checkpoint, failures = await self.fetch_chunks(
                plan,
                checkpoint,
                checkpoint_path_value,
                outstanding if max_chunks is None else outstanding[:max_chunks],
                concurrency,
            )
            extras = self.backfill_extras(plan, checkpoint)
        except Exception as exc:
            if "checkpoint_path_value" in locals() and "checkpoint" in locals():
                self.write_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
            raise click.ClickException(self.failure_reason(exc)) from exc
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        remaining = [chunk.key for chunk in plan.chunks if chunk.key not in receipted]
        payload = {
            "checkpoint": str(checkpoint_path_value),
            "state": checkpoint.state,
            "completed_chunk_count": len(checkpoint.receipts),
            "chunk_count": len(plan.chunks),
            "pending_chunk_count": len(remaining),
            "failed_chunks": failures,
            "release_receipt_manifest_checksum": (
                self.release_manifest(plan, checkpoint) if checkpoint.state == "validated" else None
            ),
            **extras,
        }
        if failures:
            # A chunk that dropped records must never read as success: record why, report, exit non-zero.
            self.write_blocked_checkpoint(
                checkpoint_path_value,
                checkpoint,
                ValueError(f"{len(failures)} chunk(s) failed; first: {failures[0]['reason']}"),
            )
            raise click.ClickException(json.dumps({**payload, "error": self.error_token}, indent=2))
        click.echo(json.dumps(payload, indent=2))


def _open_meteo_lane() -> ChunkedLane[
    OpenMeteoArchiveChunk,
    HistoricalOpenMeteoArchivePlan,
    HistoricalOpenMeteoCheckpoint,
    OpenMeteoArchiveChunkResult,
]:
    """Bind the Open-Meteo ERA5-Land archive lane, reading every module-level name at call time."""
    return ChunkedLane(
        error_token="open_meteo_chunks_failed",
        parse_plan=HistoricalOpenMeteoArchivePlan.model_validate_json,
        plan_checksum=historical_open_meteo_plan_checksum,
        checkpoint_path=historical_open_meteo_checkpoint_path,
        initialize_checkpoint=initialize_historical_open_meteo_checkpoint,
        read_checkpoint=load_historical_open_meteo_checkpoint,
        rederive_checkpoint=rederive_historical_open_meteo_checkpoint_state,
        write_checkpoint=write_historical_open_meteo_checkpoint,
        load_cached_result=load_cached_historical_open_meteo_result,
        cache_result=cache_historical_open_meteo_result,
        record_result=record_historical_open_meteo_result,
        run_chunks=run_open_meteo_archive_chunks,
        release_manifest=historical_open_meteo_release_manifest,
        failure_reason=_historical_open_meteo_failure_reason,
        status_identity=lambda _plan: {},
        status_totals=lambda _plan, checkpoint: {
            "observed_value_count": sum(receipt.observed_value_count for receipt in checkpoint.receipts),
            "no_data_series_count": sum(receipt.no_data_series_count for receipt in checkpoint.receipts),
        },
        backfill_extras=lambda _plan, _checkpoint: {"next_steps": ["historical-open-meteo-persist"]},
    )


@cli.command("historical-open-meteo-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_open_meteo_status(plan: Path) -> None:
    """Report which archive chunks are already cached and what a resume would still fetch."""
    _open_meteo_lane().report_status(plan)


@cli.command("historical-open-meteo-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--max-chunks", type=click.IntRange(min=1), default=None, help="Stop after this many new chunks.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2)
def historical_open_meteo_backfill(plan: Path, max_chunks: int | None, concurrency: int) -> None:
    """Fetch, validate, and cache reviewed Open-Meteo ERA5-Land archive chunks; resumable and bounded."""
    asyncio.run(_open_meteo_lane().run_backfill(plan, max_chunks, concurrency))


@cli.command("historical-open-meteo-persist")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_open_meteo_persist(plan: Path) -> None:
    """Persist every cached Open-Meteo archive chunk into the warehouse; finalize only when complete."""
    asyncio.run(_historical_open_meteo_persist(plan))


async def _historical_open_meteo_persist(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalOpenMeteoArchivePlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_open_meteo_checkpoint_path(settings.local_execution_root, plan)
        if not checkpoint_path_value.exists():
            raise ValueError("Open-Meteo archive persistence requires a matching checkpoint")
        checkpoint = _open_meteo_lane().load_checkpoint(plan, checkpoint_path_value)
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        persisted: list[dict[str, object]] = []
        observed_by_parameter: dict[str, int] = {}
        # One engine for the whole verb, one session and one transaction per chunk: the per-chunk
        # write boundary is unchanged, but the connect handshake is paid once instead of per chunk.
        async with local_source_loader_engine(loader_database_url) as loader_session:
            for chunk in plan.chunks:
                if chunk.key not in receipted:
                    continue
                result = load_cached_historical_open_meteo_result(settings.local_execution_root, plan, chunk)
                if result is None:
                    raise ValueError("Open-Meteo archive persistence requires every receipted local chunk document")
                persisted.append(await _persist_open_meteo_chunk(loader_session, plan, result))
                for parameter, observed in open_meteo_observed_values_by_parameter(result).items():
                    observed_by_parameter[parameter] = observed_by_parameter.get(parameter, 0) + observed
            # Coverage complete but the as-of time predates a receipt is a governance failure, not a
            # wait-and-resume state: the chunks an operator would be sent to fetch do not exist.
            stale_chunk_keys = sorted(
                receipt.chunk_key for receipt in checkpoint.receipts if receipt.retrieved_at > plan.release_set_as_of
            )
            # Neither is a variable that came back empty in every reviewed cell: there is nothing to
            # resume, because the fetch already succeeded. See execution/AGENTS.md §historical_open_meteo.
            unanswered = (
                unanswered_open_meteo_parameters(plan, observed_by_parameter) if checkpoint.state == "validated" else ()
            )
            release_set = None
            if checkpoint.state == "validated" and not stale_chunk_keys and not unanswered:
                async with loader_session() as session, session.begin():
                    release_set = await finalize_open_meteo_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_open_meteo_failure_reason(exc)) from exc
    payload = {
        "checkpoint": str(checkpoint_path_value),
        "state": checkpoint.state,
        "persisted_chunk_count": len(persisted),
        "chunk_count": len(plan.chunks),
        "observation_row_count": sum(int(cast("int", item["observation_count"])) for item in persisted),
        "observed_value_count": sum(int(cast("int", item["observed_value_count"])) for item in persisted),
        "no_data_series_count": sum(int(cast("int", item["no_data_series_count"])) for item in persisted),
        "release_set_id": None if release_set is None else str(release_set.release_set_id),
        "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
        "finalization_blocked_by_incomplete_coverage": (
            release_set is None and not stale_chunk_keys and not unanswered
        ),
        "finalization_blocked_by_stale_release_set_as_of": bool(stale_chunk_keys),
        "finalization_blocked_by_unanswered_parameters": bool(unanswered),
        "release_set_as_of": plan.release_set_as_of.isoformat(),
        "stale_receipt_chunk_keys": stale_chunk_keys[:_OPEN_METEO_PENDING_PREVIEW],
        "unanswered_parameters": list(unanswered),
    }
    if stale_chunk_keys:
        raise click.ClickException(
            json.dumps({**payload, "error": "open_meteo_release_set_as_of_precedes_a_persisted_receipt"}, indent=2)
        )
    if unanswered:
        raise click.ClickException(
            json.dumps({**payload, "error": "open_meteo_parameters_answered_no_values"}, indent=2)
        )
    click.echo(json.dumps(payload, indent=2))


async def _persist_open_meteo_chunk(
    loader_session: async_sessionmaker[AsyncSession],
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> dict[str, object]:
    """Persist one chunk in its own transaction so a later chunk's failure never rolls back an earlier one."""
    async with loader_session() as session, session.begin():
        written = await persist_open_meteo_archive_chunk(session, plan=plan, result=result)
    return {
        "chunk_key": result.chunk_key,
        "observation_count": written.observation_count,
        "observed_value_count": written.observed_value_count,
        "no_data_series_count": written.no_data_series_count,
    }


# Both flood and air-quality lanes fetch, validate and cache; neither has a warehouse writer yet, so
# there is deliberately no `-persist` verb and a validated checkpoint is a local cache, not a release.
_WAREHOUSE_PERSISTENCE_NOT_IMPLEMENTED = "not_implemented"


def _glofas_lane() -> ChunkedLane[
    GlofasFloodChunk,
    HistoricalGlofasFloodPlan,
    HistoricalGlofasCheckpoint,
    GlofasFloodChunkResult,
]:
    """Bind the GloFAS river-discharge lane, reading every module-level name at call time."""
    return ChunkedLane(
        error_token="glofas_chunks_failed",
        parse_plan=HistoricalGlofasFloodPlan.model_validate_json,
        plan_checksum=historical_glofas_plan_checksum,
        checkpoint_path=historical_glofas_checkpoint_path,
        initialize_checkpoint=initialize_historical_glofas_checkpoint,
        read_checkpoint=load_historical_glofas_checkpoint,
        rederive_checkpoint=rederive_historical_glofas_checkpoint_state,
        write_checkpoint=write_historical_glofas_checkpoint,
        load_cached_result=load_cached_historical_glofas_result,
        cache_result=cache_historical_glofas_result,
        record_result=record_historical_glofas_result,
        run_chunks=run_glofas_flood_chunks,
        release_manifest=historical_glofas_release_manifest,
        failure_reason=_historical_glofas_failure_reason,
        status_identity=lambda plan: {"model": plan.model, "support_key": plan.support_key},
        status_totals=lambda _plan, checkpoint: {
            "observed_value_count": sum(receipt.observed_value_count for receipt in checkpoint.receipts),
            "no_data_series_count": sum(receipt.no_data_series_count for receipt in checkpoint.receipts),
        },
        backfill_extras=lambda _plan, _checkpoint: {"next_steps": ["historical-glofas-persist"]},
    )


@cli.command("historical-glofas-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_glofas_status(plan: Path) -> None:
    """Report which GloFAS flood chunks are already cached and what a resume would still fetch."""
    _glofas_lane().report_status(plan)


@cli.command("historical-glofas-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--max-chunks", type=click.IntRange(min=1), default=None, help="Stop after this many new chunks.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2)
def historical_glofas_backfill(plan: Path, max_chunks: int | None, concurrency: int) -> None:
    """Fetch, validate, and cache reviewed GloFAS river-discharge chunks; resumable and bounded."""
    asyncio.run(_glofas_lane().run_backfill(plan, max_chunks, concurrency))


@cli.command("historical-glofas-persist")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_glofas_persist(plan: Path) -> None:
    """Persist every cached GloFAS flood chunk into the warehouse; finalize only when complete."""
    asyncio.run(_historical_glofas_persist(plan))


async def _historical_glofas_persist(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalGlofasFloodPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_glofas_checkpoint_path(settings.local_execution_root, plan)
        if not checkpoint_path_value.exists():
            raise ValueError("GloFAS flood persistence requires a matching checkpoint")
        checkpoint = _glofas_lane().load_checkpoint(plan, checkpoint_path_value)
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        persisted: list[dict[str, object]] = []
        async with local_source_loader_engine(loader_database_url) as loader_session:
            for chunk in plan.chunks:
                if chunk.key not in receipted:
                    continue
                result = load_cached_historical_glofas_result(settings.local_execution_root, plan, chunk)
                if result is None:
                    raise ValueError("GloFAS flood persistence requires every receipted local chunk document")
                async with loader_session() as session, session.begin():
                    written = await persist_glofas_flood_chunk(session, plan=plan, result=result)
                persisted.append(
                    {
                        "chunk_key": result.chunk_key,
                        "observation_count": written.observation_count,
                        "observed_value_count": written.observed_value_count,
                        "no_data_series_count": written.no_data_series_count,
                    }
                )
            release_set = None
            if checkpoint.state == "validated":
                async with loader_session() as session, session.begin():
                    release_set = await finalize_glofas_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_glofas_failure_reason(exc)) from exc
    payload = {
        "checkpoint": str(checkpoint_path_value),
        "state": checkpoint.state,
        "persisted_chunk_count": len(persisted),
        "chunk_count": len(plan.chunks),
        "observation_row_count": sum(int(cast("int", item["observation_count"])) for item in persisted),
        "observed_value_count": sum(int(cast("int", item["observed_value_count"])) for item in persisted),
        "no_data_series_count": sum(int(cast("int", item["no_data_series_count"])) for item in persisted),
        "release_set_id": None if release_set is None else str(release_set.release_set_id),
        "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
    }
    click.echo(json.dumps(payload, indent=2))


def _historical_glofas_failure_reason(exc: BaseException) -> str:
    """Name the provider condition an operator must act on rather than collapsing it to a class name."""
    if isinstance(exc, GlofasFloodFetchError | ValueError):
        return str(exc)
    return f"GloFAS flood operation failed ({exc.__class__.__name__})"


def _cams_lane() -> ChunkedLane[
    CamsAirQualityChunk,
    HistoricalCamsAirQualityPlan,
    HistoricalCamsCheckpoint,
    CamsAirQualityChunkResult,
]:
    """Bind the CAMS air-quality lane, reading every module-level name at call time."""
    return ChunkedLane(
        error_token="cams_chunks_failed",
        parse_plan=HistoricalCamsAirQualityPlan.model_validate_json,
        plan_checksum=historical_cams_plan_checksum,
        checkpoint_path=historical_cams_checkpoint_path,
        initialize_checkpoint=initialize_historical_cams_checkpoint,
        read_checkpoint=load_historical_cams_checkpoint,
        rederive_checkpoint=rederive_historical_cams_checkpoint_state,
        write_checkpoint=write_historical_cams_checkpoint,
        load_cached_result=load_cached_historical_cams_result,
        cache_result=cache_historical_cams_result,
        record_result=record_historical_cams_result,
        run_chunks=run_cams_air_quality_chunks,
        release_manifest=historical_cams_release_manifest,
        failure_reason=_historical_cams_failure_reason,
        status_identity=lambda plan: {
            "domain": plan.domain,
            "support_key": plan.support_key,
            "day_block_count": len(plan.day_blocks),
        },
        status_totals=lambda _plan, checkpoint: {
            "observed_value_count": sum(receipt.observed_value_count for receipt in checkpoint.receipts),
            "insufficient_hour_day_count": sum(receipt.insufficient_hour_day_count for receipt in checkpoint.receipts),
            "no_data_series_count": sum(receipt.no_data_series_count for receipt in checkpoint.receipts),
            "failed_series_count": sum(receipt.failed_series_count for receipt in checkpoint.receipts),
        },
        backfill_extras=lambda _plan, _checkpoint: {"next_steps": ["historical-cams-persist"]},
    )


@cli.command("historical-cams-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_cams_status(plan: Path) -> None:
    """Report which CAMS air-quality chunks are already cached and what a resume would still fetch."""
    _cams_lane().report_status(plan)


@cli.command("historical-cams-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--max-chunks", type=click.IntRange(min=1), default=None, help="Stop after this many new chunks.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2)
def historical_cams_backfill(plan: Path, max_chunks: int | None, concurrency: int) -> None:
    """Fetch, validate, and cache reviewed CAMS air-quality chunks; resumable and bounded."""
    asyncio.run(_cams_lane().run_backfill(plan, max_chunks, concurrency))


@cli.command("historical-cams-persist")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_cams_persist(plan: Path) -> None:
    """Persist every cached CAMS air-quality chunk into the warehouse; finalize only when complete."""
    asyncio.run(_historical_cams_persist(plan))


async def _historical_cams_persist(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalCamsAirQualityPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_cams_checkpoint_path(settings.local_execution_root, plan)
        if not checkpoint_path_value.exists():
            raise ValueError("CAMS air-quality persistence requires a matching checkpoint")
        checkpoint = _cams_lane().load_checkpoint(plan, checkpoint_path_value)
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        persisted: list[dict[str, object]] = []
        async with local_source_loader_engine(loader_database_url) as loader_session:
            for chunk in plan.chunks:
                if chunk.key not in receipted:
                    continue
                result = load_cached_historical_cams_result(settings.local_execution_root, plan, chunk)
                if result is None:
                    raise ValueError("CAMS air-quality persistence requires every receipted local chunk document")
                async with loader_session() as session, session.begin():
                    written = await persist_cams_air_quality_chunk(session, plan=plan, result=result)
                persisted.append(
                    {
                        "chunk_key": result.chunk_key,
                        "observation_count": written.observation_count,
                        "observed_value_count": written.observed_value_count,
                        "insufficient_hour_day_count": written.insufficient_hour_day_count,
                        "no_data_series_count": written.no_data_series_count,
                    }
                )
            release_set = None
            if checkpoint.state == "validated":
                async with loader_session() as session, session.begin():
                    release_set = await finalize_cams_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_cams_failure_reason(exc)) from exc
    payload = {
        "checkpoint": str(checkpoint_path_value),
        "state": checkpoint.state,
        "persisted_chunk_count": len(persisted),
        "chunk_count": len(plan.chunks),
        "observation_row_count": sum(int(cast("int", item["observation_count"])) for item in persisted),
        "observed_value_count": sum(int(cast("int", item["observed_value_count"])) for item in persisted),
        "insufficient_hour_day_count": sum(int(cast("int", item["insufficient_hour_day_count"])) for item in persisted),
        "no_data_series_count": sum(int(cast("int", item["no_data_series_count"])) for item in persisted),
        "release_set_id": None if release_set is None else str(release_set.release_set_id),
        "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
    }
    click.echo(json.dumps(payload, indent=2))


def _historical_cams_failure_reason(exc: BaseException) -> str:
    """Name the provider condition an operator must act on rather than collapsing it to a class name."""
    if isinstance(exc, CamsAirQualityFetchError | ValueError):
        return str(exc)
    return f"CAMS air-quality operation failed ({exc.__class__.__name__})"


def _ensemble_forecast_lane() -> ChunkedLane[
    EnsembleForecastChunk,
    EnsembleForecastPlan,
    EnsembleForecastCheckpoint,
    EnsembleForecastChunkResult,
]:
    """Bind the Open-Meteo Ensemble forecast lane, reading every module-level name at call time."""
    return ChunkedLane(
        error_token="ensemble_chunks_failed",
        parse_plan=EnsembleForecastPlan.model_validate_json,
        plan_checksum=ensemble_forecast_plan_checksum,
        checkpoint_path=ensemble_forecast_checkpoint_path,
        initialize_checkpoint=initialize_ensemble_forecast_checkpoint,
        read_checkpoint=load_ensemble_forecast_checkpoint,
        rederive_checkpoint=rederive_ensemble_forecast_checkpoint_state,
        write_checkpoint=write_ensemble_forecast_checkpoint,
        load_cached_result=load_cached_ensemble_forecast_result,
        cache_result=cache_ensemble_forecast_result,
        record_result=record_ensemble_forecast_result,
        run_chunks=run_ensemble_forecast_chunks,
        release_manifest=ensemble_forecast_release_manifest,
        failure_reason=_forecast_ensemble_failure_reason,
        status_identity=lambda plan: {
            "model": plan.model,
            "member_count": plan.member_count,
            "support_key": plan.support_key,
            "issue_time": plan.issue_time.isoformat(),
            "horizon_step_count": plan.step_count,
            "quantile_levels": plan.quantile_levels,
        },
        status_totals=lambda plan, checkpoint: {
            **_ensemble_forecast_receipt_totals(checkpoint),
            "staged_document": str(ensemble_forecast_staged_document_path(settings.local_execution_root, plan)),
            "warehouse_persistence": ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE,
        },
        # Staging runs here rather than after the driver returns so a staging failure still lands in
        # the blocked checkpoint, exactly as it did when this lane had its own backfill body.
        backfill_extras=lambda plan, checkpoint: {
            **_ensemble_forecast_receipt_totals(checkpoint),
            **_stage_ensemble_forecast_document(plan, checkpoint),
            "warehouse_persistence": ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE,
        },
    )


def _ensemble_forecast_receipt_totals(checkpoint: EnsembleForecastCheckpoint) -> dict[str, Any]:
    """Sum the per-chunk staging counters both the status and fetch payloads report."""
    return {
        "staged_receipt_count": sum(receipt.staged_receipt_count for receipt in checkpoint.receipts),
        "staged_value_count": sum(receipt.staged_value_count for receipt in checkpoint.receipts),
        "failed_series_count": sum(receipt.failed_series_count for receipt in checkpoint.receipts),
    }


@cli.command("forecast-ensemble-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def forecast_ensemble_status(plan: Path) -> None:
    """Report which Open-Meteo Ensemble chunks are already cached and what a resume would still fetch."""
    _ensemble_forecast_lane().report_status(plan)


@cli.command("forecast-ensemble-fetch")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--max-chunks", type=click.IntRange(min=1), default=None, help="Stop after this many new chunks.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2)
def forecast_ensemble_fetch(plan: Path, max_chunks: int | None, concurrency: int) -> None:
    """Fetch reviewed Open-Meteo Ensemble chunks and stage their quantile receipts; resumable and bounded."""
    asyncio.run(_ensemble_forecast_lane().run_backfill(plan, max_chunks, concurrency))


def _stage_ensemble_forecast_document(
    plan: EnsembleForecastPlan,
    checkpoint: EnsembleForecastCheckpoint,
) -> dict[str, object]:
    """Write the staged receipt document only once every reviewed chunk is cached and accounted for."""
    if checkpoint.state != "validated":
        return {"staged_document": None, "staged_document_checksum": None}
    receipts: list[StagedForecastReceipt] = []
    for chunk in plan.chunks:
        cached = load_cached_ensemble_forecast_result(settings.local_execution_root, plan, chunk)
        if cached is None:
            raise ValueError(f"ensemble forecast chunk {chunk.key} is receipted but no longer cached")
        receipts.extend(cached.receipts)
    document_path = ensemble_forecast_staged_document_path(settings.local_execution_root, plan)
    document = ensemble_forecast_staged_document(plan, receipts)
    checksum = write_ensemble_forecast_staged_document(document_path, document)
    return {"staged_document": str(document_path), "staged_document_checksum": checksum}


def _forecast_ensemble_failure_reason(exc: BaseException) -> str:
    """Name the provider condition an operator must act on rather than collapsing it to a class name."""
    if isinstance(exc, EnsembleForecastFetchError | ValueError):
        return str(exc)
    return f"ensemble forecast operation failed ({exc.__class__.__name__})"


@cli.command("historical-era5-materialize-parquet")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_era5_materialize_parquet(plan: Path) -> None:
    """Build the local daily-partitioned ERA5 Parquet lake from cached source receipts."""
    try:
        value = HistoricalEra5LandBackfillPlan.model_validate_json(plan.read_bytes())
        checkpoint_path_value = historical_era5_checkpoint_path(settings.local_execution_root, value)
        checkpoint = load_historical_era5_checkpoint(checkpoint_path_value)
        manifest = materialize_historical_era5_parquet(settings.local_execution_root, value, checkpoint)
    except (OSError, ValueError) as exc:
        raise click.ClickException(_historical_era5_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "dataset_root": str(historical_era5_parquet_root(settings.local_execution_root, value)),
                **manifest.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@cli.command("historical-era5-finalize")
@click.option("--source-plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--finalization", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output-plan", type=click.Path(path_type=Path, dir_okay=False), required=True)
def historical_era5_finalize(source_plan: Path, finalization: Path, output_plan: Path) -> None:
    """Finalize cache-backed ERA5 monthly receipts under a later governed as-of time."""
    asyncio.run(_historical_era5_finalize(source_plan, finalization, output_plan))


async def _historical_era5_finalize(
    source_plan_path: Path,
    finalization_path: Path,
    output_plan_path: Path,
) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        source_plan = HistoricalEra5LandBackfillPlan.model_validate_json(source_plan_path.read_bytes())
        finalization = HistoricalEra5Finalization.model_validate_json(finalization_path.read_bytes())
        source_checkpoint_path = historical_era5_checkpoint_path(settings.local_execution_root, source_plan)
        source_checkpoint = load_historical_era5_checkpoint(source_checkpoint_path)
        release_plan, checkpoint = rebind_historical_era5_checkpoint_for_finalization(
            source_plan,
            finalization,
            source_checkpoint,
            updated_at=datetime.now(UTC),
        )
        checkpoint_path_value = historical_era5_checkpoint_path(settings.local_execution_root, release_plan)
        write_historical_era5_checkpoint(checkpoint_path_value, checkpoint)
        write_historical_era5_release_plan(output_plan_path, release_plan)
        for period in release_plan.periods:
            # Off-thread: see the note in the backfill verb -- the same month-sized parse.
            result = await asyncio.to_thread(
                load_cached_historical_era5_result,
                settings.local_execution_root,
                release_plan,
                period,
                cache_plan_checksum=checkpoint.raw_cache_plan_checksum,
            )
            if result is None:
                raise ValueError("ERA5 finalization requires every validated local raw archive")
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                await persist_era5_land_month(session, plan=release_plan, result=result)
        async with local_source_loader_session(loader_database_url) as session, session.begin():
            release_set = await finalize_era5_release_set(session, plan=release_plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_era5_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_era5_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "source_checkpoint": str(source_checkpoint_path),
                "checkpoint": str(checkpoint_path_value),
                "release_plan": str(output_plan_path),
                "state": checkpoint.state,
                "source_month_count": len(checkpoint.receipts),
                "release_set_key": release_plan.release_set_key,
                "release_set_id": str(release_set.release_set_id),
                "release_set_manifest_checksum": release_set.manifest_checksum,
                "release_set_idempotent": release_set.idempotent,
            },
            indent=2,
        )
    )


@cli.command("historical-usdm-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_usdm_backfill(plan: Path) -> None:
    """Locally fetch, validate, persist, and finalize one reviewed USDM four-year backfill."""
    asyncio.run(_historical_usdm_backfill(plan))


async def _historical_usdm_backfill(plan_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        plan = HistoricalUsdmBackfillPlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_usdm_checkpoint_path(settings.local_execution_root, plan)
        checkpoint = (
            load_historical_usdm_checkpoint(checkpoint_path_value)
            if checkpoint_path_value.exists()
            else initialize_historical_usdm_checkpoint(plan)
        )
        if checkpoint.plan_checksum != historical_usdm_plan_checksum(plan):
            raise ValueError("historical USDM checkpoint does not bind the reviewed plan")
        if checkpoint.state == "blocked" and {receipt.issue_date for receipt in checkpoint.receipts} == set(
            plan.issue_dates
        ):
            checkpoint = checkpoint.model_copy(
                update={"state": "validated", "updated_at": datetime.now().astimezone(), "reason": None}
            )
        write_historical_usdm_checkpoint(checkpoint_path_value, checkpoint)
        completed_dates = {receipt.issue_date for receipt in checkpoint.receipts}
        for issue_date in plan.issue_dates:
            if issue_date in completed_dates:
                continue
            result = await fetch_usdm_shapefile(plan, issue_date)
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                await persist_usdm_shapefile(session, plan=plan, result=result)
            checkpoint = record_historical_usdm_result(plan, checkpoint, result)
            write_historical_usdm_checkpoint(checkpoint_path_value, checkpoint)
            completed_dates.add(issue_date)
        if checkpoint.state != "validated":
            raise ValueError("historical USDM backfill did not produce complete weekly coverage")
        async with local_source_loader_session(loader_database_url) as session, session.begin():
            release_set = await finalize_usdm_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError, httpx.HTTPError) as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_usdm_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_usdm_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "weekly_source_release_count": len(checkpoint.receipts),
                "release_set_id": str(release_set.release_set_id),
                "release_set_manifest_checksum": release_set.manifest_checksum,
                "release_set_idempotent": release_set.idempotent,
            },
            indent=2,
        )
    )


@cli.command("historical-usdm-finalize")
@click.option("--source-plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--finalization", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_usdm_finalize(source_plan: Path, finalization: Path) -> None:
    """Finalize completed USDM source receipts under a later governed as-of time."""
    asyncio.run(_historical_usdm_finalize(source_plan, finalization))


async def _historical_usdm_finalize(source_plan_path: Path, finalization_path: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        source_plan = HistoricalUsdmBackfillPlan.model_validate_json(source_plan_path.read_bytes())
        finalization = HistoricalUsdmFinalization.model_validate_json(finalization_path.read_bytes())
        source_checkpoint_path = historical_usdm_checkpoint_path(settings.local_execution_root, source_plan)
        source_checkpoint = load_historical_usdm_checkpoint(source_checkpoint_path)
        release_plan, checkpoint = rebind_historical_usdm_checkpoint_for_finalization(
            source_plan,
            finalization,
            source_checkpoint,
            updated_at=datetime.now(UTC),
        )
        checkpoint_path_value = historical_usdm_checkpoint_path(settings.local_execution_root, release_plan)
        write_historical_usdm_checkpoint(checkpoint_path_value, checkpoint)
        async with local_source_loader_session(loader_database_url) as session, session.begin():
            release_set = await finalize_usdm_release_set(session, plan=release_plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_usdm_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_usdm_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "source_checkpoint": str(source_checkpoint_path),
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "weekly_source_release_count": len(checkpoint.receipts),
                "release_set_key": release_plan.release_set_key,
                "release_set_id": str(release_set.release_set_id),
                "release_set_manifest_checksum": release_set.manifest_checksum,
                "release_set_idempotent": release_set.idempotent,
            },
            indent=2,
        )
    )


@cli.command("historical-usdm-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_usdm_status(plan: Path) -> None:
    """Read the durable local USDM checkpoint without network or warehouse access."""
    try:
        value = HistoricalUsdmBackfillPlan.model_validate_json(plan.read_bytes())
        path = historical_usdm_checkpoint_path(settings.local_execution_root, value)
        checkpoint = load_historical_usdm_checkpoint(path)
        if checkpoint.plan_checksum != historical_usdm_plan_checksum(value):
            raise ValueError("historical USDM checkpoint does not bind the reviewed plan")
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(checkpoint.model_dump_json(indent=2))


@cli.command("historical-plan-continue")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option(
    "--output-directory",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Where the continuation plan is written. Defaults to the source plan's own directory.",
)
@click.option(
    "--minimum-advance-days",
    type=click.IntRange(min=1),
    default=MINIMUM_CONTINUATION_ADVANCE_DAYS,
    show_default=True,
    help="Refuse to author a continuation that buys fewer new days than this.",
)
@click.option(
    "--as-of-horizon-days",
    type=click.IntRange(min=1),
    default=CONTINUATION_AS_OF_HORIZON_DAYS,
    show_default=True,
    help="How far past today the new release_set_as_of is placed.",
)
@click.option(
    "--probe-cells",
    type=click.IntRange(min=1),
    default=FRONTIER_PROBE_CELL_COUNT,
    show_default=True,
    help="How many lattice cells the provider frontier is measured at.",
)
@click.option(
    "--end-date",
    default=None,
    help="Skip the provider probe and declare the frontier as YYYY-MM-DD. The operator owns its honesty.",
)
@click.option(
    "--allow-incomplete",
    is_flag=True,
    default=False,
    help="Continue a plan the durable driver has not yet marked complete.",
)
@click.option("--write", is_flag=True, default=False, help="Write the plan. Off by default; nothing is written.")
def historical_plan_continue(  # noqa: PLR0913 - one parameter per click option, as this file's own verbs are
    plan: Path,
    output_directory: Path | None,
    minimum_advance_days: int,
    as_of_horizon_days: int,
    probe_cells: int,
    end_date: str | None,
    allow_incomplete: bool,
    write: bool,
) -> None:
    """Author the forward continuation of one completed fixed-window historical backfill plan."""
    asyncio.run(
        _historical_plan_continue(
            plan,
            output_directory,
            minimum_advance_days,
            as_of_horizon_days,
            probe_cells,
            end_date,
            allow_incomplete=allow_incomplete,
            write=write,
        )
    )


async def _historical_plan_continue(  # noqa: PLR0913 - mirrors its verb's options one for one
    plan_path: Path,
    output_directory: Path | None,
    minimum_advance_days: int,
    as_of_horizon_days: int,
    probe_cells: int,
    end_date: str | None,
    *,
    allow_incomplete: bool,
    write: bool,
) -> None:
    try:
        source = load_continuation_source(plan_path, local_execution_root=settings.local_execution_root)
        frontier = (
            declared_frontier(_forecast_cli_day(end_date, "--end-date"), measured_at=datetime.now(UTC))
            if end_date is not None
            else await probe_provider_frontier(source, probe_cell_count=probe_cells)
        )
        decision = decide_continuation(
            source,
            frontier,
            output_directory=output_directory or plan_path.parent,
            minimum_advance_days=minimum_advance_days,
            as_of_horizon_days=as_of_horizon_days,
            allow_incomplete=allow_incomplete,
        )
        written = False
        if write and decision.refusal is None:
            write_continuation_plan(decision)
            written = True
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(_plan_continuation_failure_reason(exc)) from exc
    # A refusal is the normal scheduled outcome -- the provider has simply not published enough new
    # days yet -- so it reports as JSON and exits zero, matching durable-backfill.sh's own semantics.
    click.echo(json.dumps(continuation_decision_payload(decision, written=written), indent=2))


@cli.command("historical-plan-staleness")
@click.option(
    "--plans-directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
    help="Directory of reviewed plan artifacts to report on.",
)
@click.option(
    "--probe/--no-probe",
    default=True,
    show_default=True,
    help="Measure each distinct lane/parameter set's provider frontier. --no-probe stays offline.",
)
@click.option("--probe-cells", type=click.IntRange(min=1), default=FRONTIER_PROBE_CELL_COUNT, show_default=True)
@click.option(
    "--minimum-advance-days",
    type=click.IntRange(min=1),
    default=MINIMUM_CONTINUATION_ADVANCE_DAYS,
    show_default=True,
)
def historical_plan_staleness(
    plans_directory: Path,
    probe: bool,
    probe_cells: int,
    minimum_advance_days: int,
) -> None:
    """Report how far every continuable plan sits behind today and behind its provider's frontier."""
    asyncio.run(_historical_plan_staleness(plans_directory, probe, probe_cells, minimum_advance_days))


async def _historical_plan_staleness(
    plans_directory: Path,
    probe: bool,
    probe_cells: int,
    minimum_advance_days: int,
) -> None:
    try:
        report = await scan_plan_staleness(
            sorted(plans_directory.glob("*.json")),
            local_execution_root=settings.local_execution_root,
            probe=probe,
            probe_cell_count=probe_cells,
            minimum_advance_days=minimum_advance_days,
        )
    except (OSError, ValueError, httpx.HTTPError) as exc:
        raise click.ClickException(_plan_continuation_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "plans_directory": str(plans_directory),
                "probed": probe,
                "minimum_advance_days": minimum_advance_days,
                "plans": [plan_staleness_payload(entry) for entry in report],
            },
            indent=2,
        )
    )


def _plan_continuation_failure_reason(exc: Exception) -> str:
    if isinstance(exc, PlanContinuationError):
        return f"plan continuation refused: {exc}"
    if isinstance(exc, httpx.HTTPError):
        return "plan continuation could not reach the provider to measure its frontier"
    if isinstance(exc, OSError):
        return "plan continuation could not read or write a local plan artifact"
    return f"plan continuation input is invalid: {exc}"


@cli.command("coverage-status")
@click.option(
    "--source-key",
    "source_keys",
    multiple=True,
    help="Report only these lanes. Repeatable; the default is every declared coverage contract.",
)
@click.option(
    "--through",
    default=None,
    help="Hold every lane to this YYYY-MM-DD instead of today minus the provider's measured lag.",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit the machine payload instead of the table.")
def coverage_status(source_keys: tuple[str, ...], through: str | None, as_json: bool) -> None:
    """Report, per signal, how complete a lane is, how many days are missing, and where the holes are.

    READ ONLY. This verb opens no write transaction, authors nothing and fetches nothing; it is safe
    to run against production at any time and is the only liveness signal the gap-fill cron has.

    Read the three numbers in this order. `contracted through` first -- it is chosen as today minus
    the provider's measured publication lag, and it is the single fact that decides whether the
    trailing fortnight counts as a hole or as a release that has simply not happened yet. Then
    `complete`, which counts a day as satisfied when it either landed at or above the lane's cell
    floor OR carries a governed absence; an absence is evidence, not a hole, which is why a lane
    whose provider never published one day can still reach 100%. Then the collapsed ranges under
    each signal, which are the actual work list -- `coverage-fill` acts on the oldest of them.

    `thin` is reported apart from `missing` on purpose. A thin day landed SOME cells and is a
    partial fill, not a hole; folding the two together is how a settler writes a silent hole back
    in. Partial is never complete.

    EXIT CODES -- a finding never changes the exit code. An incomplete lane is a measurement, not
    an incident, and this verb exits 0 whether every lane is whole or none is. A fault that stops
    the measurement from happening at all -- an unreachable warehouse, an undeclared source key --
    is a different thing and still raises.
    """
    asyncio.run(_coverage_status(source_keys, through, as_json=as_json))


async def _coverage_status(source_keys: tuple[str, ...], through: str | None, *, as_json: bool) -> None:
    through_day = _forecast_cli_day(through, "--through") if through is not None else None
    try:
        contracts = contracts_for_keys(source_keys)
        async with ingest_session() as session:
            censuses = await census_contracts(session, contracts, through_day=through_day)
            # An explicit rollback rather than trusting the session's close, so a read-only verb
            # leaves nothing behind even if a future edit starts writing on a path it does not today.
            await session.rollback()
    except (CoverageCensusError, SQLAlchemyError) as exc:
        raise click.ClickException(_coverage_failure_reason(exc)) from exc
    click.echo(json.dumps(coverage_status_payload(censuses), indent=2) if as_json else render_census(censuses))


@cli.command("coverage-fill")
@click.option(
    "--plan",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
    help="The reviewed plan artifact whose lane, lattice and parameters the fill inherits verbatim.",
)
@click.option(
    "--source-key",
    default=None,
    help="Assert the plan belongs to this agri.data_source.key. The run refuses on a mismatch.",
)
@click.option(
    "--output-directory",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Where the fill plan would be written. Defaults to the source plan's own directory.",
)
@click.option(
    "--through",
    default=None,
    help="Hold the lane to this YYYY-MM-DD instead of today minus the provider's measured lag.",
)
@click.option(
    "--probe-cells",
    type=click.IntRange(min=1),
    default=GAP_PROBE_CELL_COUNT,
    show_default=True,
    help="How many lattice cells the gap is probed at before it is called fillable or absent.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    default=False,
    help="Perform the write; the default is a dry run that writes nothing.",
)
def coverage_fill(  # noqa: PLR0913 - one parameter per click option, as this file's own verbs are
    plan: Path,
    source_key: str | None,
    output_directory: Path | None,
    through: str | None,
    probe_cells: int,
    apply_changes: bool,
) -> None:
    """Turn the oldest interior gap in one lane into a backfill plan, or into a governed absence.

    DRY RUN BY DEFAULT. Without `--apply` this writes no plan artifact and opens no write
    transaction; it prints the whole decision -- which run it targeted, what the provider answered
    when asked for exactly that run, and what it would have written. Read the target gap first: it
    is the fact that tells you whether the fill is closing the hole you think it is.

    ONE upstream request per run, not one per day. The census's missing days are collapsed into the
    contiguous runs they actually form, the OLDEST run is targeted so a lane converges instead of
    thrashing on whichever hole is newest, and the provider is asked for that whole run in a single
    request per probed cell. Re-run the verb to take the next run.

    SCOPED TO THE PLAN'S OWN SIGNALS. A lane's contracts span every signal its source publishes, but
    one reviewed plan carries one parameter subset -- NASA POWER's eleven signals are split across
    three plans and ERA5-Land's eight across three more. Only holes in the signals THIS plan fetches
    are considered, so a lane is drained by running the verb once per plan.

    Two outcomes, and the difference between them is measured rather than assumed:

      * the provider serves the run -- a plan is authored, anchored on the run's last day, inheriting
        the source plan's cells, parameters, grid, chunking, source governance and transform version
        verbatim. `HistoricalBackfillWindow` fixes the span at four calendar years, so the plan
        necessarily re-requests already-persisted days either side of the hole; the projected row
        counts in the payload are what that costs.

      * the provider serves nothing at any probed cell for any requested parameter -- the run is not
        a hole at all and is recorded under `--apply` as a governed absence in
        `agri.signal_coverage_audit`, one row per probed cell per signal spanning the whole run,
        with the probe's own evidence in `details`. The next census reads it back as satisfied and
        the run stops being re-walked forever.

    Neither outcome fabricates a day. A run that reaches today is refused as the forward refresh's
    business, a run longer than four calendar years is refused rather than half-planned, and a run
    whose plan artifact already exists is refused rather than re-authored.

    EXIT CODES -- always 0. Every refusal above is a normal scheduled outcome, and a lane with
    nothing missing is the state this verb exists to reach.
    """
    asyncio.run(_coverage_fill(plan, source_key, output_directory, through, probe_cells, apply_changes=apply_changes))


async def _coverage_fill(  # noqa: PLR0913 - mirrors its verb's options one for one
    plan_path: Path,
    asserted_source_key: str | None,
    output_directory: Path | None,
    through: str | None,
    probe_cells: int,
    *,
    apply_changes: bool,
) -> None:
    through_day = _forecast_cli_day(through, "--through") if through is not None else None
    destination = output_directory or plan_path.parent
    try:
        source = load_continuation_source(plan_path, local_execution_root=settings.local_execution_root)
    except (PlanContinuationError, OSError) as exc:
        # A plan this verb cannot parse reaches the operator as one sentence, never as a traceback.
        raise click.ClickException(_coverage_failure_reason(exc)) from exc
    source_key = source.plan.source.key
    if asserted_source_key is not None and asserted_source_key != source_key:
        # The plan is the authority on which lane it belongs to. `--source-key` exists so a scheduled
        # invocation states the lane it believes it is filling and fails loudly when the plan path is
        # later pointed somewhere else, rather than silently filling a different lane.
        raise click.ClickException(
            f"--source-key {asserted_source_key} does not match the plan's own source key {source_key}"
        )
    contracts = contracts_for_source(source_key)
    if not contracts:
        raise click.ClickException(
            f"no coverage contract declares source key {source_key}; declare the lane in "
            "execution/coverage_contract.py before a gap in it can be filled"
        )
    grid_names = {contract.grid_name for contract in contracts}
    support_keys = {contract.support_key for contract in contracts}
    if len(grid_names) != 1 or len(support_keys) != 1:
        # Every contract on one source must agree, because one fetch serves them all. Two grids or
        # two supports would mean one probe answering for lattices it never asked about.
        raise click.ClickException(
            f"lane {source_key} declares more than one grid or support across its contracts, so one "
            "gap probe cannot speak for all of them"
        )
    grid_name = next(iter(grid_names))
    support_key = next(iter(support_keys))
    # One clock for the whole run. `gap_to_probe` and `decide_coverage_fill` both consult it, and
    # sampling twice across UTC midnight turns a GAP_AT_LIVE_EDGE refusal into a hard error.
    decided_at = datetime.now(UTC)
    try:
        # The census read and the probe run outside any write transaction on purpose: a probe is up
        # to three provider requests at 30s each, and holding the loader connection idle-in-
        # transaction for that long is how a cron ties up a pooled DSN it is not using.
        async with ingest_session() as read_session:
            censuses = await census_contracts(read_session, contracts, through_day=through_day)
            lane_cells = {cell.cell_key: cell for cell in await load_lane_cells(read_session, grid_name)}
            await read_session.rollback()
        signals = signals_this_plan_can_fill(source, tuple(signal for census in censuses for signal in census.signals))
        if not signals:
            raise click.ClickException(
                f"{plan_path.name} fetches no parameter that maps to a contracted signal of lane "
                f"{source_key}, so a gap in it cannot be filled from this plan"
            )
        target = gap_to_probe(source, signals, output_directory=destination, now=decided_at)
        probe = await probe_gap_window(source, target, probe_cell_count=probe_cells, now=decided_at) if target else None
        decision = decide_coverage_fill(
            source,
            signals,
            output_directory=destination,
            lane_cells=lane_cells,
            support_key=support_key,
            probe=probe,
            now=decided_at,
        )
        plan_written = False
        absence_rows_written = 0
        if apply_changes and decision.refusal is None:
            write_fill_plan(decision)
            plan_written = True
        elif apply_changes and decision.refusal is FillRefusal.UPSTREAM_SERVES_NOTHING:
            async with ingest_session() as write_session:
                absence_rows_written = await record_governed_absence(write_session, decision)
                await write_session.commit()
    except (
        CoverageCensusError,
        CoverageFillError,
        PlanContinuationError,
        SQLAlchemyError,
        httpx.HTTPError,
        OSError,
    ) as exc:
        raise click.ClickException(_coverage_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            coverage_fill_payload(decision, plan_written=plan_written, absence_rows_written=absence_rows_written),
            indent=2,
        )
    )


def _coverage_failure_reason(exc: Exception) -> str:  # noqa: PLR0911 - an ordered ladder: one return per fault class
    if isinstance(exc, CoverageFillError):
        return f"coverage fill refused: {exc}"
    if isinstance(exc, CoverageCensusError):
        return f"coverage census could not read the warehouse: {exc}"
    if isinstance(exc, PlanContinuationError):
        return f"coverage fill could not read the source plan: {exc}"
    if isinstance(exc, httpx.HTTPError):
        return "coverage fill could not reach the provider to probe the gap"
    if isinstance(exc, SQLAlchemyError):
        return "coverage verb could not reach the warehouse"
    if isinstance(exc, OSError):
        return "coverage fill could not read or write a local plan artifact"
    return f"coverage verb input is invalid: {exc}"


@cli.command("historical-promotion-spool")
@click.option("--release-set-key", required=True)
@click.option("--minimum-target-revision", default="20260720_0004", show_default=True)
def historical_promotion_spool(release_set_key: str, minimum_target_revision: str) -> None:
    """Stream one complete local root into a resumable typed-promotion spool."""
    asyncio.run(_historical_promotion_spool(release_set_key, minimum_target_revision))


async def _historical_promotion_spool(release_set_key: str, minimum_target_revision: str) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        exporter = LocalHistoricalPromotionExporter(
            spool_root=settings.local_execution_root,
            minimum_target_revision=minimum_target_revision,
            max_chunk_bytes=settings.historical_promotion_max_chunk_bytes,
        )
        async with local_source_loader_session(loader_database_url) as session:
            spool = await exporter.spool(session, release_set_key=release_set_key)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_promotion_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "spool_directory": str(spool.directory),
                "manifest_checksum": spool.manifest.manifest_checksum,
                "record_count": spool.manifest.total_record_count,
                "chunk_count": len(spool.manifest.chunks),
                "artifact_count": len(spool.artifacts),
            },
            indent=2,
        )
    )


@cli.command("historical-promotion-upload")
@click.option("--spool-directory", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True)
def historical_promotion_upload(spool_directory: Path) -> None:
    """Resume a spooled promotion through the configured private Railway receiver."""
    asyncio.run(_historical_promotion_upload(spool_directory))


async def _historical_promotion_upload(spool_directory: Path) -> None:
    try:
        loader_database_url = settings.require_local_source_loader_database_url()
        api_url, token = settings.require_historical_promotion_client()
        spool = load_historical_promotion_spool(spool_directory)
        uploader = HistoricalPromotionUploader(
            api_url=api_url,
            token=token,
            retry_attempts=settings.historical_promotion_retry_attempts,
            retry_base_seconds=settings.historical_promotion_retry_base_seconds,
        )
        exporter = LocalHistoricalPromotionExporter(spool_root=settings.local_execution_root)
        async with local_source_loader_session(loader_database_url) as session:
            checkpoint = await exporter.upload(session, spool=spool, uploader=uploader)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_promotion_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "state": checkpoint.state,
                "manifest_checksum": checkpoint.manifest_checksum,
                "bundle_id": str(checkpoint.bundle_id) if checkpoint.bundle_id else None,
                "next_chunk_sequence": checkpoint.next_chunk_sequence,
                "uploaded_artifact_count": len(checkpoint.uploaded_artifact_tokens),
            },
            indent=2,
        )
    )


@cli.command("pipeline-status")
@click.option(
    "--checkpoint",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Optional local source-ingestion checkpoint to inspect.",
)
def pipeline_status(checkpoint: Path | None) -> None:
    """Report the inactive/runnable/blocked state without starting any work."""
    try:
        settings.require_local_source_loader_database_url()
    except ValueError as exc:
        local_bulk_ingestion = f"blocked: {exc}"
    else:
        local_bulk_ingestion = "runnable with a reviewed plan and payload"
    result: dict[str, object] = {
        "state": "inactive",
        "active_jobs": 0,
        "server_current_observations": "runnable only with an approved bounded source configuration",
        "local_bulk_ingestion": local_bulk_ingestion,
        "preaggregation_forecasts_training": "blocked pending separate implementation and evaluation",
        "published_outputs": (
            "source artifacts and validated release sets only; no model, forecast, or waypoint outputs"
        ),
    }
    if checkpoint is not None:
        try:
            saved = load_checkpoint(checkpoint)
        except (OSError, ValueError) as exc:
            raise click.ClickException(str(exc)) from exc
        result["source_ingestion"] = {
            "state": saved.state,
            "checkpoint_schema_version": saved.schema_version,
            "source_key": saved.source_key,
            "source_version": saved.source_version,
            "payload_bytes": saved.payload_bytes,
            "plan_checksum": saved.plan_checksum,
            "release_set_manifest_checksum": saved.release_set_manifest_checksum,
            "release_set_id": str(saved.release_set_id) if saved.release_set_id else None,
            "artifact_id": str(saved.artifact_id) if saved.artifact_id else None,
            "reason": saved.reason,
        }
        result["state"] = {
            "validated": "runnable",
            "blocked": "blocked",
            "published": "inactive",
        }[saved.state]
    click.echo(json.dumps(result, indent=2, sort_keys=True))


def _source_ingestion_failure_reason(exc: Exception) -> str:
    """Keep database connection details out of durable checkpoints and CLI output."""
    if isinstance(exc, SQLAlchemyError):
        return f"warehouse operation failed ({exc.__class__.__name__})"
    return str(exc)


def _write_historical_blocked_checkpoint(
    path: Path,
    checkpoint: HistoricalNasaCheckpoint,
    exc: Exception,
) -> None:
    """Record a redacted retry boundary after a historical local operation fails."""
    with suppress(OSError):
        write_historical_nasa_checkpoint(
            path,
            checkpoint.model_copy(
                update={
                    "state": "blocked",
                    "updated_at": datetime.now().astimezone(),
                    "reason": _historical_nasa_failure_reason(exc),
                }
            ),
        )


def _write_historical_usdm_blocked_checkpoint(
    path: Path,
    checkpoint: HistoricalUsdmCheckpoint,
    exc: Exception,
) -> None:
    """Record a redacted retry boundary after a USDM local operation fails."""
    with suppress(OSError):
        write_historical_usdm_checkpoint(
            path,
            checkpoint.model_copy(
                update={
                    "state": "blocked",
                    "updated_at": datetime.now().astimezone(),
                    "reason": _historical_usdm_failure_reason(exc),
                }
            ),
        )


def _write_historical_era5_blocked_checkpoint(
    path: Path,
    checkpoint: HistoricalEra5Checkpoint,
    exc: Exception,
) -> None:
    """Record a redacted retry boundary after a local ERA5 acquisition failure."""
    with suppress(OSError):
        write_historical_era5_checkpoint(
            path,
            checkpoint.model_copy(
                update={
                    "state": "blocked",
                    "updated_at": datetime.now(UTC),
                    "reason": _historical_era5_failure_reason(exc),
                }
            ),
        )


def _historical_nasa_failure_reason(exc: Exception) -> str:
    """Avoid persisting database or HTTP details in historical checkpoints."""
    if isinstance(exc, SQLAlchemyError):
        return f"historical warehouse operation failed ({exc.__class__.__name__})"
    if isinstance(exc, httpx.HTTPError):
        return f"NASA POWER request failed ({exc.__class__.__name__})"
    return str(exc)


def _historical_usdm_failure_reason(exc: Exception) -> str:
    """Avoid persisting database and HTTP details in USDM checkpoints."""
    if isinstance(exc, SQLAlchemyError):
        return f"historical warehouse operation failed ({exc.__class__.__name__})"
    if isinstance(exc, httpx.HTTPError):
        return f"USDM request failed ({exc.__class__.__name__})"
    return str(exc)


def _historical_era5_failure_reason(exc: Exception) -> str:
    """Keep CDS credentials and provider details out of ERA5 checkpoint failures."""
    if isinstance(exc, ValueError):
        return str(exc)
    return f"ERA5-Land operation failed ({exc.__class__.__name__})"


def _historical_open_meteo_failure_reason(exc: BaseException) -> str:
    """Name the provider condition an operator must act on rather than collapsing it to a class name."""
    if isinstance(exc, OpenMeteoArchiveFetchError | ValueError):
        return str(exc)
    if isinstance(exc, SQLAlchemyError):
        return f"Open-Meteo archive warehouse operation failed ({exc.__class__.__name__})"
    return f"Open-Meteo archive operation failed ({exc.__class__.__name__})"


def _historical_promotion_failure_reason(exc: Exception) -> str:
    """Keep receiver URLs, tokens, and database details out of CLI failures."""
    if isinstance(exc, SQLAlchemyError):
        return f"historical promotion warehouse operation failed ({exc.__class__.__name__})"
    if isinstance(exc, httpx.HTTPError):
        return f"historical promotion request failed ({exc.__class__.__name__})"
    return str(exc)


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scheduled-for must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("scheduled-for must include a timezone")
    return parsed


def _require_sha256(value: str, field_name: str) -> None:
    if len(value) != _SHA256_HEX_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _load_run_plan(path: Path) -> tuple[list[str], list[str], list[ExpectedOutput]]:
    with path.open("rb") as plan_file:
        plan_bytes = plan_file.read(_RUN_PLAN_MAX_BYTES + 1)
    if len(plan_bytes) > _RUN_PLAN_MAX_BYTES:
        raise ValueError("run plan exceeds the 512000-byte limit")
    value = json.loads(plan_bytes.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("run plan must be a JSON object")
    expected_keys = {"partitions", "expected_shards", "expected_outputs"}
    if set(value) != expected_keys:
        raise ValueError("run plan must contain only partitions, expected_shards, expected_outputs")
    partitions = _string_list(value["partitions"], "partitions")
    expected_shards = _string_list(value["expected_shards"], "expected_shards")
    raw_outputs = value["expected_outputs"]
    if not isinstance(raw_outputs, list) or not raw_outputs or len(raw_outputs) > _MAX_RUN_PLAN_OUTPUTS:
        raise ValueError("expected_outputs must contain between 1 and 1000 entries")
    outputs = [ExpectedOutput.model_validate(output) for output in raw_outputs]
    output_keys = [output.output_key for output in outputs]
    if len(output_keys) != len(set(output_keys)):
        raise ValueError("expected output keys must be unique")
    if any(
        not set(output.covered_shards).issubset(expected_shards)
        or not set(output.covered_partitions).issubset(partitions)
        for output in outputs
    ):
        raise ValueError("expected output coverage must stay within the run plan")
    if set().union(*(set(output.covered_shards) for output in outputs)) != set(expected_shards) or set().union(
        *(set(output.covered_partitions) for output in outputs)
    ) != set(partitions):
        raise ValueError("expected outputs must cover every shard and partition")
    return (
        partitions,
        expected_shards,
        outputs,
    )


def _gap_fill_lanes(layer_slugs: tuple[str, ...]) -> tuple[LaneRegistration, ...]:
    """Resolve `--layer` against the STATIC registry before any listing or query, naming what is known."""
    if not layer_slugs:
        return LANE_REGISTRATIONS
    try:
        return resolve_lanes(layer_slugs)
    except LaneRegistryError as exc:
        raise click.BadParameter(str(exc), param_hint="--layer") from exc


def _gap_fill_failure_reason(exc: Exception) -> str:
    """Degrade one failure the driver itself could not isolate into an operator-facing sentence."""
    if isinstance(exc, LaneRegistryError):
        return f"parquet-gap-fill refused: {exc}"
    if isinstance(exc, ParquetWriteError):
        return f"parquet-gap-fill could not write a partition: {exc}"
    if isinstance(exc, SQLAlchemyError):
        return "parquet-gap-fill could not reach the warehouse"
    return f"parquet-gap-fill input is invalid: {exc}"


async def _read_gap_fill_watermarks(
    lanes: tuple[LaneRegistration, ...],
    store: ObjectStore,
    *,
    today: date,
) -> dict[str, LaneWatermarkReading]:
    """Open one loader session purely to read the static lanes' source watermarks."""
    loader_database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(loader_database_url) as session:
        return await resolve_lane_watermarks(session, store, lanes=lanes, today=today)


def _dry_run_watermarks(
    lanes: tuple[LaneRegistration, ...],
    store: ObjectStore,
    *,
    today: date,
) -> dict[str, LaneWatermarkReading]:
    """Resolve the static lanes' watermarks for `--dry-run`, degrading to 'unread' rather than failing.

    A dry run over series lanes alone stays a pure object listing and opens no database at all. When
    a `static_lookup` lane IS in scope, its watermark is the only thing separating "this reference
    set is current" from "nobody looked", so the session is worth opening -- but a census that cannot
    reach Postgres must still print, saying plainly which lanes it could not answer for.
    """
    if not any(lane.watermark is not None for lane in lanes):
        return {}
    try:
        return asyncio.run(_read_gap_fill_watermarks(lanes, store, today=today))
    except (SQLAlchemyError, ValueError) as exc:
        reason = f"no source watermark was read: {type(exc).__name__}: {exc}"
        return {lane.slug: LaneWatermarkReading(error=reason) for lane in lanes if lane.watermark is not None}


async def _parquet_gap_fill(
    lanes: tuple[LaneRegistration, ...],
    *,
    today: date,
    run_id: str,
    time_budget_seconds: float,
    max_days_per_lane: int | None,
) -> GapFillSummary:
    """Open one loader session for the whole tick and drive every requested lane through it."""
    loader_database_url = settings.require_local_source_loader_database_url()
    store = ObjectStore.from_settings()
    async with local_source_loader_session(loader_database_url) as session:
        return await run_gap_fill(
            session,
            store,
            lanes=lanes,
            today=today,
            run_id=run_id,
            time_budget_seconds=time_budget_seconds,
            max_days_per_lane=max_days_per_lane,
        )


@cli.command("parquet-gap-fill")
@click.option(
    "--layer",
    "layer_slugs",
    multiple=True,
    help="Restrict this tick to one or more registered stream slugs (e.g. signal, water-gauges); "
    f"repeatable. Default: every registered lane -- {', '.join(registered_lane_slugs())}.",
)
@click.option(
    "--time-budget-seconds",
    type=click.FloatRange(min=0.0),
    default=DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS,
    show_default=True,
    help="Stop STARTING a new lane-day once this many seconds of this tick have elapsed. A day "
    "already in hand always finishes its own export; this never kills one mid-write.",
)
@click.option(
    "--max-days-per-lane",
    type=click.IntRange(min=1),
    default=None,
    help="Cap how many missing days each lane may attempt this tick. Default: uncapped, bounded only "
    "by the time budget. Lanes are walked round-robin, so an uncapped deep lane still cannot starve a "
    "shallow one of its leading edge.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report the gap census -- each lane's nature, window, data/absent/missing day counts, newest "
    "gaps, source watermark and floor citation -- WITHOUT writing a single object. This is how the "
    "cron is audited. NOTE: when a static_lookup lane is in scope this OPENS THE LOADER DSN and runs "
    "one read-only aggregate per such lane, because a reference set's coverage cannot be told from "
    "the object listing alone. Pass --skip-watermarks to keep the audit offline.",
)
@click.option(
    "--skip-watermarks",
    is_flag=True,
    help="Never read a static lane's source watermark, keeping the run a pure object listing with no "
    "database connection at all. Those lanes then report `watermark_unread` -- honestly 'nobody "
    "looked', which is NOT the same claim as 'current'. Only meaningful with --dry-run.",
)
@click.pass_context
def parquet_gap_fill(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single tick
    context: click.Context,
    layer_slugs: tuple[str, ...],
    time_budget_seconds: float,
    max_days_per_lane: int | None,
    dry_run: bool,
    skip_watermarks: bool,
) -> None:
    """Fill every Parquet stream's missing observed days, newest first, inside one wall-clock budget.

    ONE MECHANISM SERVES BOTH THE INCREMENTAL TICK AND THE BACKFILL. A newly published day is simply
    the newest missing day of its lane, so ordering gaps newest-first keeps every leading edge current
    while years of history stay unfilled behind it. Lanes are visited round-robin, one day each per
    round, so the ~9,400-day `fire-detections` window cannot consume a tick before `signal` writes.

    EVERY LANE DECLARES A NATURE, AND `--dry-run` PRINTS IT. `daily_series` and `release_series`
    lanes get the window walk above. A `static_lookup` lane -- reference data with a version and no
    time axis -- gets none: it reads its SOURCE WATERMARK, owes exactly one snapshot dated at that
    watermark, and reports `current` while a partition dated at or after it already exists. A tick
    such a lane sits out costs nothing, because no calendar day ever carried an obligation for it.
    `current` and `watermark_unread` are reported separately for exactly this reason -- both show
    zero missing days, and they are different claims. Reading a watermark needs the loader DSN even
    under --dry-run; `--skip-watermarks` keeps the audit offline and says `watermark_unread` instead.

    A day the export genuinely has no rows for is recorded as a GOVERNED ABSENCE, whose evidence says
    only what this run observed -- that the day-scoped query over this warehouse returned zero rows --
    and never claims the upstream source system was asked, because this verb never contacts one. A
    marked day is covered, not a gap, so it is not re-attempted on the next tick.

    EXIT CODE 0 means the tick ran, and days may still remain. A partially drained backlog is the
    expected steady state of a multi-tick driver, not an incident, and so is a lane whose window is
    already fully covered or whose floor has not settled past its publication lag yet.

    EXIT CODE 1 means a lane's own export raised, its object listing could not be read, or a
    governed-absence marker was refused. Per-lane isolation means one such lane never stops another
    lane's turn; it only changes THIS TICK'S exit code, once every other lane has had its rounds.
    """
    lanes = _gap_fill_lanes(layer_slugs)
    today = datetime.now(UTC).date()
    run_id = f"parquet-gap-fill:{uuid.uuid4()}"
    try:
        if dry_run:
            store = ObjectStore.from_settings()
            census = build_gap_census(
                lanes,
                store,
                today=today,
                max_days_per_lane=max_days_per_lane,
                watermarks={} if skip_watermarks else _dry_run_watermarks(lanes, store, today=today),
            )
            click.echo(json.dumps(gap_census_report(census), sort_keys=True))
            return
        summary = asyncio.run(
            _parquet_gap_fill(
                lanes,
                today=today,
                run_id=run_id,
                time_budget_seconds=time_budget_seconds,
                max_days_per_lane=max_days_per_lane,
            )
        )
    except (LaneRegistryError, ParquetWriteError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_gap_fill_failure_reason(exc)) from exc
    click.echo(json.dumps(summary.to_summary(), sort_keys=True))
    if summary.failed:
        context.exit(_GAP_FILL_FAILED_EXIT_CODE)


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must be a non-empty string array")
    strings = cast("list[str]", value)
    normalized = [item.strip() for item in strings]
    if (
        len(strings) > _MAX_RUN_PLAN_KEYS
        or normalized != strings
        or normalized != sorted(set(normalized))
        or any(not item or len(item) > _MAX_RUN_PLAN_KEY_LENGTH for item in normalized)
    ):
        raise ValueError(f"{field_name} must be sorted, unique, nonblank, and at most 10000 entries")
    return normalized
