"""The single stateful scheduler for PlantGeo ingestion and Parquet publication work.

The declarative lane table and cadence-bucket/failed-checkpoint math have their own modules
(`lane_specs.py`, `lane_scheduling.py`); the per-tick result containers live in `turn_reports.py`. This
module re-exports their public names so no importer of `job_executor_service` changes, and keeps the
turn-report cache, subprocess command execution, leader-lock/definition-registration/tick-planning and
service-loop orchestration together: `tests/execution/test_command_stderr_capture.py` monkeypatches
`LANE_SPECS`, `parse_activation`, `_default_stderr_sink`, `_default_stdout_sink` and `_LANE_TURN_REPORTS`
as one unit on this module, so `run_scheduled_command` and the cache it folds into must keep reading
those same module globals. See execution/AGENTS.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

import click
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_pool
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_LANE_IDS,
    REPAIR_LANE_SUFFIX,
    RepairRequest,
    RepairRequestError,
)
from agri_data_service.execution.lane_scheduling import (
    SUPERSEDE_RUN_COMMAND,
    CheckpointVerdict,
    DueLane,
    LatestRun,
    bucket_after,
    fair_due_order,
    judge_failed_checkpoint,
    next_scheduled_bucket,
    scheduled_bucket,
    supersession_command,
)
from agri_data_service.execution.lane_specs import (
    ACTIVE_LANES_VARIABLE,
    CLOCK_RELEASE_STREAK_LIMIT,
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_DEFINITION_VERSION,
    EXECUTOR_HANDLER_TOKEN,
    EXECUTOR_REQUESTED_BY,
    EXECUTOR_WORK_ITEM_KIND,
    FAILURE_STREAK_PROBE_LIMIT,
    LANE_SPECS,
    ActivationConfig,
    ExecutorConfigurationError,
    LaneExecutionSpec,
    parse_activation,
)
from agri_data_service.execution.turn_reports import (
    ExecutorTickSummary,
    LaneTickResult,
    LaneTickState,
    OperatorAction,
)
from agri_data_service.jobs import (
    JobDefinitionRecord,
    JobHandlerOutcome,
    JobInvocation,
    JobWorkItemSpec,
    ShutdownSignal,
    job_handler,
    load_job_definition,
    open_job_run,
    read_lane_pause_state,
    run_job_slice,
    shutdown_signal,
)
from agri_data_service.jobs.lease import (
    FAILURE_SUMMARY_MAX_LENGTH,
    apply_statement_timeout,
    canonical_json,
    fetch_row,
    redact_text,
    required_column,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from collections.abc import Set as AbstractSet

logger = structlog.get_logger(__name__)

EXECUTOR_LEADER_LOCK_KEY: Final = "plantgeo:unified-job-executor:v1"
#: The two work item kinds the one handler runs: a cadence bucket's command, or a bounded repair turn of the
#: same command. See execution/AGENTS.md, "Bounded gap repair".
EXECUTOR_WORK_ITEM_KINDS: Final[frozenset[str]] = frozenset({EXECUTOR_WORK_ITEM_KIND, EXECUTOR_REPAIR_WORK_ITEM_KIND})

#: A failed or partial checkpoint run an operator has superseded is one resolved `agri.job_incident` row
#: keyed by this prefix plus the run id; `select_latest_run.sql` reads it as `superseded_by_operator`.
#: See execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an operator".
RUN_SUPERSESSION_INCIDENT_TYPE: Final = "plantgeo.executor.run_superseded"
RUN_SUPERSESSION_FINGERPRINT_PREFIX: Final = "plantgeo.executor.run-superseded:"
#: The two run statuses that settle a checkpoint without success and block the lane behind it.
SETTLED_WITHOUT_SUCCESS: Final[frozenset[str]] = frozenset({"failed", "partial"})

POLL_SECONDS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_POLL_SECONDS"
MAX_LANES_PER_TICK_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_MAX_LANES_PER_TICK"
#: How often the leader re-reads Parquet coverage and authors bounded repair turns; `0` disables authoring.
REPAIR_INTERVAL_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS"
DEFAULT_REPAIR_INTERVAL_SECONDS: Final = 6 * 3600.0
#: How long an authored layer sits out of the pass budget so the other layers get their turn.
DEFAULT_REPAIR_ROTATION_SECONDS: Final = 24 * 3600.0
#: Whether a NEW executor process releases a breaker-held lane once. `0` keeps every hold for an operator.
PROCESS_START_RELEASE_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER"
PROCESS_START_RELEASE_OPERATOR: Final = "executor:process-start"
DEPLOYMENT_ID_VARIABLE: Final = "RAILWAY_DEPLOYMENT_ID"

DEFAULT_POLL_SECONDS: Final = 30.0
MIN_LANES_PER_TICK: Final = 2
DEFAULT_MAX_LANES_PER_TICK: Final = MIN_LANES_PER_TICK
MAX_LOOP_BACKOFF_SECONDS: Final = 300.0
COMMAND_HEARTBEAT_SECONDS: Final = 30.0
COMMAND_TIMEOUT_RESERVE_SECONDS: Final = 5.0
COMMAND_CLEANUP_MARGIN_SECONDS: Final = 300
COMMAND_TERMINATE_GRACE_SECONDS: Final = 30
COMMAND_KILL_WAIT_SECONDS: Final = 10
WORKER_ID_MAX_LENGTH: Final = 255
#: How much of a child's stderr the wrapper keeps for the ledger. The TAIL, because a Python traceback ends
#: with the exception that matters and a bounded head would keep only the warnings that preceded it.
COMMAND_STDERR_TAIL_BYTES: Final = 4096
COMMAND_STDERR_READ_BYTES: Final = 4096
#: How long the wrapper waits for the child's stderr pipe to reach EOF once the child has exited or been killed.
COMMAND_STDERR_DRAIN_SECONDS: Final = 5.0
#: Characters of that tail allowed into `last_error_summary`, leaving the headline room inside the ledger's clamp.
COMMAND_STDERR_SUMMARY_CHARS: Final = FAILURE_SUMMARY_MAX_LENGTH - 120
#: How much of a child's stdout the wrapper keeps: enough for the ONE terminal JSON report every direct writer
#: prints last, whose `unwritten` list is what the ledger must not lose at exit 0.
COMMAND_STDOUT_TAIL_BYTES: Final = 64 * 1024
#: How many unwritten days one checkpoint records verbatim, and how long each day's detail may be.
TURN_REPORT_UNWRITTEN_MAX: Final = 12
TURN_REPORT_DETAIL_CHARS: Final = 200
#: The blocker string prefix `_held_checkpoint_result` writes; the typed `operator_action` carries the same command.
OPERATOR_SUPERSESSION_BLOCKER_PREFIX: Final = "operator supersession required: "


class ExecutorLeaderUnlockError(RuntimeError):
    """Raised when the pinned PostgreSQL backend cannot confirm leader-lock release."""


_TRY_LEADER_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0)) AS acquired")
_RELEASE_LEADER_LOCK: Final = text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0)) AS released")
_SELECT_DEFINITION_STATE: Final = text(load_query_sql("execution/select_definition_state.sql"))
_INSERT_DEFINITION: Final = text(load_query_sql("execution/insert_definition.sql"))
_SELECT_LATEST_RUN: Final = text(load_query_sql("execution/select_latest_run.sql"))


@dataclass(frozen=True, slots=True)
class TurnReport:
    """The bounded facts kept from a writer's terminal stdout report: did the turn leave days unwritten?

    A direct lane exits 0 when at least one day wrote and reports `outcome=incomplete` with an `unwritten`
    list; before this nothing consumed that list, so a day stuck refusing re-refused every bucket silently.
    `consecutive_incomplete_buckets` is held per DEFINITION in THIS process (`_LANE_TURN_REPORTS`, a repair
    definition counts separately from its owning lane) and is honest about that: a restart resets it to
    the buckets seen since. See execution/AGENTS.md, "Turn reports".
    """

    outcome: str | None
    days_unwritten: int
    unwritten: tuple[Mapping[str, object], ...]
    unwritten_truncated: bool
    consecutive_incomplete_buckets: int = 0

    @property
    def incomplete(self) -> bool:
        return self.days_unwritten > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "days_unwritten": self.days_unwritten,
            "unwritten": [dict(entry) for entry in self.unwritten],
            "unwritten_truncated": self.unwritten_truncated,
            "consecutive_incomplete_buckets": self.consecutive_incomplete_buckets,
        }


#: Per owning lane, the newest turn report this process ran and its incomplete-bucket streak.
_LANE_TURN_REPORTS: dict[str, TurnReport] = {}


def parse_terminal_report(stdout_tail: bytes) -> Mapping[str, object] | None:
    """Return the LAST stdout line that is a JSON object -- the one terminal report a writer prints -- or `None`."""
    for raw in reversed(stdout_tail.decode("utf-8", errors="replace").splitlines()):
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _unwritten_entries(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Collect `unwritten` entries from the report and from any per-product `results` it fans out into."""
    found: list[Mapping[str, object]] = []
    own = report.get("unwritten")
    if isinstance(own, list):
        found.extend(entry for entry in own if isinstance(entry, dict))
    results = report.get("results")
    if isinstance(results, list):
        for product in results:
            if isinstance(product, dict):
                found.extend(_unwritten_entries(product))
    return found


