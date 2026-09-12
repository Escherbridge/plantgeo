"""The installed PostgreSQL extensions the `agri` schema requires -- one definition, three readers.

This module exists because the list was written out three times (the greenfield baseline's
preflight, ``routes/health/contracts.py`` and the readiness probe SQL) and a fourth copy was about
to appear in the pre-stamp verifier. It lives under ``db/`` rather than ``routes/health/`` so a
migration can import it without dragging Sanic, structlog and the async engine into
``alembic upgrade head``: importing ``routes.health.contracts`` executes
``routes/health/__init__.py``, which imports all three.

Deliberately not ``timescaledb``; the current baseline does not use hypertables or continuous
aggregates. Also deliberately not ``btree_gist``: the `agri` baseline uses no exclusion
constraint or non-geometry GiST index. See ``db/AGENTS.md`` for why this list is a governance
assertion, not a default.
"""

from __future__ import annotations

REQUIRED_EXTENSIONS: tuple[str, ...] = ("postgis", "vector", "pgcrypto")
