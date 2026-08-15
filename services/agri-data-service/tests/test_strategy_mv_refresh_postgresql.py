"""Real-PostgreSQL proof that the strategy-mv-refresh lane runs against the shared job ledger.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn`` fixture,
which pins the Alembic head and refuses the persistent ``plantgeo`` warehouse. Follows the same
uuid-scoped, delete-on-teardown shape as ``test_jobs_protocol_agri_db.py``.

What this proves, and what it deliberately does not.
``drizzle/0027_ml_strategy_materialized_views.sql`` -- the ROOT repo's Next.js/Drizzle migration
tree, a different system from this service's Alembic one -- is what actually creates
``geo.mv_strategy_recommendations_*``, and the disposable database ``agri_db_async_dsn`` points at
is migrated to Alembic head ONLY. So every test in this file runs against a database where those
three views do not exist, which is exactly the "drizzle 0027 may not be applied everywhere" case
the job callable is required to handle gracefully. This file proves that SKIP path against a real
server, and proves the ledger wiring (``ensure_job_definition``, ``open_job_run``,
``run_job_slice``, the handler's own commit) really works end to end against real SQL. It does
NOT prove the ``REFRESH MATERIALIZED VIEW CONCURRENTLY`` statement itself parses or runs against
real matviews -- that would need a database with 0027 applied, which
``test_strategy_mv_refresh.py``'s mocked ``FakeSession`` covers instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.jobs.lease import apply_statement_timeout, fetch_row
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    STRATEGY_MV_REFRESH_HANDLER_TOKEN,
    STRATEGY_MV_REFRESH_SCHEDULE_CRON,
    STRATEGY_RECOMMENDATION_VIEWS,
    refresh_strategy_recommendation_views,
    trigger_strategy_mv_refresh,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_DELETE_RUN: Final = text("DELETE FROM agri.job_run WHERE id = :job_run_id")

_DEFINITION_ROW: Final = text(
    "SELECT handler, schedule, queue_name, max_attempts, lease_seconds, time_budget_seconds "
    "FROM agri.job_definition WHERE name = :name"
)


@dataclass
class _RunTracker:
    """One engine, plus every job_run id a test minted -- deleted on teardown, definition left alone.

    The definition row (``agri.job_definition`` name=``strategy-mv-refresh``) is real, permanent
    metadata a production deploy would also upsert; leaving it behind is what a real trigger does,
    and ``ensure_job_definition``'s UPSERT makes re-running this file against it idempotent. Only
    the runs THIS test minted are deleted, scoped by id rather than by definition, so this file
    never races a concurrently-running production or test tick on the same shared disposable
    database.
    """

    engine: AsyncEngine
    run_ids: list[uuid.UUID] = field(default_factory=list)

    async def open_session(self) -> AsyncSession:
        session = AsyncSession(self.engine, expire_on_commit=False)
        await apply_statement_timeout(session)
        return session

    async def discard(self) -> None:
        if not self.run_ids:
            return
        async with AsyncSession(self.engine) as session:
            for run_id in self.run_ids:
                await session.execute(_DELETE_RUN, {"job_run_id": run_id})
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


def _unique_moment() -> datetime:
    """An instant unlikely to collide with any other test's or production's poll-interval bucket."""
    offset_days = (uuid.uuid4().int % 3650) + 1
    return datetime.now(UTC) - timedelta(days=offset_days)


async def test_refresh_skips_every_view_against_a_database_with_no_drizzle_0027(
    tracker: _RunTracker,
) -> None:
    session = await tracker.open_session()

    report = await refresh_strategy_recommendation_views(session)
    await session.commit()

    assert [result.qualified_name for result in report.results] == list(STRATEGY_RECOMMENDATION_VIEWS)
    assert [result.status for result in report.results] == ["skipped_missing"] * len(STRATEGY_RECOMMENDATION_VIEWS)
    # All three guardrailed views missing is a degraded outcome, not a quiet success: the run
    # refreshed nothing, so it must not read as identical to a healthy tick in the ledger.
    assert report.has_failures
    assert all(result.row_count is None for result in report.results)


async def test_trigger_registers_the_real_definition_and_retries_with_no_views_present(
    tracker: _RunTracker,
) -> None:
    session = await tracker.open_session()

    summary = await trigger_strategy_mv_refresh(
        session,
        requested_by="test_strategy_mv_refresh_postgresql",
        now=_unique_moment(),
    )
    assert summary.job_run_id is not None
    tracker.run_ids.append(summary.job_run_id)

    # All three views are absent (drizzle 0027 lives in a different migration tree this database
    # never ran), so the handler's one work item now reports JobHandlerOutcome.failed and the
    # worker schedules a retry -- attempt 1 of STRATEGY_MV_REFRESH_MAX_ATTEMPTS=3 -- rather than
    # dead-lettering or (the pre-fix behaviour) completing as if work had actually been done.
    assert summary.dead_lettered == 0
    assert summary.succeeded == 0
    assert summary.retried == 1
    assert summary.stop_reason == "no_claimable_work"

    definition = await fetch_row(session, _DEFINITION_ROW, {"name": STRATEGY_MV_REFRESH_DEFINITION_NAME})
    assert definition is not None
    assert definition["handler"] == STRATEGY_MV_REFRESH_HANDLER_TOKEN
    assert definition["schedule"] == STRATEGY_MV_REFRESH_SCHEDULE_CRON
    assert definition["lease_seconds"] > definition["time_budget_seconds"]


async def test_triggering_twice_inside_one_window_shares_a_run_and_the_second_call_finds_no_work(
    tracker: _RunTracker,
) -> None:
    session = await tracker.open_session()
    moment = _unique_moment()

    first = await trigger_strategy_mv_refresh(session, requested_by="test_strategy_mv_refresh_postgresql", now=moment)
    assert first.job_run_id is not None
    tracker.run_ids.append(first.job_run_id)
    # No views present -> JobHandlerOutcome.failed -> scheduled retry, not a completion.
    assert first.succeeded == 0
    assert first.retried == 1

    # A few seconds later, still inside the same STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS bucket:
    # open_job_run's ON CONFLICT (logical_run_key) DO NOTHING means this rejoins first's run rather
    # than minting a second one, and its single work item is still behind first's retry backoff
    # (DEFAULT_INITIAL_BACKOFF_SECONDS=30s, far longer than the 5s gap below), so nothing is claimable yet.
    second = await trigger_strategy_mv_refresh(
        session,
        requested_by="test_strategy_mv_refresh_postgresql",
        now=moment + timedelta(seconds=5),
    )
    assert second.job_run_id == first.job_run_id
    assert second.succeeded == 0
    assert second.retried == 0
    assert second.stop_reason == "no_claimable_work"
