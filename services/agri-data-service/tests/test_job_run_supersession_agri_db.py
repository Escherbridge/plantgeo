"""Real-PostgreSQL proof that the checkpoint query, the breaker and a recorded supersession agree.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn`` fixture. The
unit suites fake every statement; this file executes the ones that matter against a server: the
incident INSERT's casts and its ``ON CONFLICT (fingerprint)`` no-op, the ``superseded_by_operator``
probe and the ``consecutive_failures`` window added to ``select_latest_run.sql``, and the planner
holding and releasing lanes through the exact production query. Each test scopes itself to a
uuid-suffixed lane and erases its definition, run tree and incident rows on teardown.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.execution import job_executor_service
from agri_data_service.execution.job_executor_service import (
    EXECUTOR_WORK_ITEM_KIND,
    ActivationConfig,
    LaneExecutionSpec,
    read_lane_checkpoint,
)
from agri_data_service.execution.job_run_supersession import (
    SupersessionReceipt,
    supersede_failed_run,
    supersession_fingerprint,
)
from agri_data_service.jobs.lease import apply_statement_timeout, fetch_row
from agri_data_service.jobs.registry import JobWorkItemSpec
from agri_data_service.jobs.worker import ensure_job_definition, open_job_run, refresh_job_run_rollup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

FIRST_BUCKET: Final = datetime(2026, 9, 2, 18, tzinfo=UTC)
NOW: Final = datetime(2026, 9, 3, 20, 30, tzinfo=UTC)
CURRENT_BUCKET: Final = datetime(2026, 9, 3, 20, tzinfo=UTC)
LEDGER: Final = "agri-db-gate/agri_sweep"

_SETTLE_THE_SHARD: Final = text("""
UPDATE agri.job_work_item
SET status = CAST(:status AS varchar),
    attempt_count = CASE WHEN CAST(:status AS varchar) = 'dead_letter' THEN max_attempts ELSE 1 END,
    completed_at = now(),
    last_error_summary = CASE WHEN CAST(:status AS varchar) = 'dead_letter' THEN 'test: exited 1' ELSE NULL END
