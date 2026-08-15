"""Real-PostgreSQL proof that a dispatched lane writes the ledger, and that a paused one writes nothing.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn`` fixture,
which pins the Alembic head and refuses the persistent ``plantgeo`` warehouse. Follows the same
uuid-scoped, delete-on-teardown shape as ``test_strategy_mv_refresh_postgresql.py``.

The pause switch is ``agri.job_definition.enabled`` -- a column the ledger has carried since
``20260719_0001``, so this track added no migration. The test that matters most here is the paused
one: ``ensure_job_definition`` writes ``enabled = EXCLUDED.enabled`` on conflict, so a dispatcher
that let a paused lane's trigger start would silently un-pause it as a side effect of the trigger's
own upsert. That is asserted against real SQL, not against a stub.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.jobs.dispatch import dispatch_lane, read_lane_pause_state
from agri_data_service.jobs.lease import apply_statement_timeout, fetch_row
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    strategy_mv_refresh_definition_spec,
)
from agri_data_service.jobs.worker import ensure_job_definition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_LANE: Final = STRATEGY_MV_REFRESH_DEFINITION_NAME
_REQUESTED_BY: Final = "test_jobs_dispatch_agri_db"

_DELETE_RUN: Final = text("DELETE FROM agri.job_run WHERE id = :job_run_id")

_SET_ENABLED: Final = text("UPDATE agri.job_definition SET enabled = :enabled, updated_at = now() WHERE name = :name")

_SELECT_RUN: Final = text("SELECT status, total_work_items, requested_by FROM agri.job_run WHERE id = :job_run_id")

_COUNT_RUNS: Final = text(
    "SELECT count(*) AS run_count FROM agri.job_run run "
    "JOIN agri.job_definition definition ON definition.id = run.job_definition_id "
    "WHERE definition.name = :name"
)


@dataclass
class _RunTracker:
    """One engine, every job_run id a test minted, and the pause flag restored on teardown.

    The definition row is real, permanent metadata a production deploy also upserts, so it is left
    behind -- but it is left ENABLED, because a test that walks away from a disabled lane silently
    disables it for every later test in the sweep.
    """

    engine: AsyncEngine
    run_ids: list[uuid.UUID] = field(default_factory=list)
    sessions: list[AsyncSession] = field(default_factory=list)

    async def open_session(self) -> AsyncSession:
        session = AsyncSession(self.engine, expire_on_commit=False)
        await apply_statement_timeout(session)
        self.sessions.append(session)
        return session

    async def discard(self) -> None:
        for session in self.sessions:
            await session.close()
        async with AsyncSession(self.engine) as session:
            for run_id in self.run_ids:
                await session.execute(_DELETE_RUN, {"job_run_id": run_id})
            await session.execute(_SET_ENABLED, {"name": _LANE, "enabled": True})
            await session.commit()


@pytest.fixture
async def tracker(agri_db_async_dsn: str) -> AsyncIterator[_RunTracker]:
    engine = create_async_engine(agri_db_async_dsn)
    scoped = _RunTracker(engine=engine)
    try:
        yield scoped
    finally:
        await scoped.discard()
        await engine.dispose()


async def _clear_existing_runs(session: AsyncSession) -> None:
    """Delete every run this lane already has, so a dispatch in this window mints a fresh one."""
    await session.execute(
        text(
            "DELETE FROM agri.job_run WHERE job_definition_id IN "
            "(SELECT id FROM agri.job_definition WHERE name = :name)"
        ),
        {"name": _LANE},
    )
    await session.commit()


async def test_dispatching_a_registered_lane_opens_a_real_run_on_the_ledger(tracker: _RunTracker) -> None:
    session = await tracker.open_session()
    await ensure_job_definition(session, strategy_mv_refresh_definition_spec())
    await session.commit()
    await _clear_existing_runs(session)

    outcome = await dispatch_lane(session, _LANE, requested_by=_REQUESTED_BY)

    assert outcome.state == "dispatched"
    assert outcome.summary is not None
    assert outcome.summary.job_run_id is not None
    tracker.run_ids.append(outcome.summary.job_run_id)

    run = await fetch_row(session, _SELECT_RUN, {"job_run_id": outcome.summary.job_run_id})
    assert run is not None
    assert run["requested_by"] == _REQUESTED_BY
    assert run["total_work_items"] == 1
    # The dispatcher's own contract ends here: it opened a real run and drove one real work item
    # through `run_job_slice`. Whether THIS lane's handler then reports success or failure is the
    # lane's own contract (`test_strategy_mv_refresh*.py`), and asserting it here would make the
    # dispatcher's test fail whenever that lane revises what "a degraded refresh" means.
    assert outcome.summary.claimed == 1
    assert run["status"] in {"queued", "running", "succeeded", "partial", "failed", "dead_letter"}


async def test_a_paused_lane_runs_nothing_and_stays_paused(tracker: _RunTracker) -> None:
    session = await tracker.open_session()
    await ensure_job_definition(session, strategy_mv_refresh_definition_spec())
    await session.commit()
    await _clear_existing_runs(session)

    await session.execute(_SET_ENABLED, {"name": _LANE, "enabled": False})
    await session.commit()
    assert (await read_lane_pause_state(session, _LANE)).paused is True

    outcome = await dispatch_lane(session, _LANE, requested_by=_REQUESTED_BY)

    assert outcome.state == "paused"
    assert outcome.summary is None
    runs = await fetch_row(session, _COUNT_RUNS, {"name": _LANE})
    assert runs is not None
    assert int(runs["run_count"]) == 0
    # The regression this file exists for: the lane's own trigger would have re-enabled it.
    assert (await read_lane_pause_state(session, _LANE)).paused is True


async def test_pause_state_tells_an_unregistered_lane_apart_from_a_paused_one(tracker: _RunTracker) -> None:
    session = await tracker.open_session()

    absent = await read_lane_pause_state(session, f"lane-that-does-not-exist-{uuid.uuid4()}")
    assert absent.registered is False
    assert absent.paused is False

    await ensure_job_definition(session, strategy_mv_refresh_definition_spec())
    await session.commit()
    present = await read_lane_pause_state(session, _LANE)
    assert present.registered is True
    assert present.paused is False
