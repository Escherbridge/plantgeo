"""Freeze the readiness contract: the extensions and the migration revision this build requires.

Both names here are governance assertions, not defaults. ``tests/test_health_readiness.py``
reads them back and compares them to the live database, and ``queries.py`` renders
``REQUIRED_EXTENSIONS`` into the SQL row-literal the probe statement compares against -- so an
edit here changes what /ready refuses to serve on. See ``routes/AGENTS.md`` for why the probe
SQL lives in ``sql/routes/health_*.sql``, and for what revision ``20260808_0019`` removed from
this file: the whole forecast-role and calling-login privilege matrix, which governed roles that
had no members and no DSN.
"""

EXPECTED_ALEMBIC_REVISION = "20260808_0019"
REQUIRED_EXTENSIONS = ("postgis", "timescaledb", "vector", "pgcrypto")