WHERE job_run_id = :job_run_id
RETURNING id
""")

_RUN_STATE: Final = text("""
SELECT status, failed_work_items, completed_at FROM agri.job_run WHERE id = :job_run_id
""")

_ITEM_STATE: Final = text("""
SELECT status, attempt_count FROM agri.job_work_item WHERE job_run_id = :job_run_id
""")

_INCIDENT_ROWS: Final = text("""
SELECT count(*) AS incidents, min(status) AS status, min(summary) AS summary, min(owner) AS owner
FROM agri.job_incident
WHERE fingerprint = :fingerprint
""")

_DELETE_INCIDENTS: Final = text("""
DELETE FROM agri.job_incident
WHERE job_run_id IN (
    SELECT run.id FROM agri.job_run AS run
    JOIN agri.job_definition AS definition ON definition.id = run.job_definition_id
    WHERE definition.name = :name
)
""")

_DELETE_RUNS: Final = text("""
DELETE FROM agri.job_run
WHERE job_definition_id IN (SELECT id FROM agri.job_definition WHERE name = :name)
""")

_DELETE_DEFINITION: Final = text("DELETE FROM agri.job_definition WHERE name = :name")

Settlement = Literal["dead_letter", "succeeded"]
CatchUp = Literal["coalesce_latest", "replay_oldest"]


def _lane(lane_id: str, catch_up_policy: CatchUp) -> LaneExecutionSpec:
    """A minimal hourly lane whose definition name is unique to this test run."""
    return LaneExecutionSpec(
        lane_id=lane_id,
        legacy_owners=(),
        required_handoff_acknowledgements=(),
        conflicts_with=(),
        work_class="backlog" if catch_up_policy == "replay_oldest" else "incremental",
        migration_disposition="consolidatable",
        cadence_seconds=3600,
        phase_offset_seconds=0,
        schedule="0 * * * *",
        publication_lag_days=None,
        publication_cadence_days=None,
        publication_lag_source="test_job_run_supersession_agri_db",
        selection_policy="test",
        catch_up_policy=catch_up_policy,
        command=("agri-service", "ops", "jobs-pulse", "--dry-run"),
        command_timeout_seconds=60,
        description="disposable lane for the supersession protocol test",
    )


class Scaffold:
    """One disposable executor lane, its sessions, and the teardown that erases everything it wrote."""

    def __init__(self, engine: AsyncEngine, spec: LaneExecutionSpec) -> None:
        self.engine = engine
        self.spec = spec
        self._sessions: list[AsyncSession] = []

    async def open_session(self) -> AsyncSession:
        session = AsyncSession(self.engine, expire_on_commit=False)
        self._sessions.append(session)
        await apply_statement_timeout(session)
        return session

    async def discard(self) -> None:
        for session in self._sessions:
            await session.rollback()
            await session.close()
        self._sessions.clear()
        async with AsyncSession(self.engine) as session:
            name = {"name": self.spec.definition_name}
            # job_incident.job_run_id does not cascade, so incidents go before the runs they reference.
            await session.execute(_DELETE_INCIDENTS, name)
            await session.execute(_DELETE_RUNS, name)
            await session.execute(_DELETE_DEFINITION, name)
            await session.commit()


async def _scaffold(dsn: str, catch_up_policy: CatchUp) -> Scaffold:
    engine = create_async_engine(dsn)
    return Scaffold(engine, _lane(f"test-supersession-{uuid.uuid4().hex[:12]}", catch_up_policy))


@pytest.fixture
async def replay_lane(agri_db_async_dsn: str) -> AsyncIterator[Scaffold]:
    lane = await _scaffold(agri_db_async_dsn, "replay_oldest")
    try:
        yield lane
    finally:
        await lane.discard()
        await lane.engine.dispose()


@pytest.fixture
async def coalesce_lane(agri_db_async_dsn: str) -> AsyncIterator[Scaffold]:
    lane = await _scaffold(agri_db_async_dsn, "coalesce_latest")
    try:
        yield lane
    finally:
        await lane.discard()
        await lane.engine.dispose()


async def _settle_checkpoint(
    session: AsyncSession,
    spec: LaneExecutionSpec,
    bucket: datetime,
    settlement: Settlement,
) -> uuid.UUID:
    """Register the lane once, open one bucket with its command shard, and settle that shard as asked."""
    definition = await ensure_job_definition(session, spec.definition_spec())
    bucket_key = bucket.isoformat()
    opened = await open_job_run(
        session,
        definition,
        logical_run_key=f"{spec.definition_name}:{bucket_key}",
        scheduled_for=bucket,
        requested_by="test_job_run_supersession_agri_db",
        target_partitions={"lane_id": spec.lane_id, "scheduled_for": bucket_key},
        work_items=(
            JobWorkItemSpec(
                shard_key=bucket_key,
                kind=EXECUTOR_WORK_ITEM_KIND,
                payload={"lane_id": spec.lane_id, "scheduled_for": bucket_key},
            ),
        ),
    )
    settled = await fetch_row(session, _SETTLE_THE_SHARD, {"job_run_id": opened.job_run_id, "status": settlement})
    assert settled is not None
    rollup = await refresh_job_run_rollup(session, opened.job_run_id)
    assert rollup.status == ("failed" if settlement == "dead_letter" else "succeeded")
    await session.commit()
    await apply_statement_timeout(session)
    return opened.job_run_id


async def _supersede(
    session: AsyncSession,
    spec: LaneExecutionSpec,
    run_id: uuid.UUID,
    *,
    evidence: str,
    apply: bool,
) -> SupersessionReceipt:
    """Drive the verb's core exactly as `_supersede_process` does: commit a recording, discard the rest."""
    receipt = await supersede_failed_run(
        session, spec, run_id, ledger=LEDGER, evidence=evidence, operator="tester", now=NOW, apply=apply
    )
    if receipt.outcome == "recorded":
        await session.commit()
    else:
        await session.rollback()
    await apply_statement_timeout(session)
    return receipt


async def _run_state(session: AsyncSession, run_id: uuid.UUID) -> Mapping[str, object]:
    row = await fetch_row(session, _RUN_STATE, {"job_run_id": run_id})
    assert row is not None
    return row


async def _streak(session: AsyncSession, spec: LaneExecutionSpec) -> int:
    latest = await read_lane_checkpoint(session, spec)
    await session.rollback()
    await apply_statement_timeout(session)
    assert latest is not None
    return latest.consecutive_failures