def summarize_turn_report(report: Mapping[str, object] | None, *, previous: TurnReport | None) -> TurnReport | None:
    """Bound one parsed report to what a checkpoint may hold, continuing the lane's incomplete-bucket streak."""
    if report is None:
        return None
    entries = _unwritten_entries(report)
    declared = report.get("days_unwritten")
    days_unwritten = declared if isinstance(declared, int) and not isinstance(declared, bool) else len(entries)
    # Redacted HERE because the cursor path (`record_checkpoint`) canonicalises but never redacts; a child
    # that echoed a keyed URL into a day's detail must not put it on a durable row.
    kept = tuple(
        {
            "day": entry.get("day"),
            "outcome": entry.get("outcome"),
            "detail": redact_text(str(entry.get("detail", "")))[:TURN_REPORT_DETAIL_CHARS],
        }
        for entry in entries[:TURN_REPORT_UNWRITTEN_MAX]
    )
    outcome = report.get("outcome", report.get("status"))
    streak = (previous.consecutive_incomplete_buckets if previous is not None else 0) + 1 if days_unwritten else 0
    return TurnReport(
        outcome=str(outcome) if outcome is not None else None,
        days_unwritten=days_unwritten,
        unwritten=kept,
        unwritten_truncated=len(entries) > len(kept),
        consecutive_incomplete_buckets=streak,
    )


def record_turn_report(lane_id: str, report: Mapping[str, object] | None) -> TurnReport | None:
    """Fold one bucket's report into the process-held streak for its owning lane and return what was kept."""
    kept = summarize_turn_report(report, previous=_LANE_TURN_REPORTS.get(lane_id))
    if kept is not None:
        _LANE_TURN_REPORTS[lane_id] = kept
    return kept


async def _try_leader_lock(session: AsyncSession) -> bool:
    row = await fetch_row(session, _TRY_LEADER_LOCK, {"lock_key": EXECUTOR_LEADER_LOCK_KEY})
    return False if row is None else required_column(row, "acquired", bool)


async def _release_leader_lock(session: AsyncSession) -> None:
    try:
        row = await fetch_row(session, _RELEASE_LEADER_LOCK, {"lock_key": EXECUTOR_LEADER_LOCK_KEY})
    except Exception as error:
        raise ExecutorLeaderUnlockError("leader advisory unlock query failed") from error
    if row is None or not required_column(row, "released", bool):
        raise ExecutorLeaderUnlockError("the pinned PostgreSQL backend did not release the leader lock")


async def _invalidate_leader_connection(session: AsyncSession) -> None:
    bind = getattr(session, "bind", None)
    if not isinstance(bind, AsyncConnection):
        logger.error("plantgeo_job_executor_leader_connection_not_pinned")
        return
    try:
        await bind.invalidate()
    except BaseException as error:
        logger.error(
            "plantgeo_job_executor_leader_connection_invalidate_failed",
            error_type=type(error).__name__,
        )


def _pinned_connection_invalidated(session: AsyncSession) -> bool:
    """Report whether this tick's externally held connection lost its backend."""
    bind = getattr(session, "bind", None)
    return bool(bind is not None and getattr(bind, "invalidated", False))


async def _commit_planning_transaction(session: AsyncSession) -> None:
    """Start the next planning transaction with its transaction-local timeout restored."""
    await session.commit()
    await apply_statement_timeout(session)


async def _rollback_planning_transaction(session: AsyncSession) -> None:
    """Start the next planning transaction with its transaction-local timeout restored."""
    await session.rollback()
    await apply_statement_timeout(session)


async def _definition_state(session: AsyncSession, spec: LaneExecutionSpec) -> tuple[uuid.UUID, bool] | None:
    row = await fetch_row(
        session,
        _SELECT_DEFINITION_STATE,
        {"name": spec.definition_name, "version": EXECUTOR_DEFINITION_VERSION},
    )
    if row is None:
        return None
    return required_column(row, "id", uuid.UUID), required_column(row, "enabled", bool)


async def _load_or_register_definition(
    session: AsyncSession,
    spec: LaneExecutionSpec,
) -> JobDefinitionRecord | None:
    """Register a missing version fail-closed and preserve the lane-wide operator pause."""
    pause_state = await read_lane_pause_state(session, spec.definition_name)
    state = await _definition_state(session, spec)
    if state is None:
        definition_spec = spec.definition_spec()
        await fetch_row(
            session,
            _INSERT_DEFINITION,
            {
                "name": definition_spec.name,
                "version": definition_spec.version,
                "handler": definition_spec.handler,
                "queue_name": definition_spec.queue_name,
                "schedule": definition_spec.schedule,
                "schedule_timezone": definition_spec.schedule_timezone,
                "enabled": not pause_state.registered,
                "concurrency_key": definition_spec.concurrency_key,
                "max_attempts": definition_spec.max_attempts,
                "lease_seconds": definition_spec.lease_seconds,
                "time_budget_seconds": definition_spec.time_budget_seconds,
                "retry_policy": canonical_json(definition_spec.retry_policy.to_json()),
                "parameters": canonical_json(definition_spec.parameters),
            },
        )
        await _commit_planning_transaction(session)
        state = await _definition_state(session, spec)
        if state is None:
            raise RuntimeError(f"executor definition {spec.definition_name!r} was neither inserted nor readable")
    _, enabled = state
    if pause_state.paused or not enabled:
        await _rollback_planning_transaction(session)
        return None
    definition = await load_job_definition(
        session,
        spec.definition_name,
        version=EXECUTOR_DEFINITION_VERSION,
    )
    await _rollback_planning_transaction(session)
    return definition


async def read_lane_checkpoint(session: AsyncSession, spec: LaneExecutionSpec) -> LatestRun | None:
    """Read the lane's scheduler checkpoint: the run a tick plans from, with its supersession and failure streak."""
    row = await fetch_row(
        session,
        _SELECT_LATEST_RUN,
        {
            "name": spec.definition_name,
            "current_version": EXECUTOR_DEFINITION_VERSION,
            "supersession_fingerprint_prefix": RUN_SUPERSESSION_FINGERPRINT_PREFIX,
            "failure_streak_limit": FAILURE_STREAK_PROBE_LIMIT,
        },
    )
    if row is None:
        return None
    return LatestRun(
        run_id=required_column(row, "id", uuid.UUID),
        scheduled_for=required_column(row, "scheduled_for", datetime),
        status=required_column(row, "status", str),
        work_claimable=required_column(row, "work_claimable", bool),
        has_work_items=required_column(row, "has_work_items", bool),
        terminal_items_need_rollup=required_column(row, "terminal_items_need_rollup", bool),
        definition_id=required_column(row, "job_definition_id", uuid.UUID),
        definition_version=required_column(row, "definition_version", str),
        definition_enabled=required_column(row, "definition_enabled", bool),
        superseded_by_operator=required_column(row, "superseded_by_operator", bool),
        consecutive_failures=required_column(row, "consecutive_failures", int),
    )


