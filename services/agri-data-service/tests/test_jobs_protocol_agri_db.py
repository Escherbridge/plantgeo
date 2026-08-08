"""Real-PostgreSQL proof of the fenced-lease protocol the unit suites only ever simulate.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn``
fixture, which pins the Alembic head and refuses the persistent ``plantgeo`` warehouse.

``test_jobs_lease.py`` and ``test_jobs_worker.py`` answer every statement from a
``RecordingSession`` keyed on the ``-- <statement_name>`` marker comment, so no SQL in
``jobs/lease.py`` or ``jobs/worker.py`` had ever reached a server. This file is the
counterpart that executes it: the CHECK constraints named in those modules' comments, the
``job_checkpoint`` composite fence FK, ``jsonb_to_recordset`` column coercion, the
``CAST(:resume_at AS timestamptz)`` and ``make_interval(secs => ...)`` bindings, and
``FOR UPDATE SKIP LOCKED`` interleaving across two real connections are all observed here
rather than reasoned about. See `jobs/AGENTS.md` "Tests".

The protocol commits, so these tests cannot hide inside one rolled-back transaction. Each
instead scopes itself to a uuid-suffixed definition and deletes that definition's whole run
tree on teardown, leaving the database exactly as it was found.
"""

# ruff: noqa: PLR2004

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.jobs.lease import (
    apply_statement_timeout,
    claim_work_item,
    complete_work_item,
    cursor_checksum,
    defer_work_item,
    extend_lease,
    fail_work_item,
    fetch_row,
    fetch_rows,
    mark_work_item_running,
    reclaim_expired_leases,
    record_checkpoint,
    release_lost_attempt,
)
from agri_data_service.jobs.registry import (
    DEFAULT_RETRY_POLICY,
    JobDefinitionSpec,
    JobWorkItemSpec,
    RetryPolicy,
)
from agri_data_service.jobs.worker import ensure_job_definition, open_job_run, refresh_job_run_rollup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from agri_data_service.jobs.worker import JobDefinitionRecord, OpenedJobRun

# `JobDefinitionSpec` refuses `lease_seconds <= time_budget_seconds`, so the two travel together.
LEASE_SECONDS: Final = 600
TIME_BUDGET_SECONDS: Final = 240

WORKER_A: Final = "worker-a"
WORKER_B: Final = "worker-b"

HANDLER_TOKEN: Final = "tests.jobs.protocol"

# A half second rather than a whole one. `make_interval(secs => :backoff_seconds)` is the binding that
# would silently truncate if the parameter reached PostgreSQL as an integer instead of a double, and
# only a fractional value makes that visible.
FRACTIONAL_BACKOFF_SECONDS: Final = 90.5

# Deliberately in the past so `available_at <= now()` holds and the fanned-out rows are claimable, while
# still being an exact instant a timestamptz round trip either preserves or does not.
FAN_OUT_AVAILABLE_AT: Final = datetime(2026, 8, 1, 6, 30, 15, 123456, tzinfo=UTC)
DEFERRAL_RESUME_AT: Final = datetime(2026, 12, 24, 18, 0, tzinfo=UTC)

# Non-ASCII keys and values, a nested object and an array: `_work_items_json` renders a payload as a
# JSON *string* inside the fan-out array, `jsonb_to_recordset` hands it back as `text`, and
# `CAST(item.payload AS jsonb)` re-parses it. Anything lost in that double hop shows up here.
FAN_OUT_PAYLOAD: Final[Mapping[str, object]] = {
    "región": "pacífico noroeste",
    "window": {"start": "2003-04-01", "days": 5},
    "retries": [1, 2, 3],
}

FIRST_CURSOR: Final[Mapping[str, object]] = {"last_window_end": "2003-04-05", "rows": 1200}
SECOND_CURSOR: Final[Mapping[str, object]] = {"last_window_end": "2003-04-10", "rows": 2400}

# The two priorities are chosen so a text sort and an integer sort disagree: as text "40" < "5", so a
# `priority` column that arrived from jsonb_to_recordset as text would order these the other way round.
LOW_PRIORITY: Final = 5
HIGH_PRIORITY: Final = 40

_WORK_ITEM_ROW: Final = text("""
SELECT id, shard_key, status, lease_owner, lease_expires_at, fencing_token, attempt_count,
       max_attempts, priority, available_at, next_attempt_at, completed_at, started_at,
       heartbeat_at, progress_fraction, checkpoint_sequence, payload, last_error_class,
       last_error_summary
FROM agri.job_work_item
WHERE id = :work_item_id
""")