async def test_a_recorded_supersession_releases_a_replay_lane_and_touches_nothing_else(replay_lane: Scaffold) -> None:
    spec = replay_lane.spec
    session = await replay_lane.open_session()
    run_id = await _settle_checkpoint(session, spec, FIRST_BUCKET, "dead_letter")
    fingerprint = supersession_fingerprint(run_id)

    # The production query sees a failed checkpoint, a streak of one, and no supersession.
    before = await read_lane_checkpoint(session, spec)
    assert before is not None
    assert before.run_id == run_id
    assert before.status == "failed"
    assert before.superseded_by_operator is False
    assert before.consecutive_failures == 1
    await session.rollback()
    await apply_statement_timeout(session)

    # Without --apply nothing is written.
    dry = await _supersede(session, spec, run_id, evidence="dry run", apply=False)
    assert dry.outcome == "dry_run"
    assert dry.next_bucket == CURRENT_BUCKET
    assert [item.status for item in dry.work_items] == ["dead_letter"]
    incidents = await fetch_row(session, _INCIDENT_ROWS, {"fingerprint": fingerprint})
    assert incidents is not None
    assert incidents["incidents"] == 0

    # With --apply exactly one resolved incident row lands, cast and checked by the server.
    applied = await _supersede(session, spec, run_id, evidence="understood: old code exited 1", apply=True)
    assert applied.outcome == "recorded"
    assert applied.incident_id is not None
    incidents = await fetch_row(session, _INCIDENT_ROWS, {"fingerprint": fingerprint})
    assert incidents is not None
    assert incidents["incidents"] == 1
    assert incidents["status"] == "resolved"
    assert incidents["summary"] == "understood: old code exited 1"
    assert incidents["owner"] == "tester"

    # The run and its dead letter are exactly as they were.
    run = await _run_state(session, run_id)
    assert run["status"] == "failed"
    assert run["failed_work_items"] == 1
    item = await fetch_row(session, _ITEM_STATE, {"job_run_id": run_id})
    assert item is not None
    assert item["status"] == "dead_letter"

    # Recording it again is a no-op the conflict clause absorbs, reported with the FIRST evidence.
    again = await _supersede(session, spec, run_id, evidence="second recording", apply=True)
    assert again.outcome == "already_superseded"
    assert again.incident_id == applied.incident_id
    assert again.evidence == "understood: old code exited 1"
    incidents = await fetch_row(session, _INCIDENT_ROWS, {"fingerprint": fingerprint})
    assert incidents is not None
    assert incidents["incidents"] == 1

    after = await read_lane_checkpoint(session, spec)
    assert after is not None
    assert after.superseded_by_operator is True
    await session.rollback()


async def test_the_planner_holds_a_replay_lane_and_resumes_it_at_the_current_bucket_once_superseded(
    replay_lane: Scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = replay_lane.spec
    session = await replay_lane.open_session()
    run_id = await _settle_checkpoint(session, spec, FIRST_BUCKET, "dead_letter")
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({spec.lane_id: spec}))
    activation = ActivationConfig(frozenset({spec.lane_id}))

    held, due = await job_executor_service._plan_active_lanes(session, activation, NOW)
    assert due == []
    assert [result.state for result in held] == ["failed"]
    assert held[0].run_id == run_id
    assert held[0].handoff_blockers == (
        f"operator supersession required: agri-service ops jobs-supersede-run --lane {spec.lane_id} --run-id {run_id}",
    )

    recorded = await _supersede(session, spec, run_id, evidence="planner proof", apply=True)
    assert recorded.outcome == "recorded"

    released, due = await job_executor_service._plan_active_lanes(session, activation, NOW)
    assert released == []
    assert [
        (candidate.scheduled_for, candidate.existing_run_id, candidate.last_scheduled_for, candidate.superseded_run_id)
        for candidate in due
    ] == [(CURRENT_BUCKET, None, FIRST_BUCKET, run_id)]
    assert due[0].supersession == "operator"


async def test_the_failure_streak_counts_only_the_unbroken_newest_run_of_failures(
    coalesce_lane: Scaffold,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = coalesce_lane.spec
    session = await coalesce_lane.open_session()
    monkeypatch.setattr(job_executor_service, "LANE_SPECS", MappingProxyType({spec.lane_id: spec}))
    activation = ActivationConfig(frozenset({spec.lane_id}))
    hour = timedelta(hours=1)

    # Two failures in a row: the clock still releases a coalesce lane at the current bucket.
    await _settle_checkpoint(session, spec, FIRST_BUCKET, "dead_letter")
    await _settle_checkpoint(session, spec, FIRST_BUCKET + hour, "dead_letter")
    assert await _streak(session, spec) == 2
    held, due = await job_executor_service._plan_active_lanes(session, activation, NOW)
    assert held == []
    assert [(candidate.scheduled_for, candidate.supersession) for candidate in due] == [(CURRENT_BUCKET, "clock")]

    # A success resets the streak; the window counts from the newest run backwards.
    await _settle_checkpoint(session, spec, FIRST_BUCKET + 2 * hour, "succeeded")
    await _settle_checkpoint(session, spec, FIRST_BUCKET + 3 * hour, "dead_letter")
    assert await _streak(session, spec) == 1

    # Three in a row trips the breaker: the lane is held with the verb named, and the verb accepts it.
    await _settle_checkpoint(session, spec, FIRST_BUCKET + 4 * hour, "dead_letter")
    third = await _settle_checkpoint(session, spec, FIRST_BUCKET + 5 * hour, "dead_letter")
    assert await _streak(session, spec) == 3
    held, due = await job_executor_service._plan_active_lanes(session, activation, NOW)
    assert due == []
    assert [result.state for result in held] == ["failed"]
    assert held[0].run_id == third
    assert len(held[0].handoff_blockers) == 1

    recorded = await _supersede(session, spec, third, evidence="breaker proof", apply=True)
    assert recorded.outcome == "recorded"
    assert recorded.consecutive_failures == 3
    held, due = await job_executor_service._plan_active_lanes(session, activation, NOW)
    assert held == []
    assert [(candidate.scheduled_for, candidate.supersession) for candidate in due] == [(CURRENT_BUCKET, "operator")]
