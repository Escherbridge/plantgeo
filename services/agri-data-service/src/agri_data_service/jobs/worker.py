"""The bounded run loop a one-shot cron container invokes, plus the idempotent definition and run openers."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import structlog
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
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
    find_missing_relations,
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
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
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

SHUTDOWN_RELEASE_REASON: Final = "the container was signalled to stop mid-shard"
CANCELLED_RELEASE_REASON: Final = "the slice task was cancelled mid-shard"

# The signals a one-shot cron container is actually stopped with. SIGTERM is what Railway sends before it
# kills a container -- on a redeploy, on an eviction, on a manual restart -- and SIGINT is the same event
# at a developer's terminal. Neither has a Python-level handler by default, so the process simply ended
# and left its shard 'running' behind a lease no living process owned. See jobs/AGENTS.md
# "Shutdown and heartbeat semantics".
_STOP_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT)

# `job_event.environment` and `job_event.service` are both NOT NULL and nothing else in this service names
# either. Railway sets the environment name per container; off Railway the honest answer is "local", not a
# guess. Both columns are VARCHAR(100), and an over-long value aborts the INSERT rather than truncating.
JOB_EVENT_SERVICE: Final = "agri-data-service"
JOB_EVENT_ENVIRONMENT_VARIABLE: Final = "RAILWAY_ENVIRONMENT_NAME"
DEFAULT_JOB_EVENT_ENVIRONMENT: Final = "local"
JOB_EVENT_TEXT_MAX_LENGTH: Final = 100

# One event code, written once per tick. `max(occurred_at) WHERE event_code = 'slice_finished'` is then a
# true per-lane heartbeat, which is the single fact the ledger could not previously produce.
SLICE_FINISHED_EVENT_CODE: Final = "slice_finished"

SliceStopReason = Literal[
    "no_open_run",
    "no_claimable_work",
    "time_budget_exhausted",
    "shutdown_requested",
    "preflight_missing_relations",
]

# The stop reason a preflight refusal reports. See `preflight_required_relations` and jobs/AGENTS.md
# "Preflight": a lane whose required relation does not exist must refuse before it ever opens a run
# or claims a shard, rather than burning its retry budget on a step certain to raise.
PREFLIGHT_MISSING_RELATIONS_STOP_REASON: Final[SliceStopReason] = "preflight_missing_relations"


def slice_summary_is_failing(summary: JobSliceSummary) -> bool:
    """True when one slice's landing means this tick found something wrong, not merely nothing to do.

    The single source of truth `jobs/dispatch.py`'s `lane_dispatched` log severity and
    `execution/jobs_pulse_command.py`'s `_slice_outcome`/`FAILING_PULSE_OUTCOMES` both read, so the two
    can never quietly diverge on what counts as "failing" -- which is exactly how a fully dead-lettered
    lane logged `stop_reason=no_claimable_work succeeded=0` at INFO for roughly 24 hours: dispatch.py's
    own `lane_dispatched` line described one slice's landing but was never told which landings meant
    trouble. See `execution/AGENTS.md` § "Outcomes and the exit rule".

    THIS PREDICATE DESCRIBES ONE SLICE'S LANDING AND NOTHING ELSE. A lane whose work was buried on some
    EARLIER tick is a standing condition, not a landing, and is deliberately not readable from here:
    `JobSliceSummary.run_status` cannot carry it. Two independent reasons, both provable from the SQL:

      * It cannot fire when it should. `sql/jobs/select_open_job_run.sql` selects only a run whose
        status is 'queued' or 'running', so the tick AFTER a run rolls up terminal selects no run at
        all and reports `run_status=None` (see `run_job_slice`'s `no_open_run` branch) -- healthy.
      * It fires when it must not. `sql/jobs/refresh_job_run_rollup.sql` counts a 'cancelled' WORK ITEM
        as `failed` and its run CASE has no 'cancelled' branch, so an operator cancellation rolls the
        run up to 'partial'/'failed'. Nothing anywhere writes 'cancelled' or 'dead_letter' to
        `job_run.status` at all -- `insert_job_run.sql` writes 'queued' and that rollup CASE writes the
        other four, and those are the only two statements that touch the column.

    The standing signal lives in `execution/jobs_pulse_command.py` instead, counted from
    `sql/jobs/count_dead_lettered_work_items.sql`, which has neither defect.
    """
    return summary.dead_lettered > 0 or summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON


ItemLanding = Literal[
    "succeeded",
    "retried",
    "dead_lettered",
    "deferred",
    "yielded",
    "released",
    "abandoned",
]


class JobDefinitionNotFoundError(LookupError):
    """Raised when a slice is asked for a definition name that no enabled ledger row matches."""


class JobRunError(RuntimeError):
    """Raised when a run row the runtime just wrote cannot be read back, so the ledger contradicts itself."""


_UPSERT_JOB_DEFINITION: Final = text(load_query_sql("jobs/upsert_job_definition.sql"))

_LOAD_JOB_DEFINITION: Final = text(load_query_sql("jobs/load_job_definition.sql"))

_INSERT_JOB_RUN: Final = text(load_query_sql("jobs/insert_job_run.sql"))

_SELECT_JOB_RUN: Final = text(load_query_sql("jobs/select_job_run.sql"))

_INSERT_JOB_WORK_ITEMS: Final = text(load_query_sql("jobs/insert_job_work_items.sql"))

_SELECT_OPEN_JOB_RUN: Final = text(load_query_sql("jobs/select_open_job_run.sql"))

_REFRESH_JOB_RUN_ROLLUP: Final = text(load_query_sql("jobs/refresh_job_run_rollup.sql"))

_WRITE_SLICE_EVENT: Final = text(load_query_sql("jobs/write_slice_event.sql"))


class ShutdownSignal:
    """A stop flag a container-stop signal sets and the slice loop reads between units of work."""

    def __init__(self) -> None:
        """Start un-signalled; `reason` is both the flag and the text a released shard records."""
        self.reason: str | None = None

    @property
    def requested(self) -> bool:
        """True once a stop signal has arrived and the slice should hand its shard back and finish."""
        return self.reason is not None

    def request(self, reason: str) -> None:
        """Record the first stop reason; a second signal must not overwrite what the first will report."""
        if self.reason is None:
            self.reason = reason


def _no_restore() -> None:
    """Undo nothing, for a platform on which no stop-signal handler could be installed in the first place."""


def _bind_stop_signal(
    loop: asyncio.AbstractEventLoop,
    number: int,
    stop: ShutdownSignal,
) -> Callable[[], None]:
    """Route one signal number into the stop flag and return the callable that puts the old handler back."""
    reason = f"{SHUTDOWN_RELEASE_REASON} ({signal.Signals(number).name})"
    try:
        loop.add_signal_handler(number, stop.request, reason)
    except (NotImplementedError, RuntimeError, ValueError):
        # Windows' proactor loop implements no signal handlers at all, and neither API works off the main
        # thread. Production is Linux, so this is the developer machine's branch: `signal.signal` runs the
        # callback between bytecodes rather than through the loop, which is all a flag nobody awaits needs.
        try:
            previous = signal.signal(number, lambda _number, _frame: stop.request(reason))
        except (OSError, ValueError):
            # No signal handling is available here at all. A slice that cannot be told to stop is exactly
            # the behaviour that shipped before this existed, so degrade to it rather than refusing to run.
            return _no_restore

        def restore_previous_handler() -> None:
            signal.signal(number, previous)

        return restore_previous_handler

    def restore_loop_handler() -> None:
        loop.remove_signal_handler(number)

    return restore_loop_handler


@asynccontextmanager
async def shutdown_signal() -> AsyncIterator[ShutdownSignal]:
    """Bind SIGTERM/SIGINT to a stop flag for the length of one slice, restoring the previous handlers after."""
    stop = ShutdownSignal()
    loop = asyncio.get_running_loop()
    restore = [_bind_stop_signal(loop, number, stop) for number in _STOP_SIGNALS]
    try:
        yield stop
    finally:
        for undo in restore:
            undo()


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
    released: int = 0
    abandoned: int = 0
    reclaimed: int = 0
    elapsed_seconds: float = 0.0
    run_status: str | None = None
    # Populated only when stop_reason == "preflight_missing_relations": the relations the lane
    # declared as required that do not exist right now, named plainly so an operator reading the
    # ledger never has to SSH anywhere to know what is missing. Empty on every other tick.
    preflight_missing_relations: tuple[str, ...] = ()

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
            "released": self.released,
            "abandoned": self.abandoned,
            "reclaimed": self.reclaimed,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "run_status": self.run_status,
            "preflight_missing_relations": list(self.preflight_missing_relations),
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


def _job_event_environment() -> str:
    """Name the deployment this tick ran in, because job_event.environment is NOT NULL and nothing else names it."""
    named = os.environ.get(JOB_EVENT_ENVIRONMENT_VARIABLE, "").strip()
    return (named or DEFAULT_JOB_EVENT_ENVIRONMENT)[:JOB_EVENT_TEXT_MAX_LENGTH]


async def _write_slice_event(session: AsyncSession, summary: JobSliceSummary) -> None:
    """Write this tick's single durable heartbeat row, so an idle lane reads differently from a dead one."""
    # Severity, not a second event code: an operator filtering job_event on severity gets the ticks that
    # dead-lettered something without having to know a second vocabulary word. The slice summary goes in
    # `progress` verbatim -- it is already the operator-facing shape the cron log carries -- and the queue
    # depth the statement computes for itself goes in `detail`.
    severity = "info"
    if summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON:
        # Louder than a dead-lettered tick's "warning": this is not one shard failing, it is the
        # whole lane refusing to run at all, and it will refuse identically on every future tick
        # until someone applies the missing migration -- exactly the class of silence this exists
        # to end.
        severity = "error"
    elif summary.dead_lettered:
        severity = "warning"
    await fetch_row(
        session,
        _WRITE_SLICE_EVENT,
        {
            "job_run_id": summary.job_run_id,
            "severity": severity,
            "event_code": SLICE_FINISHED_EVENT_CODE,
            "environment": _job_event_environment(),
            "service": JOB_EVENT_SERVICE,
            "duration_ms": int(summary.elapsed_seconds * 1000),
            "progress": canonical_json(summary.to_summary()),
        },
    )


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


async def preflight_required_relations(
    session: AsyncSession,
    *,
    definition_name: str,
    worker_id: str,
    required_relations: Sequence[str],
) -> JobSliceSummary | None:
    """Refuse a lane immediately, naming what is missing, before it opens a run or mints a shard.

    A lane declares the relations its handler cannot function without and calls this FIRST, before
    `ensure_job_definition`/`open_job_run`. Returns the terminal summary the caller should return
    as-is when one or more are missing; returns None when every relation exists and the caller
    should proceed to open its run.

    Without this, a lane whose dependency does not exist burns its full retry budget on EVERY tick,
    forever, and mints a brand-new work item each tick to burn it on -- exactly the production shape
    this exists to prevent (see jobs/AGENTS.md "Preflight"): `agri.matview_refresh_state` missing
    dead-lettered ten `matview-refresh` shards and parked thirteen `strategy-mv-refresh` ones across
    a single day, none of it visible without reading `job_attempt.error_summary` row by row.

    The refusal is not a silent early return: it writes the same `job_event` heartbeat every other
    stop path writes (severity `error`, distinct from a dead-lettered tick's `warning`), so
    `max(occurred_at) WHERE event_code = 'slice_finished'` still reads as "this lane is ticking, and
    refusing" rather than looking identical to a lane that quietly did nothing, or one that never
    ran at all.
    """
    await _reset(session)
    missing = await find_missing_relations(session, required_relations)
    if not missing:
        return None
    logger.error(
        "job_preflight_refused_missing_relations",
        definition=definition_name,
        missing_relations=missing,
    )
    summary = JobSliceSummary(
        definition_name=definition_name,
        worker_id=worker_id,
        job_run_id=None,
        stop_reason=PREFLIGHT_MISSING_RELATIONS_STOP_REASON,
        elapsed_seconds=0.0,
        preflight_missing_relations=missing,
    )
    await _write_slice_event(session, summary)
    await _commit(session)
    return summary


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


async def _release_in_hand(
    session: AsyncSession,
    claim: ClaimedWorkItem,
    metrics: Mapping[str, object],
    *,
    reason: str,
    landing: ItemLanding,
) -> ItemLanding:
    """Hand back a shard this worker still holds, on the fenced park path, so the next tick claims it at once.

    The one primitive behind three stops -- the time budget, a container-stop signal and a cancellation --
    because all three mean the same thing to the ledger: this worker is done, the shard is not, and nobody
    failed. `resume_at=None` parks to `now()`, which is claimable immediately, rather than leaving the lease
    to rot for the rest of `lease_seconds` before a later tick's reaper may touch it.
    """
    if not await defer_work_item(session, claim, resume_at=None, reason=reason, metrics=metrics):
        return await _abandon(session, claim)
    await _commit(session)
    return landing


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


async def _drive_work_item(  # noqa: PLR0913 - the handler, its policy, its claim, the budget and the stop flag
    session: AsyncSession,
    definition: JobDefinitionRecord,
    handler: JobHandler,
    claim: ClaimedWorkItem,
    deadline: float,
    monotonic: Callable[[], float],
    stop: ShutdownSignal,
) -> ItemLanding:
    """Run one claimed shard to a landing: completed, failed, deferred, yielded, released, or fenced out."""
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
        if stop.requested:
            # The boundary between two handler steps is the only place a shard can be handed back
            # cleanly, so it is where the stop flag is read. Without this the container simply died and
            # left the shard 'running' behind a lease of up to `lease_seconds` that the next tick cannot
            # claim and the reaper may not touch -- an hour of a lane's frontier lost per redeploy, with
            # nothing anywhere recording that it happened.
            return await _release_in_hand(
                session,
                claim,
                metrics,
                reason=stop.reason or SHUTDOWN_RELEASE_REASON,
                landing="released",
            )
        if monotonic() >= deadline:
            # The budget is the durability primitive: park the shard so the NEXT tick resumes it from
            # its checkpoint, rather than leaving the lease to rot until the reaper notices.
            return await _release_in_hand(
                session,
                claim,
                metrics,
                reason=BUDGET_YIELD_REASON,
                landing="yielded",
            )
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


async def _release_after_cancellation(
    session: AsyncSession,
    claim: ClaimedWorkItem,
    guard: _LeaseGuard,
    metrics: Mapping[str, object],
) -> None:
    """Hand a cancelled shard back before the cancellation finishes unwinding, on a possibly-aborted session."""
    # `_reset` first and unconditionally, exactly as `_fail_after_error` opens: the handler shares this
    # session and a cancellation delivered mid-statement can leave it in InFailedSQLTransaction, in which
    # the release below would raise a second exception and strand the shard after all.
    await _reset(session)
    if not guard.fence_held:
        await _release_abandoned(session, claim)
        return
    await _release_in_hand(session, claim, metrics, reason=CANCELLED_RELEASE_REASON, landing="released")


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
    except asyncio.CancelledError:
        # CancelledError derives from BaseException, so the `except Exception` below never saw it. A
        # cancelled slice therefore unwound with its shard still 'running' behind a live lease -- the
        # same stranded-window failure a killed container produces, reached by a different door. Release
        # it on the same fenced path a signalled shutdown takes, then let the cancellation finish: a
        # swallowed cancellation is worse than the leak it would fix.
        await _release_after_cancellation(session, claim, guard, metrics)
        raise
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
    stop: ShutdownSignal | None = None,
) -> JobSliceSummary:
    """Claim and drive shards until the work, the time budget or the container runs out, then report the tick.

    This is the whole durability model for a one-shot Railway cron container: do as much as fits inside
    `budget_seconds`, checkpoint every step, park whatever did not finish, exit 0. The next tick claims
    the same shards straight out of the ledger and resumes each from its last cursor. Nothing is kept
    on the container's filesystem, so a killed container loses only the work since its last checkpoint.

    Pass `stop` -- from `shutdown_signal()`, which the CLI entrypoint installs -- to make a SIGTERM hand
    the shard in hand back to the queue instead of stranding it behind a lease. Without one the loop
    behaves exactly as before. Every return path writes one `job_event` heartbeat row.
    """
    shutdown = ShutdownSignal() if stop is None else stop
    started = monotonic()
    await apply_statement_timeout(session)
    definition = await load_job_definition(session, definition_name, version=version)
    handler = registry.handler_for(definition.handler)
    budget = definition.time_budget_seconds if budget_seconds is None else budget_seconds
    _require_budget_within_lease(budget, definition)
    deadline = started + budget
    run_id = job_run_id if job_run_id is not None else await _select_open_job_run(session, definition.id)
    if run_id is None:
        # This tick still writes its heartbeat. `no_open_run` is emitted identically by a lane that
        # finished, a lane whose windows were never fanned out and a lane whose definition name drifted,
        # and without a durable row all three are also indistinguishable from a cron that never ran.
        await _reset(session)
        idle = JobSliceSummary(
            definition_name=definition.name,
            worker_id=worker_id,
            job_run_id=None,
            stop_reason="no_open_run",
            elapsed_seconds=monotonic() - started,
        )
        await _write_slice_event(session, idle)
        await _commit(session)
        return idle
    tally = _SliceTally()
    if reclaim_expired:
        # Scoped to the DEFINITION, not to the run this tick drives. Only the oldest open run is ever
        # driven, and a lane mints a second run whenever its floor is lowered, so a run-scoped reaper
        # leaves a shard stranded behind a dead lease in a sibling run unreclaimable by any tick at all.
        reclaimed = await reclaim_expired_leases(
            session,
            job_definition_id=definition.id,
            backoff_seconds=definition.retry_policy.backoff_seconds(1),
        )
        tally.reclaimed = reclaimed.total
        await _commit(session)
    stop_reason: SliceStopReason = "time_budget_exhausted"
    while monotonic() < deadline:
        if shutdown.requested:
            # Nothing is in hand here, so there is nothing to release -- just stop claiming work this
            # container has been told it will not live long enough to finish.
            stop_reason = "shutdown_requested"
            break
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
        landing = await _drive_work_item(session, definition, handler, claim, deadline, monotonic, shutdown)
        tally.record(landing)
        if landing == "released":
            stop_reason = "shutdown_requested"
            break
        if landing == "yielded":
            # Either the loop's own deadline check parked the shard -- in which case the `while` is
            # already false -- or the handler said it had no clock for its next unit of work. Claiming
            # another shard after that only buys another yield, so stop and let the next tick resume.
            stop_reason = "time_budget_exhausted"
            break
    rollup = await refresh_job_run_rollup(session, run_id)
    summary = JobSliceSummary(
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
        released=tally.count("released"),
        abandoned=tally.count("abandoned"),
        reclaimed=tally.reclaimed,
        elapsed_seconds=monotonic() - started,
        run_status=rollup.status,
    )
    # The heartbeat rides the rollup's transaction rather than opening its own: one commit per tick, as
    # before, and a tick whose rollup could not be written writes no heartbeat claiming that it ran.
    await _write_slice_event(session, summary)
    await _commit(session)
    return summary