def _held_checkpoint_result(spec: LaneExecutionSpec, latest: LatestRun, verdict: CheckpointVerdict) -> LaneTickResult:
    """Report a checkpoint that settled without success and still holds its lane, naming what releases it."""
    needs_operator = verdict.release == "operator" and not latest.superseded_by_operator
    if not verdict.newer_bucket_exists:
        opens = "only after a recorded supersession" if needs_operator else "by itself"
        detail = (
            f"current bucket settled {latest.status}; its logical run is spent, and bucket "
            f"{verdict.next_bucket.isoformat()} opens {opens}"
        )
    else:
        detail = (
            f"{verdict.consecutive_failures} consecutive bucket(s) settled without success; the clock no longer "
            f"releases this {spec.catch_up_policy} lane, so bucket {verdict.next_bucket.isoformat()} waits for a "
            "recorded operator supersession"
        )
    command = supersession_command(spec, latest.run_id) if needs_operator else None
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="failed",
        scheduled_for=latest.scheduled_for,
        run_id=latest.run_id,
        run_status=latest.status,
        detail=detail,
        blockers=(f"{OPERATOR_SUPERSESSION_BLOCKER_PREFIX}{command}",) if command is not None else (),
        operator_action=command,
    )


def _work_priority(spec: LaneExecutionSpec) -> int:
    return 100 if spec.work_class == "incremental" else 10


async def _open_scheduled_run(
    session: AsyncSession,
    candidate: DueLane,
) -> uuid.UUID:
    spec = candidate.spec
    scheduled_iso = candidate.scheduled_for.isoformat()
    opened = await open_job_run(
        session,
        candidate.definition,
        logical_run_key=f"{spec.definition_name}:{scheduled_iso}",
        scheduled_for=candidate.scheduled_for,
        requested_by=EXECUTOR_REQUESTED_BY,
        target_partitions={"lane_id": spec.lane_id, "scheduled_for": scheduled_iso},
        work_items=(
            JobWorkItemSpec(
                shard_key=scheduled_iso,
                kind=EXECUTOR_WORK_ITEM_KIND,
                payload={"lane_id": spec.lane_id, "scheduled_for": scheduled_iso},
                priority=_work_priority(spec),
            ),
        ),
    )
    await _commit_planning_transaction(session)
    return opened.job_run_id


def _worker_id(spec: LaneExecutionSpec) -> str:
    replica = os.environ.get("RAILWAY_REPLICA_ID", "").strip()
    identity = replica or f"{socket.gethostname()}:{os.getpid()}"
    return f"job-executor:{identity}:{spec.lane_id}"[:WORKER_ID_MAX_LENGTH]


async def _execute_due_lane(
    session: AsyncSession,
    candidate: DueLane,
    *,
    stop: ShutdownSignal | None,
) -> LaneTickResult:
    if stop is not None and stop.requested:
        return _deferred_shutdown_result(candidate)
    run_id = candidate.existing_run_id or await _open_scheduled_run(session, candidate)
    if candidate.superseded_run_id is not None:
        logger.info(
            "plantgeo_job_executor_failed_run_superseded",
            lane_id=candidate.spec.lane_id,
            superseded_run_id=str(candidate.superseded_run_id),
            release=candidate.supersession,
            bucket=candidate.scheduled_for.isoformat(),
            run_id=str(run_id),
        )
    summary = await run_job_slice(
        session,
        definition_name=candidate.spec.definition_name,
        version=candidate.definition.version,
        job_run_id=run_id,
        worker_id=_worker_id(candidate.spec),
        budget_seconds=float(candidate.definition.time_budget_seconds),
        stop=stop,
    )
    failed = (
        summary.retried > 0
        or summary.dead_lettered > 0
        or summary.abandoned > 0
        or summary.run_status in SETTLED_WITHOUT_SUCCESS
    )
    detail: str = (
        "work item dead-lettered"
        if summary.dead_lettered
        else "work item abandoned after losing its fenced lease"
        if summary.abandoned
        else "work item entered retry backoff"
        if summary.retried
        else summary.stop_reason
    )
    if candidate.superseded_run_id is not None:
        detail = f"supersedes run {candidate.superseded_run_id} by {candidate.supersession}; {detail}"
    turn_report = _LANE_TURN_REPORTS.get(candidate.spec.lane_id) if summary.claimed else None
    if turn_report is not None and turn_report.incomplete:
        detail = (
            f"{detail}; left {turn_report.days_unwritten} day(s) unwritten for "
            f"{turn_report.consecutive_incomplete_buckets} consecutive bucket(s) in this process"
        )
    return LaneTickResult(
        lane_id=candidate.spec.lane_id,
        state="failed" if failed else "ran",
        scheduled_for=candidate.scheduled_for,
        run_id=run_id,
        run_status=summary.run_status,
        detail=detail,
        slice_summary=summary.to_summary(),
        turn_report=turn_report,
    )


def _deferred_shutdown_result(candidate: DueLane) -> LaneTickResult:
    return LaneTickResult(
        lane_id=candidate.spec.lane_id,
        state="deferred_shutdown",
        scheduled_for=candidate.scheduled_for,
        run_id=candidate.existing_run_id,
        detail="shutdown requested before this lane was opened",
    )


def _blocked_open_run_result(
    spec: LaneExecutionSpec,
    latest: LatestRun,
    *,
    prior_version: bool,
) -> LaneTickResult | None:
    version_detail = (
        f"prior definition version {latest.definition_version!r}" if prior_version else "current definition"
    )
    if not latest.has_work_items:
        return LaneTickResult(
            lane_id=spec.lane_id,
            state="failed",
            scheduled_for=latest.scheduled_for,
            run_id=latest.run_id,
            run_status=latest.status,
            detail=f"{version_detail} has a nonterminal run with no work items; explicitly repair or cancel it",
        )
    if latest.work_claimable or latest.terminal_items_need_rollup:
        return None
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="not_due",
        scheduled_for=latest.scheduled_for,
        run_id=latest.run_id,
        run_status=latest.status,
        detail=f"{version_detail} has no currently claimable work; retry, defer, or live lease wait remains",
    )


async def _plan_prior_version_run(
    session: AsyncSession,
    spec: LaneExecutionSpec,
    latest: LatestRun | None,
) -> tuple[LaneTickResult | None, DueLane | None]:
    """Resume or refuse prior-version work before current-version scheduling."""
    if latest is None or not latest.open or latest.definition_version == EXECUTOR_DEFINITION_VERSION:
        return None, None
    if not latest.definition_enabled:
        return (
            LaneTickResult(
                lane_id=spec.lane_id,
                state="failed",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                run_status=latest.status,
                detail=(
                    f"prior definition version {latest.definition_version!r} has nonterminal work but is "
                    "disabled; explicitly resume or cancel that durable run before current-version work"
                ),
            ),
            None,
        )
    blocked = _blocked_open_run_result(spec, latest, prior_version=True)
    if blocked is not None:
        return blocked, None
    definition = await load_job_definition(
        session,
        spec.definition_name,
        version=latest.definition_version,
    )
    if definition.handler != EXECUTOR_HANDLER_TOKEN:
        return (
            LaneTickResult(
                lane_id=spec.lane_id,
                state="failed",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                run_status=latest.status,
                detail=(
                    f"prior definition version {latest.definition_version!r} uses incompatible handler "
                    f"{definition.handler!r}; reconcile it before current-version work"
                ),
            ),
            None,
        )
    return (
        None,
        DueLane(
            spec=spec,
            definition=definition,
            scheduled_for=latest.scheduled_for,
            existing_run_id=latest.run_id,
            last_scheduled_for=latest.scheduled_for,
        ),
    )


