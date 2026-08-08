"""Separate process liveness from publication-plane readiness.

The two handlers live here rather than in a submodule on purpose. ``settings``,
``published_reader_session`` and ``receiver_writer_session`` are module globals
that ``tests/test_health_readiness.py`` rebinds with ``monkeypatch.setattr`` on
this package; a handler defined in a submodule would read its *own* globals and
sail straight past those patches into a real database connection. The contract
constants, probe SQL and probe sequence live in ``contracts``, ``queries`` and
``probes``, and every name this module ever exposed is re-exported below so no
importer or test has to change. See ``routes/AGENTS.md`` for the layout.
"""

import asyncio

import structlog
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse

from agri_data_service.config import settings
from agri_data_service.db.engine import published_reader_session, receiver_writer_session
from agri_data_service.routes.health.contracts import (
    COLUMN_PRIVILEGE_UNIVERSE,
    EXPECTED_ALEMBIC_REVISION,
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
from agri_data_service.routes.health.probes import apply_database_probes, initial_checks
from agri_data_service.routes.health.queries import (
    _COLUMN_PRIVILEGE_VALUES,
    _EXTENSION_VALUES,
    _FORECAST_COLUMN_PRIVILEGE_VALUES,
    _FORECAST_FUNCTION_PRIVILEGE_VALUES,
    _FORECAST_RELATION_PRIVILEGE_VALUES,
    _FORECAST_ROLE_CONTRACT_SQL,
    _FORECAST_ROLE_VALUES,
    _FORECAST_SEQUENCE_PRIVILEGE_VALUES,
    _MIGRATION_SQL,
    _PRIVILEGE_VALUES,
    _READINESS_SQL,
    _RECEIVER_RELATION_PRIVILEGE_VALUES,
    _RECEIVER_ROLE_BOUNDARY_SQL,
    _RECEIVER_SEQUENCE_PRIVILEGE_VALUES,
    _SEQUENCE_PRIVILEGE_VALUES,
    _TABLE_PRIVILEGE_VALUES,
    _sql_values,
)

logger = structlog.get_logger()

health_bp = Blueprint("health", url_prefix="/")


@health_bp.get("/health")
async def health_check(_request: Request) -> HTTPResponse:
    """Report process liveness without coupling it to dependencies."""
    return json({"status": "ok"})


@health_bp.get("/ready")
async def readiness_check(_request: Request) -> HTTPResponse:
    """Require one explicit least-privilege production database profile."""
    profile = settings.service_profile
    checks = initial_checks(profile)
    if profile == "combined_local":
        return json({"status": "not_ready", "profile": profile, "checks": checks}, status=503)

    session_factory = receiver_writer_session if profile == "receiver_writer" else published_reader_session
    try:
        async with asyncio.timeout(2):
            async with session_factory() as session:
                await apply_database_probes(session, profile, checks)
    except Exception:
        logger.warning("readiness_check_failed")

    ready = all(checks.values())
    return json(
        {"status": "ready" if ready else "not_ready", "profile": profile, "checks": checks},
        status=200 if ready else 503,
    )


__all__ = [
    "COLUMN_PRIVILEGE_UNIVERSE",
    "EXPECTED_ALEMBIC_REVISION",
    "FORECAST_ROLES",
    "FORECAST_ROLE_COLUMN_PRIVILEGES",
    "FORECAST_ROLE_FUNCTION_PRIVILEGES",
    "FORECAST_ROLE_RELATION_PRIVILEGES",
    "FORECAST_ROLE_SEQUENCE_PRIVILEGES",
    "MIGRATION_TABLE_PRIVILEGES",
    "PUBLICATION_TABLE_PRIVILEGES",
    "RECEIVER_SEQUENCE_PRIVILEGES",
    "REQUIRED_EXTENSIONS",
    "SEQUENCE_PRIVILEGE_UNIVERSE",
    "TABLE_PRIVILEGE_UNIVERSE",
    "_COLUMN_PRIVILEGE_VALUES",
    "_EXTENSION_VALUES",
    "_FORECAST_COLUMN_PRIVILEGE_VALUES",
    "_FORECAST_FUNCTION_PRIVILEGE_VALUES",
    "_FORECAST_RELATION_PRIVILEGE_VALUES",
    "_FORECAST_ROLE_CONTRACT_SQL",
    "_FORECAST_ROLE_VALUES",
    "_FORECAST_SEQUENCE_PRIVILEGE_VALUES",
    "_MIGRATION_SQL",
    "_PRIVILEGE_VALUES",
    "_READINESS_SQL",
    "_RECEIVER_RELATION_PRIVILEGE_VALUES",
    "_RECEIVER_ROLE_BOUNDARY_SQL",
    "_RECEIVER_SEQUENCE_PRIVILEGE_VALUES",
    "_SEQUENCE_PRIVILEGE_VALUES",
    "_TABLE_PRIVILEGE_VALUES",
    "_sql_values",
    "apply_database_probes",
    "health_bp",
    "health_check",
    "initial_checks",
    "logger",
    "published_reader_session",
    "readiness_check",
    "receiver_writer_session",
    "settings",
]
