"""CLI commands for migrations and reviewed seed data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
from contextlib import suppress
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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
    local_source_loader_session,
)
from agri_data_service.db.maintenance import (
    MaintenanceBusyError,
    maintain_job_event_partitions,
)
from agri_data_service.execution.contracts import ExpectedOutput
from agri_data_service.execution.historical_backfill import (
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
from agri_data_service.execution.historical_open_meteo import (
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
    record_historical_open_meteo_result,
    run_open_meteo_archive_chunks,
    write_historical_open_meteo_checkpoint,
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
    finalize_era5_release_set,
    finalize_nasa_release_set,
    finalize_open_meteo_release_set,
    finalize_usdm_release_set,
    persist_era5_land_month,
    persist_nasa_power_cell,
    persist_open_meteo_archive_chunk,
    persist_usdm_shapefile,
)
from agri_data_service.execution.local_store import LocalRunStore
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
    load_governed_history,
    load_governed_plane,
    load_license_snapshots,
    load_outcome_rows,
    load_series_identities,
    pin_determinism,
    reconcile_actuals,
    register_governed_plane,
    select_candidate_cell_keys,
    simulate_cells,
    summarize_holdout,
)
from agri_data_service.ingest.commands import register_ingest_commands
from agri_data_service.models.strategy import Strategy
from agri_data_service.seed.strategies import STRATEGY_SEEDS
from alembic import command

logger = structlog.get_logger()
_RUN_PLAN_MAX_BYTES = 512_000
_SHA256_HEX_LENGTH = 64
_OPEN_METEO_PENDING_PREVIEW = 8
_MAX_RUN_PLAN_OUTPUTS = 1_000
_MAX_RUN_PLAN_KEYS = 10_000
_MAX_RUN_PLAN_KEY_LENGTH = 500

if TYPE_CHECKING:
    import uuid

    from agri_data_service.execution.vegetation_ndvi_forecast import SeasonalHistory


@click.group()
def cli() -> None:
    """Agri Data Service CLI."""


register_ingest_commands(cli)


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
    default_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    return Config(os.environ.get("AGRI_ALEMBIC_CONFIG", str(default_path)))


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


async def _forecast_refresh_ml_daily() -> int:
    database_url = settings.require_forecast_mv_refresh_database_url()
    async with forecast_mv_refresh_session(database_url) as session, session.begin():
        eligibility = await session.execute(
            text(
                """
                SELECT
                    role.rolcanlogin
                    AND NOT role.rolinherit
                    AND NOT role.rolsuper
                    AND NOT role.rolcreatedb
                    AND NOT role.rolcreaterole
                    AND NOT role.rolreplication
                    AND NOT role.rolbypassrls
                    AND EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS membership
                        INNER JOIN pg_roles AS capability
                            ON capability.oid = membership.roleid
                        WHERE membership.member = role.oid
                          AND capability.rolname = 'plantgeo_forecast_mv_refresher'
                          AND NOT membership.admin_option
                          AND NOT membership.inherit_option
                          AND membership.set_option
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_auth_members AS membership
                        INNER JOIN pg_roles AS capability
                            ON capability.oid = membership.roleid
                        WHERE membership.member = role.oid
                          AND capability.rolname <> 'plantgeo_forecast_mv_refresher'
                    )
                    AND NOT has_database_privilege(
                        current_user,
                        current_database(),
                        'CREATE'
                    )
                    AND NOT pg_has_role(
                        current_user,
                        (SELECT database.datdba FROM pg_database AS database
                         WHERE database.datname = current_database()),
                        'MEMBER'
                    )
                    AND NOT pg_has_role(
                        current_user,
                        (SELECT namespace.nspowner FROM pg_namespace AS namespace
                         WHERE namespace.nspname = 'agri'),
                        'MEMBER'
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_class AS class
                        INNER JOIN pg_namespace AS namespace
                            ON namespace.oid = class.relnamespace
                        WHERE namespace.nspname = 'agri'
                          AND pg_has_role(current_user, class.relowner, 'MEMBER')
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_proc AS procedure
                        INNER JOIN pg_namespace AS namespace
                            ON namespace.oid = procedure.pronamespace
                        WHERE namespace.nspname = 'agri'
                          AND pg_has_role(current_user, procedure.proowner, 'MEMBER')
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_class AS class
                        INNER JOIN pg_namespace AS namespace
                            ON namespace.oid = class.relnamespace
                        CROSS JOIN LATERAL aclexplode(
                            coalesce(
                                class.relacl,
                                acldefault(
                                    CASE WHEN class.relkind = 'S' THEN 'S'::"char" ELSE 'r'::"char" END,
                                    class.relowner
                                )
                            )
                        ) AS access
                        WHERE namespace.nspname = 'agri'
                          AND access.grantee = role.oid
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_proc AS procedure
                        INNER JOIN pg_namespace AS namespace
                            ON namespace.oid = procedure.pronamespace
                        CROSS JOIN LATERAL aclexplode(
                            coalesce(
                                procedure.proacl,
                                acldefault('f', procedure.proowner)
                            )
                        ) AS access
                        WHERE namespace.nspname = 'agri'
                          AND access.grantee = role.oid
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_attribute AS attribute
                        INNER JOIN pg_class AS class
                            ON class.oid = attribute.attrelid
                        INNER JOIN pg_namespace AS namespace
                            ON namespace.oid = class.relnamespace
                        CROSS JOIN LATERAL aclexplode(attribute.attacl) AS access
                        WHERE namespace.nspname = 'agri'
                          AND attribute.attnum > 0
                          AND NOT attribute.attisdropped
                          AND access.grantee = role.oid
                    )
                    AND NOT EXISTS (
                        SELECT 1
                        FROM pg_namespace AS namespace
                        CROSS JOIN LATERAL aclexplode(
                            coalesce(
                                namespace.nspacl,
                                acldefault('n', namespace.nspowner)
                            )
                        ) AS access
                        WHERE namespace.nspname = 'agri'
                          AND access.grantee = role.oid
                    )
                FROM pg_roles AS role
                WHERE role.rolname = current_user
                """
            )
        )
        if eligibility.scalar_one_or_none() is not True:
            raise ValueError(
                "forecast MV refresh requires a dedicated NOINHERIT, non-elevated, non-owner "
                "operator login whose only role membership is plantgeo_forecast_mv_refresher"
            )
        await session.execute(text("SET LOCAL ROLE plantgeo_forecast_mv_refresher"))
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
        call_result = await session.execute(
            text(
                """
                CALL agri.materialize_forecast_iteration(
                    CAST(NULL AS uuid),
                    CAST(:iteration_key AS varchar),
                    CAST(:series_id AS uuid),
                    CAST(:release_set_id AS uuid),
                    CAST(:as_of_time AS timestamptz),
                    CAST(:cutoff_time AS timestamptz),
                    CAST(:history_start AS timestamptz),
                    :horizon_days,
                    :simulation_count,
                    :seed,
                    CAST(:gap_policy AS varchar),
                    CAST(:lower_bound AS double precision),
                    CAST(:upper_bound AS double precision)
                )
                """
            ),
            parameters,
        )
        iteration_id = call_result.scalar_one()
        summary_result = await session.execute(
            text(
                """
                SELECT
                    iteration.id,
                    iteration.iteration_key,
                    iteration.status,
                    iteration.method,
                    iteration.purpose,
                    iteration.availability_mode,
                    iteration.series_id,
                    iteration.release_set_id,
                    iteration.cutoff_time,
                    iteration.horizon_days,
                    iteration.simulation_count,
                    iteration.simulation_seed,
                    iteration.gap_policy,
                    iteration.training_day_count,
                    iteration.increment_count,
                    iteration.receipt_checksum,
                    iteration.recorded_at,
                    count(value.id)::integer AS value_count
                FROM agri.forecast_iteration AS iteration
                INNER JOIN agri.forecast_iteration_value AS value
                    ON value.iteration_id = iteration.id
                WHERE iteration.id = :iteration_id
                GROUP BY iteration.id
                """
            ),
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
            text(
                """
                CALL agri.reconcile_forecast_iteration_actuals(
                    0,
                    CAST(:iteration_id AS uuid),
                    CAST(:actual_release_set_id AS uuid),
                    CAST(:as_of_time AS timestamptz)
                )
                """
            ),
            {
                "iteration_id": iteration_id,
                "actual_release_set_id": actual_release_set_id,
                "as_of_time": as_of_time,
            },
        )
        inserted_count = int(call_result.scalar_one())
        result = await session.execute(
            text(
                """
                SELECT
                    count(*)::integer AS forecast_value_count,
                    count(outcome.actual_checksum)::integer AS actual_count,
                    avg(outcome.absolute_error) AS mean_absolute_error,
                    avg(
                        CASE WHEN outcome.interval_covered THEN 1.0 ELSE 0.0 END
                    ) AS interval_coverage
                FROM agri.v_forecast_iteration_outcome AS outcome
                WHERE outcome.iteration_id = :iteration_id
                """
            ),
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
        for cell in plan.nasa.cells:
            if cell.cell_key in completed_cells:
                continue
            result = load_cached_historical_nasa_result(settings.local_execution_root, plan, cell)
            if result is None:
                result = await fetch_nasa_power_daily(plan.nasa, cell)
                cache_historical_nasa_result(settings.local_execution_root, plan, result)
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                await persist_nasa_power_cell(session, plan=plan, result=result)
            checkpoint = record_historical_nasa_result(plan, checkpoint, result)
            write_historical_nasa_checkpoint(checkpoint_path_value, checkpoint)
            completed_cells.add(cell.cell_key)
        if checkpoint.state != "validated":
            raise ValueError("historical backfill did not produce complete source-cell coverage")
        release_set = None
        if all(receipt.retrieved_at <= plan.release_set_as_of for receipt in checkpoint.receipts):
            async with local_source_loader_session(loader_database_url) as session, session.begin():
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
            result = load_cached_historical_era5_result(
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
            result = load_cached_historical_era5_result(
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


@cli.command("historical-open-meteo-status")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
def historical_open_meteo_status(plan: Path) -> None:
    """Report which archive chunks are already cached and what a resume would still fetch."""
    try:
        value = HistoricalOpenMeteoArchivePlan.model_validate_json(plan.read_bytes())
        checkpoint_path_value = historical_open_meteo_checkpoint_path(settings.local_execution_root, value)
        checkpoint = (
            load_historical_open_meteo_checkpoint(checkpoint_path_value)
            if checkpoint_path_value.exists()
            else initialize_historical_open_meteo_checkpoint(value)
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(_historical_open_meteo_failure_reason(exc)) from exc
    completed = {receipt.chunk_key for receipt in checkpoint.receipts}
    pending = [chunk.key for chunk in value.chunks if chunk.key not in completed]
    click.echo(
        json.dumps(
            {
                "plan_checksum": historical_open_meteo_plan_checksum(value),
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "reason": checkpoint.reason,
                "cell_count": len(value.cells),
                "chunk_count": len(value.chunks),
                "completed_chunk_count": len(completed),
                "pending_chunk_count": len(pending),
                "pending_chunks": pending[:_OPEN_METEO_PENDING_PREVIEW],
                "observed_value_count": sum(receipt.observed_value_count for receipt in checkpoint.receipts),
                "no_data_series_count": sum(receipt.no_data_series_count for receipt in checkpoint.receipts),
            },
            indent=2,
        )
    )


@cli.command("historical-open-meteo-backfill")
@click.option("--plan", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--max-chunks", type=click.IntRange(min=1), default=None, help="Stop after this many new chunks.")
@click.option("--concurrency", type=click.IntRange(min=1, max=4), default=2)
def historical_open_meteo_backfill(plan: Path, max_chunks: int | None, concurrency: int) -> None:
    """Fetch, validate, and cache reviewed Open-Meteo ERA5-Land archive chunks; resumable and bounded."""
    asyncio.run(_historical_open_meteo_backfill(plan, max_chunks, concurrency))


async def _historical_open_meteo_backfill(plan_path: Path, max_chunks: int | None, concurrency: int) -> None:
    failures: list[dict[str, str]] = []
    try:
        plan = HistoricalOpenMeteoArchivePlan.model_validate_json(plan_path.read_bytes())
        checkpoint_path_value = historical_open_meteo_checkpoint_path(settings.local_execution_root, plan)
        checkpoint = (
            load_historical_open_meteo_checkpoint(checkpoint_path_value)
            if checkpoint_path_value.exists()
            else initialize_historical_open_meteo_checkpoint(plan)
        )
        if checkpoint.plan_checksum != historical_open_meteo_plan_checksum(plan):
            raise ValueError("Open-Meteo archive checkpoint does not bind the reviewed plan")
        write_historical_open_meteo_checkpoint(checkpoint_path_value, checkpoint)
        completed = {receipt.chunk_key for receipt in checkpoint.receipts}
        outstanding = [chunk for chunk in plan.chunks if chunk.key not in completed]
        checkpoint, failures = await _fetch_open_meteo_chunks(
            plan,
            checkpoint,
            checkpoint_path_value,
            outstanding if max_chunks is None else outstanding[:max_chunks],
            concurrency,
        )
    except Exception as exc:
        if "checkpoint_path_value" in locals() and "checkpoint" in locals():
            _write_historical_open_meteo_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
        raise click.ClickException(_historical_open_meteo_failure_reason(exc)) from exc
    remaining = [chunk.key for chunk in plan.chunks if chunk.key not in {r.chunk_key for r in checkpoint.receipts}]
    payload = {
        "checkpoint": str(checkpoint_path_value),
        "state": checkpoint.state,
        "completed_chunk_count": len(checkpoint.receipts),
        "chunk_count": len(plan.chunks),
        "pending_chunk_count": len(remaining),
        "failed_chunks": failures,
        "release_receipt_manifest_checksum": (
            historical_open_meteo_release_manifest(plan, checkpoint) if checkpoint.state == "validated" else None
        ),
        "next_steps": ["historical-open-meteo-persist"],
    }
    if failures:
        # A chunk that dropped records must never read as success: record why, report, exit non-zero.
        _write_historical_open_meteo_blocked_checkpoint(
            checkpoint_path_value,
            checkpoint,
            ValueError(f"{len(failures)} chunk(s) failed; first: {failures[0]['reason']}"),
        )
        raise click.ClickException(json.dumps({**payload, "error": "open_meteo_chunks_failed"}, indent=2))
    click.echo(json.dumps(payload, indent=2))


async def _fetch_open_meteo_chunks(
    plan: HistoricalOpenMeteoArchivePlan,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    checkpoint_path_value: Path,
    chunks: list[OpenMeteoArchiveChunk],
    concurrency: int,
) -> tuple[HistoricalOpenMeteoCheckpoint, list[dict[str, str]]]:
    """Reuse the local cache first, fetch the rest under bounded concurrency, and keep failures visible."""
    failures: list[dict[str, str]] = []
    pending: list[OpenMeteoArchiveChunk] = []
    for chunk in chunks:
        cached = load_cached_historical_open_meteo_result(settings.local_execution_root, plan, chunk)
        if cached is None:
            pending.append(chunk)
            continue
        checkpoint = record_historical_open_meteo_result(plan, checkpoint, cached)
        write_historical_open_meteo_checkpoint(checkpoint_path_value, checkpoint)
    # Harvested in waves of `concurrency` rather than one gather over everything, so an interrupted
    # long run keeps every chunk that already answered instead of discarding the whole batch.
    for start in range(0, len(pending), concurrency):
        wave = pending[start : start + concurrency]
        results = await run_open_meteo_archive_chunks(plan, wave, concurrency=concurrency)
        for chunk, result in zip(wave, results, strict=True):
            if isinstance(result, BaseException):
                failures.append({"chunk_key": chunk.key, "reason": _historical_open_meteo_failure_reason(result)})
                continue
            cache_historical_open_meteo_result(settings.local_execution_root, plan, result)
            checkpoint = record_historical_open_meteo_result(plan, checkpoint, result)
            write_historical_open_meteo_checkpoint(checkpoint_path_value, checkpoint)
        if failures:
            # A quota wall does not clear inside one run; stop rather than burn the remaining waves.
            break
    return checkpoint, failures


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
        checkpoint = load_historical_open_meteo_checkpoint(checkpoint_path_value)
        if checkpoint.plan_checksum != historical_open_meteo_plan_checksum(plan):
            raise ValueError("Open-Meteo archive persistence requires a matching checkpoint")
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        persisted: list[dict[str, object]] = []
        for chunk in plan.chunks:
            if chunk.key not in receipted:
                continue
            result = load_cached_historical_open_meteo_result(settings.local_execution_root, plan, chunk)
            if result is None:
                raise ValueError("Open-Meteo archive persistence requires every receipted local chunk document")
            persisted.append(await _persist_open_meteo_chunk(loader_database_url, plan, result))
        release_set = None
        if checkpoint.state == "validated" and all(
            receipt.retrieved_at <= plan.release_set_as_of for receipt in checkpoint.receipts
        ):
            async with local_source_loader_session(loader_database_url) as session, session.begin():
                release_set = await finalize_open_meteo_release_set(session, plan=plan, checkpoint=checkpoint)
    except (OSError, SQLAlchemyError, ValueError) as exc:
        raise click.ClickException(_historical_open_meteo_failure_reason(exc)) from exc
    click.echo(
        json.dumps(
            {
                "checkpoint": str(checkpoint_path_value),
                "state": checkpoint.state,
                "persisted_chunk_count": len(persisted),
                "chunk_count": len(plan.chunks),
                "observation_row_count": sum(int(cast("int", item["observation_count"])) for item in persisted),
                "observed_value_count": sum(int(cast("int", item["observed_value_count"])) for item in persisted),
                "no_data_series_count": sum(int(cast("int", item["no_data_series_count"])) for item in persisted),
                "release_set_id": None if release_set is None else str(release_set.release_set_id),
                "release_set_manifest_checksum": None if release_set is None else release_set.manifest_checksum,
                "finalization_blocked_by_incomplete_coverage": release_set is None,
            },
            indent=2,
        )
    )


async def _persist_open_meteo_chunk(
    loader_database_url: str,
    plan: HistoricalOpenMeteoArchivePlan,
    result: OpenMeteoArchiveChunkResult,
) -> dict[str, object]:
    """Persist one chunk in its own transaction so a later chunk's failure never rolls back an earlier one."""
    async with local_source_loader_session(loader_database_url) as session, session.begin():
        written = await persist_open_meteo_archive_chunk(session, plan=plan, result=result)
    return {
        "chunk_key": result.chunk_key,
        "observation_count": written.observation_count,
        "observed_value_count": written.observed_value_count,
        "no_data_series_count": written.no_data_series_count,
    }


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
            result = load_cached_historical_era5_result(
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


def _write_historical_open_meteo_blocked_checkpoint(
    path: Path,
    checkpoint: HistoricalOpenMeteoCheckpoint,
    exc: Exception,
) -> None:
    """Record why a run stopped so a resume starts from evidence rather than a rerun of everything."""
    with suppress(OSError, ValueError):
        write_historical_open_meteo_checkpoint(
            path,
            checkpoint.model_copy(
                update={
                    "state": "blocked",
                    "updated_at": datetime.now(UTC),
                    "reason": _historical_open_meteo_failure_reason(exc),
                }
            ),
        )


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