@dataclass(slots=True)
class ProcessStartRelease:
    """OPT-IN: a new DEPLOYMENT releases each breaker-held lane once, by recording a real supersession.

    A breaker hold means "this code failed three buckets running; a human must look". A deploy IS the human
    having looked, so under `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER=1` the lane gets exactly
    one bucket per deployment; if that fails, the streak is longer than before and the hold returns until
    the NEXT deployment. Off by default (owner decision 2026-09-18: the sensors release is an explicit CLI
    supersession until the fix has proven itself). Three bounds, in order of strength: only a run whose
    bucket lies strictly before this process's start bucket qualifies, so a bucket this process opened is
    never its own release; the ledger marker `claim_process_start_release` is one row per (deployment,
    lane), so a container restart under the same deployment re-releases nothing; `released` is the
    process-local memo that keeps a refused or failed attempt from being retried every tick.
    """

    started_at: datetime
    deployment: str
    released: set[uuid.UUID] = field(default_factory=set)

    @classmethod
    def from_environment(
        cls, *, now: datetime, environment: Mapping[str, str] | None = None
    ) -> ProcessStartRelease | None:
        source = os.environ if environment is None else environment
        if source.get(PROCESS_START_RELEASE_VARIABLE, "0").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        return cls(started_at=now, deployment=source.get(DEPLOYMENT_ID_VARIABLE, "").strip() or "local")

    def qualifies(self, spec: LaneExecutionSpec, latest: LatestRun) -> bool:
        """True for a held run whose BUCKET settled strictly before the bucket this process started in."""
        return latest.scheduled_for < scheduled_bucket(spec, self.started_at) and latest.run_id not in self.released

    def evidence(self, latest: LatestRun, verdict: CheckpointVerdict) -> str:
        return (
            f"released once by executor process start at {self.started_at.isoformat()} (deployment "
            f"{self.deployment}); the breaker held run {latest.run_id} after {verdict.consecutive_failures} "
            "consecutive failed bucket(s) settled before this process existed, and a fresh process earns one bucket"
        )


def _ledger_label() -> str:
    """Name the ledger for a receipt without ever echoing its credential; `unknown` when no DSN resolves."""
    from agri_data_service.execution.job_run_supersession import ledger_target  # noqa: PLC0415 - import cycle

    try:
        return ledger_target(settings.require_local_source_loader_database_url())
    except Exception:  # a label, never a gate: the recording itself proves the ledger answered
        return "unknown"


async def _release_by_process_start(  # noqa: PLR0913 - the held run, its verdict, the clock and the policy
    session: AsyncSession,
    spec: LaneExecutionSpec,
    latest: LatestRun,
    verdict: CheckpointVerdict,
    *,
    now: datetime,
    release: ProcessStartRelease,
) -> LatestRun | None:
    """Record this deployment's one supersession of a breaker-held run; `None` when it was not recorded.

    Marker first, supersession second, one commit: the marker's `ON CONFLICT DO NOTHING` refuses a second
    release under the same deployment before any supersession is written. Every refusal and every ledger
    fault rolls back, logs and returns `None` -- a planning tick must never abort because a release could
    not be recorded -- and `released` is only extended once the ledger has answered, so a transient fault
    is retried on a later tick rather than remembered as done.
    """
    from agri_data_service.execution.job_run_supersession import (  # noqa: PLC0415 - import cycle
        SupersessionRefusal,
        claim_process_start_release,
        supersede_failed_run,
    )

    try:
        claimed = await claim_process_start_release(
            session,
            lane_id=spec.lane_id,
            deployment=release.deployment,
            operator=PROCESS_START_RELEASE_OPERATOR,
            now=now,
            detail={
                "lane_id": spec.lane_id,
                "deployment": release.deployment,
                "run_id": str(latest.run_id),
                "process_started_at": release.started_at.isoformat(),
            },
        )
        if not claimed:
            await _rollback_planning_transaction(session)
            release.released.add(latest.run_id)
            logger.info(
                "plantgeo_job_executor_breaker_release_already_spent",
                lane_id=spec.lane_id,
                run_id=str(latest.run_id),
                deployment=release.deployment,
                detail="this deployment already released this lane once; the hold waits for an operator",
            )
            return None
        receipt = await supersede_failed_run(
            session,
            spec,
            latest.run_id,
            ledger=_ledger_label(),
            evidence=release.evidence(latest, verdict),
            operator=PROCESS_START_RELEASE_OPERATOR,
            now=now,
            apply=True,
        )
        if receipt.outcome not in {"recorded", "already_superseded"}:
            await _rollback_planning_transaction(session)
            return None
        await _commit_planning_transaction(session)
    except SupersessionRefusal as refusal:
        await _rollback_planning_transaction(session)
        release.released.add(latest.run_id)
        logger.warning(
            "plantgeo_job_executor_breaker_release_refused",
            lane_id=spec.lane_id,
            run_id=str(latest.run_id),
            reason=str(refusal),
        )
        return None
    except SQLAlchemyError as error:
        await _rollback_planning_transaction(session)
        logger.error(
            "plantgeo_job_executor_breaker_release_failed",
            lane_id=spec.lane_id,
            run_id=str(latest.run_id),
            error_type=type(error).__name__,
        )
        return None
    release.released.add(latest.run_id)
    logger.error(
        "plantgeo_job_executor_breaker_released_by_process_start",
        lane_id=spec.lane_id,
        run_id=str(latest.run_id),
        consecutive_failures=verdict.consecutive_failures,
        deployment=release.deployment,
        detail="one bucket is granted; a failure now re-holds the lane for an operator or the next process start",
    )
    return replace(latest, superseded_by_operator=True)


async def _plan_active_lanes(  # noqa: PLR0912 - one branch per lane state the planner can find
    session: AsyncSession,
    activation: ActivationConfig,
    now: datetime,
    *,
    breaker_release: ProcessStartRelease | None = None,
) -> tuple[list[LaneTickResult], list[DueLane]]:
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for spec in LANE_SPECS.values():
        if not activation.is_active(spec.lane_id):
            state: LaneTickState = "shadow" if spec.executable else "source_specific"
            current_bucket = (
                scheduled_bucket(spec, now) if spec.executable and spec.cadence_seconds is not None else None
            )
            blockers = ["lane is not in the active allow-list"]
            if not spec.executable:
                blockers.append("no executable command exists in this runtime")
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state=state,
                    scheduled_for=current_bucket,
                    command=spec.command,
                    blockers=tuple(blockers),
                    due_prediction=(
                        "would_be_due_if_activated; source watermark parity not evaluated"
                        if spec.executable
                        else "not_executable"
                    ),
                    detail=(
                        "shadow schedule prediction only; no ledger or source watermark parity was read"
                        if spec.executable
                        else f"{spec.migration_disposition}: no command in this runtime"
                    ),
                )
            )
            continue

        definition = await _load_or_register_definition(session, spec)
        latest = await read_lane_checkpoint(session, spec)
        prior_result, prior_due = await _plan_prior_version_run(session, spec, latest)
        await _rollback_planning_transaction(session)
        if prior_result is not None:
            results.append(prior_result)
            continue
        if prior_due is not None:
            due.append(prior_due)
            continue
        if definition is None:
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="paused",
                    detail="the lane-wide job_definition pause or current-version pause is active",
                )
            )
            continue
        current_bucket = scheduled_bucket(spec, now)
        if latest is not None and latest.status in SETTLED_WITHOUT_SUCCESS:
            verdict = judge_failed_checkpoint(spec, latest, now)
            if (
                not verdict.released
                and verdict.release == "operator"
                and not latest.superseded_by_operator
                and breaker_release is not None
                and breaker_release.qualifies(spec, latest)
            ):
                released = await _release_by_process_start(
                    session, spec, latest, verdict, now=now, release=breaker_release
                )
                if released is not None:
                    latest = released
                    verdict = judge_failed_checkpoint(spec, latest, now)
            if not verdict.released:
                results.append(_held_checkpoint_result(spec, latest, verdict))
                continue
            due.append(
                DueLane(
                    spec=spec,
                    definition=definition,
                    scheduled_for=verdict.next_bucket,
                    existing_run_id=None,
                    last_scheduled_for=latest.scheduled_for,
                    superseded_run_id=latest.run_id,
                    supersession=verdict.release,
                )
            )
            continue
        if latest is not None and latest.open:
            blocked = _blocked_open_run_result(spec, latest, prior_version=False)
            if blocked is not None:
                results.append(blocked)
                continue
            due.append(
                DueLane(
                    spec=spec,
                    definition=definition,
                    scheduled_for=latest.scheduled_for,
                    existing_run_id=latest.run_id,
                    last_scheduled_for=latest.scheduled_for,
                )
            )
            continue
        if latest is not None and latest.scheduled_for >= current_bucket:
            detail = f"current bucket already settled with status {latest.status}"
            results.append(
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="not_due",
                    scheduled_for=latest.scheduled_for,
                    run_id=latest.run_id,
                    run_status=latest.status,
                    detail=detail,
                )
            )
            continue
        bucket = next_scheduled_bucket(
            spec,
            now,
            None if latest is None else latest.scheduled_for,
        )
        due.append(
            DueLane(
                spec=spec,
                definition=definition,
                scheduled_for=bucket,
                existing_run_id=None,
                last_scheduled_for=None if latest is None else latest.scheduled_for,
            )
        )
    return results, due


