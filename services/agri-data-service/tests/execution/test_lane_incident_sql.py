"""`o2b-incidents`'s four owned SQL files: their documentation header, their declared bind
parameters and exactly one statement each (the same static proof `test_usage_sql.py` runs for GL-4's
two files, `test_each_file_holds_one_statement`), plus one real-PostgreSQL functional test per
statement, gated on `AGRI_TEST_DATABASE_URL` through `tests/conftest.py`'s `agri_db_async_dsn`
fixture (auto-skipped when it is unset, per `pytest_collection_modifyitems`). Every functional test
runs inside one transaction that is rolled back at the end, so the database is left exactly as it
was found -- the same convention `test_vegetation_partition_registration_postgresql.py` uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
import sqlalchemy
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import asyncpg as asyncpg_dialect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution.lane_incidents import (
    resolve_lane_incident,
    select_lane_incidents,
    select_run_final_attempt,
    upsert_lane_incident,
)
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.models.jobs import EventSeverity

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

_SQL_ROOT = Path(__file__).resolve().parents[2] / "src" / "agri_data_service" / "sql" / "execution"

_EXPECTED_BINDS: dict[str, frozenset[str]] = {
    "upsert_lane_incident.sql": frozenset(
        {"fingerprint", "incident_type", "severity", "job_run_id", "job_work_item_id", "summary", "now", "detail"}
    ),
    "resolve_lane_incident.sql": frozenset({"fingerprint", "now", "detail_patch"}),
    "select_lane_incidents.sql": frozenset({"now"}),
    "select_run_final_attempt.sql": frozenset({"job_run_id"}),
}

_INSERT_DEFINITION: Final = text("""
INSERT INTO agri.job_definition (name, version, handler)
VALUES (:name, '1', 'test.handler.v1')
RETURNING id
""")

_INSERT_RUN: Final = text("""
INSERT INTO agri.job_run (job_definition_id, logical_run_key, scheduled_for)
VALUES (:job_definition_id, :logical_run_key, :scheduled_for)
RETURNING id
""")

_INSERT_WORK_ITEM: Final = text("""
INSERT INTO agri.job_work_item (job_run_id, shard_key, kind)
VALUES (:job_run_id, 'shard-1', 'scheduled-command')
RETURNING id
""")

_INSERT_ATTEMPT: Final = text("""
INSERT INTO agri.job_attempt (
    job_work_item_id, attempt_number, fencing_token, status, worker_id, finished_at,
    failure_class, error_summary, metrics
)
VALUES (
    :job_work_item_id, :attempt_number, :attempt_number, CAST(:status AS varchar), 'test-worker', now(),
    :failure_class, :error_summary, CAST(:metrics AS jsonb)
)
""")


def _body_only(raw: str) -> str:
    """The file with every `--` comment line blanked out."""
    return "\n".join("" if line.lstrip().startswith("--") else line for line in raw.splitlines())


@pytest.mark.parametrize("filename", sorted(_EXPECTED_BINDS))
def test_each_file_holds_one_statement(filename: str) -> None:
    path = _SQL_ROOT / filename
    raw = path.read_text(encoding="utf-8")
    head = "\n".join(raw.splitlines()[:30])
    assert "-- Purpose:" in head, f"{filename}: missing '-- Purpose:' within the header window"
    assert "-- Loaded by:" in head, f"{filename}: missing '-- Loaded by:' within the header window"

    body = _body_only(raw)
    binds = set(sqlalchemy.text(body)._bindparams)
    assert binds == _EXPECTED_BINDS[filename], (
        f"{filename}: bind parameters {sorted(binds)} do not match the documented/loaded set "
        f"{sorted(_EXPECTED_BINDS[filename])}"
    )
    assert ";" not in body, f"{filename}: a semicolon in the statement body suggests more than one statement"


def test_every_owned_sql_file_is_present() -> None:
    for filename in _EXPECTED_BINDS:
        assert (_SQL_ROOT / filename).is_file(), f"expected {filename} under {_SQL_ROOT}"


@pytest.mark.parametrize("filename", sorted(_EXPECTED_BINDS))
def test_compiled_statement_sends_no_backslash_escape(filename: str) -> None:
    """What asyncpg actually receives carries no `\\` -- a `\\:word:` escape is not unescaped by
    SQLAlchemy when the word is followed by a colon, so it would reach PostgreSQL verbatim."""
    raw = (_SQL_ROOT / filename).read_text(encoding="utf-8")
    compiled = str(sqlalchemy.text(_body_only(raw)).compile(dialect=asyncpg_dialect.dialect()))
    assert "\\" not in compiled, f"{filename}: compiled statement still carries a backslash"


def test_resolve_renames_to_the_colon_resolved_colon_id_form() -> None:
    """The rename suffix the executor's flapping count and `/admin/jobs` parse: `<fp>:resolved:<id>`."""
    raw = (_SQL_ROOT / "resolve_lane_incident.sql").read_text(encoding="utf-8")
    compiled = str(sqlalchemy.text(_body_only(raw)).compile(dialect=asyncpg_dialect.dialect()))
    assert "fingerprint || ':' || 'resolved' || ':' || CAST(id AS varchar)" in compiled


