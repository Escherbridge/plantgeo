"""CLI commands for migrations and reviewed seed data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import click
import structlog
from alembic.config import Config
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.config import settings
from agri_data_service.db.engine import async_session, engine, local_source_loader_session
from agri_data_service.db.maintenance import (
    MaintenanceBusyError,
    maintain_job_event_partitions,
)
from agri_data_service.execution.contracts import ExpectedOutput
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
from agri_data_service.models.strategy import Strategy
from agri_data_service.seed.strategies import STRATEGY_SEEDS
from alembic import command

logger = structlog.get_logger()
_RUN_PLAN_MAX_BYTES = 512_000
_SHA256_HEX_LENGTH = 64
_MAX_RUN_PLAN_OUTPUTS = 1_000
_MAX_RUN_PLAN_KEYS = 10_000
_MAX_RUN_PLAN_KEY_LENGTH = 500

if TYPE_CHECKING:
    import uuid


@click.group()
def cli() -> None:
    """Agri Data Service CLI."""


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
        async with engine.begin() as connection:
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