def repair_lane_spec(spec: LaneExecutionSpec) -> LaneExecutionSpec:
    """Derive the definition a lane's bounded gap repairs run under: the same command and timeout, its own ledger name.

    A SEPARATE definition, not a second work item on the cadence definition: `select_latest_run.sql` reads a
    lane's newest terminal run as its cadence checkpoint, so a repair run filed under the forward definition
    would settle a bucket the forward writer never ran. Backlog class, so `fair_due_order` never lets a repair
    take the incremental turn its owning lane's hourly bucket needs.
    """
    return replace(
        spec,
        lane_id=f"{spec.lane_id}{REPAIR_LANE_SUFFIX}",
        conflicts_with=(),
        work_class="backlog",
        schedule=None,
        catch_up_policy="coalesce_latest",
        selection_policy="operator-authored bounded repair turns; the writer selects the days",
        description=(
            f"Bounded gap repair for {spec.lane_id}: the same source-direct writer with a --max-days bound, "
            "authored from Parquet coverage by `agri-service ops jobs-plan-gap-repair`, never scheduled."
        ),
    )


async def ensure_lane_definition(session: AsyncSession, spec: LaneExecutionSpec) -> JobDefinitionRecord | None:
    """Register the lane's durable definition if absent and return it, or `None` while its pause switch is set."""
    return await _load_or_register_definition(session, spec)


async def _plan_repair_runs(
    session: AsyncSession,
    activation: ActivationConfig,
    *,
    forward_due: AbstractSet[str],
) -> tuple[list[LaneTickResult], list[DueLane]]:
    """Drive the repair runs an operator already authored. Never authors one, registers nothing, lists nothing.

    A repair definition that does not exist in the ledger costs one probe and is skipped: the tick must not
    create definitions for work nobody asked for. A repair run that settled is history; the authoring verb
    opens the next one under a new logical key. `forward_due` keeps a lane's forward bucket and its repair
    out of the same tick, so one turn never doubles that lane's egress.
    """
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for lane_id in sorted(REPAIR_LANE_IDS):
        spec = LANE_SPECS.get(lane_id)
        if spec is None or not activation.is_active(lane_id):
            continue
        repair = repair_lane_spec(spec)
        state = await _definition_state(session, repair)
        if state is None:
            await _rollback_planning_transaction(session)
            continue
        definition = await _load_or_register_definition(session, repair)
        latest = await read_lane_checkpoint(session, repair)
        await _rollback_planning_transaction(session)
        if definition is None:
            results.append(
                LaneTickResult(
                    lane_id=repair.lane_id,
                    state="paused",
                    detail="the repair definition's pause switch is set",
                )
            )
            continue
        if latest is None or not latest.open:
            continue
        if lane_id in forward_due:
            results.append(
                LaneTickResult(
                    lane_id=repair.lane_id,
                    state="deferred_fairness",
                    scheduled_for=latest.scheduled_for,
                    run_id=latest.run_id,
                    detail="the owning lane's forward bucket is due this tick; its repair waits for the next one",
                )
            )
            continue
        blocked = _blocked_open_run_result(repair, latest, prior_version=False)
        if blocked is not None:
            results.append(blocked)
            continue
        due.append(
            DueLane(
                spec=repair,
                definition=definition,
                scheduled_for=latest.scheduled_for,
                existing_run_id=latest.run_id,
                last_scheduled_for=latest.scheduled_for,
            )
        )
    return results, due


@dataclass(slots=True)
class RepairAuthoringClock:
    """When the leader last authored repairs, and how long it waits before reading coverage again.

    Coverage is one pointer GET per lane, so it is read on this interval and never per tick. A failed
    authoring pass advances the clock too: a broken object store must not be probed every 30 seconds.
    """

    interval_seconds: float
    last_authored: float | None = None
    #: Layer -> monotonic instant it was last authored for, in THIS process. A layer inside
    #: `rotation_seconds` is excluded from the next pass so two persistently unfillable layers cannot take
    #: the pass budget every interval and starve the rest (`deferred_by_rotation`). Process-held on purpose:
    #: a restart forgets the rotation, which costs at most one pass of the old ordering.
    recently_authored: dict[str, float] = field(default_factory=dict)
    rotation_seconds: float = DEFAULT_REPAIR_ROTATION_SECONDS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> RepairAuthoringClock | None:
        source = os.environ if environment is None else environment
        raw = source.get(REPAIR_INTERVAL_VARIABLE, "").strip()
        if not raw:
            return cls(interval_seconds=DEFAULT_REPAIR_INTERVAL_SECONDS)
        try:
            interval = float(raw)
        except ValueError as error:
            raise ExecutorConfigurationError(f"{REPAIR_INTERVAL_VARIABLE} must be a number") from error
        if interval < 0:
            raise ExecutorConfigurationError(f"{REPAIR_INTERVAL_VARIABLE} must not be negative")
        return None if interval == 0 else cls(interval_seconds=interval)

    def due(self, monotonic_now: float) -> bool:
        return self.last_authored is None or monotonic_now - self.last_authored >= self.interval_seconds

    def mark(self, monotonic_now: float) -> None:
        self.last_authored = monotonic_now

    def excluded(self, monotonic_now: float) -> frozenset[str]:
        """Layers authored within the rotation window; the next pass looks past them."""
        return frozenset(
            layer for layer, when in self.recently_authored.items() if monotonic_now - when < self.rotation_seconds
        )

    def remember(self, layers: Iterable[str], monotonic_now: float) -> None:
        for layer in layers:
            self.recently_authored[layer] = monotonic_now


async def _author_due_repairs(
    session: AsyncSession,
    activation: ActivationConfig,
    *,
    now: datetime,
    clock: RepairAuthoringClock,
) -> dict[str, object] | None:
    """Author the bounded repair turns the measured gaps call for, once per interval, with no hand on the ledger.

    The self-healing half of gap repair: `gap_repair.py`'s verb does exactly this by hand, and after a stall
    nobody is at the keyboard. One coverage read (availability authority, never a listing), one plan, one
    committed pass; the tick then drives whatever was opened through `_plan_repair_runs` like any other run.
    """
    from agri_data_service.execution import gap_repair  # noqa: PLC0415 - import cycle
    from agri_data_service.execution.gap_repair_contract import RepairBudget  # noqa: PLC0415 - import cycle

    started = time.monotonic()
    clock.mark(started)
    try:
        coverage = await asyncio.to_thread(gap_repair.read_parquet_coverage, now=now)
        plan = gap_repair.plan_gap_repairs(
            coverage,
            activation=activation,
            now=now,
            budget=RepairBudget(),
            recently_authored=clock.excluded(started),
        )
        receipts = await gap_repair.author_gap_repairs(session, plan=plan, now=now, apply=True)
        await _commit_planning_transaction(session)
    except Exception as error:  # authoring is best-effort; the forward lanes never wait on it
        await _rollback_planning_transaction(session)
        logger.error("plantgeo_job_executor_repair_authoring_failed", error_type=type(error).__name__)
        return None
    clock.remember((receipt.layer for receipt in receipts), started)
    summary: dict[str, object] = {
        "authorized": [candidate.layer for candidate in plan.authorized],
        "verdicts": {candidate.layer: candidate.verdict for candidate in plan.candidates},
        "receipts": [receipt.to_dict() for receipt in receipts],
    }
    logger.info("plantgeo_job_executor_repairs_authored", **summary)
    return summary