async def _open_session(engine: AsyncEngine) -> AsyncSession:
    session = AsyncSession(engine, expire_on_commit=False)
    await session.begin()
    await apply_statement_timeout(session)
    return session


async def _seed_attempt(
    session: AsyncSession,
    *,
    lane_id: str,
    status: str,
    exit_class: str | None,
    failure_class: str | None = None,
) -> uuid.UUID:
    """One definition/run/work-item/attempt chain, minimal columns only. Returns the run id."""
    definition_id = (await session.execute(_INSERT_DEFINITION, {"name": f"plantgeo.executor.{lane_id}"})).scalar_one()
    run_id = (
        await session.execute(
            _INSERT_RUN,
            {
                "job_definition_id": definition_id,
                "logical_run_key": f"{lane_id}:{uuid.uuid4().hex}",
                "scheduled_for": datetime(2026, 9, 28, 6, tzinfo=UTC),
            },
        )
    ).scalar_one()
    work_item_id = (await session.execute(_INSERT_WORK_ITEM, {"job_run_id": run_id})).scalar_one()
    metrics = "{}" if exit_class is None else f'{{"exit_class": "{exit_class}"}}'
    await session.execute(
        _INSERT_ATTEMPT,
        {
            "job_work_item_id": work_item_id,
            "attempt_number": 1,
            "status": status,
            "failure_class": failure_class,
            "error_summary": None,
            "metrics": metrics,
        },
    )
    return run_id


async def test_upsert_never_moves_last_seen_backwards(agri_db_async_dsn: str) -> None:
    engine = create_async_engine(agri_db_async_dsn)
    try:
        session = await _open_session(engine)
        try:
            fingerprint = f"test:lane_hold:{uuid.uuid4().hex}"
            later = datetime(2026, 9, 28, 12, tzinfo=UTC)
            earlier = later - timedelta(hours=6)

            first = await upsert_lane_incident(
                session,
                fingerprint=fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="held",
                now=later,
                detail={"state": "held"},
            )
            assert first.occurrence_count == 1
            assert first.last_seen_at == later

            # A second call carrying an OLDER clock reading must never move last_seen_at backwards.
            bumped = await upsert_lane_incident(
                session,
                fingerprint=fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="still held",
                now=earlier,
                detail={"state": "held"},
            )
            assert bumped.incident_id == first.incident_id
            assert bumped.occurrence_count == first.occurrence_count + 1
            assert bumped.last_seen_at == later
            assert bumped.first_seen_at == first.first_seen_at
        finally:
            await session.rollback()
            await session.close()
    finally:
        await engine.dispose()


