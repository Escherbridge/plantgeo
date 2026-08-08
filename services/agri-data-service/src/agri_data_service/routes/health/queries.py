"""Bind the two readiness probe statements from ``sql/routes/health_*.sql`` at import time.

``health_readiness.sql`` takes no request input at all: the one value it compares the live
database against is ``REQUIRED_EXTENSIONS`` from ``contracts.py``, rendered into a SQL
row-literal by :func:`_sql_values` and baked into the text by a single ``.format()``. That is
the one case ``sql/AGENTS.md`` allows a value to enter statement text. ``health_migration.sql``
is the exception that binds a parameter, taking the pinned revision as ``expected_revision``.

The plain ``str`` constants are a frozen public surface: ``_READINESS_SQL`` is imported by name
from ``tests/test_forecasting_postgresql.py``, which hands it to psycopg2, and
``tests/test_health_readiness.py`` asserts substrings of it. The ``text()`` objects beside them
are what the route actually executes.
"""

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.routes.health.contracts import REQUIRED_EXTENSIONS


def _sql_values(rows: tuple[tuple[str, ...], ...]) -> str:
    """Render contract tuples as a SQL VALUES row-list: ``('a', 'b'), ('c', 'd')``."""
    return ", ".join("(" + ", ".join(f"'{value}'" for value in row) + ")" for row in rows)


_EXTENSION_VALUES = _sql_values(tuple((name,) for name in REQUIRED_EXTENSIONS))

_READINESS_SQL = load_query_sql("routes/health_readiness.sql").format(
    extension_values=_EXTENSION_VALUES,
    required_extension_count=len(REQUIRED_EXTENSIONS),
)
_MIGRATION_SQL = load_query_sql("routes/health_migration.sql")

READINESS_STATEMENT: TextClause = text(_READINESS_SQL)
MIGRATION_STATEMENT: TextClause = text(_MIGRATION_SQL)