async def run_executor_tick(  # noqa: PLR0913 - one operator-tunable knob of the tick per argument
    session: AsyncSession,
    *,
    activation: ActivationConfig,
    now: datetime,
    max_lanes_per_tick: int,
    stop: ShutdownSignal | None = None,
    breaker_release: ProcessStartRelease | None = None,
    repair_clock: RepairAuthoringClock | None = None,
) -> ExecutorTickSummary:
    """Run one leader-elected, durable, fairly selected scheduler tick."""
    if max_lanes_per_tick < MIN_LANES_PER_TICK:
        raise ExecutorConfigurationError(
            f"max_lanes_per_tick must be at least {MIN_LANES_PER_TICK} to preserve class fairness"
        )
    logger.info(
        "plantgeo_job_executor_tick_started",
        observed_at=now.isoformat(),
        active_lane_count=len(activation.active_lanes),
    )
    await apply_statement_timeout(session)
    if not await _try_leader_lock(session):
        await session.rollback()
        logger.info("plantgeo_job_executor_leader_not_acquired", observed_at=now.isoformat())
        return ExecutorTickSummary(observed_at=now, leader=False, lanes=())
    logger.info("plantgeo_job_executor_leader_acquired", observed_at=now.isoformat())
    primary_error: BaseException | None = None
    try:
        results, due = await _plan_active_lanes(session, activation, now, breaker_release=breaker_release)
        if repair_clock is not None and repair_clock.due(time.monotonic()):
            await _author_due_repairs(session, activation, now=now, clock=repair_clock)
        repair_results, repair_due = await _plan_repair_runs(
            session,
            activation,
            forward_due={candidate.spec.lane_id for candidate in due},
        )
        results.extend(repair_results)
        due.extend(repair_due)
        ordered = fair_due_order(due)
        selected = ordered[:max_lanes_per_tick]
        for index, candidate in enumerate(selected):
            if stop is not None and stop.requested:
                results.extend(_deferred_shutdown_result(deferred) for deferred in selected[index:])
                break
            try:
                results.append(await _execute_due_lane(session, candidate, stop=stop))
            except Exception as error:  # isolate lane-local faults only while the pinned backend is intact
                await session.rollback()
                if isinstance(error, SQLAlchemyError) or _pinned_connection_invalidated(session):
                    logger.error(
                        "plantgeo_job_executor_pinned_connection_lost",
                        lane_id=candidate.spec.lane_id,
                        error_type=type(error).__name__,
                    )
                    raise
                await apply_statement_timeout(session)
                logger.error(
                    "plantgeo_job_executor_lane_failed",
                    lane_id=candidate.spec.lane_id,
                    error_type=type(error).__name__,
                )
                results.append(
                    LaneTickResult(
                        lane_id=candidate.spec.lane_id,
                        state="failed",
                        scheduled_for=candidate.scheduled_for,
                        run_id=candidate.existing_run_id,
                        detail=f"scheduler lane failed ({type(error).__name__})",
                    )
                )
        for candidate in ordered[max_lanes_per_tick:]:
            results.append(
                LaneTickResult(
                    lane_id=candidate.spec.lane_id,
                    state="deferred_fairness",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="due; another work class received this bounded tick's turn",
                )
            )
        return ExecutorTickSummary(
            observed_at=now,
            leader=True,
            lanes=tuple(sorted(results, key=lambda result: result.lane_id)),
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        unlock_error: BaseException | None = None
        try:
            await _rollback_planning_transaction(session)
            await _release_leader_lock(session)
            await session.rollback()
        except BaseException as error:
            unlock_error = error
            logger.error(
                "plantgeo_job_executor_leader_unlock_failed",
                error_type=type(error).__name__,
                primary_error_type=None if primary_error is None else type(primary_error).__name__,
            )
            await _invalidate_leader_connection(session)
        if unlock_error is not None and primary_error is None:
            raise unlock_error


async def _stop_process(
    process: asyncio.subprocess.Process,
    wait_task: asyncio.Task[int],
) -> None:
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=COMMAND_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        try:
            await asyncio.wait_for(asyncio.shield(wait_task), timeout=COMMAND_KILL_WAIT_SECONDS)
        except TimeoutError:
            logger.error("plantgeo_job_executor_subprocess_reap_timeout")
            wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await wait_task


CommandMonitorState = Literal["exited", "shutdown", "fence_lost", "timeout"]


async def _monitor_subprocess(
    process: asyncio.subprocess.Process,
    invocation: JobInvocation,
    *,
    timeout: float,
) -> tuple[CommandMonitorState, int | None]:
    """React to exit, shutdown, fence loss, and timeout without serial waits."""
    wait_task = asyncio.create_task(process.wait())
    deadline = time.monotonic() + timeout
    next_heartbeat = time.monotonic() + COMMAND_HEARTBEAT_SECONDS
    try:
        while True:
            if invocation.shutdown_requested():
                await _stop_process(process, wait_task)
                return "shutdown", process.returncode
            now = time.monotonic()
            if now >= deadline:
                await _stop_process(process, wait_task)
                return "timeout", process.returncode
            wait_seconds = min(0.25, deadline - now, max(next_heartbeat - now, 0.0))
            done, _ = await asyncio.wait((wait_task,), timeout=wait_seconds)
            if done:
                return "exited", wait_task.result()
            now = time.monotonic()
            if now >= next_heartbeat:
                if not await invocation.heartbeat():
                    await _stop_process(process, wait_task)
                    return "fence_lost", process.returncode
                next_heartbeat = time.monotonic() + COMMAND_HEARTBEAT_SECONDS
    except BaseException:
        await _stop_process(process, wait_task)
        raise


def _write_through(stream: object, chunk: bytes) -> None:
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(chunk)
        buffer.flush()
        return
    stream.write(chunk.decode("utf-8", errors="replace"))  # type: ignore[attr-defined]
    stream.flush()  # type: ignore[attr-defined]


def _default_stderr_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stderr on this process's stderr, so the Railway log stream still carries it."""
    _write_through(sys.stderr, chunk)


def _default_stdout_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stdout on this process's stdout: the log stream keeps the JSON report."""
    _write_through(sys.stdout, chunk)


class CommandOutputTail:
    """Tee one of a child's output streams through to this process while keeping only its bounded TAIL.

    stderr: before this the child inherited it outright, its traceback reached the log stream and nothing
    else, and `agri.job_attempt.last_error_summary` read only `command exited with status 1`. The tail is
    what `run_scheduled_command` folds into every failure reason; `jobs.lease.fail_work_item` then redacts
    and clamps it like any other summary, so a secret printed by a child still never reaches the ledger.

    stdout: the one terminal JSON report a writer prints last is parsed out of the tail at exit 0, so an
    `outcome=incomplete` turn is persisted rather than lost with the stream.
    """

    def __init__(
        self,
        *,
        limit: int = COMMAND_STDERR_TAIL_BYTES,
        sink: Callable[[bytes], None] | None = None,
    ) -> None:
        self._limit = limit
        self._sink = _default_stderr_sink if sink is None else sink
        self._tail = bytearray()
        self.bytes_seen = 0

    @property
    def tail(self) -> bytes:
        """The bytes kept, at most `limit` of them and always the newest."""
        return bytes(self._tail)

    @property
    def truncated(self) -> bool:
        """True when the child wrote more than the tail holds, so the summary is the END of its output."""
        return self.bytes_seen > len(self._tail)

    def feed(self, chunk: bytes) -> None:
        """Forward one chunk to the sink and fold it into the bounded tail."""
        if not chunk:
            return
        self.bytes_seen += len(chunk)
        self._sink(chunk)
        self._tail.extend(chunk)
        if len(self._tail) > self._limit:
            del self._tail[: len(self._tail) - self._limit]

    def summary(self, *, max_chars: int = COMMAND_STDERR_SUMMARY_CHARS) -> str | None:
        """One line: the tail's non-blank lines joined with ` | `, cut from the FRONT so the last line survives."""
        text = self._tail.decode("utf-8", errors="replace")
        lines = (" ".join(line.split()) for line in text.splitlines())
        joined = " | ".join(line for line in lines if line)
        if not joined:
            return None
        if len(joined) <= max_chars:
            return joined
        return "..." + joined[-(max_chars - 3) :]

    def metrics(self) -> dict[str, object]:
        """Counts only, never content: `metrics` is stored unredacted, so the tail itself goes through `reason`."""
        return {"stderr_bytes": self.bytes_seen, "stderr_truncated": self.truncated}


#: The stderr-flavoured name the first tests were written against; one class serves both streams.
CommandStderrTail = CommandOutputTail


async def _drain_stream(stream: asyncio.StreamReader, tail: CommandOutputTail) -> None:
    """Read one child stream to EOF; running concurrently keeps a chatty child from blocking on a full pipe."""
    while True:
        chunk = await stream.read(COMMAND_STDERR_READ_BYTES)
        if not chunk:
            return
        tail.feed(chunk)


async def _drain_both(
    process: asyncio.subprocess.Process, *, stdout: CommandOutputTail, stderr: CommandOutputTail
) -> None:
    if process.stdout is None or process.stderr is None:  # pragma: no cover - PIPE always yields readers
        raise RuntimeError("the child's output pipes were not created")
    await asyncio.gather(_drain_stream(process.stdout, stdout), _drain_stream(process.stderr, stderr))


async def _finish_drain(drain: asyncio.Task[None]) -> None:
    """Wait a bounded moment for EOF after exit or kill; a grandchild holding the pipe must not hold the attempt."""
    try:
        await asyncio.wait_for(asyncio.shield(drain), timeout=COMMAND_STDERR_DRAIN_SECONDS)
    except TimeoutError:
        drain.cancel()
        with suppress(asyncio.CancelledError):
            await drain
    except Exception:  # a broken pipe reader must not mask the child's own exit status
        logger.exception("plantgeo_job_executor_stderr_drain_failed")


def _command_failure_reason(headline: str, tail: CommandOutputTail) -> str:
    """Attach the bounded stderr tail to a failure headline; the ledger's own clamp bounds the whole."""
    summary = tail.summary()
    if summary is None:
        return f"{headline}; stderr: nothing captured"
    marker = " tail" if tail.truncated else ""
    return f"{headline}; stderr{marker}: {summary}"


def _resolve_command(spec: LaneExecutionSpec, invocation: JobInvocation) -> tuple[str, ...]:
    """Return the exact argv this work item runs: the lane's own command, plus a repair's bounded knobs.

    A repair item stores its REQUEST, never a command: the argv is rebuilt here from the registered spec and
    a payload `RepairRequest.from_payload` has already refused to accept out of bounds, so a stored payload
    cannot smuggle an argument the writer's contract does not expose.
    """
    if spec.command is None:
        raise RepairRequestError(f"lane {spec.lane_id!r} has no executor command")
    if invocation.kind == EXECUTOR_WORK_ITEM_KIND:
        return spec.command
    request = RepairRequest.from_payload(invocation.payload)
    if request.lane_id != spec.lane_id:
        raise RepairRequestError(f"repair request names lane {request.lane_id!r}, work item names {spec.lane_id!r}")
    return (*spec.command, *request.command_arguments())


@job_handler(EXECUTOR_HANDLER_TOKEN)
async def run_scheduled_command(  # noqa: PLR0911, PLR0912 - each terminal state maps to a ledger outcome
    invocation: JobInvocation,
) -> JobHandlerOutcome:
    """Execute one registry-bound command under the outer work item's fence."""
    if invocation.kind not in EXECUTOR_WORK_ITEM_KINDS:
        return JobHandlerOutcome.failed("unknown_work_item_kind", f"unexpected kind {invocation.kind!r}")
    lane_id = invocation.payload.get("lane_id")
    if not isinstance(lane_id, str) or lane_id not in LANE_SPECS:
        return JobHandlerOutcome.failed("unknown_executor_lane", "work item names no registered executor lane")
    spec = LANE_SPECS[lane_id]
    try:
        activation = parse_activation()
    except ExecutorConfigurationError as error:
        return JobHandlerOutcome.failed("invalid_ownership_activation", str(error))
    if not activation.is_active(lane_id):
        return JobHandlerOutcome.failed(
            "ownership_activation_removed",
            f"lane {lane_id!r} is no longer explicitly activated",
        )
    if spec.command is None:
        return JobHandlerOutcome.failed("source_specific_lane", f"lane {lane_id!r} has no executor command")
    try:
        command = _resolve_command(spec, invocation)
    except RepairRequestError as error:
        return JobHandlerOutcome.failed("invalid_repair_request", f"lane {lane_id!r}: {error}")

    scheduled_for = invocation.payload.get("scheduled_for")
    if invocation.cursor is None:
        return JobHandlerOutcome.progressed(
            {
                "state": "ready",
                "scheduled_for": scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key,
            },
            progress_fraction=0.01,
            metrics={"command_started": False},
        )
    if invocation.cursor.get("state") != "ready":
        return JobHandlerOutcome.failed(
            "invalid_executor_checkpoint",
            f"lane {lane_id!r} cannot resume from its stored command checkpoint",
        )

    timeout = min(
        float(spec.command_timeout_seconds),
        max(invocation.seconds_remaining - COMMAND_TIMEOUT_RESERVE_SECONDS, 0.0),
    )
    if timeout <= 0:
        return JobHandlerOutcome.yielded(reason="no command budget remains in this scheduler slice")

    tail = CommandOutputTail()
    stdout = CommandOutputTail(limit=COMMAND_STDOUT_TAIL_BYTES, sink=_default_stdout_sink)
    # Both streams are piped and teed back through this process chunk by chunk, so the Railway log stream
    # carries exactly what it did before. stderr's tail is folded into the failure reason; stdout's tail is
    # where the writer's one terminal JSON report is parsed from, whatever the exit status.
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    drain = asyncio.create_task(_drain_both(process, stdout=stdout, stderr=tail))
    started = time.monotonic()
    try:
        monitor_state, return_code = await _monitor_subprocess(process, invocation, timeout=timeout)
    finally:
        await _finish_drain(drain)
    elapsed = round(time.monotonic() - started, 3)
    # Keyed by the DEFINITION that ran, not the owning lane: a `--max-days 5` repair turn is legitimately
    # partial and must not count against the hourly lane's incomplete-bucket streak.
    report_key = lane_id if invocation.kind == EXECUTOR_WORK_ITEM_KIND else f"{lane_id}{REPAIR_LANE_SUFFIX}"
    turn_report = record_turn_report(report_key, parse_terminal_report(stdout.tail))
    metrics: dict[str, object] = {
        "elapsed_seconds": elapsed,
        **tail.metrics(),
        "days_unwritten": None if turn_report is None else turn_report.days_unwritten,
    }
    if monitor_state == "shutdown":
        return JobHandlerOutcome.yielded(
            cursor=invocation.cursor,
            progress_fraction=invocation.progress_fraction,
            reason=f"lane {lane_id!r} stopped for service shutdown before command completion",
            metrics=metrics,
        )
    if monitor_state == "fence_lost":
        return JobHandlerOutcome.failed(
            "executor_lease_lost",
            _command_failure_reason(f"lane {lane_id!r} lost its fenced lease while the command was running", tail),
            metrics=metrics,
        )
    if monitor_state == "timeout":
        return JobHandlerOutcome.failed(
            "scheduled_command_timeout",
            _command_failure_reason(f"lane {lane_id!r} exceeded its {int(timeout)} second command budget", tail),
            metrics=metrics,
        )
    if return_code is None:  # pragma: no cover - exited always carries Process.wait's integer
        return JobHandlerOutcome.failed("scheduled_command_exit", f"lane {lane_id!r} returned no exit status")
    if return_code != 0:
        return JobHandlerOutcome.failed(
            "scheduled_command_exit",
            _command_failure_reason(f"lane {lane_id!r} command exited with status {return_code}", tail),
            metrics={**metrics, "exit_code": return_code},
        )
    cursor = {
        "state": "completed",
        "scheduled_for": scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key,
        "completed_at": datetime.now(UTC).isoformat(),
        # The checkpoint row is where an exit-0-but-incomplete turn survives the log stream's retention.
        "turn_report": None if turn_report is None else turn_report.to_dict(),
    }
    return JobHandlerOutcome.completed(
        cursor=cursor,
        metrics={**metrics, "exit_code": return_code},
    )


def executor_inventory(activation: ActivationConfig) -> dict[str, object]:
    return {
        "event": "plantgeo_job_executor_inventory",
        "mode": "active" if activation.active_lanes else "shadow",
        "activation_variables": [ACTIVE_LANES_VARIABLE],
        "lanes": [spec.inventory_row(active=activation.is_active(spec.lane_id)) for spec in LANE_SPECS.values()],
    }


async def _wait_for_shutdown(stop: ShutdownSignal, delay_seconds: float) -> bool:
    """Wait for either the next service tick or an event-backed shutdown request."""
    if stop.requested:
        return True
    try:
        await asyncio.wait_for(stop.wait_requested(), timeout=delay_seconds)
    except TimeoutError:
        return False
    return True


def announce_operator_actions(summary: ExecutorTickSummary, announced: set[tuple[str, str | None]]) -> None:
    """Log each held lane's release command ONCE per process while it holds, and once more when it clears.

    The tick already printed the same command inside `blockers` every thirty seconds for a week with nothing
    consuming it; a flood is as invisible as silence. One `error`-severity event per held run, at top level
    with the exact verb to run, is what a log-based alert or a human skim can actually see. `announced` is
    the caller's per-process memory; a restart re-announces, which is the right side to err on.
    """
    if not summary.leader:
        # A follower sees no lanes at all; treating that as "cleared" would re-announce on every leadership flip.
        return
    current = {
        (action.lane_id, None if action.run_id is None else str(action.run_id)): action
        for action in summary.operator_actions
    }
    for key, action in current.items():
        if key in announced:
            continue
        announced.add(key)
        logger.error(
            "plantgeo_job_executor_operator_action_required",
            lane_id=action.lane_id,
            run_id=key[1],
            command=action.command,
            detail="the clock no longer releases this lane; nothing runs on it until this command is recorded",
        )
    for key in [key for key in announced if key not in current]:
        announced.discard(key)
        logger.info("plantgeo_job_executor_operator_action_cleared", lane_id=key[0], run_id=key[1])


async def _service_loop(
    *,
    activation: ActivationConfig,
    poll_seconds: float,
    max_lanes_per_tick: int,
    once: bool,
) -> int:
    failures = 0
    announced: set[tuple[str, str | None]] = set()
    breaker_release = ProcessStartRelease.from_environment(now=datetime.now(UTC))
    repair_clock = RepairAuthoringClock.from_environment()
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_pool(database_url) as loader_pool, shutdown_signal() as stop:
        while not stop.requested:
            try:
                async with (
                    loader_pool.connect() as tick_connection,
                    AsyncSession(
                        bind=tick_connection,
                        expire_on_commit=False,
                    ) as session,
                ):
                    summary = await run_executor_tick(
                        session,
                        activation=activation,
                        now=datetime.now(UTC),
                        max_lanes_per_tick=max_lanes_per_tick,
                        stop=stop,
                        breaker_release=breaker_release,
                        repair_clock=repair_clock,
                    )
                click.echo(json.dumps(summary.to_dict(), sort_keys=True))
                announce_operator_actions(summary, announced)
                for lane in summary.incomplete_lanes:
                    if lane.turn_report is not None:
                        logger.warning(
                            "plantgeo_job_executor_lane_incomplete",
                            lane_id=lane.lane_id,
                            days_unwritten=lane.turn_report.days_unwritten,
                            consecutive_incomplete_buckets=lane.turn_report.consecutive_incomplete_buckets,
                            unwritten=[dict(entry) for entry in lane.turn_report.unwritten],
                        )
                if summary.failed:
                    logger.error(
                        "plantgeo_job_executor_tick_unhealthy",
                        failing_lanes=[lane.lane_id for lane in summary.lanes if lane.state == "failed"],
                        incomplete_lanes=[lane.lane_id for lane in summary.incomplete_lanes],
                        operator_actions=[action.command for action in summary.operator_actions],
                    )
                else:
                    logger.info(
                        "plantgeo_job_executor_tick_healthy",
                        leader=summary.leader,
                        lane_count=len(summary.lanes),
                        incomplete_lanes=[lane.lane_id for lane in summary.incomplete_lanes],
                    )
                failures = 0
                if once:
                    return 1 if summary.failed else 0
                if await _wait_for_shutdown(stop, poll_seconds):
                    break
            except Exception as error:
                failures += 1
                delay = min(poll_seconds * (2 ** (failures - 1)), MAX_LOOP_BACKOFF_SECONDS)
                logger.error(
                    "plantgeo_job_executor_tick_failed",
                    error_type=type(error).__name__,
                    consecutive_failures=failures,
                    retry_seconds=delay,
                )
                if once:
                    return 1
                if await _wait_for_shutdown(stop, delay):
                    break
    return 0


def _environment_float(name: str, fallback: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError as error:
        raise ExecutorConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ExecutorConfigurationError(f"{name} must be positive")
    return value


def _environment_int(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = int(raw)
    except ValueError as error:
        raise ExecutorConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ExecutorConfigurationError(f"{name} must be positive")
    return value


@click.command("jobs-executor")
@click.option("--once", is_flag=True, help="Run one leader-elected scheduler tick and exit.")
@click.option("--inventory", "inventory_only", is_flag=True, help="Print the code-owned lane inventory and exit.")
def jobs_executor(once: bool, inventory_only: bool) -> None:
    """Run the single continuous PlantGeo ingestion and Parquet job service."""
    try:
        activation = parse_activation()
        inventory = executor_inventory(activation)
        click.echo(json.dumps(inventory, sort_keys=True))
        if inventory_only:
            return
        poll_seconds = _environment_float(POLL_SECONDS_VARIABLE, DEFAULT_POLL_SECONDS)
        max_lanes = _environment_int(MAX_LANES_PER_TICK_VARIABLE, DEFAULT_MAX_LANES_PER_TICK)
        if max_lanes < MIN_LANES_PER_TICK:
            raise ExecutorConfigurationError(
                f"{MAX_LANES_PER_TICK_VARIABLE} must be at least {MIN_LANES_PER_TICK} to preserve class fairness"
            )
        exit_code = asyncio.run(
            _service_loop(
                activation=activation,
                poll_seconds=poll_seconds,
                max_lanes_per_tick=max_lanes,
                once=once,
            )
        )
    except ExecutorConfigurationError as error:
        raise click.ClickException(str(error)) from error
    if exit_code:
        raise click.exceptions.Exit(exit_code)


__all__ = [
    "ACTIVE_LANES_VARIABLE",
    "CLOCK_RELEASE_STREAK_LIMIT",
    "COMMAND_STDERR_SUMMARY_CHARS",
    "COMMAND_STDERR_TAIL_BYTES",
    "EXECUTOR_DEFINITION_PREFIX",
    "EXECUTOR_DEFINITION_VERSION",
    "EXECUTOR_WORK_ITEM_KINDS",
    "FAILURE_STREAK_PROBE_LIMIT",
    "LANE_SPECS",
    "OPERATOR_SUPERSESSION_BLOCKER_PREFIX",
    "RUN_SUPERSESSION_FINGERPRINT_PREFIX",
    "RUN_SUPERSESSION_INCIDENT_TYPE",
    "SETTLED_WITHOUT_SUCCESS",
    "SUPERSEDE_RUN_COMMAND",
    "ActivationConfig",
    "CheckpointVerdict",
    "CommandOutputTail",
    "CommandStderrTail",
    "DueLane",
    "ExecutorConfigurationError",
    "ExecutorLeaderUnlockError",
    "ExecutorTickSummary",
    "LaneExecutionSpec",
    "LaneTickResult",
    "LatestRun",
    "OperatorAction",
    "ProcessStartRelease",
    "RepairAuthoringClock",
    "TurnReport",
    "announce_operator_actions",
    "bucket_after",
    "ensure_lane_definition",
    "executor_inventory",
    "fair_due_order",
    "jobs_executor",
    "judge_failed_checkpoint",
    "next_scheduled_bucket",
    "parse_activation",
    "parse_terminal_report",
    "read_lane_checkpoint",
    "record_turn_report",
    "repair_lane_spec",
    "run_executor_tick",
    "run_scheduled_command",
    "scheduled_bucket",
    "summarize_turn_report",
    "supersession_command",
]
