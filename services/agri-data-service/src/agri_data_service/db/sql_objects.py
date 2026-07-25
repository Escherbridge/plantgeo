"""Load canonical declarative SQL objects from the ``db/`` tree inside migrations.

The ``db/agri/**`` tree is the source of truth for every database object (see
``db/AGENTS.md``). When a future migration changes a programmable object it
edits the canonical ``.sql`` file and loads it here instead of re-embedding the
DDL as a Python string, so the file and the applied schema can never drift.

Typical use inside a migration ``upgrade()``::

    from agri_data_service.db.sql_objects import load_object_sql

    # body-only change to a function/procedure/view -> CREATE OR REPLACE
    op.execute(load_object_sql("functions/forecast_daily_bootstrap.sql", or_replace=True))

    # signature/return-shape change -> drop then load the canonical definition
    op.execute("DROP FUNCTION IF EXISTS agri.forecast_daily_bootstrap(...);")
    op.execute(load_object_sql("functions/forecast_daily_bootstrap.sql"))
"""

from __future__ import annotations

import re
from pathlib import Path

# services/agri-data-service/db/agri
SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "db" / "agri"

_CREATE = re.compile(r"^\s*CREATE\s+(FUNCTION|PROCEDURE|VIEW|MATERIALIZED VIEW)\b", re.IGNORECASE | re.MULTILINE)


def load_object_sql(relative_path: str, *, or_replace: bool = False) -> str:
    """Return the canonical SQL for a declarative object, e.g. ``"views/v_x.sql"``.

    ``or_replace=True`` rewrites a leading ``CREATE <kind>`` into
    ``CREATE OR REPLACE <kind>`` for a body-only change to a function,
    procedure, or view. It is a convenience only: PostgreSQL still forbids
    ``OR REPLACE`` across a changed return type or a changed view column set, so
    use an explicit ``DROP`` for those.
    """
    target = (SCHEMA_ROOT / relative_path).resolve()
    if SCHEMA_ROOT not in target.parents:
        raise ValueError(f"{relative_path!r} escapes the declarative schema root")
    if not target.is_file():
        raise FileNotFoundError(f"no declarative object at {target}")
    sql = target.read_text(encoding="utf-8")
    if or_replace:
        sql = _CREATE.sub(lambda m: f"CREATE OR REPLACE {m.group(1)}", sql, count=1)
    return sql