async def test_resolve_renames_the_fingerprint(agri_db_async_dsn: str) -> None:
    engine = create_async_engine(agri_db_async_dsn)
    try:
        session = await _open_session(engine)
        try:
            fingerprint = f"test:lane_hold:{uuid.uuid4().hex}"
            now = datetime(2026, 9, 28, 12, tzinfo=UTC)
            opened = await upsert_lane_incident(
                session,
                fingerprint=fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="held",
                now=now,
                detail={"state": "held"},
            )

            resolved = await resolve_lane_incident(
                session, fingerprint=fingerprint, now=now, detail_patch={"released_by": "reconciled"}
            )
            assert resolved is not None
            assert resolved.incident_id == opened.incident_id
            assert resolved.fingerprint == f"{fingerprint}:resolved:{opened.incident_id}"
            assert resolved.fingerprint != fingerprint

            # The base fingerprint is free again: a fresh episode opens a NEW row under it.
            reopened = await upsert_lane_incident(
                session,
                fingerprint=fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="held again",
                now=now,
                detail={"state": "held"},
            )
            assert reopened.incident_id != opened.incident_id
            assert reopened.occurrence_count == 1

            # A retried resolve of the already-resolved (renamed-away) fingerprint is a no-op.
            again = await resolve_lane_incident(session, fingerprint=fingerprint + ":stale", now=now, detail_patch={})
            assert again is None
        finally:
            await session.rollback()
            await session.close()
    finally:
        await engine.dispose()


async def test_select_returns_open_and_recent_resolved_holds(agri_db_async_dsn: str) -> None:
    engine = create_async_engine(agri_db_async_dsn)
    try:
        session = await _open_session(engine)
        try:
            now = datetime(2026, 9, 28, 12, tzinfo=UTC)
            open_fingerprint = f"test:lane_hold:{uuid.uuid4().hex}"
            recent_fingerprint = f"test:lane_hold:{uuid.uuid4().hex}"
            stale_fingerprint = f"test:lane_hold:{uuid.uuid4().hex}"
            other_kind_fingerprint = f"test:lane_incomplete:{uuid.uuid4().hex}"

            await upsert_lane_incident(
                session,
                fingerprint=open_fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="open hold",
                now=now,
                detail={"state": "held", "rung": 0},
            )
            recent = await upsert_lane_incident(
                session,
                fingerprint=recent_fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="recently resolved hold",
                now=now - timedelta(days=2),
                detail={"state": "held"},
            )
            recent_resolved = await resolve_lane_incident(
                session, fingerprint=recent.fingerprint, now=now - timedelta(days=1), detail_patch={}
            )
            assert recent_resolved is not None
            stale = await upsert_lane_incident(
                session,
                fingerprint=stale_fingerprint,
                incident_type="lane_hold",
                severity=EventSeverity.WARNING,
                summary="long-resolved hold",
                now=now - timedelta(days=20),
                detail={"state": "held"},
            )
            stale_resolved = await resolve_lane_incident(
                session, fingerprint=stale.fingerprint, now=now - timedelta(days=10), detail_patch={}
            )
            assert stale_resolved is not None
            other = await upsert_lane_incident(
                session,
                fingerprint=other_kind_fingerprint,
                incident_type="lane_incomplete",
                severity=EventSeverity.WARNING,
                summary="open incomplete",
                now=now,
                detail={},
            )

            rows = await select_lane_incidents(session, now=now)
            fingerprints = {row.fingerprint for row in rows}
            statuses = {row.fingerprint: row.status for row in rows}
            # Open incidents of both kinds, and the hold resolved 1 day ago (inside the 7-day window).
            assert fingerprints >= {open_fingerprint, other.fingerprint, recent_resolved.fingerprint}
            assert statuses[open_fingerprint] == "open"
            assert statuses[recent_resolved.fingerprint] == "resolved"
            # The hold resolved 10 days ago (outside the 7-day window) is excluded entirely.
            assert stale_resolved.fingerprint not in fingerprints
        finally:
            await session.rollback()
            await session.close()
    finally:
        await engine.dispose()


async def test_final_attempt_query_returns_status_and_metrics(agri_db_async_dsn: str) -> None:
    engine = create_async_engine(agri_db_async_dsn)
    try:
        session = await _open_session(engine)
        try:
            lane_id = f"test-final-attempt-{uuid.uuid4().hex[:12]}"
            run_id = await _seed_attempt(session, lane_id=lane_id, status="failed", exit_class="upstream")

            attempt = await select_run_final_attempt(session, job_run_id=run_id)
            assert attempt is not None
            assert attempt.status == "failed"
            assert attempt.exit_class == "upstream"
            assert attempt.finished_at is not None

            missing = await select_run_final_attempt(session, job_run_id=uuid.uuid4())
            assert missing is None
        finally:
            await session.rollback()
            await session.close()
    finally:
        await engine.dispose()
