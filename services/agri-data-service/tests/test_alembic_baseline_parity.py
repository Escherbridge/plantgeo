"""Catalogue checks for a disposable database created from the current baseline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.routes.health.contracts import EXPECTED_ALEMBIC_REVISION, REQUIRED_EXTENSIONS
from tests.test_alembic_baseline_contract import EXPECTED_TABLES

if TYPE_CHECKING:
    import psycopg2.extensions


def _fetch(connection: psycopg2.extensions.connection, sql: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchall()


def test_database_matches_the_single_baseline_revision(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    assert _fetch(agri_db_connection, "SELECT version_num FROM public.alembic_version") == [
        (EXPECTED_ALEMBIC_REVISION,)
    ]


def test_database_contains_exactly_the_baseline_agri_tables(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    tables = {
        name
        for (name,) in _fetch(
            agri_db_connection,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'agri'",
        )
    }
    assert tables == EXPECTED_TABLES


def test_required_extensions_and_baseline_privileges(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    installed = {name for (name,) in _fetch(agri_db_connection, "SELECT extname FROM pg_extension")}
    assert set(REQUIRED_EXTENSIONS) <= installed
    assert "timescaledb" not in installed

    assert _fetch(
        agri_db_connection,
        "SELECT has_schema_privilege('public', 'agri', 'create')",
    ) == [(False,)]
    public_table_grants = _fetch(
        agri_db_connection,
        "SELECT table_name FROM information_schema.role_table_grants "
        "WHERE grantee = 'PUBLIC' AND table_schema = 'agri'",
    )
    assert public_table_grants == []
