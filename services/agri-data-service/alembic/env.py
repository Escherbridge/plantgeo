"""Alembic environment configuration for the isolated sync migration identity."""

import logging
from logging.config import fileConfig
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import engine_from_config, pool

from agri_data_service import models as model_registry  # noqa: F401
from agri_data_service.config import settings
from agri_data_service.db.base import Base
from alembic import context

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url_sync.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def announce_target(dsn: str) -> None:
    """Log the host/port/database this run will migrate, with no credentials.

    ``alembic upgrade head`` reads ``DATABASE_URL_SYNC``, never ``DATABASE_URL``, so overriding the
    latter to redirect a run silently migrates whatever the former points at. Naming the target
    before any DDL runs is what turns that into something an operator sees. See alembic/AGENTS.md.
    """
    parts = urlsplit(dsn)
    logging.getLogger("alembic.runtime.migration").info(
        "migration target: %s:%s%s", parts.hostname, parts.port or 5432, parts.path
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Any) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with the dedicated synchronous administrative DSN."""
    announce_target(settings.database_url_sync)
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
