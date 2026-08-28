"""Load runtime query SQL from the ``sql/`` tree that ships inside the package.

``src/agri_data_service/sql/**`` holds the DML a service issues at request or
job time (see ``sql/AGENTS.md``). It is a different tree from the declarative
``db/agri/**`` one behind ``sql_objects.load_object_sql``: that tree is DDL
regenerated from the migration head, this one is hand-written queries. Files
here live under ``src/`` so hatchling packages them into the wheel and the
installed ``agri-service`` console script can read them.

Call this at module import time, wrapped in ``text(...)``, exactly where an
inline literal sits today::

    from sqlalchemy import text

    from agri_data_service.db.sql_queries import load_query_sql

    _INSERT_FEATURES = text(load_query_sql("ingest/insert_features.sql"))

Reading at import time means the file is read once per process and a missing or
unreadable file fails at startup, not at first query.
"""

from __future__ import annotations

from pathlib import Path

# src/agri_data_service/sql
SQL_ROOT = Path(__file__).resolve().parents[1] / "sql"


def load_query_sql(relative_path: str) -> str:
    """Return the runtime query SQL at ``relative_path``, e.g. ``"ingest/insert_features.sql"``."""
    target = (SQL_ROOT / relative_path).resolve()
    if SQL_ROOT not in target.parents:
        raise ValueError(f"{relative_path!r} escapes the runtime query root")
    if not target.is_file():
        raise FileNotFoundError(f"no runtime query at {target}")
    return target.read_text(encoding="utf-8")
