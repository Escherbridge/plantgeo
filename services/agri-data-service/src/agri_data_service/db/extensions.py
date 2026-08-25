"""The installed PostgreSQL extensions the `agri` schema requires -- one definition, three readers.

This module exists because the list was written out three times (the greenfield baseline's
preflight, ``routes/health/contracts.py`` and the readiness probe SQL) and a fourth copy was about
to appear in the pre-stamp verifier. It lives under ``db/`` rather than ``routes/health/`` so a
migration can import it without dragging Sanic, structlog and the async engine into
``alembic upgrade head``: importing ``routes.health.contracts`` executes
``routes/health/__init__.py``, which imports all three.

Deliberately NOT ``timescaledb`` (dropped 2026-08-25, see ``alembic/archive/AGENTS.md``) and
deliberately not ``btree_gist``: nothing in the tree uses it, every gist index is on a geometry
column. See ``db/AGENTS.md`` for why this list is a governance assertion, not a default.
"""

from __future__ import annotations

REQUIRED_EXTENSIONS: tuple[str, ...] = ("postgis", "vector", "pgcrypto")
