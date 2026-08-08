"""Bind the four readiness probe statements from ``sql/routes/health_*.sql`` at import time.

The statements take no request input at all: every value they compare the live
database against is a governance constant from ``contracts.py``, rendered into a
SQL row-literal by :func:`_sql_values` and baked into the text by one
``.format()`` per file. That is the one case ``sql/AGENTS.md`` allows a value to
enter statement text, and it is why nothing here is a bind parameter --
``health_migration.sql`` is the sole exception, taking the pinned revision as
``expected_revision``.

The plain ``str`` constants are a frozen public surface: ``_READINESS_SQL``,
``_FORECAST_ROLE_CONTRACT_SQL`` and ``_RECEIVER_ROLE_BOUNDARY_SQL`` are imported
by name from ``tests/test_forecasting_postgresql.py``, which hands them to
psycopg2, and ``tests/test_health_readiness.py`` asserts substrings of them. The
``text()`` objects beside them are what the route actually executes.
"""

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.routes.health.contracts import (
    COLUMN_PRIVILEGE_UNIVERSE,
    FORECAST_ROLE_COLUMN_PRIVILEGES,
    FORECAST_ROLE_FUNCTION_PRIVILEGES,
    FORECAST_ROLE_RELATION_PRIVILEGES,
    FORECAST_ROLE_SEQUENCE_PRIVILEGES,
    FORECAST_ROLES,
    MIGRATION_TABLE_PRIVILEGES,
    PUBLICATION_TABLE_PRIVILEGES,
    RECEIVER_SEQUENCE_PRIVILEGES,
    REQUIRED_EXTENSIONS,
    SEQUENCE_PRIVILEGE_UNIVERSE,
    TABLE_PRIVILEGE_UNIVERSE,
)


def _sql_values(rows: tuple[tuple[str, ...], ...]) -> str:
    """Render contract tuples as a SQL VALUES row-list: ``('a', 'b'), ('c', 'd')``."""
    return ", ".join("(" + ", ".join(f"'{value}'" for value in row) + ")" for row in rows)


_EXTENSION_VALUES = _sql_values(tuple((name,) for name in REQUIRED_EXTENSIONS))
_PRIVILEGE_VALUES = _sql_values(PUBLICATION_TABLE_PRIVILEGES + MIGRATION_TABLE_PRIVILEGES)
_TABLE_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in TABLE_PRIVILEGE_UNIVERSE))
_COLUMN_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in COLUMN_PRIVILEGE_UNIVERSE))
_FORECAST_ROLE_VALUES = _sql_values(tuple((role_name,) for role_name in FORECAST_ROLES))
_FORECAST_RELATION_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_RELATION_PRIVILEGES)
_FORECAST_COLUMN_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_COLUMN_PRIVILEGES)
_FORECAST_SEQUENCE_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_SEQUENCE_PRIVILEGES)
_FORECAST_FUNCTION_PRIVILEGE_VALUES = _sql_values(FORECAST_ROLE_FUNCTION_PRIVILEGES)
_SEQUENCE_PRIVILEGE_VALUES = _sql_values(tuple((privilege,) for privilege in SEQUENCE_PRIVILEGE_UNIVERSE))
_RECEIVER_RELATION_PRIVILEGE_VALUES = _sql_values(PUBLICATION_TABLE_PRIVILEGES)
_RECEIVER_SEQUENCE_PRIVILEGE_VALUES = _sql_values(RECEIVER_SEQUENCE_PRIVILEGES)

_READINESS_SQL = load_query_sql("routes/health_readiness.sql").format(
    extension_values=_EXTENSION_VALUES,
    privilege_values=_PRIVILEGE_VALUES,
    table_privilege_values=_TABLE_PRIVILEGE_VALUES,
    column_privilege_values=_COLUMN_PRIVILEGE_VALUES,
    forecast_role_values=_FORECAST_ROLE_VALUES,
    forecast_relation_privilege_values=_FORECAST_RELATION_PRIVILEGE_VALUES,
    forecast_column_privilege_values=_FORECAST_COLUMN_PRIVILEGE_VALUES,
    forecast_sequence_privilege_values=_FORECAST_SEQUENCE_PRIVILEGE_VALUES,
    forecast_function_privilege_values=_FORECAST_FUNCTION_PRIVILEGE_VALUES,
    sequence_privilege_values=_SEQUENCE_PRIVILEGE_VALUES,
    required_extension_count=len(REQUIRED_EXTENSIONS),
    forecast_role_count=len(FORECAST_ROLES),
)
_FORECAST_ROLE_CONTRACT_SQL = load_query_sql("routes/health_forecast_role_contract.sql").format(
    forecast_role_values=_FORECAST_ROLE_VALUES,
    forecast_relation_privilege_values=_FORECAST_RELATION_PRIVILEGE_VALUES,
    forecast_column_privilege_values=_FORECAST_COLUMN_PRIVILEGE_VALUES,
    forecast_sequence_privilege_values=_FORECAST_SEQUENCE_PRIVILEGE_VALUES,
    forecast_function_privilege_values=_FORECAST_FUNCTION_PRIVILEGE_VALUES,
    table_privilege_values=_TABLE_PRIVILEGE_VALUES,
    column_privilege_values=_COLUMN_PRIVILEGE_VALUES,
    sequence_privilege_values=_SEQUENCE_PRIVILEGE_VALUES,
    forecast_role_count=len(FORECAST_ROLES),
)
_RECEIVER_ROLE_BOUNDARY_SQL = load_query_sql("routes/health_receiver_role_boundary.sql").format(
    receiver_relation_privilege_values=_RECEIVER_RELATION_PRIVILEGE_VALUES,
    receiver_sequence_privilege_values=_RECEIVER_SEQUENCE_PRIVILEGE_VALUES,
    table_privilege_values=_TABLE_PRIVILEGE_VALUES,
    column_privilege_values=_COLUMN_PRIVILEGE_VALUES,
    sequence_privilege_values=_SEQUENCE_PRIVILEGE_VALUES,
)
_MIGRATION_SQL = load_query_sql("routes/health_migration.sql")

READINESS_STATEMENT: TextClause = text(_READINESS_SQL)
FORECAST_ROLE_CONTRACT_STATEMENT: TextClause = text(_FORECAST_ROLE_CONTRACT_SQL)
RECEIVER_ROLE_BOUNDARY_STATEMENT: TextClause = text(_RECEIVER_ROLE_BOUNDARY_SQL)
MIGRATION_STATEMENT: TextClause = text(_MIGRATION_SQL)
