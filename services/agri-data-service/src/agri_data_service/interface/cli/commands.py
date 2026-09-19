"""Operational and lookup-model CLI commands."""

from __future__ import annotations

import asyncio
import json
import os
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
    return (
        insert(Strategy)
        .values(**draft)
        .on_conflict_do_update(
            index_elements=["slug"],
            set_={key: value for key, value in draft.items() if key != "slug"},
            where=changed,
        )
    )


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
