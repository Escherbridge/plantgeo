"""Real-PostgreSQL proof that `jobs-pulse` discovers and drives BOTH namespaces from one ledger.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn`` fixture,
which pins the Alembic head and refuses the persistent ``plantgeo`` warehouse. Follows the same
uuid-scoped, delete-on-teardown shape as ``test_jobs_dispatch_agri_db.py``.

What this proves against real SQL, that a mocked-session unit test cannot: `discover_pulse_plan`'s
join between `agri.job_definition` and this verb's own archive-lane namespace
(`_ARCHIVE_LANE_TOKEN_BY_DEFINITION_NAME`) actually matches real rows, real pause state flows through
both namespaces, and `run_jobs_pulse` reaches BOTH a real dispatchable lane and a real durable
definition from one ledger in one call.

What it deliberately does NOT prove: that `run_archive_definition_slice` itself walks a real archive
window. That function opens a real `httpx` client against FIRMS/USGS NWIS, and this suite must not
call a live API (`conductor/code_styleguides/python.md`, "Tests and review gates"). It is monkeypatched
here to a stub that records its call and returns a canned summary; `run_archive_definition_slice`'s
own contract is `test_ingest_commands_jobs.py`'s job, unchanged by this file. The dispatchable lane,
by contrast, runs for real: `strategy-mv-refresh`'s handler touches no network, only the `geo.*`
materialized views (absent from an Alembic-only disposable database, which the handler already
degrades to a reported failure rather than a raise -- see `jobs/strategy_mv_refresh.py`).
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution import jobs_pulse_command
from agri_data_service.ingest.archive_walk import archive_lane_definition_name, archive_lane_definition_spec
from agri_data_service.ingest.lanes import STREAMFLOW_ARCHIVE_LANE
from agri_data_service.jobs.lease import apply_statement_timeout
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    strategy_mv_refresh_definition_spec,
)
from agri_data_service.jobs.worker import JobSliceSummary, ensure_job_definition

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_DISPATCHABLE_LANE: Final = STRATEGY_MV_REFRESH_DEFINITION_NAME
_STREAMFLOW_TOKEN: Final = STREAMFLOW_ARCHIVE_LANE.name
_STREAMFLOW_DEFINITION: Final = archive_lane_definition_name(STREAMFLOW_ARCHIVE_LANE)
_REQUESTED_BY: Final = "test_jobs_pulse_agri_db"

_DELETE_RUN_BY_DEFINITION_NAME: Final = text(
    "DELETE FROM agri.job_run WHERE job_definition_id IN (SELECT id FROM agri.job_definition WHERE name = :name)"
)
_SET_ENABLED: Final = text("UPDATE agri.job_definition SET enabled = :enabled, updated_at = now() WHERE name = :name")

# Seeds for the dead-letter census. Written as raw INSERTs rather than through the runtime because the
# state under test is a lane whose items were settled by SOME EARLIER TICK, which no single call can
# produce -- and because it must contain a 'cancelled' item, which only an operator ever writes.
_INSERT_RUN: Final = text(
    "INSERT INTO agri.job_run (id, job_definition_id, logical_run_key, scheduled_for, status) "
    "SELECT :run_id, id, :logical_run_key, now(), 'running' FROM agri.job_definition WHERE name = :name LIMIT 1"
)
_INSERT_SETTLED_WORK_ITEM: Final = text(
    "INSERT INTO agri.job_work_item (job_run_id, shard_key, kind, status, completed_at) "
    "VALUES (:run_id, :shard_key, 'test_census_shard', :status, now())"
)


@dataclass
class _RunTracker:
    """One engine, and the pause flag restored to enabled on both definitions at teardown.

    The definition rows themselves are left behind, exactly as `test_jobs_dispatch_agri_db.py`
    leaves the strategy-mv-refresh one: real, permanent metadata a production deploy also upserts.
    """

    engine: AsyncEngine
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
            for name in (_DISPATCHABLE_LANE, _STREAMFLOW_DEFINITION):
                await session.execute(_DELETE_RUN_BY_DEFINITION_NAME, {"name": name})
                await session.execute(_SET_ENABLED, {"name": name, "enabled": True})
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


async def _seed_both_definitions(tracker: _RunTracker) -> AsyncSession:
    """Upsert (enabled) rows for both namespaces, and clear any run either already has."""
    session = await tracker.open_session()
    await ensure_job_definition(session, strategy_mv_refresh_definition_spec())
    await ensure_job_definition(session, archive_lane_definition_spec(STREAMFLOW_ARCHIVE_LANE))
    await session.commit()
    for name in (_DISPATCHABLE_LANE, _STREAMFLOW_DEFINITION):
        await session.execute(_DELETE_RUN_BY_DEFINITION_NAME, {"name": name})
    await session.commit()
    return session


def _bind_real_ingest_session(monkeypatch: pytest.MonkeyPatch, engine: AsyncEngine) -> None:
    """Point this module's `ingest_session()` seam at the real disposable engine for this test."""

    @asynccontextmanager
    async def _factory() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await apply_statement_timeout(session)
            yield session

    monkeypatch.setattr(jobs_pulse_command, "ingest_session", _factory)


