"""Operational and lookup-model CLI commands."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from alembic.config import Config
from sqlalchemy import or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.engine import async_session, combined_local_engine
from agri_data_service.db.maintenance import MaintenanceBusyError, maintain_job_event_partitions
from agri_data_service.execution.strategy_label_mapping import preflight_strategy_label_source_mapping
from agri_data_service.execution.strategy_selection import load_strategy_label_bundle, train_strategy_models
from agri_data_service.models.strategy import Strategy
from agri_data_service.seed.strategies import STRATEGY_SEEDS
from alembic import command


@click.command()
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
    draft = {**data, "review_state": "draft", "reviewed_at": None, "reviewed_by": None}
    immutable = {"slug", "review_state", "reviewed_at", "reviewed_by"}
    governed = {key: value for key, value in draft.items() if key not in immutable}
    changed = or_(*(getattr(Strategy, key).is_distinct_from(value) for key, value in governed.items()))
    return insert(Strategy).values(**draft).on_conflict_do_update(
        index_elements=["slug"],
        set_={key: value for key, value in draft.items() if key != "slug"},
        where=changed,
    )


@click.command("strategy-label-map-preflight")
@click.option("--mapping-manifest", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.pass_context
def strategy_label_map_preflight(context: click.Context, mapping_manifest: Path) -> None:
    """Validate an intervention-label source mapping."""
    try:
        result = preflight_strategy_label_source_mapping(mapping_manifest)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result.to_json())
    if not result.ready:
        context.exit(2)


@click.command("strategy-train")
@click.option("--label-bundle", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--output-artifact", type=click.Path(path_type=Path, dir_okay=False), required=True)
def strategy_train(label_bundle: Path, output_artifact: Path) -> None:
    """Train the local evaluation-only strategy benchmark."""
    try:
        artifact = train_strategy_models(load_strategy_label_bundle(label_bundle))
        _write_atomic(output_artifact, artifact.to_json())
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {"artifact_checksum": artifact.checksum, "output_artifact": str(output_artifact)}
    click.echo(json.dumps(payload, sort_keys=True))


def _write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            delete=False,
            dir=path.parent,
            encoding="utf-8",
            newline="\n",
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


def _alembic_config() -> Config:
    configured = os.environ.get("AGRI_ALEMBIC_CONFIG")
    if configured:
        return Config(configured)
    current = Path(__file__).resolve().parent
    default = Path(__file__).resolve().parents[2] / "alembic.ini"
    while current != current.parent:
        candidate = current / "alembic.ini"
        if candidate.is_file():
            default = candidate
            break
        current = current.parent
    return Config(str(default))


@click.command("db-status")
def db_status() -> None:
    """Show the current Alembic revision."""
    command.current(_alembic_config(), verbose=True)


@click.command("db-upgrade")
@click.argument("revision", default="head")
def db_upgrade(revision: str) -> None:
    """Upgrade the relational control and lookup schema."""
    command.upgrade(_alembic_config(), revision)


@click.command("job-logs-maintain")
@click.option("--retention-days", type=click.IntRange(1, 365), default=30, show_default=True)
@click.option("--future-days", type=click.IntRange(1, 31), default=7, show_default=True)
def job_logs_maintain(retention_days: int, future_days: int) -> None:
    """Maintain UTC job-event partitions and their retention window."""
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
