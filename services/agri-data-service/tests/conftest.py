"""Shared test fixtures.

Single canonical entry point for PostgreSQL-backed governance tests: set
``AGRI_TEST_DATABASE_URL`` to a disposable database migrated to the current
Alembic head and every test that requests ``agri_db_dsn``/``agri_db_connection``/
``agri_db_async_dsn`` runs against it. See `pytest_sessionfinish` below for the
no-silent-skip sweep gate, and `db/AGENTS.md` for the migration/parity contract
this DSN must satisfy.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final

import psycopg2
import pytest
import structlog

from agri_data_service.app import AgriApp, create_app
from agri_data_service.foundation.observability import logging as observability_logging
from agri_data_service.foundation.observability.bootstrap import TURN_CONTEXT_ENV_VARS

# Enables the `pytester` fixture (see test_no_silent_skip_gate_pytester.py), which
# proves the no-silent-skip sweep gate below actually flips the exit code.
pytest_plugins = ["pytester"]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.terminal import TerminalReporter

AGRI_TEST_DATABASE_URL_ENV = "AGRI_TEST_DATABASE_URL"
EXPECTED_ALEMBIC_HEAD = "20260912_0000"
PROTECTED_DATABASE_NAME = "plantgeo"
AGRI_DB_MARKER = "agri_db"
AGRI_DB_REHEARSAL_MARKER = "agri_db_migration_rehearsal"
AGRI_DB_CROSS_MAJOR_MARKER = "agri_db_cross_major"
# Exempt from the strict no-skip gate: each needs a precondition AGRI_TEST_DATABASE_URL
# alone cannot guarantee (a database deliberately not at head, or a second server on
# another major), so a skip here is a structural fact, not an avoidable oversight.
_EXEMPT_MARKERS = (AGRI_DB_REHEARSAL_MARKER, AGRI_DB_CROSS_MAJOR_MARKER)
_SKIP_REASON = (
    f"set {AGRI_TEST_DATABASE_URL_ENV} to a disposable database migrated to "
    f"Alembic head {EXPECTED_ALEMBIC_HEAD!r} (never the persistent {PROTECTED_DATABASE_NAME!r} warehouse)"
)


def agri_test_database_url() -> str | None:
    """Return the single governance-test DSN, or ``None`` if unset."""
    return os.environ.get(AGRI_TEST_DATABASE_URL_ENV)


def _to_asyncpg_url(dsn: str) -> str:
    """Rewrite a libpq-style DSN for SQLAlchemy's asyncpg driver."""
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+asyncpg://" + dsn[len(prefix) :]
    return dsn


def _assert_head_and_safe(dsn: str) -> None:
    connection = psycopg2.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(), (SELECT version_num FROM public.alembic_version)")
            row = cursor.fetchone()
            assert row is not None
            database_name, revision = row
    finally:
        connection.close()
    if database_name == PROTECTED_DATABASE_NAME:
        pytest.fail(
            f"refusing to run tests against the persistent {PROTECTED_DATABASE_NAME!r} warehouse; "
            f"point {AGRI_TEST_DATABASE_URL_ENV} at a disposable database instead"
        )
    if revision != EXPECTED_ALEMBIC_HEAD:
        pytest.fail(
            f"{AGRI_TEST_DATABASE_URL_ENV} database {database_name!r} is at Alembic revision "
            f"{revision!r}, expected head {EXPECTED_ALEMBIC_HEAD!r}. Recreate the disposable "
            "database and run `alembic upgrade head` against it."
        )


@pytest.fixture
def agri_db_dsn() -> str:
    """The ``AGRI_TEST_DATABASE_URL`` DSN, verified to be at the migration head.

    Skips only when the env var itself is unset; a wrong database or a stale
    revision is a hard failure, not a skip.
    """
    dsn = agri_test_database_url()
    if not dsn:
        pytest.skip(_SKIP_REASON)
    _assert_head_and_safe(dsn)
    return dsn


@pytest.fixture
def agri_db_async_dsn(agri_db_dsn: str) -> str:
    """``agri_db_dsn`` rewritten for SQLAlchemy's async (asyncpg) engines."""
    return _to_asyncpg_url(agri_db_dsn)


@pytest.fixture
def agri_db_connection(agri_db_dsn: str) -> Iterator[psycopg2.extensions.connection]:
    """A psycopg2 connection to the head-migrated governance database, rolled back on teardown."""
    connection = psycopg2.connect(agri_db_dsn)
    connection.autocommit = False
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def app() -> AgriApp:
    """Create a test Sanic application."""
    return create_app()