def _stub_archive_slice(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace the real archive-walk slice runner with a recorder, so no live HTTP call is ever made."""
    calls: list[dict[str, object]] = []

    async def _fake_run_archive_definition_slice(
        *,
        definition_name: str,
        worker_id: str,
        budget_seconds: float | None,
    ) -> JobSliceSummary:
        calls.append({"definition_name": definition_name, "worker_id": worker_id, "budget_seconds": budget_seconds})
        return JobSliceSummary(
            definition_name=definition_name,
            worker_id=worker_id,
            job_run_id=uuid.uuid4(),
            stop_reason="no_claimable_work",
            claimed=3,
            succeeded=3,
            run_status="succeeded",
        )

    monkeypatch.setattr(jobs_pulse_command, "run_archive_definition_slice", _fake_run_archive_definition_slice)
    return calls


async def test_discover_pulse_plan_joins_both_namespaces_against_the_real_ledger(tracker: _RunTracker) -> None:
    session = await _seed_both_definitions(tracker)

    plan = await jobs_pulse_command.discover_pulse_plan(
        session,
        lane_filter=frozenset({_DISPATCHABLE_LANE, _STREAMFLOW_TOKEN}),
    )

    dispatchable_ids = {entry.lane_id for entry in plan.dispatchable}
    assert _DISPATCHABLE_LANE in dispatchable_ids
    dispatchable_entry = next(entry for entry in plan.dispatchable if entry.lane_id == _DISPATCHABLE_LANE)
    assert dispatchable_entry.pause_state.paused is False

    durable_tokens = {entry.lane_token for entry in plan.durable}
    assert _STREAMFLOW_TOKEN in durable_tokens
    durable_entry = next(entry for entry in plan.durable if entry.lane_token == _STREAMFLOW_TOKEN)
    assert durable_entry.definition_name == _STREAMFLOW_DEFINITION
    assert durable_entry.enabled is True


async def test_a_paused_durable_definition_is_discovered_as_paused_not_absent(tracker: _RunTracker) -> None:
    session = await _seed_both_definitions(tracker)
    await session.execute(_SET_ENABLED, {"name": _STREAMFLOW_DEFINITION, "enabled": False})
    await session.commit()

    plan = await jobs_pulse_command.discover_pulse_plan(session, lane_filter=frozenset({_STREAMFLOW_TOKEN}))

    assert len(plan.durable) == 1
    assert plan.durable[0].enabled is False


async def test_run_jobs_pulse_reaches_a_real_dispatchable_lane_and_a_real_durable_definition_in_one_call(
    tracker: _RunTracker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_both_definitions(tracker)
    _bind_real_ingest_session(monkeypatch, tracker.engine)
    archive_calls = _stub_archive_slice(monkeypatch)

    summary = await jobs_pulse_command.run_jobs_pulse(
        lane_filter=frozenset({_DISPATCHABLE_LANE, _STREAMFLOW_TOKEN}),
        time_budget_seconds=600.0,
        worker_id="test-jobs-pulse-worker",
        # This test's whole contract (see the module docstring) is the dispatchable + durable
        # passes reaching real rows in one call; the maintenance pass is a third, separate pass
        # with its own reconcile/plan-gaps/validate-streams tests. It is also unsafe to include
        # here unmocked: `reconcile_archive_lane`/`plan_archive_lane_gaps` resolve their own
        # session through `ingest.commands`'s own `ingest_session` import, not this module's --
        # `_bind_real_ingest_session` above only binds `jobs_pulse_command.ingest_session` -- so a
        # maintenance pass here would reach for `LOCAL_SOURCE_LOADER_DATABASE_URL` instead of this
        # test's disposable engine.
        include_maintenance=False,
    )

    lanes_by_id = {lane.lane: lane for lane in summary.lanes}
    assert set(lanes_by_id) == {_DISPATCHABLE_LANE, _STREAMFLOW_TOKEN}

    dispatchable_result = lanes_by_id[_DISPATCHABLE_LANE]
    assert dispatchable_result.kind == "dispatchable"
    # A real slice ran through the real ledger; whatever the matview refresh itself decided (this
    # disposable database carries no geo.* schema) is strategy_mv_refresh's own contract, not this
    # verb's -- claiming exactly one work item is.
    assert dispatchable_result.outcome == "ran"
    assert dispatchable_result.records == 1

    durable_result = lanes_by_id[_STREAMFLOW_TOKEN]
    assert durable_result.kind == "durable"
    assert durable_result.outcome == "ran"
    assert durable_result.records == 3
    assert durable_result.dead_lettered == 0
    assert durable_result.standing_dead_letters == 0
    assert len(archive_calls) == 1
    assert archive_calls[0]["definition_name"] == _STREAMFLOW_DEFINITION


async def test_the_dead_letter_census_counts_buried_items_and_never_an_operator_cancellation(
    tracker: _RunTracker,
) -> None:
    """Real-SQL proof of the standing-failure signal, including the safety property it exists to hold.

    The census joins `agri.job_work_item` up through `agri.job_run` to `agri.job_definition.name`, and
    that join is exactly what a mocked session cannot prove. It also asserts the property finding (b)
    of the 2026-08-16 review showed the old `job_run.status` signal violated: a lane whose only settled
    work is an operator CANCELLATION is not carrying buried work and must not red the hourly tick.
    """
    session = await _seed_both_definitions(tracker)
    run_id = uuid.uuid4()
    await session.execute(
        _INSERT_RUN,
        {"run_id": run_id, "logical_run_key": f"census-test-{run_id}", "name": _STREAMFLOW_DEFINITION},
    )
    await session.execute(
        _INSERT_SETTLED_WORK_ITEM,
        {"run_id": run_id, "shard_key": "2026-01-01", "status": "dead_letter"},
    )
    await session.execute(
        _INSERT_SETTLED_WORK_ITEM,
        {"run_id": run_id, "shard_key": "2026-01-02", "status": "cancelled"},
    )
    await session.execute(
        _INSERT_SETTLED_WORK_ITEM,
        {"run_id": run_id, "shard_key": "2026-01-03", "status": "succeeded"},
    )
    await session.commit()

    standing = await jobs_pulse_command.read_standing_dead_letters(session, _STREAMFLOW_DEFINITION)

    assert standing.count == 1, "only the 'dead_letter' item is buried work"
    assert standing.first_shard_key == "2026-01-01"
    assert standing.present is True

    # And the lane next door, whose ledger rows this run never touched, stays at zero.
    other = await jobs_pulse_command.read_standing_dead_letters(session, _DISPATCHABLE_LANE)
    assert other.count == 0
    assert other.present is False