_WORK_ITEM_BY_SHARD: Final = text("""
SELECT id, shard_key, status, priority, available_at, next_attempt_at, payload
FROM agri.job_work_item
WHERE job_run_id = :job_run_id AND shard_key = :shard_key
""")

_ATTEMPT_ROWS: Final = text("""
SELECT id, attempt_number, fencing_token, status, worker_id, failure_class, error_summary,
       finished_at, metrics
FROM agri.job_attempt
WHERE job_work_item_id = :work_item_id
ORDER BY attempt_number
""")

_CHECKPOINT_ROWS: Final = text("""
SELECT sequence, fencing_token, job_attempt_id, cursor, cursor_checksum, progress_fraction
FROM agri.job_checkpoint
WHERE job_work_item_id = :work_item_id
ORDER BY sequence
""")

_RUN_ROW: Final = text("""
SELECT status, total_work_items, succeeded_work_items, failed_work_items, started_at, completed_at
FROM agri.job_run
WHERE id = :job_run_id
""")

# The interval is measured against `heartbeat_at`, not against a Python clock: `retry_work_item` sets
# both columns from the same `now()` in one statement, so this difference is exactly the interval
# `make_interval` was handed and the assertion has no timing tolerance at all.
_RETRY_WAIT_INTERVAL: Final = text("""
SELECT EXTRACT(EPOCH FROM (next_attempt_at - heartbeat_at)) AS seconds
FROM agri.job_work_item
WHERE id = :work_item_id
""")

_EXPIRE_LEASE: Final = text("""
UPDATE agri.job_work_item
SET lease_expires_at = now() - make_interval(secs => 1)
WHERE id = :work_item_id
RETURNING id
""")

_REWIND_NEXT_ATTEMPT: Final = text("""
UPDATE agri.job_work_item
SET next_attempt_at = now() - make_interval(secs => 1)
WHERE id = :work_item_id
RETURNING id
""")

_SET_ITEM_MAX_ATTEMPTS: Final = text("""
UPDATE agri.job_work_item
SET max_attempts = :max_attempts
WHERE id = :work_item_id
RETURNING id
""")

_SET_RUN_TOTAL: Final = text("""
UPDATE agri.job_run
SET total_work_items = :total_work_items
WHERE id = :job_run_id
RETURNING total_work_items
""")

_ATTEMPT_STATUS: Final = text("""
SELECT status FROM agri.job_attempt WHERE id = :attempt_id
""")

_DELETE_RUNS: Final = text("""
DELETE FROM agri.job_run
WHERE job_definition_id IN (SELECT id FROM agri.job_definition WHERE name = :name)
""")

_DELETE_DEFINITION: Final = text("""
DELETE FROM agri.job_definition WHERE name = :name
""")


class Ledger:
    """One uuid-scoped job definition, the sessions a test drives it through, and the teardown that erases both."""

    def __init__(self, engine: AsyncEngine, definition_name: str) -> None:
        """Bind the scaffold to one engine and one deliberately unique definition name."""
        self.engine = engine
        self.definition_name = definition_name
        self._sessions: list[AsyncSession] = []

    async def open_session(self) -> AsyncSession:
        """Open a session on its own connection, armed with the transaction-local timeout the protocol pins."""
        session = AsyncSession(self.engine, expire_on_commit=False)
        self._sessions.append(session)
        await apply_statement_timeout(session)
        return session

    async def discard(self) -> None:
        """Close every session and delete this definition's whole run tree, leaving the database as found."""
        for session in self._sessions:
            await session.rollback()
            await session.close()
        self._sessions.clear()
        async with AsyncSession(self.engine) as session:
            # job_work_item cascades from job_run, and job_attempt/job_checkpoint cascade from the item,
            # so deleting the runs and then the definition takes the entire tree with it.
            await session.execute(_DELETE_RUNS, {"name": self.definition_name})
            await session.execute(_DELETE_DEFINITION, {"name": self.definition_name})
            await session.commit()


@pytest.fixture
async def ledger(agri_db_async_dsn: str) -> AsyncIterator[Ledger]:
    """A disposable job definition on the gated database, erased with every row it produced on teardown."""
    engine = create_async_engine(agri_db_async_dsn)
    scaffold = Ledger(engine, f"jobs-protocol-{uuid.uuid4().hex}")
    try:
        yield scaffold
    finally:
        await scaffold.discard()
        await engine.dispose()


def _shards(*shard_keys: str) -> tuple[JobWorkItemSpec, ...]:
    """The plain fan-out most tests need: one archive-window shard per key, default priority, available now."""
    return tuple(
        JobWorkItemSpec(shard_key=shard_key, kind="archive_window", payload={"shard": shard_key})
        for shard_key in shard_keys
    )