# Every logger `configure_logging` sets a level on, kept here (rather than reached for as a private
# import) the same way `router.py` duplicates `logging.py`'s turn-context table -- see this
# project's foundation/observability/AGENTS.md for the convention. Restoring only the ROOT logger's
# handlers/level (as this fixture did before) left `agri_data_service` and every third-party floor
# logger permanently changed after the FIRST test that called `configure_logging`, which is exactly
# the test-order-dependent flake class this fixture exists to prevent (security review).
_OBSERVABILITY_LOGGER_NAMES: Final = (
    "agri_data_service",
    "httpx",
    "httpcore",
    "sqlalchemy",
    "asyncpg",
    "botocore",
    "boto3",
    "s3transfer",
    "urllib3",
    "rasterio",
    "asyncio",
    "sanic",
)


@pytest.fixture(autouse=True)
def _reset_observability_state() -> Iterator[None]:
    """Isolate every test from another test's logging configuration and turn-context environment.

    Without this (GL-1; see `foundation/observability/AGENTS.md`), one test's `configure_logging`
    call or `PLANTGEO_TURN_*`/`PLANTGEO_LANE_ID`/`PLANTGEO_ATTEMPT` environment would leak into the
    next test's collection, which is exactly the class of test-order-dependent flake this fixture
    exists to rule out.
    """
    for name in TURN_CONTEXT_ENV_VARS:
        os.environ.pop(name, None)
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_logger_levels = {name: logging.getLogger(name).level for name in _OBSERVABILITY_LOGGER_NAMES}
    original_long_running = observability_logging.is_long_running_configured()
    try:
        yield
    finally:
        for name in TURN_CONTEXT_ENV_VARS:
            os.environ.pop(name, None)
        structlog.reset_defaults()
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
        for name, level in original_logger_levels.items():
            logging.getLogger(name).setLevel(level)
        observability_logging._configuration_state.long_running = original_long_running
        logging.captureWarnings(False)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{AGRI_DB_MARKER}: PostgreSQL-backed governance test gated on {AGRI_TEST_DATABASE_URL_ENV}",
    )
    config.addinivalue_line(
        "markers",
        f"{AGRI_DB_REHEARSAL_MARKER}: multi-phase Alembic upgrade rehearsal with its own dedicated "
        f"DSN/phase env vars; exempt from the {AGRI_DB_MARKER} no-silent-skip sweep gate because it "
        "requires a database deliberately not at head",
    )
    config.addinivalue_line(
        "markers",
        f"{AGRI_DB_CROSS_MAJOR_MARKER}: needs a second PostgreSQL server on a major other than the canonical "
        "one (AGRI_CROSS_MAJOR_DATABASE_URL), which AGRI_TEST_DATABASE_URL cannot supply; exempt from the "
        f"{AGRI_DB_MARKER} no-silent-skip sweep gate for that structural reason",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    for item in items:
        if set(_EXEMPT_MARKERS) & set(item.keywords):
            continue
        if {"agri_db_dsn", "agri_db_connection", "agri_db_async_dsn"} & set(item.fixturenames):
            item.add_marker(AGRI_DB_MARKER)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    terminal_reporter: TerminalReporter | None = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal_reporter is None:
        return

    def _skips(marker: str) -> list[pytest.TestReport]:
        return [report for report in terminal_reporter.stats.get("skipped", []) if marker in report.keywords]

    def _report_block(label: str, reports: list[pytest.TestReport]) -> str:
        lines = "\n".join(f"  - {report.nodeid}" for report in reports)
        return f"{len(reports)} {label} test(s) skipped:\n{lines}"

    agri_db_skips = _skips(AGRI_DB_MARKER)
    exempt_skips = {marker: _skips(marker) for marker in _EXEMPT_MARKERS}
    if not agri_db_skips and not any(exempt_skips.values()):
        return

    configured = agri_test_database_url() is not None

    if agri_db_skips:
        message = _report_block(AGRI_DB_MARKER, agri_db_skips)
        if configured:
            terminal_reporter.write_line(
                f"\nAGRI_DB SWEEP FAILURE -- {message}\n"
                f"{AGRI_TEST_DATABASE_URL_ENV} is set; no database-backed test may skip.",
                red=True,
                bold=True,
            )
            session.exitstatus = 1
        else:
            terminal_reporter.write_line(
                f"\nAGRI_DB SWEEP NOTICE -- {message}\n"
                f"{AGRI_TEST_DATABASE_URL_ENV} is unset; these tests were allowed to skip.",
                yellow=True,
                bold=True,
            )
    for marker, reports in exempt_skips.items():
        if not reports:
            continue
        terminal_reporter.write_line(
            f"\nAGRI_DB SWEEP NOTICE -- {_report_block(marker, reports)}\n"
            f"exempt from the {AGRI_DB_MARKER} no-skip gate (see the `{marker}` marker registration "
            "for why).",
            yellow=True,
            bold=True,
        )
