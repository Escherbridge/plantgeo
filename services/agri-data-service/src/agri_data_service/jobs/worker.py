"""The bounded run loop a one-shot cron container invokes, plus the idempotent definition and run openers."""

from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import structlog
from sqlalchemy import text

from agri_data_service.jobs.lease import (
    apply_statement_timeout,
    canonical_json,
    claim_work_item,
    complete_work_item,
    defer_work_item,
    extend_lease,
    fail_work_item,
    failure_summary,
    fetch_row,
    fetch_rows,
    mark_work_item_running,
    optional_column,
    reclaim_expired_leases,
    record_checkpoint,
    release_lost_attempt,
    required_column,
)
from agri_data_service.jobs.registry import (
    EMPTY_JSON_OBJECT,
    JOB_HANDLERS,
    JobInvocation,
    JobSpecificationError,
    RetryPolicy,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.jobs.lease import ClaimedWorkItem
    from agri_data_service.jobs.registry import (
        JobDefinitionSpec,
        JobHandler,
        JobHandlerOutcome,
        JobHandlerRegistry,
        JobWorkItemSpec,
    )

# Operational telemetry is bound to stderr, never stdout, for the same reason ingest/commands.py binds
# it there: a cron container's stdout is a JSON-lines summary stream and nothing else, and structlog's
# default factory sinks to stdout, which would corrupt every line the cron log parser reads.
logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

BUDGET_YIELD_REASON: Final = "worker time budget exhausted mid-shard"

SliceStopReason = Literal["no_open_run", "no_claimable_work", "time_budget_exhausted"]

ItemLanding = Literal["succeeded", "retried", "dead_lettered", "deferred", "yielded", "abandoned"]


class JobDefinitionNotFoundError(LookupError):
    """Raised when a slice is asked for a definition name that no enabled ledger row matches."""


class JobRunError(RuntimeError):
    """Raised when a run row the runtime just wrote cannot be read back, so the ledger contradicts itself."""


_UPSERT_JOB_DEFINITION: Final = text("""
-- upsert_job_definition
INSERT INTO agri.job_definition (
    name, version, handler, queue_name, schedule, schedule_timezone, enabled,
    concurrency_key, max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
)
VALUES (
    :name, :version, :handler, :queue_name, :schedule, :schedule_timezone, :enabled,
    :concurrency_key, :max_attempts, :lease_seconds, :time_budget_seconds,
    CAST(:retry_policy AS jsonb), CAST(:parameters AS jsonb)
)
ON CONFLICT (name, version) DO UPDATE
SET handler = EXCLUDED.handler,
    queue_name = EXCLUDED.queue_name,
    schedule = EXCLUDED.schedule,
    schedule_timezone = EXCLUDED.schedule_timezone,
    enabled = EXCLUDED.enabled,
    concurrency_key = EXCLUDED.concurrency_key,
    max_attempts = EXCLUDED.max_attempts,
    lease_seconds = EXCLUDED.lease_seconds,
    time_budget_seconds = EXCLUDED.time_budget_seconds,
    retry_policy = EXCLUDED.retry_policy,
    parameters = EXCLUDED.parameters,
    updated_at = now()
RETURNING id, name, version, handler, queue_name, concurrency_key,
          max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
""")

# `ORDER BY version DESC` picks the newest version of a name when the caller pinned none. `version` is
# free-text VARCHAR(100), so this is a string sort -- a lane that wants predictable ordering versions
# with a sortable scheme (a date, or zero-padded numbers), because "10" sorts before "2".
_LOAD_JOB_DEFINITION: Final = text("""
-- load_job_definition
SELECT id, name, version, handler, queue_name, concurrency_key,
       max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
FROM agri.job_definition
WHERE name = :name
  AND enabled
  AND (CAST(:version AS text) IS NULL OR version = CAST(:version AS text))
ORDER BY version DESC
LIMIT 1
""")

# `logical_run_key` is UNIQUE and is the run-level idempotency key. DO NOTHING plus a follow-up SELECT
# is the only shape that stays correct when two schedulers race -- the loser gets zero rows back and
# reads the winner's row instead of minting a duplicate run.
_INSERT_JOB_RUN: Final = text("""
-- insert_job_run
INSERT INTO agri.job_run (
    job_definition_id, logical_run_key, scheduled_for, status, requested_by, target_partitions
)
VALUES (
    :job_definition_id,
    :logical_run_key,
    COALESCE(CAST(:scheduled_for AS timestamptz), now()),
    'queued',
    :requested_by,
    CAST(:target_partitions AS jsonb)
)
ON CONFLICT (logical_run_key) DO NOTHING
RETURNING id
""")

_SELECT_JOB_RUN: Final = text("""
-- select_job_run
SELECT id, status, total_work_items FROM agri.job_run WHERE logical_run_key = :logical_run_key
""")

# One statement for any number of shards. A per-shard INSERT would be one round trip each against the
# Railway proxy, and the ingest session opens exactly one connection, so a thousand-shard fan-out
# would be a thousand serial round trips. `ON CONFLICT (job_run_id, shard_key) DO NOTHING` is what
# makes re-planning a run a no-op instead of a duplicate fan-out.
#
# `next_attempt_at` is seeded to `available_at` even though 'queued' does not require it, so that every
# row carries the same claim-eligibility shape the 'retry_wait' and 'deferred' rows are forced into.
# This is a convenience, NOT a correctness requirement: _CLAIM_WORK_ITEM spells the NULL case out as
# `(item.next_attempt_at IS NULL OR item.next_attempt_at <= now())`, so an unseeded row is claimable
# either way. It matters only to a query written without that disjunction -- an operator's ad-hoc
# completeness check, or a future claim variant -- where a NULL would make the comparison UNKNOWN and
# hide the row. Nothing here rides on the index: ix_job_work_item_claim is
# (status, next_attempt_at, available_at, priority), which leads with neither job_run_id (the claim
# filters on it) nor priority DESC (the claim sorts by it), so the claim is a filter-and-sort at any
# seeding. Harmless at this scale; do not quote it as load-bearing.
_INSERT_JOB_WORK_ITEMS: Final = text("""
-- insert_job_work_items
INSERT INTO agri.job_work_item (
    job_run_id, shard_key, kind, payload, priority, available_at, next_attempt_at, max_attempts
)
SELECT :job_run_id,
       item.shard_key,
       item.kind,
       CAST(item.payload AS jsonb),
       item.priority,
       COALESCE(item.available_at, now()),
       COALESCE(item.available_at, now()),
       :max_attempts
FROM jsonb_to_recordset(CAST(:items AS jsonb))
     AS item(shard_key text, kind text, payload text, priority integer, available_at timestamptz)
ON CONFLICT (job_run_id, shard_key) DO NOTHING
RETURNING id
""")

_SELECT_OPEN_JOB_RUN: Final = text("""
-- select_open_job_run
SELECT id
FROM agri.job_run
WHERE job_definition_id = :job_definition_id AND status IN ('queued', 'running')
ORDER BY scheduled_for, created_at
LIMIT 1
""")

# Every counter is assigned an absolute value recomputed from the work items themselves, in ONE
# statement, so ck_job_run_work_item_counts_within_total never sees a half-updated triple -- these
# constraints are IMMEDIATE and abort the statement, not the commit. The terminal status and
# `completed_at` move together for ck_job_run_terminal_run_has_completion_time. Nothing in the database
# maintains these counters; this statement is the only thing that makes them true.
_REFRESH_JOB_RUN_ROLLUP: Final = text("""
-- refresh_job_run_rollup
UPDATE agri.job_run AS run
SET total_work_items = tally.total,
    succeeded_work_items = tally.succeeded,
    failed_work_items = tally.failed,
    started_at = COALESCE(run.started_at, now()),
    status = CASE
        WHEN tally.total = 0 THEN run.status
        WHEN tally.succeeded + tally.failed < tally.total THEN 'running'
        WHEN tally.failed = 0 THEN 'succeeded'
        WHEN tally.succeeded = 0 THEN 'failed'
        ELSE 'partial'
    END,
    completed_at = CASE
        WHEN tally.total > 0 AND tally.succeeded + tally.failed = tally.total
            THEN COALESCE(run.completed_at, now())
        ELSE run.completed_at
    END
FROM (
    SELECT count(*) AS total,
           count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
           count(*) FILTER (WHERE status IN ('dead_letter', 'cancelled')) AS failed
    FROM agri.job_work_item
    WHERE job_run_id = :job_run_id
) AS tally
WHERE run.id = :job_run_id
RETURNING run.status, run.total_work_items, run.succeeded_work_items, run.failed_work_items
""")


@dataclass(frozen=True, slots=True)
class JobDefinitionRecord:
    """One enabled `agri.job_definition` row, with its retry policy already parsed out of JSONB."""

    id: uuid.UUID
    name: str
    version: str
    handler: str
    queue_name: str
    concurrency_key: str | None
    max_attempts: int
    lease_seconds: int
    time_budget_seconds: int
    retry_policy: RetryPolicy
    parameters: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class JobRunRollup:
    """A run's counters recomputed from its work items: the authority the incremental bumps approximate."""

    status: str
    total_work_items: int
    succeeded_work_items: int
    failed_work_items: int


@dataclass(frozen=True, slots=True)
class OpenedJobRun:
    """The outcome of opening a run: which run it is, whether this call created it, and what it now holds."""

    job_run_id: uuid.UUID
    logical_run_key: str
    created: bool
    added_work_items: int
    total_work_items: int
    status: str


@dataclass(frozen=True, slots=True)
class JobSliceSummary:
    """One cron tick's durable work: what it claimed, how each shard landed, and why the tick stopped."""

    definition_name: str
    worker_id: str
    job_run_id: uuid.UUID | None
    stop_reason: SliceStopReason
    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    dead_lettered: int = 0
    deferred: int = 0
    yielded: int = 0
    abandoned: int = 0
    reclaimed: int = 0
    elapsed_seconds: float = 0.0
    run_status: str | None = None

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object a cron container echoes as one line, per ingest/results.py."""
        return {
            "definition": self.definition_name,
            "worker_id": self.worker_id,
            "job_run_id": None if self.job_run_id is None else str(self.job_run_id),
            "stop_reason": self.stop_reason,
            "claimed": self.claimed,
            "succeeded": self.succeeded,
            "retried": self.retried,
            "dead_lettered": self.dead_lettered,
            "deferred": self.deferred,
            "yielded": self.yielded,
            "abandoned": self.abandoned,
            "reclaimed": self.reclaimed,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "run_status": self.run_status,
        }


class _SliceTally:
    """Mutable per-slice counters, kept out of the frozen summary the caller finally receives."""

    def __init__(self) -> None:
        """Start every counter at zero."""
        self.claimed = 0
        self.reclaimed = 0
        self._landings: dict[str, int] = {}

    def record(self, landing: ItemLanding) -> None:
        """Fold one shard's landing into the tally."""
        self._landings[landing] = self._landings.get(landing, 0) + 1

    def count(self, landing: ItemLanding) -> int:
        """How many shards landed this way in this slice."""
        return self._landings.get(landing, 0)


class _LeaseGuard:
    """The heartbeat a handler is handed: it extends the lease, and it remembers when the fence was lost."""

    def __init__(self, session: AsyncSession, claim: ClaimedWorkItem, lease_seconds: int) -> None:
        """Bind the guard to one claim; the fence is held until an extension comes back empty."""
        self._session = session
        self._claim = claim
        self._lease_seconds = lease_seconds
        self.fence_held = True

    async def heartbeat(self) -> bool:
        """Extend the lease and commit it; False means another worker owns this shard and work must stop."""
        if not self.fence_held:
            return False
        # Re-pin the statement timeout before touching the ledger. A handler shares this session and
        # commits it (ingest/writer.py commits per batch), and SET LOCAL dies with its transaction, so
        # by the time a handler calls back in the timeout the runtime armed is usually already gone.
        await apply_statement_timeout(self._session)
        held = await extend_lease(self._session, self._claim, lease_seconds=self._lease_seconds)
        if held:
            # An uncommitted heartbeat is invisible to every other worker, so it cannot hold off the
            # reaper. A handler sharing this session must therefore treat a heartbeat as a commit.
            await _commit(self._session)
            return True
        await _reset(self._session)
        self.fence_held = False
        return False


async def _commit(session: AsyncSession) -> None:
    """Commit, then re-arm the transaction-local statement timeout the next transaction needs."""
    await session.commit()
    await apply_statement_timeout(session)


async def _reset(session: AsyncSession) -> None:
    """Discard an aborted or refused transaction and re-arm the statement timeout."""
    await session.rollback()
    await apply_statement_timeout(session)


def _definition_record(row: Mapping[str, object]) -> JobDefinitionRecord:
    """Read one job_definition row into its typed record, validating every column's shape."""
    return JobDefinitionRecord(
        id=required_column(row, "id", uuid.UUID),
        name=required_column(row, "name", str),
        version=required_column(row, "version", str),
        handler=required_column(row, "handler", str),
        queue_name=required_column(row, "queue_name", str),
        concurrency_key=optional_column(row, "concurrency_key", str),
        max_attempts=required_column(row, "max_attempts", int),
        lease_seconds=required_column(row, "lease_seconds", int),
        time_budget_seconds=required_column(row, "time_budget_seconds", int),
        retry_policy=RetryPolicy.from_json(row.get("retry_policy")),
        parameters=required_column(row, "parameters", dict),
    )


async def ensure_job_definition(session: AsyncSession, spec: JobDefinitionSpec) -> JobDefinitionRecord:
    """Upsert one declared lane by (name, version) and return the row the runtime will execute against."""
    row = await fetch_row(
        session,
        _UPSERT_JOB_DEFINITION,
        {
            "name": spec.name,
            "version": spec.version,
            "handler": spec.handler,
            "queue_name": spec.queue_name,
            "schedule": spec.schedule,
            "schedule_timezone": spec.schedule_timezone,
            "enabled": spec.enabled,
            "concurrency_key": spec.concurrency_key,
            "max_attempts": spec.max_attempts,
            "lease_seconds": spec.lease_seconds,
            "time_budget_seconds": spec.time_budget_seconds,
            "retry_policy": canonical_json(spec.retry_policy.to_json()),
            "parameters": canonical_json(spec.parameters),
        },
    )
    if row is None:
        raise JobRunError(f"upserting job definition {spec.name!r} returned no row")
    return _definition_record(row)


async def load_job_definition(
    session: AsyncSession,
    name: str,
    *,
    version: str | None = None,
) -> JobDefinitionRecord:
    """Load the enabled definition a slice will run, refusing by name rather than quietly doing nothing."""
    row = await fetch_row(session, _LOAD_JOB_DEFINITION, {"name": name, "version": version})
    if row is None:
        pinned = "" if version is None else f" at version {version!r}"
        raise JobDefinitionNotFoundError(f"no enabled job definition named {name!r}{pinned}")
    return _definition_record(row)


def _work_items_json(work_items: Sequence[JobWorkItemSpec]) -> str:
    """Render the fan-out as the single JSON array `jsonb_to_recordset` expands back into rows."""
    rendered = [
        {
            "shard_key": item.shard_key,
            "kind": item.kind,
            "payload": canonical_json(item.payload),
            "priority": item.priority,
            "available_at": None if item.available_at is None else item.available_at.isoformat(),
        }
        for item in work_items
    ]
    return json.dumps(rendered, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def refresh_job_run_rollup(session: AsyncSession, job_run_id: uuid.UUID) -> JobRunRollup:
    """Recompute a run's counters and status from its work items, in the one statement the CHECKs allow."""
    row = await fetch_row(session, _REFRESH_JOB_RUN_ROLLUP, {"job_run_id": job_run_id})
    if row is None:
        raise JobRunError(f"job run {job_run_id} disappeared before its rollup")
    return JobRunRollup(
        status=required_column(row, "status", str),
        total_work_items=required_column(row, "total_work_items", int),
        succeeded_work_items=required_column(row, "succeeded_work_items", int),
        failed_work_items=required_column(row, "failed_work_items", int),
    )


async def open_job_run(  # noqa: PLR0913 - one parameter per column the run row pins at creation
    session: AsyncSession,
    definition: JobDefinitionRecord,
    *,
    logical_run_key: str,
    work_items: Sequence[JobWorkItemSpec],
    scheduled_for: datetime | None = None,
    requested_by: str | None = None,
    target_partitions: Mapping[str, object] = EMPTY_JSON_OBJECT,
) -> OpenedJobRun:
    """Open (or re-open) one logical run and fan its shards out, idempotently on both unique keys."""
    inserted = await fetch_row(
        session,
        _INSERT_JOB_RUN,
        {
            "job_definition_id": definition.id,
            "logical_run_key": logical_run_key,
            "scheduled_for": scheduled_for,
            "requested_by": requested_by,
            "target_partitions": canonical_json(target_partitions),
        },
    )
    if inserted is not None:
        job_run_id = required_column(inserted, "id", uuid.UUID)
    else:
        existing = await fetch_row(session, _SELECT_JOB_RUN, {"logical_run_key": logical_run_key})
        if existing is None:
            raise JobRunError(f"logical run {logical_run_key!r} was neither inserted nor readable")
        job_run_id = required_column(existing, "id", uuid.UUID)
    added = 0
    if work_items:
        added = len(
            await fetch_rows(
                session,
                _INSERT_JOB_WORK_ITEMS,
                {
                    "job_run_id": job_run_id,
                    "max_attempts": definition.max_attempts,
                    "items": _work_items_json(work_items),
                },
            )
        )
    rollup = await refresh_job_run_rollup(session, job_run_id)
    return OpenedJobRun(
        job_run_id=job_run_id,
        logical_run_key=logical_run_key,
        created=inserted is not None,
        added_work_items=added,
        total_work_items=rollup.total_work_items,
        status=rollup.status,
    )


def _require_budget_within_lease(budget: float, definition: JobDefinitionRecord) -> None:
    """Refuse a slice budget the lease cannot outlive, which is what makes `seconds_remaining` trustworthy."""
    if budget < 0:
        raise JobSpecificationError("budget_seconds must not be negative")
    if budget >= definition.lease_seconds:
        # JobDefinitionSpec already refuses lease_seconds <= time_budget_seconds, but `budget_seconds`
        # overrides that value at call time and bypasses the spec entirely. Without this the worker's
        # own lease expires while it is still working, another worker claims behind it, and every
        # checkpoint from here on is refused by the fence -- a self-inflicted fence loss per slice. It
        # is also the invariant that lets a handler treat seconds_remaining as its only clock.
        raise JobSpecificationError(
            f"budget_seconds must be under the definition's lease_seconds ({definition.lease_seconds})"
        )


async def _select_open_job_run(session: AsyncSession, job_definition_id: uuid.UUID) -> uuid.UUID | None:
    """Pick the oldest run of this definition that is still open, or None when there is nothing to drive."""
    row = await fetch_row(session, _SELECT_OPEN_JOB_RUN, {"job_definition_id": job_definition_id})
    return None if row is None else required_column(row, "id", uuid.UUID)


async def _abandon(session: AsyncSession, claim: ClaimedWorkItem) -> ItemLanding:
    """Give the shard up without marking it failed: another worker owns it, and its work is theirs now."""
    await _reset(session)
    return await _release_abandoned(session, claim)


async def _release_abandoned(session: AsyncSession, claim: ClaimedWorkItem) -> ItemLanding:
    """Close this worker's own attempt as lost, on a session a caller has already rolled back."""
    await release_lost_attempt(session, claim)
    await _commit(session)
    logger.warning("job_work_item_fence_lost", shard_key=claim.shard_key, fencing_token=claim.fencing_token)
    return "abandoned"


async def _record_failure(  # noqa: PLR0913 - the claim, its policy, its metrics and both failure fields
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    *,
    failure_class: str,
    reason: str,
    metrics: Mapping[str, object],
) -> ItemLanding:
    """Land a failure as a scheduled retry or, once the attempt budget is spent, as a dead letter."""
    failure = await fail_work_item(
        session,
        claim,
        failure_class=failure_class,
        reason=reason,
        retry_policy=definition.retry_policy,
        metrics=metrics,
    )
    if failure is None:
        return await _abandon(session, claim)
    await _commit(session)
    logger.warning(
        "job_work_item_failed",
        shard_key=claim.shard_key,
        disposition=failure.disposition,
        attempt_number=failure.attempt_number,
        max_attempts=failure.max_attempts,
        failure_class=failure.failure_class,
    )
    return "dead_lettered" if failure.is_dead_letter else "retried"


async def _apply_progress(
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    outcome: JobHandlerOutcome,
) -> ItemLanding | None:
    """Checkpoint a bounded step forward; None keeps the shard so the drive loop can call the handler again."""
    cursor = outcome.cursor
    if cursor is None:  # pragma: no cover - JobHandlerOutcome.__post_init__ already refuses this
        raise JobSpecificationError("a progressed outcome reached the runtime without a cursor")
    record = await record_checkpoint(
        session,
        claim,
        cursor=cursor,
        progress_fraction=outcome.progress_fraction,
        lease_seconds=definition.lease_seconds,
    )
    if record is None:
        return await _abandon(session, claim)
    await _commit(session)
    return None


async def _apply_completion(
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    outcome: JobHandlerOutcome,
    metrics: Mapping[str, object],
) -> ItemLanding:
    """Checkpoint any final cursor, then close the shard, the attempt and the run counter."""
    if outcome.cursor is not None:
        final = await record_checkpoint(
            session,
            claim,
            cursor=outcome.cursor,
            progress_fraction=outcome.progress_fraction,
            lease_seconds=definition.lease_seconds,
        )
        if final is None:
            return await _abandon(session, claim)
    if not await complete_work_item(session, claim, metrics=metrics):
        return await _abandon(session, claim)
    await _commit(session)
    return "succeeded"


async def _apply_park(  # noqa: PLR0913 - a park needs the claim, the outcome, the lease and the landing it reports
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    outcome: JobHandlerOutcome,
    metrics: Mapping[str, object],
    landing: ItemLanding,
) -> ItemLanding:
    """Checkpoint any cursor the park carries, then park the shard without spending the retry budget."""
    if outcome.cursor is not None:
        # A park's cursor is checkpointed exactly as a completion's is. Without this, a lane that walked
        # four of five chunks and then hit an upstream "come back at 06:00" throws those four away and
        # re-walks them on every deferral, forever -- and the outcome constructors accept a cursor, so a
        # handler that supplies one is entitled to assume it landed.
        parked = await record_checkpoint(
            session,
            claim,
            cursor=outcome.cursor,
            progress_fraction=outcome.progress_fraction,
            lease_seconds=definition.lease_seconds,
        )
        if parked is None:
            return await _abandon(session, claim)
    if not await defer_work_item(
        session,
        claim,
        resume_at=outcome.resume_at,
        reason=outcome.reason or "",
        metrics=metrics,
    ):
        return await _abandon(session, claim)
    await _commit(session)
    return landing


async def _apply_outcome(
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    outcome: JobHandlerOutcome,
    metrics: Mapping[str, object],
) -> ItemLanding | None:
    """Persist one handler outcome; None means "progressed, keep this shard" and the drive loop continues."""
    if outcome.kind == "progressed":
        return await _apply_progress(session, definition, claim, outcome)
    if outcome.kind == "completed":
        return await _apply_completion(session, definition, claim, outcome, metrics)
    if outcome.kind in {"deferred", "yielded"}:
        # Both park on the same primitive, which raises max_attempts rather than spending it, because
        # neither waiting on upstream nor running out of clock is a failure. They are counted apart so
        # an operator can tell "upstream had nothing" from "we ran out of tick".
        landing: ItemLanding = "deferred" if outcome.kind == "deferred" else "yielded"
        return await _apply_park(session, definition, claim, outcome, metrics, landing)
    return await _record_failure(
        session,
        definition,
        claim,
        failure_class=outcome.failure_class or "handler_failure",
        reason=outcome.reason or "",
        metrics=metrics,
    )


async def _drive_work_item(  # noqa: PLR0913 - the handler, its policy, its claim and the budget are all needed
    session: AsyncSession,
    definition: JobDefinitionRecord,
    handler: JobHandler,
    claim: ClaimedWorkItem,
    deadline: float,
    monotonic: Callable[[], float],
) -> ItemLanding:
    """Run one claimed shard to a landing: completed, failed, deferred, budget-yielded, or fenced out."""
    guard = _LeaseGuard(session, claim, definition.lease_seconds)
    if not await mark_work_item_running(session, claim, lease_seconds=definition.lease_seconds):
        return await _abandon(session, claim)
    await _commit(session)
    cursor = claim.cursor
    progress = claim.progress_fraction
    # Metrics accumulate across this shard's handler calls and are written when the attempt closes, so a
    # step that only progressed still has its counters land. Merging is key-wise with the newest call
    # winning, which is why a handler reports cumulative figures rather than per-step deltas.
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT
    while True:
        if monotonic() >= deadline:
            # The budget is the durability primitive: park the shard so the NEXT tick resumes it from
            # its checkpoint, rather than leaving the lease to rot until the reaper notices.
            if not await defer_work_item(
                session,
                claim,
                resume_at=None,
                reason=BUDGET_YIELD_REASON,
                metrics=metrics,
            ):
                return await _abandon(session, claim)
            await _commit(session)
            return "yielded"
        invocation = JobInvocation(
            shard_key=claim.shard_key,
            kind=claim.kind,
            payload=claim.payload,
            cursor=cursor,
            parameters=definition.parameters,
            attempt_number=claim.attempt_number,
            max_attempts=claim.max_attempts,
            progress_fraction=progress,
            seconds_remaining=max(deadline - monotonic(), 0.0),
            heartbeat=guard.heartbeat,
        )
        step = await _invoke_handler(session, definition, handler, claim, invocation, guard, metrics)
        if step.landing is not None:
            return step.landing
        cursor = step.cursor
        progress = step.progress_fraction
        metrics = step.metrics


@dataclass(frozen=True, slots=True)
class _HandlerStep:
    """One handler call's result: how the shard landed, or where the next call into it resumes from."""

    landing: ItemLanding | None
    cursor: Mapping[str, object] | None = None
    progress_fraction: float = 0.0
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT


async def _fail_after_error(  # noqa: PLR0913 - the claim, its definition, its guard, its metrics and the error
    session: AsyncSession,
    definition: JobDefinitionRecord,
    claim: ClaimedWorkItem,
    guard: _LeaseGuard,
    error: BaseException,
    metrics: Mapping[str, object],
) -> ItemLanding:
    """Land a raised step as this shard's failure, on a session the exception may have left aborted."""
    # _reset FIRST, unconditionally, exactly as _abandon opens. The handler shares this session, and a
    # connection reset, a lock timeout or the statement timeout leaves it in InFailedSQLTransaction --
    # in which every subsequent statement raises. Recording the failure on an aborted transaction would
    # therefore raise again, the second exception would escape run_job_slice, the container would die,
    # and the shard would be stranded 'running' behind a live lease no reaper may touch until it
    # expires. Rolling back also discards whatever the handler wrote and never committed, which is
    # right: no checkpoint recorded it, so the next claim re-walks that work anyway.
    await _reset(session)
    if not guard.fence_held:
        return await _release_abandoned(session, claim)
    return await _record_failure(
        session,
        definition,
        claim,
        failure_class=type(error).__name__,
        reason=failure_summary(error),
        metrics=metrics,
    )


async def _invoke_handler(  # noqa: PLR0913 - one parameter per collaborator this single step needs
    session: AsyncSession,
    definition: JobDefinitionRecord,
    handler: JobHandler,
    claim: ClaimedWorkItem,
    invocation: JobInvocation,
    guard: _LeaseGuard,
    metrics: Mapping[str, object],
) -> _HandlerStep:
    """Call the handler once and persist what it decided; a landing of None means the shard keeps going."""
    # The try covers persisting the outcome as well as producing it. Both run against the session the
    # handler just used, so both can inherit an aborted transaction, and only one `except` in this
    # module stands between that and a dead container. A handler's failure is this shard's failure,
    # never the slice's: the other shards in the run must still get their turn, exactly as
    # ingest/results.py::run_isolated_job isolates one source from the other five.
    try:
        outcome = await handler(invocation)
        # Re-pin the statement timeout at the one place control comes back from handler code. SET LOCAL
        # dies with its transaction and ingest/writer.py commits this session per batch, so the ledger
        # writes below would otherwise run under the server default from the first written batch on.
        await apply_statement_timeout(session)
        if not guard.fence_held:
            return _HandlerStep(landing=await _abandon(session, claim))
        merged: Mapping[str, object] = {**metrics, **outcome.metrics}
        applied = await _apply_outcome(session, definition, claim, outcome, merged)
    except Exception as error:
        return _HandlerStep(landing=await _fail_after_error(session, definition, claim, guard, error, metrics))
    return _HandlerStep(
        landing=applied,
        cursor=outcome.cursor,
        progress_fraction=outcome.progress_fraction,
        metrics=merged,
    )


async def run_job_slice(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single cron tick
    session: AsyncSession,
    *,
    definition_name: str,
    worker_id: str,
    budget_seconds: float | None = None,
    version: str | None = None,
    job_run_id: uuid.UUID | None = None,
    registry: JobHandlerRegistry = JOB_HANDLERS,
    reclaim_expired: bool = True,
    monotonic: Callable[[], float] = time.monotonic,
) -> JobSliceSummary:
    """Claim and drive shards until the work or the time budget runs out, then report what this tick did.

    This is the whole durability model for a one-shot Railway cron container: do as much as fits inside
    `budget_seconds`, checkpoint every step, park whatever did not finish, exit 0. The next tick claims
    the same shards straight out of the ledger and resumes each from its last cursor. Nothing is kept
    on the container's filesystem, so a killed container loses only the work since its last checkpoint.
    """
    started = monotonic()
    await apply_statement_timeout(session)
    definition = await load_job_definition(session, definition_name, version=version)
    handler = registry.handler_for(definition.handler)
    budget = definition.time_budget_seconds if budget_seconds is None else budget_seconds
    _require_budget_within_lease(budget, definition)
    deadline = started + budget
    run_id = job_run_id if job_run_id is not None else await _select_open_job_run(session, definition.id)
    if run_id is None:
        await session.rollback()
        return JobSliceSummary(
            definition_name=definition.name,
            worker_id=worker_id,
            job_run_id=None,
            stop_reason="no_open_run",
            elapsed_seconds=monotonic() - started,
        )
    tally = _SliceTally()
    if reclaim_expired:
        reclaimed = await reclaim_expired_leases(
            session,
            job_run_id=run_id,
            backoff_seconds=definition.retry_policy.backoff_seconds(1),
        )
        tally.reclaimed = reclaimed.total
        await _commit(session)
    stop_reason: SliceStopReason = "time_budget_exhausted"
    while monotonic() < deadline:
        claim = await claim_work_item(
            session,
            job_run_id=run_id,
            worker_id=worker_id,
            lease_seconds=definition.lease_seconds,
        )
        if claim is None:
            await _reset(session)
            stop_reason = "no_claimable_work"
            break
        await _commit(session)
        tally.claimed += 1
        landing = await _drive_work_item(session, definition, handler, claim, deadline, monotonic)
        tally.record(landing)
        if landing == "yielded":
            # Either the loop's own deadline check parked the shard -- in which case the `while` is
            # already false -- or the handler said it had no clock for its next unit of work. Claiming
            # another shard after that only buys another yield, so stop and let the next tick resume.
            stop_reason = "time_budget_exhausted"
            break
    rollup = await refresh_job_run_rollup(session, run_id)
    await _commit(session)
    return JobSliceSummary(
        definition_name=definition.name,
        worker_id=worker_id,
        job_run_id=run_id,
        stop_reason=stop_reason,
        claimed=tally.claimed,
        succeeded=tally.count("succeeded"),
        retried=tally.count("retried"),
        dead_lettered=tally.count("dead_lettered"),
        deferred=tally.count("deferred"),
        yielded=tally.count("yielded"),
        abandoned=tally.count("abandoned"),
        reclaimed=tally.reclaimed,
        elapsed_seconds=monotonic() - started,
        run_status=rollup.status,
    )
