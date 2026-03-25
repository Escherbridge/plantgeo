"""CLI commands for database management and seeding."""

import asyncio

import click
import structlog

logger = structlog.get_logger()


@click.group()
def cli() -> None:
    """Agri Data Service CLI."""
    pass


@cli.command()
def seed() -> None:
    """Seed the database with regenerative strategies."""
    asyncio.run(_seed())


async def _seed() -> None:
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from agri_data_service.db.engine import async_session
    from agri_data_service.models.strategy import Strategy
    from agri_data_service.seed.strategies import STRATEGY_SEEDS

    async with async_session() as session:
        for data in STRATEGY_SEEDS:
            stmt = (
                insert(Strategy)
                .values(**data)
                .on_conflict_do_update(
                    index_elements=["slug"],
                    set_={k: v for k, v in data.items() if k != "slug"},
                )
            )
            await session.execute(stmt)
        await session.commit()

    click.echo(f"Seeded {len(STRATEGY_SEEDS)} strategies.")


@cli.command("reset-db")
def reset_db() -> None:
    """Drop and recreate all tables (DESTRUCTIVE)."""
    if not click.confirm("This will DROP ALL TABLES. Continue?"):
        return
    asyncio.run(_reset_db())


async def _reset_db() -> None:
    from sqlalchemy import text

    from agri_data_service.db.base import Base
    from agri_data_service.db.engine import engine
    from agri_data_service.models import *  # noqa: F401, F403 — import all models to register metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.run_sync(Base.metadata.create_all)

    click.echo("Database reset complete.")