async def _open_definition_and_run(
    session: AsyncSession,
    ledger: Ledger,
    work_items: Sequence[JobWorkItemSpec],
    *,
    max_attempts: int = 5,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> tuple[JobDefinitionRecord, OpenedJobRun]:
    """Upsert this test's definition, fan its shards out and commit, so a second connection can see them."""
    definition = await ensure_job_definition(
        session,
        JobDefinitionSpec(
            name=ledger.definition_name,
            version="0001",
            handler=HANDLER_TOKEN,
            max_attempts=max_attempts,
            lease_seconds=LEASE_SECONDS,
            time_budget_seconds=TIME_BUDGET_SECONDS,
            retry_policy=retry_policy,
            parameters={"origin": "test_jobs_protocol_agri_db"},
        ),
    )
    opened = await open_job_run(
        session,
        definition,
        logical_run_key=f"{ledger.definition_name}:run-1",
        work_items=work_items,
        requested_by="test_jobs_protocol_agri_db",
        target_partitions={"region": "pnw"},
    )
    await _commit(session)
    return definition, opened


async def _commit(session: AsyncSession) -> None:
    """Commit and re-arm the statement timeout, exactly as `worker.py::_commit` does between steps."""
    await session.commit()
    await apply_statement_timeout(session)


async def _rollback(session: AsyncSession) -> None:
    """Discard a refused transaction and re-arm the timeout, exactly as `worker.py::_reset` does."""
    await session.rollback()
    await apply_statement_timeout(session)


async def _work_item(session: AsyncSession, work_item_id: uuid.UUID) -> Mapping[str, object]:
    """Read one work item back by id, so an assertion names a stored column rather than an ORM attribute."""
    row = await fetch_row(session, _WORK_ITEM_ROW, {"work_item_id": work_item_id})
    assert row is not None
    return row


async def _attempts(session: AsyncSession, work_item_id: uuid.UUID) -> Sequence[Mapping[str, object]]:
    """Every attempt row this shard has accumulated, in attempt order."""
    return await fetch_rows(session, _ATTEMPT_ROWS, {"work_item_id": work_item_id})


async def _checkpoints(session: AsyncSession, work_item_id: uuid.UUID) -> Sequence[Mapping[str, object]]:
    """Every durable cursor this shard has appended, in sequence order."""
    return await fetch_rows(session, _CHECKPOINT_ROWS, {"work_item_id": work_item_id})


async def _run(session: AsyncSession, job_run_id: uuid.UUID) -> Mapping[str, object]:
    """Read the run's hand-maintained counters and status back."""
    row = await fetch_row(session, _RUN_ROW, {"job_run_id": job_run_id})
    assert row is not None
    return row


async def _assert_shard_closed_cleanly(
    session: AsyncSession,
    work_item_id: uuid.UUID,
    job_run_id: uuid.UUID,
) -> None:
    """Assert a completed shard, its attempt and its run counter all landed in the shape the CHECKs allow."""
    completed = await _work_item(session, work_item_id)
    assert completed["status"] == "succeeded"
    # ck_job_work_item_terminal_item_has_completion_time and ck_job_work_item_complete_lease_pair both
    # held at statement execution, which is the only place a NOT DEFERRABLE constraint can hold.
    assert completed["completed_at"] is not None
    assert completed["lease_owner"] is None
    assert completed["lease_expires_at"] is None
    assert completed["next_attempt_at"] is None
    assert completed["progress_fraction"] == 1.0

    attempts = await _attempts(session, work_item_id)
    assert [row["status"] for row in attempts] == ["succeeded"]
    assert attempts[0]["finished_at"] is not None
    assert attempts[0]["metrics"] == {"rows": 1200}

    run = await _run(session, job_run_id)
    assert run["succeeded_work_items"] == 1
    assert run["total_work_items"] == 1


async def test_claim_heartbeat_checkpoint_and_completion_land_every_row_under_every_check_constraint(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    definition, opened = await _open_definition_and_run(session, ledger, _shards("firms:2003-04-01"))
    assert opened.created is True
    assert opened.added_work_items == 1
    assert opened.total_work_items == 1
    assert definition.lease_seconds == LEASE_SECONDS

    claim = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim is not None
    # The fence starts at the column default of 0 and is allocated inside the claiming UPDATE, so the
    # first token is 1 -- which is also what ck_job_attempt_positive_attempt_fencing_token demands.
    assert claim.fencing_token == 1
    assert claim.attempt_number == 1
    assert claim.max_attempts == 5
    assert claim.cursor is None
    assert claim.checkpoint_sequence == 0
    assert claim.progress_fraction == 0.0
    # asyncpg hands jsonb back through SQLAlchemy's registered codec, so `payload` is a dict here and
    # `required_column(row, "payload", dict)` does not raise. A raw driver would return a str.
    assert claim.payload == {"shard": "firms:2003-04-01"}
    assert claim.lease_expires_at.tzinfo is not None
    await _commit(session)

    leased = await _work_item(session, claim.work_item_id)
    assert leased["status"] == "leased"
    assert leased["lease_owner"] == WORKER_A
    assert leased["started_at"] is not None

    assert await mark_work_item_running(session, claim, lease_seconds=LEASE_SECONDS) is True
    await _commit(session)
    assert (await _work_item(session, claim.work_item_id))["status"] == "running"

    assert await extend_lease(session, claim, lease_seconds=LEASE_SECONDS) is True
    await _commit(session)

    record = await record_checkpoint(
        session, claim, cursor=FIRST_CURSOR, progress_fraction=0.4, lease_seconds=LEASE_SECONDS
    )
    assert record is not None
    assert record.sequence == 1
    assert record.cursor_checksum == cursor_checksum(FIRST_CURSOR)
    await _commit(session)

    checkpoints = await _checkpoints(session, claim.work_item_id)
    assert len(checkpoints) == 1
    # The composite FK fk_job_checkpoint_attempt_fence is on (job_attempt_id, job_work_item_id,
    # fencing_token); the row below only exists because all three matched the open attempt.
    assert checkpoints[0]["job_attempt_id"] == claim.attempt_id
    assert checkpoints[0]["fencing_token"] == claim.fencing_token
    assert checkpoints[0]["cursor"] == FIRST_CURSOR
    assert checkpoints[0]["progress_fraction"] == 0.4

    assert await complete_work_item(session, claim, metrics={"rows": 1200}) is True
    await _commit(session)
    await _assert_shard_closed_cleanly(session, claim.work_item_id, opened.job_run_id)


async def test_a_failed_attempt_waits_in_retry_wait_until_its_next_attempt_at_has_passed(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    policy = RetryPolicy(initial_backoff_seconds=FRACTIONAL_BACKOFF_SECONDS)
    definition, opened = await _open_definition_and_run(
        session, ledger, _shards("firms:2003-04-06"), max_attempts=5, retry_policy=policy
    )
    assert definition.retry_policy.initial_backoff_seconds == FRACTIONAL_BACKOFF_SECONDS

    claim = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim is not None
    await _commit(session)

    failure = await fail_work_item(
        session,
        claim,
        failure_class="ConnectError",
        reason="upstream refused the connection",
        retry_policy=definition.retry_policy,
    )
    assert failure is not None
    assert failure.disposition == "retry_wait"
    assert failure.is_dead_letter is False
    assert failure.backoff_seconds == FRACTIONAL_BACKOFF_SECONDS
    await _commit(session)

    waiting = await _work_item(session, claim.work_item_id)
    assert waiting["status"] == "retry_wait"
    # ck_job_work_item_resumable_item_has_next_attempt forces this column for retry_wait.
    assert waiting["next_attempt_at"] is not None
    assert waiting["lease_owner"] is None
    assert waiting["lease_expires_at"] is None
    assert waiting["last_error_class"] == "ConnectError"
    assert waiting["attempt_count"] == 1

    interval = await fetch_row(session, _RETRY_WAIT_INTERVAL, {"work_item_id": claim.work_item_id})
    assert interval is not None
    # make_interval(secs => :backoff_seconds) received a genuine double, not a truncated integer.
    assert float(str(interval["seconds"])) == FRACTIONAL_BACKOFF_SECONDS

    blocked = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert blocked is None
    await _rollback(session)

    await fetch_row(session, _REWIND_NEXT_ATTEMPT, {"work_item_id": claim.work_item_id})
    await _commit(session)

    reclaimed = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert reclaimed is not None
    assert reclaimed.work_item_id == claim.work_item_id
    # uq_job_attempt_item_fence and uq_job_attempt_item_number both accepted the second attempt only
    # because the token and the attempt number were derived inside the claiming UPDATE.
    assert reclaimed.fencing_token == 2
    assert reclaimed.attempt_number == 2
    await _commit(session)

    assert (await _work_item(session, claim.work_item_id))["next_attempt_at"] is None


async def test_exhausting_max_attempts_dead_letters_the_shard_and_never_marks_it_succeeded(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    definition, opened = await _open_definition_and_run(session, ledger, _shards("firms:2003-04-11"), max_attempts=2)

    work_item_id: uuid.UUID | None = None
    for expected_attempt in (1, 2):
        if work_item_id is not None:
            await fetch_row(session, _REWIND_NEXT_ATTEMPT, {"work_item_id": work_item_id})
            await _commit(session)
        claim = await claim_work_item(
            session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
        )
        assert claim is not None
        assert claim.attempt_number == expected_attempt
        work_item_id = claim.work_item_id
        await _commit(session)
        failure = await fail_work_item(
            session,
            claim,
            failure_class="ConnectError",
            reason="upstream refused the connection",
            retry_policy=definition.retry_policy,
        )
        assert failure is not None
        await _commit(session)

    assert work_item_id is not None
    exhausted = await _work_item(session, work_item_id)
    # The whole point of the ledger: an exhausted shard reads as missing, never as done.
    assert exhausted["status"] == "dead_letter"
    assert exhausted["status"] != "succeeded"
    assert exhausted["completed_at"] is not None
    assert exhausted["next_attempt_at"] is None
    assert exhausted["attempt_count"] == 2

    attempts = await _attempts(session, work_item_id)
    assert [row["status"] for row in attempts] == ["failed", "failed"]
    assert not any(row["status"] == "succeeded" for row in attempts)

    run = await _run(session, opened.job_run_id)
    assert run["failed_work_items"] == 1
    assert run["succeeded_work_items"] == 0

    rollup = await refresh_job_run_rollup(session, opened.job_run_id)
    await _commit(session)
    assert rollup.status == "failed"
    assert rollup.total_work_items == 1
    assert rollup.failed_work_items == 1
    assert rollup.succeeded_work_items == 0
    assert (await _run(session, opened.job_run_id))["completed_at"] is not None


async def test_a_worker_that_lost_its_lease_cannot_checkpoint_or_complete_and_the_new_owner_keeps_the_shard(
    ledger: Ledger,
) -> None:
    session_a = await ledger.open_session()
    session_b = await ledger.open_session()
    operator = await ledger.open_session()
    _, opened = await _open_definition_and_run(session_a, ledger, _shards("firms:2003-04-16"))

    claim_a = await claim_work_item(
        session_a, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim_a is not None
    await _commit(session_a)
    assert await mark_work_item_running(session_a, claim_a, lease_seconds=LEASE_SECONDS) is True
    await _commit(session_a)
    first = await record_checkpoint(
        session_a, claim_a, cursor=FIRST_CURSOR, progress_fraction=0.4, lease_seconds=LEASE_SECONDS
    )
    assert first is not None
    await _commit(session_a)

    # Worker A's container is killed at minute nine of a ten-minute lease. Nothing writes; the lease
    # simply runs out, which is exactly what this UPDATE reproduces.
    await fetch_row(operator, _EXPIRE_LEASE, {"work_item_id": claim_a.work_item_id})
    await _commit(operator)

    claim_b = await claim_work_item(
        session_b, job_run_id=opened.job_run_id, worker_id=WORKER_B, lease_seconds=LEASE_SECONDS
    )
    assert claim_b is not None
    assert claim_b.work_item_id == claim_a.work_item_id
    assert claim_b.fencing_token == claim_a.fencing_token + 1
    assert claim_b.attempt_number == 2
    # B resumes from A's durable cursor rather than from the top: this is what the checkpoint is for.
    assert claim_b.cursor == FIRST_CURSOR
    await _commit(session_b)

    # A now wakes up holding a token the item has moved past. Every fenced write must match zero rows.
    refused = await record_checkpoint(
        session_a, claim_a, cursor=SECOND_CURSOR, progress_fraction=0.9, lease_seconds=LEASE_SECONDS
    )
    assert refused is None
    assert await complete_work_item(session_a, claim_a) is False

    # Observed, not assumed, and the belt to the rollback's braces: `close_attempt_succeeded` fences
    # through the ITEM row, so inside A's own still-open transaction A's attempt is still 'running'. It
    # used to read 'succeeded' here -- a terminal verdict on a shard another worker owns, one layer below
    # the item, held back only by every caller remembering to roll back. A caller that committed at this
    # point now leaves the ledger intact rather than corrupting it.
    inside = await fetch_row(session_a, _ATTEMPT_STATUS, {"attempt_id": claim_a.attempt_id})
    assert inside is not None
    assert inside["status"] == "running"
    await _rollback(session_a)

    # The braces are still expected of callers, so this pins that the rollback changes nothing rather
    # than that it repairs something: `worker.py::_abandon` rolls back before closing the attempt as lost.
    after_rollback = await fetch_row(session_a, _ATTEMPT_STATUS, {"attempt_id": claim_a.attempt_id})
    assert after_rollback is not None
    assert after_rollback["status"] == "running"

    await release_lost_attempt(session_a, claim_a)
    await _commit(session_a)

    attempts = await _attempts(operator, claim_a.work_item_id)
    assert [row["status"] for row in attempts] == ["lost", "running"]
    assert not any(row["status"] == "succeeded" for row in attempts)

    held = await _work_item(operator, claim_a.work_item_id)
    assert held["lease_owner"] == WORKER_B
    assert held["fencing_token"] == claim_b.fencing_token
    assert held["status"] == "leased"
    # A's refused checkpoint left nothing behind, so B's cursor never rewound.
    checkpoints = await _checkpoints(operator, claim_a.work_item_id)
    assert len(checkpoints) == 1
    assert checkpoints[0]["cursor"] == FIRST_CURSOR


@pytest.mark.parametrize("close_path", ["failed", "deferred"])
async def test_a_fenced_out_worker_cannot_write_any_terminal_attempt_state_not_just_success(
    ledger: Ledger,
    close_path: str,
) -> None:
    # The success path is not the only one that closes an attempt before the item is fenced: `failed`,
    # `dead_letter` and `deferred` all write a TERMINAL attempt status one statement ahead of the fenced
    # item write. Each is proved here against the real server rather than argued to be like the other.
    session_a = await ledger.open_session()
    session_b = await ledger.open_session()
    operator = await ledger.open_session()
    _, opened = await _open_definition_and_run(session_a, ledger, _shards(f"firms:2003-04-{close_path}"))

    claim_a = await claim_work_item(
        session_a, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim_a is not None
    await _commit(session_a)

    await fetch_row(operator, _EXPIRE_LEASE, {"work_item_id": claim_a.work_item_id})
    await _commit(operator)
    claim_b = await claim_work_item(
        session_b, job_run_id=opened.job_run_id, worker_id=WORKER_B, lease_seconds=LEASE_SECONDS
    )
    assert claim_b is not None
    await _commit(session_b)

    if close_path == "failed":
        landing = await fail_work_item(
            session_a,
            claim_a,
            failure_class="UpstreamHttpError",
            reason="upstream returned 503",
            retry_policy=DEFAULT_RETRY_POLICY,
        )
        assert landing is None
    else:
        assert await defer_work_item(session_a, claim_a, resume_at=None, reason="upstream had nothing") is False

    # Inside A's own uncommitted transaction, before any rollback: the attempt has not moved. A terminal
    # 'failed' or 'deferred' here would be a verdict on a shard B owns, and it would survive a caller
    # that committed instead of rolling back.
    inside = await fetch_row(session_a, _ATTEMPT_STATUS, {"attempt_id": claim_a.attempt_id})
    assert inside is not None
    assert inside["status"] == "running"
    await _rollback(session_a)

    # B still holds the shard, and nothing about A's refused close reached it.
    held = await _work_item(operator, claim_a.work_item_id)
    assert held["lease_owner"] == WORKER_B
    assert held["fencing_token"] == claim_b.fencing_token
    assert held["last_error_class"] is None
    attempts = await _attempts(operator, claim_a.work_item_id)
    assert [row["status"] for row in attempts] == ["running", "running"]


async def test_two_concurrent_claimers_get_different_work_items_and_neither_blocks_on_the_other(
    ledger: Ledger,
) -> None:
    session_a = await ledger.open_session()
    session_b = await ledger.open_session()
    _, opened = await _open_definition_and_run(
        session_a,
        ledger,
        _shards("firms:2003-05-01", "firms:2003-05-06", "firms:2003-05-11", "firms:2003-05-16"),
    )
    assert opened.added_work_items == 4

    # A claims and deliberately does NOT commit, so its row lock is still held. Without
    # FOR UPDATE SKIP LOCKED, B's identical ORDER BY would pick the same candidate row and block on
    # that lock until the 120s statement timeout fired.
    claim_a = await claim_work_item(
        session_a, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim_a is not None

    claim_b = await asyncio.wait_for(
        claim_work_item(session_b, job_run_id=opened.job_run_id, worker_id=WORKER_B, lease_seconds=LEASE_SECONDS),
        timeout=20,
    )
    assert claim_b is not None
    assert claim_b.work_item_id != claim_a.work_item_id
    assert claim_b.shard_key != claim_a.shard_key
    assert claim_b.fencing_token == 1
    await _commit(session_a)
    await _commit(session_b)

    # And again with both claims genuinely in flight at once against the two remaining shards.
    claim_c, claim_d = await asyncio.wait_for(
        asyncio.gather(
            claim_work_item(session_a, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS),
            claim_work_item(session_b, job_run_id=opened.job_run_id, worker_id=WORKER_B, lease_seconds=LEASE_SECONDS),
        ),
        timeout=20,
    )
    assert claim_c is not None
    assert claim_d is not None
    assert claim_c.work_item_id != claim_d.work_item_id
    await _commit(session_a)
    await _commit(session_b)

    claimed = {claim_a.shard_key, claim_b.shard_key, claim_c.shard_key, claim_d.shard_key}
    assert claimed == {"firms:2003-05-01", "firms:2003-05-06", "firms:2003-05-11", "firms:2003-05-16"}

    exhausted = await claim_work_item(
        session_a, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert exhausted is None
    await _rollback(session_a)


async def test_jsonb_to_recordset_coerces_the_fan_out_columns_and_resume_at_binds_through_both_arms(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    work_items = (
        JobWorkItemSpec(
            shard_key="low-priority-shard",
            kind="archive_window",
            payload=FAN_OUT_PAYLOAD,
            priority=LOW_PRIORITY,
            available_at=FAN_OUT_AVAILABLE_AT,
        ),
        JobWorkItemSpec(
            shard_key="high-priority-shard",
            kind="archive_window",
            payload=FAN_OUT_PAYLOAD,
            priority=HIGH_PRIORITY,
            available_at=FAN_OUT_AVAILABLE_AT,
        ),
    )
    _, opened = await _open_definition_and_run(session, ledger, work_items)
    assert opened.added_work_items == 2

    for shard_key, priority in (("low-priority-shard", LOW_PRIORITY), ("high-priority-shard", HIGH_PRIORITY)):
        row = await fetch_row(session, _WORK_ITEM_BY_SHARD, {"job_run_id": opened.job_run_id, "shard_key": shard_key})
        assert row is not None
        assert isinstance(row["priority"], int)
        assert row["priority"] == priority
        assert row["available_at"] == FAN_OUT_AVAILABLE_AT
        # `open_job_run` seeds next_attempt_at from available_at so ix_job_work_item_claim's leading
        # (status, next_attempt_at) columns are never NULL for a freshly fanned-out row.
        assert row["next_attempt_at"] == FAN_OUT_AVAILABLE_AT
        assert row["payload"] == FAN_OUT_PAYLOAD

    # ORDER BY priority DESC hands back 40 before 5. A text-typed priority would have ordered "5" first.
    claim = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim is not None
    assert claim.shard_key == "high-priority-shard"
    assert claim.payload == FAN_OUT_PAYLOAD
    await _commit(session)

    assert await defer_work_item(session, claim, resume_at=DEFERRAL_RESUME_AT, reason="upstream not published") is True
    await _commit(session)

    deferred = await _work_item(session, claim.work_item_id)
    assert deferred["status"] == "deferred"
    # CAST(:resume_at AS timestamptz) with a value bound: the stored instant is exactly what was passed.
    assert deferred["next_attempt_at"] == DEFERRAL_RESUME_AT
    # A deferral raises the ceiling instead of spending the budget, so max_attempts drifts up by one.
    assert deferred["max_attempts"] == 6
    assert deferred["attempt_count"] == 1
    assert deferred["lease_owner"] is None

    attempts = await _attempts(session, claim.work_item_id)
    assert [row["status"] for row in attempts] == ["deferred"]
    assert attempts[0]["metrics"] == {"deferral_reason": "upstream not published"}

    second = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert second is not None
    assert second.shard_key == "low-priority-shard"
    await _commit(session)

    assert await defer_work_item(session, second, resume_at=None, reason="waiting for the next tick") is True
    await _commit(session)

    parked = await _work_item(session, second.work_item_id)
    # CAST(:resume_at AS timestamptz) with NULL bound: COALESCE falls through to the same now() that
    # stamped heartbeat_at in this one statement, so the two columns are equal to the microsecond.
    assert parked["next_attempt_at"] == parked["heartbeat_at"]
    assert parked["status"] == "deferred"


async def test_refresh_job_run_rollup_produces_counters_that_satisfy_work_item_counts_within_total(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    definition, opened = await _open_definition_and_run(
        session, ledger, _shards("firms:2003-06-01", "firms:2003-06-06", "firms:2003-06-11"), max_attempts=1
    )
    assert opened.total_work_items == 3

    first = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert first is not None
    await _commit(session)
    assert await complete_work_item(session, first) is True
    await _commit(session)

    second = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert second is not None
    # max_attempts is 1, so the very first failure is already the final one.
    assert second.is_final_attempt is True
    await _commit(session)
    failure = await fail_work_item(
        session,
        second,
        failure_class="ConnectError",
        reason="upstream refused the connection",
        retry_policy=definition.retry_policy,
    )
    assert failure is not None
    assert failure.is_dead_letter is True
    await _commit(session)

    partial = await refresh_job_run_rollup(session, opened.job_run_id)
    await _commit(session)
    assert partial.total_work_items == 3
    assert partial.succeeded_work_items == 1
    assert partial.failed_work_items == 1
    assert partial.succeeded_work_items + partial.failed_work_items <= partial.total_work_items
    assert partial.status == "running"
    assert (await _run(session, opened.job_run_id))["completed_at"] is None

    third = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert third is not None
    await _commit(session)
    assert await complete_work_item(session, third) is True
    await _commit(session)

    final = await refresh_job_run_rollup(session, opened.job_run_id)
    await _commit(session)
    assert final.total_work_items == 3
    assert final.succeeded_work_items == 2
    assert final.failed_work_items == 1
    # A run with a dead-lettered shard is 'partial': a real, reportable outcome, not a success.
    assert final.status == "partial"
    assert (await _run(session, opened.job_run_id))["completed_at"] is not None


async def test_the_run_counter_clamp_survives_a_total_that_is_behind_the_work_items_it_counts(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    _, opened = await _open_definition_and_run(session, ledger, _shards("firms:2003-07-01"))

    # A stale total is what a slice that died before its closing rollup leaves behind. The incremental
    # bump in complete_work_item must not abort the completion of a shard that genuinely succeeded.
    await fetch_row(session, _SET_RUN_TOTAL, {"job_run_id": opened.job_run_id, "total_work_items": 0})
    await _commit(session)

    claim = await claim_work_item(
        session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
    )
    assert claim is not None
    await _commit(session)
    assert await complete_work_item(session, claim) is True
    await _commit(session)

    clamped = await _run(session, opened.job_run_id)
    assert clamped["total_work_items"] == 0
    # GREATEST(0, LEAST(0 + 1, 0 - 0)) is 0, so ck_job_run_work_item_counts_within_total held.
    assert clamped["succeeded_work_items"] == 0
    assert (await _work_item(session, claim.work_item_id))["status"] == "succeeded"

    repaired = await refresh_job_run_rollup(session, opened.job_run_id)
    await _commit(session)
    assert repaired.total_work_items == 1
    assert repaired.succeeded_work_items == 1
    assert repaired.status == "succeeded"


async def test_the_reaper_requeues_one_expired_lease_dead_letters_an_exhausted_one_and_closes_both_attempts(
    ledger: Ledger,
) -> None:
    session = await ledger.open_session()
    definition, opened = await _open_definition_and_run(
        session, ledger, _shards("firms:2003-08-01", "firms:2003-08-06"), max_attempts=2
    )

    claims = []
    for _ in range(2):
        claim = await claim_work_item(
            session, job_run_id=opened.job_run_id, worker_id=WORKER_A, lease_seconds=LEASE_SECONDS
        )
        assert claim is not None
        claims.append(claim)
        await _commit(session)

    retryable, exhausted = claims
    # The second shard has already spent its budget: attempt_count is 1 and its ceiling is now 1 too.
    await fetch_row(session, _SET_ITEM_MAX_ATTEMPTS, {"work_item_id": exhausted.work_item_id, "max_attempts": 1})
    for claim in claims:
        await fetch_row(session, _EXPIRE_LEASE, {"work_item_id": claim.work_item_id})
    await _commit(session)

    summary = await reclaim_expired_leases(
        session,
        job_run_id=opened.job_run_id,
        backoff_seconds=definition.retry_policy.backoff_seconds(1),
    )
    await _commit(session)
    assert summary.total == 2
    assert summary.requeued == 1
    assert summary.dead_lettered == 1
    # close_lost_attempts binds a Python list of uuid strings into CAST(:work_item_ids AS uuid[]).
    assert summary.attempts_closed == 2
    assert set(summary.work_item_ids) == {retryable.work_item_id, exhausted.work_item_id}

    requeued = await _work_item(session, retryable.work_item_id)
    assert requeued["status"] == "retry_wait"
    assert requeued["next_attempt_at"] is not None
    assert requeued["lease_owner"] is None
    assert requeued["last_error_class"] == "lease_expired"

    dead = await _work_item(session, exhausted.work_item_id)
    assert dead["status"] == "dead_letter"
    assert dead["completed_at"] is not None
    assert dead["next_attempt_at"] is None

    for claim in claims:
        attempts = await _attempts(session, claim.work_item_id)
        assert [row["status"] for row in attempts] == ["lost"]
        assert attempts[0]["failure_class"] == "lease_expired"

    run = await _run(session, opened.job_run_id)
    assert run["failed_work_items"] == 1
