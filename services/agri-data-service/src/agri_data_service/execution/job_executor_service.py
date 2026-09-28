"""The single stateful scheduler for PlantGeo ingestion and Parquet publication work.

The declarative lane table and cadence-bucket/failed-checkpoint math have their own modules
(`lane_specs.py`, `lane_scheduling.py`); the per-tick result containers live in `turn_reports.py`. This
module re-exports their public names so no importer of `job_executor_service` changes, and keeps the
turn-report cache, subprocess command execution, leader-lock/definition-registration/tick-planning and
service-loop orchestration together: `tests/execution/test_command_stderr_capture.py` monkeypatches
`LANE_SPECS`, `parse_activation` and `_LANE_TURN_REPORTS` as one unit on this module, so
`run_scheduled_command` and the cache it folds into must keep reading those same module globals.
`_default_stderr_sink`/`_default_stdout_sink` still exist (the `CommandOutputTail` class default and
a few standalone callers), but since o5a (Wave O, GL-3) `run_scheduled_command` itself no longer wires
them directly -- a per-attempt `ChildLogRouter` sits in front, and ITS OWN sinks
(`foundation.observability.router::_default_stdout_sink`/`_default_stderr_sink`) are what a test now
patches to see the routed mirror. See execution/AGENTS.md, "Command stderr reaches the ledger".
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import sys
import time
import uuid
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import click
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_pool
from agri_data_service.db.maintenance import MaintenanceBusyError, maintain_job_event_partitions
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.execution.exit_classes import classify_exit
from agri_data_service.execution.gap_repair_contract import (
    EXECUTOR_REPAIR_WORK_ITEM_KIND,
    REPAIR_LANE_IDS,
    REPAIR_LANE_SUFFIX,
    RepairRequest,
    RepairRequestError,
)
from agri_data_service.execution.lane_catalogue import (
    STOPPED_REASON,
    LaneCatalogue,
    current_lane_catalogue,
    owning_lane_id,
)
from agri_data_service.execution.lane_ids import (
    BURN_SEVERITY_DIRECT_LANE_ID,
    DROUGHT_DIRECT_LANE_ID,
    EVACUATION_ZONES_DIRECT_LANE_ID,
    FIRE_DETECTIONS_DIRECT_LANE_ID,
    FIRE_PERIMETERS_DIRECT_LANE_ID,
)
from agri_data_service.execution.lane_incidents import (
    CHAIN_WINDOW,
    CLASS_SOURCE_MISSING_ATTEMPT,
    CODE_PROBE_HOURS_VARIABLE,
    FLAPPING_EPISODE_THRESHOLD,
    INCIDENT_SEVERITY,
    PROBATION_CLEAN_BUCKETS,
    PROBE_HISTORY_MAX,
    REPAIR_BREAKER_TRIP_THRESHOLD,
    REPAIR_BREAKER_TRIPPING_CLASSES,
    FinalAttempt,
    HoldLadders,
    HoldProgress,
    HoldState,
    HoldVerdict,
    LaneIncidentRow,
    ProbeOutcome,
    ReconcileOutcome,
    RepairBreakerState,
    UpsertedIncident,
    after_probe,
    evaluate_repair_breaker,
    final_attempt_exit_class,
    is_chain_chronic,
    is_chain_flapping,
    is_conclusive,
    judge_probation,
    judge_probe,
    parse_code_probe_hours,
    probe_is_due,
    reconcile,
    repair_breaker_admits,
    repair_breaker_cooldown_days,
    resolve_lane_incident,
    select_lane_incidents,
    select_run_final_attempt,
    upsert_lane_incident,
    watch_max_attempts,
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
    CONFIG_EXECUTOR,
    EXECUTOR_DEFINITION_PREFIX,
    EXECUTOR_DEFINITION_VERSION,
    EXECUTOR_HANDLER_TOKEN,
    EXECUTOR_PATH_PAYLOAD_KEY,
    EXECUTOR_REQUESTED_BY,
    EXECUTOR_WORK_ITEM_KIND,
    FAILURE_STREAK_PROBE_LIMIT,
    LANE_SPECS,
    STOPPED_LANES_VARIABLE,
    TURN_MODE_PAYLOAD_KEY,
    ActivationConfig,
    ExecutorConfigurationError,
    LaneExecutionSpec,
    parse_activation,
)
from agri_data_service.execution.provider_budget import (
    BUDGET_BASIS_SUSPECT_KIND,
    BUDGET_DEFERRED_KIND,
    BUDGET_INCIDENT_KINDS,
    EVENT_BUDGET_BASIS_SUSPECT,
    AdmissionPass,
    BudgetAdmissionState,
    BudgetIncidentKind,
    PoolAdmission,
    admit_due_lanes,
    same_utc_month,
)
from agri_data_service.execution.turn_reports import (
    ExecutorTickSummary,
    LaneTickResult,
    LaneTickState,
    OperatorAction,
)
from agri_data_service.execution.usage_receipt import UsageReceiptStore, write_closed_month_receipt
from agri_data_service.foundation.observability import events, redaction
from agri_data_service.foundation.observability.router import ChildLogRouter
from agri_data_service.foundation.observability.vocabulary import (
    INCIDENT_KINDS,
    LANE_LOGICAL_CAPS,
    ExitClass,
    IncidentKind,
    LogLevel,
    TurnOutcome,
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
    release_definition_lock,
    release_provider_lock,
    required_column,
    try_definition_lock,
    try_provider_lock,
)
from agri_data_service.models.jobs import EventSeverity, IncidentState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator, Mapping
    from collections.abc import Set as AbstractSet
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncEngine

    from agri_data_service.execution.lane_specs import LaneWorkClass
    from agri_data_service.foundation.observability.router import Stream

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
#: The hold ladder's own supersession operator. Every operator the executor records under starts with
#: `EXECUTOR_OPERATOR_PREFIX`, which `select_latest_run.sql` reads to keep such a marker from resetting the
#: failure streak (F4): only a PERSON's release earns a fresh streak. See execution/AGENTS.md, "Holds and probes".
PROBE_OPERATOR: Final = "executor:probe"
EXECUTOR_OPERATOR_PREFIX: Final = "executor:"

# --- S16 runtime switches (spec S15/S16; execution/AGENTS.md, "Work queue") ---------------------------
#: `queue` (unset) drives each due lane on its own session without the tick awaiting it; `serial` is HEAD's
#: in-tick await. A garbled value is `serial`, the known-good behaviour.
DISPATCH_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_DISPATCH"
#: `split` (unset) is the hold ladder and the marker-aware streak; `legacy` turns both off and keeps GL-5's
#: operator-only hold (today's production). `PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE=off` is its own switch: with
#: `legacy` it is pre-Wave-O HEAD planning (review PH2). A garbled value is `legacy`.
BREAKER_MODE_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_BREAKER_MODE"
#: How many lanes the work queue runs at once. 1 at G1; raising it is part of G6 (spec S15).
MAX_CONCURRENT_LANES_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_MAX_CONCURRENT_LANES"
DEFAULT_MAX_CONCURRENT_LANES: Final = 1
#: How long a stopping service loop waits for its in-flight lanes to park before cancelling them.
LANE_DRAIN_SECONDS: Final = 60.0
DispatchMode = Literal["queue", "serial"]
BreakerMode = Literal["split", "legacy"]
_DISPATCH_MODES: Final[frozenset[str]] = frozenset({"queue", "serial"})
_BREAKER_MODES: Final[frozenset[str]] = frozenset({"split", "legacy"})

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

# --- Turn context (spec Sec 4.9.1/4.9.2; design record Sec 1.2) -----------------------------------
#: The environment `run_scheduled_command` passes to every spawned child, so `foundation.observability.
#: bootstrap.arm_from_environment` arms it as an executor child (`PLANTGEO_TURN_ID` is the trigger).
#: The executor's OWN router is handed the same values directly (`_TurnLogRouter`), never through
#: this process's environment -- see execution/AGENTS.md, "Command stderr reaches the ledger".
TURN_ID_ENV_VAR: Final = "PLANTGEO_TURN_ID"
TURN_MODE_ENV_VAR: Final = "PLANTGEO_TURN_MODE"
TURN_BUCKET_ENV_VAR: Final = "PLANTGEO_TURN_BUCKET"
#: `1` only from GL-6 (a probe attempt); this wave never sets it, but the child always sees the key.
TURN_PROBE_ENV_VAR: Final = "PLANTGEO_TURN_PROBE"
LANE_ID_ENV_VAR: Final = "PLANTGEO_LANE_ID"
ATTEMPT_ENV_VAR: Final = "PLANTGEO_ATTEMPT"
#: `CHARGE_BASIS=metered` is the spec Sec 4.9.2 default "until P5 answers"; `logical` is the one other
#: recognised value. Anything else reads as the default -- the same fail-safe rule every Wave O switch
#: follows (`bootstrap.py::parse_switch`'s docstring), even though this is not itself an on/off switch.
CHARGE_BASIS_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_CHARGE_BASIS"
#: The bootstrap `ready` checkpoint's wall-clock stamp; `start_lag_seconds` is spawn time minus this.
READY_AT_CURSOR_KEY: Final = "ready_at_epoch"
#: Two hosts' clocks may disagree by this much before a negative lag reads as unknown, not zero.
START_LAG_CLOCK_SKEW_SECONDS: Final = 60.0
#: A `ready` checkpoint older than this is a stale resumed cursor, not head-of-line delay.
START_LAG_MAX_SECONDS: Final = 7 * 24 * 3600.0
#: Bound on the redacted stderr tail a non-`ok` `lane_turn` line carries (the ledger keeps the full tail).
LANE_TURN_STDERR_SUMMARY_CHARS: Final = 1024
#: Whether the turn `_execute_due_lane` is driving is a hold-ladder probe: the planner's flag, threaded into
#: the handler through the task that runs it (a `ContextVar` is per task, so concurrent lanes never share it).
_PROBE_TURN: ContextVar[bool] = ContextVar("plantgeo_job_executor_probe_turn", default=False)


class ExecutorLeaderUnlockError(RuntimeError):
    """Raised when the pinned PostgreSQL backend cannot confirm leader-lock release."""


_TRY_LEADER_LOCK: Final = text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0)) AS acquired")
_RELEASE_LEADER_LOCK: Final = text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0)) AS released")
_SELECT_DEFINITION_STATE: Final = text(load_query_sql("execution/select_definition_state.sql"))
_INSERT_DEFINITION: Final = text(load_query_sql("execution/insert_definition.sql"))
_UPDATE_DEFINITION_SHAPE: Final = text(load_query_sql("execution/update_definition_shape.sql"))
#: Definitions whose stored shape this process already brought in line with its spec (review M1).
_DEFINITION_SHAPES_SYNCED: set[str] = set()
_SELECT_LATEST_RUN: Final = text(load_query_sql("execution/select_latest_run.sql"))


@dataclass(frozen=True, slots=True)
class TurnReport:
    """The bounded facts kept from a writer's terminal stdout report: what did the turn leave owed?

    TWO independent kinds of owed work, because an object write and a serving publication are
    different facts: a day that never wrote (`days_unwritten`), and a day whose objects landed while
    its availability never extended (`publication_debt`). The second is the quieter one -- the lane
    writes everything it selected, exits 0, and is not serving what it wrote.

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
    #: Owed availability-publication work this turn reported, summed over `PUBLICATION_DEBT_COUNTERS`.
    #: A day whose objects landed but whose availability never extended is NOT serving, so a turn can
    #: write every day it selected, exit 0, and still owe publication. See AGENTS.md, "Turn reports".
    publication_debt: int = 0
    #: Which debt counters were non-zero, so an operator reads WHICH duty is owed, not just that one is.
    publication_debt_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def incomplete(self) -> bool:
        """An unwritten day OR standing publication debt: an object write is not a serving publication."""
        return self.days_unwritten > 0 or self.publication_debt > 0

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "days_unwritten": self.days_unwritten,
            "unwritten": [dict(entry) for entry in self.unwritten],
            "unwritten_truncated": self.unwritten_truncated,
            "consecutive_incomplete_buckets": self.consecutive_incomplete_buckets,
            "publication_debt": self.publication_debt,
            "publication_debt_counts": dict(self.publication_debt_counts),
        }


#: The `AvailabilityExtensionTally.to_summary()` keys that name OWED work, as opposed to the two that
#: name settled work (`availability_extended`, `availability_skipped_unchanged`). Spelled out rather
#: than derived by subtraction so a new settled counter cannot silently read as debt here; the field
#: names are `pipeline/parquet/availability_extension.py::_TALLY_FIELDS`.
PUBLICATION_DEBT_COUNTERS: Final = (
    "availability_not_bootstrapped",
    "availability_ladder_incomplete",
    "availability_retry_owed",
    "availability_retry_claim_failed",
    "availability_quarantined_standing",
    "availability_reindex_owed",
)


def _publication_debt_counts(report: Mapping[str, object]) -> dict[str, int]:
    """Read the non-zero owed-work counters off one terminal report, ignoring anything malformed.

    Folded over nested per-product `results[]` the same way `_unwritten_entries` is: a multi-product
    writer reports its tally per product, and debt owed by ONE product is debt owed by the turn.
    """
    counts: dict[str, int] = {}
    for name in PUBLICATION_DEBT_COUNTERS:
        value = report.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            counts[name] = value
    results = report.get("results")
    if isinstance(results, list):
        for product in results:
            if isinstance(product, dict):
                for name, value in _publication_debt_counts(product).items():
                    counts[name] = counts.get(name, 0) + value
    return counts


#: Per owning lane, the newest turn report this process ran and its incomplete-bucket streak.
_LANE_TURN_REPORTS: dict[str, TurnReport] = {}

#: Per config definition with no checkpoint yet, the instant THIS process first planned it. Process-held:
#: a restart only makes a never-run lane wait for one more fire, the conservative direction.
_NEVER_RUN_FIRST_SEEN: dict[str, datetime] = {}


def parse_terminal_report(stdout_tail: bytes) -> Mapping[str, object] | None:
    """The one terminal report a turn's stdout carries (design Sec 1.6 point 8, o5a).

    Prefers the LAST `plantgeo_lane_turn_report` line (the future runner's own event name); absent
    that -- every `pipeline/direct/*` writer today -- falls back to the last JSON object carrying no
    `level` key at all. A usage line (`plantgeo_turn_usage[_open]`) always carries a `level`, so this
    never mistakes one for the report even though both can share the same stdout stream.
    """
    fallback: Mapping[str, object] | None = None
    for raw in reversed(stdout_tail.decode("utf-8", errors="replace").splitlines()):
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, RecursionError):  # an over-deep line is not the report
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("event") == events.EVENT_LANE_TURN_REPORT:
            return parsed
        if fallback is None and "level" not in parsed:
            fallback = parsed
    return fallback


def _unwritten_entries(report: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Collect `unwritten` entries from the report and from any per-product `results` it fans out into.

    Reads both shapes (S16): a legacy direct writer's `{day, outcome, detail}` and the runner's S5
    `{day, reason, ...}`, where `reason` is the S5 enum. Either way the kept entry's `outcome` is the word
    an operator reads; an S5 entry also keeps its `reason`. See execution/AGENTS.md, "Turn reports".
    """
    found: list[Mapping[str, object]] = []
    own = report.get("unwritten")
    if isinstance(own, list):
        found.extend(_normalised_unwritten(entry) for entry in own if isinstance(entry, dict))
    results = report.get("results")
    if isinstance(results, list):
        for product in results:
            if isinstance(product, dict):
                found.extend(_unwritten_entries(product))
    return found


def _normalised_unwritten(entry: Mapping[str, object]) -> Mapping[str, object]:
    """One unwritten entry in the legacy shape, the S5 `reason` standing in for a missing `outcome`."""
    if "outcome" in entry or "reason" not in entry:
        return entry
    return {**entry, "outcome": entry["reason"]}


def _counted_unwritten(entries: list[Mapping[str, object]]) -> int:
    """How many entries the incomplete alarm counts: S5 counts only days behind the provider edge.

    The runner states `days_unwritten` itself; this is the fallback when it does not, and an entry the
    runner marks `behind_edge: false` (a day newer than the edge, still unsettled by design) is not owed.
    """
    return sum(1 for entry in entries if entry.get("behind_edge") is not False)


def summarize_turn_report(report: Mapping[str, object] | None, *, previous: TurnReport | None) -> TurnReport | None:
    """Bound one parsed report to what a checkpoint may hold, continuing the lane's incomplete-bucket streak."""
    if report is None:
        return None
    entries = _unwritten_entries(report)
    declared = report.get("days_unwritten")
    days_unwritten = (
        declared if isinstance(declared, int) and not isinstance(declared, bool) else _counted_unwritten(entries)
    )
    # Redacted HERE because the cursor path (`record_checkpoint`) canonicalises but never redacts; a child
    # that echoed a keyed URL into a day's detail must not put it on a durable row.
    kept = tuple(
        {
            "day": entry.get("day"),
            "outcome": entry.get("outcome"),
            "detail": redact_text(str(entry.get("detail", "")))[:TURN_REPORT_DETAIL_CHARS],
            **({"reason": entry["reason"]} if isinstance(entry.get("reason"), str) else {}),
            **({"stream": entry["stream"]} if isinstance(entry.get("stream"), str) else {}),
        }
        for entry in entries[:TURN_REPORT_UNWRITTEN_MAX]
    )
    outcome = report.get("outcome", report.get("status"))
    debt_counts = _publication_debt_counts(report)
    debt = sum(debt_counts.values())
    previous_streak = previous.consecutive_incomplete_buckets if previous is not None else 0
    streak = previous_streak + 1 if days_unwritten or debt else 0
    return TurnReport(
        outcome=str(outcome) if outcome is not None else None,
        days_unwritten=days_unwritten,
        unwritten=kept,
        unwritten_truncated=len(entries) > len(kept),
        consecutive_incomplete_buckets=streak,
        publication_debt=debt,
        publication_debt_counts=debt_counts,
    )


def record_turn_report(lane_id: str, report: Mapping[str, object] | None) -> TurnReport | None:
    """Fold one bucket's report into the process-held streak for its owning lane and return what was kept."""
    kept = summarize_turn_report(report, previous=_LANE_TURN_REPORTS.get(lane_id))
    if kept is not None:
        _LANE_TURN_REPORTS[lane_id] = kept
    return kept


def _describe_turn_report_debt(turn_report: TurnReport) -> str:
    """The blocker-detail clause for an incomplete turn, worded for whichever kind of owed work caused it.

    A publication-debt-only turn wrote every day it selected (`days_unwritten == 0`), so the
    unwritten-days phrasing below would misreport it as "left 0 day(s) unwritten" -- read as nothing
    owed. See `TurnReport.incomplete` and execution/AGENTS.md, "Publication debt is the second,
    quieter half of an incomplete turn".
    """
    owed = ", ".join(f"{name}={count}" for name, count in sorted(turn_report.publication_debt_counts.items()))
    streak = f"{turn_report.consecutive_incomplete_buckets} consecutive bucket(s) in this process"
    if turn_report.days_unwritten > 0:
        detail = f"left {turn_report.days_unwritten} day(s) unwritten for {streak}"
        if owed:
            detail = f"{detail}; owes availability publication ({owed})"
        return detail
    detail = f"owes availability publication for {streak}"
    if owed:
        detail = f"{detail} ({owed})"
    return detail


def _tick_catalogue() -> LaneCatalogue:
    """This module's lane catalogue: `LANE_SPECS` read at call time (tests rebind it), the lane TOMLs, CA12."""
    return current_lane_catalogue(LANE_SPECS)


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
    await _sync_definition_shape(session, spec)
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


async def _sync_definition_shape(session: AsyncSession, spec: LaneExecutionSpec) -> None:
    """Once per process per definition, rewrite a stored row's shape to its spec's; `enabled` is never touched (M1).

    `insert_definition.sql` never updates, so a same-version spec change (soil's six-hourly schedule, a config
    lane's CA13 cut-over) would otherwise never reach the row `/admin/jobs` reads. The descriptive columns are
    rewritten for every lane; the runtime limits only for a config lane, whose TOML owns them.
    """
    if spec.definition_name in _DEFINITION_SHAPES_SYNCED:
        return
    definition_spec = spec.definition_spec()
    updated = await fetch_row(
        session,
        _UPDATE_DEFINITION_SHAPE,
        {
            "name": definition_spec.name,
            "version": definition_spec.version,
            "schedule": definition_spec.schedule,
            "schedule_timezone": definition_spec.schedule_timezone,
            "parameters": canonical_json(definition_spec.parameters),
            "runtime_shape": spec.is_config,
            "max_attempts": definition_spec.max_attempts,
            "lease_seconds": definition_spec.lease_seconds,
            "time_budget_seconds": definition_spec.time_budget_seconds,
            "retry_policy": canonical_json(definition_spec.retry_policy.to_json()),
        },
    )
    if updated is not None:
        await _commit_planning_transaction(session)
        logger.info("plantgeo_job_executor_definition_shape_updated", lane_id=spec.lane_id, runtime=spec.is_config)
    _DEFINITION_SHAPES_SYNCED.add(spec.definition_name)


async def read_lane_checkpoint(session: AsyncSession, spec: LaneExecutionSpec) -> LatestRun | None:
    """Read the lane's scheduler checkpoint: the run a tick plans from, with its supersession and failure streak.

    Under the split breaker the streak is marker-aware (F4): a person's supersession ends it. The planner and
    the `jobs-supersede-run` verb both read the mode from this process's environment, so they always agree.
    """
    row = await fetch_row(
        session,
        _SELECT_LATEST_RUN,
        {
            "name": spec.definition_name,
            "current_version": EXECUTOR_DEFINITION_VERSION,
            "supersession_fingerprint_prefix": RUN_SUPERSESSION_FINGERPRINT_PREFIX,
            "failure_streak_limit": FAILURE_STREAK_PROBE_LIMIT,
            "streak_after_marker": breaker_mode() == "split",
            "executor_operator_prefix": EXECUTOR_OPERATOR_PREFIX,
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
        operator_apply_action=supersession_command(spec, latest.run_id, apply=True) if needs_operator else None,
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
        target_partitions=spec.run_target_partitions(scheduled_iso),
        work_items=(
            JobWorkItemSpec(
                shard_key=scheduled_iso,
                kind=EXECUTOR_WORK_ITEM_KIND,
                # CA1: a config definition's item carries the config-path marker the ledger can be read by.
                payload=spec.work_item_payload(scheduled_iso),
                priority=_work_priority(spec),
            ),
        ),
        # A probe or probation bucket is single-attempt, a watched one two (GL-6); `None` is the definition's.
        max_attempts=candidate.max_attempts,
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
    # The planner's probe flag reaches the handler through this task's context (GL-6), never a payload key.
    probe_token = _PROBE_TURN.set(candidate.probe)
    try:
        summary = await run_job_slice(
            session,
            definition_name=candidate.spec.definition_name,
            version=candidate.definition.version,
            job_run_id=run_id,
            worker_id=_worker_id(candidate.spec),
            budget_seconds=float(candidate.definition.time_budget_seconds),
            stop=stop,
        )
    finally:
        _PROBE_TURN.reset(probe_token)
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
        detail = f"{detail}; {_describe_turn_report_debt(turn_report)}"
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


#: The `LaneTickResult.detail` prefix a lane whose planning raised carries (spec Sec 4.9.3 "Isolation"):
#: `_SoftFailureTick.record_plan_failures` reads it back to bump `lane_plan_failed:<lane>`.
PLAN_FAILED_DETAIL_PREFIX: Final = "plan_failed: "


@dataclass(frozen=True, slots=True)
class _LanePlan:
    """One active lane's planning outcome, plus the ledger facts the hold record reads (o5b).

    `operator_held` is the same predicate `_held_checkpoint_result` prints a supersession command for:
    the checkpoint settled without success, the clock no longer releases it, and no supersession is
    recorded yet. `blocked` is a `failed` blocked-open-run or prior-version result (`lane_blocked`).
    """

    result: LaneTickResult | None = None
    due: DueLane | None = None
    latest: LatestRun | None = None
    definition_enabled: bool = True
    operator_held: bool = False
    blocked: bool = False
    #: The hold ladder started a probe while planning this lane (the hold row already reads `probing`).
    probe_started: bool = False


def _is_operator_held(spec: LaneExecutionSpec, latest: LatestRun | None, now: datetime) -> bool:
    """True when only a recorded supersession releases this checkpoint (pure; `judge_failed_checkpoint`)."""
    if latest is None or latest.status not in SETTLED_WITHOUT_SUCCESS or not spec.executable:
        return False
    verdict = judge_failed_checkpoint(spec, latest, now)
    return not verdict.released and verdict.release == "operator" and not latest.superseded_by_operator


async def _isolate_plan_fault(session: AsyncSession, lane_id: str, error: Exception) -> LaneTickResult:
    """One lane's non-SQL planning fault becomes that lane's `plan_failed` result (spec Sec 4.9.3).

    A `SQLAlchemyError` from HEAD's own statements, or a pinned connection that lost its backend,
    keeps today's tick-level re-raise; anything else rolls back this lane's planning transaction and
    the loop moves on to the next lane. See execution/AGENTS.md, "Soft failure".
    """
    if isinstance(error, SQLAlchemyError) or _pinned_connection_invalidated(session):
        raise error
    await _rollback_planning_transaction(session)
    _warn_fail_open(events.EVENT_LANE_PLAN_FAILED, error, lane_id=lane_id)
    return LaneTickResult(
        lane_id=lane_id,
        state="failed",
        detail=(
            f"{PLAN_FAILED_DETAIL_PREFIX}{type(error).__name__}; this lane is skipped this tick and every other "
            "lane plans as usual"
        ),
    )


def _stopped_lane_result(spec: LaneExecutionSpec) -> LaneTickResult:
    """A definition the CA12 kill-switch names: planned by nobody, on either path, until the variable drops it."""
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="paused",
        command=spec.command,
        blockers=(f"named in {STOPPED_LANES_VARIABLE}",),
        detail=f"stopped by {STOPPED_LANES_VARIABLE}; no ledger was read and nothing dispatches",
    )


def _undispatched_config_result(spec: LaneExecutionSpec, reason: str, now: datetime) -> LaneTickResult:
    """A config definition its TOML, the kill-switch or a declared conflict keeps from dispatching (CA3)."""
    return LaneTickResult(
        lane_id=spec.lane_id,
        state="paused" if reason == STOPPED_REASON else "shadow",
        scheduled_for=scheduled_bucket(spec, now),
        command=spec.command,
        blockers=(reason,),
        due_prediction="would_be_due_if_dispatchable; source watermark parity not evaluated",
        detail=f"config lane not dispatched: {reason}",
    )


def _quarantined_lane_result(lane_id: str, reasons: tuple[str, ...]) -> LaneTickResult:
    """A lane whose TOML did not load (S8): it runs on neither path, and every other lane runs."""
    return LaneTickResult(
        lane_id=lane_id,
        state="paused",
        blockers=tuple(f"lane TOML quarantined: {reason}" for reason in reasons),
        detail=f"lanes/{lane_id}.toml is quarantined (S8); this lane runs on neither path until it loads",
    )


def _inactive_lane_result(spec: LaneExecutionSpec, now: datetime) -> LaneTickResult:
    state: LaneTickState = "shadow" if spec.executable else "source_specific"
    current_bucket = scheduled_bucket(spec, now) if spec.executable else None
    blockers = ["lane is not in the active allow-list"]
    if not spec.executable:
        blockers.append("no executable command exists in this runtime")
    return LaneTickResult(
        lane_id=spec.lane_id,
        state=state,
        scheduled_for=current_bucket,
        command=spec.command,
        blockers=tuple(blockers),
        due_prediction=(
            "would_be_due_if_activated; source watermark parity not evaluated" if spec.executable else "not_executable"
        ),
        detail=(
            "shadow schedule prediction only; no ledger or source watermark parity was read"
            if spec.executable
            else f"{spec.migration_disposition}: no command in this runtime"
        ),
    )


async def _plan_lane(  # noqa: PLR0911, PLR0912 - one return per lane state the planner can find
    session: AsyncSession,
    spec: LaneExecutionSpec,
    now: datetime,
    *,
    breaker_release: ProcessStartRelease | None,
    ladder: _SoftFailureTick | None = None,
) -> _LanePlan:
    """Plan one ACTIVE lane as HEAD did; the `_LanePlan` only adds what the hold record reads.

    `ladder` (the split breaker's tick, GL-6) may start a single-attempt probe of a held lane and shapes each
    new bucket's failure budget (probe, probation, watch). Without it the plan is HEAD's exactly. See
    execution/AGENTS.md, "Holds and probes".
    """
    definition = await _load_or_register_definition(session, spec)
    latest = await read_lane_checkpoint(session, spec)
    prior_result, prior_due = await _plan_prior_version_run(session, spec, latest)
    await _rollback_planning_transaction(session)
    if prior_result is not None:
        return _LanePlan(result=prior_result, latest=latest, blocked=prior_result.state == "failed")
    if prior_due is not None:
        return _LanePlan(due=prior_due, latest=latest)
    if definition is None:
        return _LanePlan(
            result=LaneTickResult(
                lane_id=spec.lane_id,
                state="paused",
                detail="the lane-wide job_definition pause or current-version pause is active",
            ),
            latest=latest,
            definition_enabled=False,
            operator_held=_is_operator_held(spec, latest, now),
        )
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
            released = await _release_by_process_start(session, spec, latest, verdict, now=now, release=breaker_release)
            if released is not None:
                latest = released
                verdict = judge_failed_checkpoint(spec, latest, now)
                if ladder is not None:
                    # WQ-7 fold: the opt-in deploy release is recorded as a probe, then probation.
                    await ladder.record_deploy_probe(session, spec, latest, verdict)
        if not verdict.released:
            if ladder is not None:
                probe = await ladder.start_probe(session, spec, definition, latest, verdict)
                if probe is not None:
                    return _LanePlan(due=probe, latest=latest, probe_started=True)
            return _LanePlan(
                result=_held_checkpoint_result(spec, latest, verdict),
                latest=latest,
                operator_held=verdict.release == "operator" and not latest.superseded_by_operator,
            )
        released_bucket = DueLane(
            spec=spec,
            definition=definition,
            scheduled_for=verdict.next_bucket,
            existing_run_id=None,
            last_scheduled_for=latest.scheduled_for,
            superseded_run_id=latest.run_id,
            supersession=verdict.release,
        )
        return _LanePlan(
            due=released_bucket if ladder is None else ladder.shape_bucket(spec, latest, released_bucket),
            latest=latest,
        )
    if latest is not None and latest.open:
        blocked = _blocked_open_run_result(spec, latest, prior_version=False)
        if blocked is not None:
            return _LanePlan(result=blocked, latest=latest, blocked=blocked.state == "failed")
        open_bucket = DueLane(
            spec=spec,
            definition=definition,
            scheduled_for=latest.scheduled_for,
            existing_run_id=latest.run_id,
            last_scheduled_for=latest.scheduled_for,
        )
        return _LanePlan(
            due=open_bucket if ladder is None else ladder.shape_bucket(spec, latest, open_bucket),
            latest=latest,
        )
    if latest is not None and latest.scheduled_for >= current_bucket:
        return _LanePlan(
            result=LaneTickResult(
                lane_id=spec.lane_id,
                state="not_due",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                run_status=latest.status,
                detail=f"current bucket already settled with status {latest.status}",
            ),
            latest=latest,
        )
    if latest is None and spec.cron is not None:
        # S9: a never-run config definition waits for its first fire after this process first saw it,
        # rather than opening the fire it was registered after (executor F8c's immediate first bucket).
        first_seen = _NEVER_RUN_FIRST_SEEN.setdefault(spec.definition_name, now)
        if current_bucket <= first_seen:
            return _LanePlan(
                result=LaneTickResult(
                    lane_id=spec.lane_id,
                    state="not_due",
                    scheduled_for=bucket_after(spec, first_seen),
                    detail="a never-run config lane waits for its next cron fire",
                ),
                latest=latest,
            )
    bucket = next_scheduled_bucket(spec, now, None if latest is None else latest.scheduled_for)
    next_bucket = DueLane(
        spec=spec,
        definition=definition,
        scheduled_for=bucket,
        existing_run_id=None,
        last_scheduled_for=None if latest is None else latest.scheduled_for,
    )
    return _LanePlan(
        due=next_bucket if ladder is None else ladder.shape_bucket(spec, latest, next_bucket),
        latest=latest,
    )


async def _plan_active_lanes(  # noqa: PLR0913 - the session, the clock, the allow-list and three collaborators
    session: AsyncSession,
    activation: ActivationConfig,
    now: datetime,
    *,
    breaker_release: ProcessStartRelease | None = None,
    observer: _SoftFailureTick | None = None,
    catalogue: LaneCatalogue | None = None,
) -> tuple[list[LaneTickResult], list[DueLane]]:
    """Plan every lane on both paths, one failure domain each: a lane whose planning raises is reported
    `plan_failed` and the loop continues (spec Sec 4.9.3 "Isolation"); `observer` records the hold
    incidents (o5b). A legacy lane is gated by the allow-list, a config definition by its TOML (CA3), and
    both by the CA12 kill-switch. See execution/AGENTS.md, "Lane catalogue"."""
    catalogue = _tick_catalogue() if catalogue is None else catalogue
    legacy_activation = catalogue.legacy_activation(activation)
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for spec in catalogue.legacy_specs.values():
        if catalogue.kill_switch.stops(spec.lane_id):
            results.append(_stopped_lane_result(spec))
            continue
        if not activation.is_active(spec.lane_id):
            results.append(_inactive_lane_result(spec, now))
            continue
        await _plan_and_observe(session, spec, now, results, due, breaker_release=breaker_release, observer=observer)
    for spec in catalogue.config_specs:
        try:
            refusal = catalogue.config_gate(spec) or catalogue.config_conflict(spec, legacy_activation)
            if refusal is not None:
                results.append(_undispatched_config_result(spec, refusal, now))
                continue
        except Exception as error:  # a config lane's fault is that lane's plan_failed, never the tick's
            results.append(await _isolate_plan_fault(session, spec.lane_id, error))
            continue
        await _plan_and_observe(session, spec, now, results, due, breaker_release=breaker_release, observer=observer)
    results.extend(_quarantined_lane_result(lane_id, reasons) for lane_id, reasons in catalogue.quarantined.items())
    return results, due


async def _plan_and_observe(  # noqa: PLR0913 - the lane, the clock, the two accumulators and two collaborators
    session: AsyncSession,
    spec: LaneExecutionSpec,
    now: datetime,
    results: list[LaneTickResult],
    due: list[DueLane],
    *,
    breaker_release: ProcessStartRelease | None,
    observer: _SoftFailureTick | None,
) -> None:
    """Plan one dispatchable definition and record its hold state, isolating a non-SQL planning fault."""
    ladder = None if observer is None else observer.ladder_gate(spec.lane_id)
    try:
        plan = await _plan_lane(session, spec, now, breaker_release=breaker_release, ladder=ladder)
    except Exception as error:  # one lane's fault is that lane's plan_failed, never the tick's
        results.append(await _isolate_plan_fault(session, spec.lane_id, error))
        return
    if plan.result is not None:
        results.append(plan.result)
    if plan.due is not None:
        due.append(plan.due)
    if observer is not None:
        await observer.observe_lane(session, spec, plan)


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


async def _plan_repair_lane(
    session: AsyncSession,
    spec: LaneExecutionSpec,
    *,
    forward_due: AbstractSet[str],
) -> tuple[LaneTickResult | None, DueLane | None]:
    """Plan one active lane's already-authored repair run, exactly as HEAD's loop body did."""
    repair = repair_lane_spec(spec)
    state = await _definition_state(session, repair)
    if state is None:
        await _rollback_planning_transaction(session)
        return None, None
    definition = await _load_or_register_definition(session, repair)
    latest = await read_lane_checkpoint(session, repair)
    await _rollback_planning_transaction(session)
    if definition is None:
        return (
            LaneTickResult(
                lane_id=repair.lane_id, state="paused", detail="the repair definition's pause switch is set"
            ),
            None,
        )
    if latest is None or not latest.open:
        return None, None
    if spec.lane_id in forward_due:
        return (
            LaneTickResult(
                lane_id=repair.lane_id,
                state="deferred_fairness",
                scheduled_for=latest.scheduled_for,
                run_id=latest.run_id,
                detail="the owning lane's forward bucket is due or running; its repair waits for the next tick",
            ),
            None,
        )
    blocked = _blocked_open_run_result(repair, latest, prior_version=False)
    if blocked is not None:
        return blocked, None
    return (
        None,
        DueLane(
            spec=repair,
            definition=definition,
            scheduled_for=latest.scheduled_for,
            existing_run_id=latest.run_id,
            last_scheduled_for=latest.scheduled_for,
        ),
    )


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
    out of the same tick, so one turn never doubles that lane's egress. Each lane is its own failure domain
    (spec Sec 4.9.3): a non-SQL fault planning one lane's repair is that repair's `plan_failed` result.
    A lane the soft-failure layer withholds arrives here already removed from `activation`. A lane on the
    config path is never driven here, even with an open `:gap-repair` run (CA8), nor is a stopped one (CA12).
    """
    catalogue = _tick_catalogue()
    results: list[LaneTickResult] = []
    due: list[DueLane] = []
    for lane_id in sorted(REPAIR_LANE_IDS):
        spec = LANE_SPECS.get(lane_id)
        if spec is None or not activation.is_active(lane_id):
            continue
        if lane_id not in catalogue.legacy_specs or catalogue.kill_switch.stops(f"{lane_id}{REPAIR_LANE_SUFFIX}"):
            continue  # the lane itself, or only its `:gap-repair`, is named in the kill-switch (CA12, L8)
        try:
            result, candidate = await _plan_repair_lane(session, spec, forward_due=forward_due)
        except Exception as error:  # one repair's fault is that repair's plan_failed only
            results.append(await _isolate_plan_fault(session, f"{lane_id}{REPAIR_LANE_SUFFIX}", error))
            continue
        if result is not None:
            results.append(result)
        if candidate is not None:
            due.append(candidate)
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
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        faults: list[ConfigFault] | None = None,
    ) -> RepairAuthoringClock | None:
        """`0` disables authoring; a garbled or negative value falls back to the default interval with one
        `config_fallback` warning, appended to `faults` for the `executor_config:<VAR>` incident (spec
        Sec 4.9.3 "Switches") -- never a startup exit."""
        source = os.environ if environment is None else environment
        raw = source.get(REPAIR_INTERVAL_VARIABLE, "").strip()
        if not raw:
            return cls(interval_seconds=DEFAULT_REPAIR_INTERVAL_SECONDS)
        try:
            interval = float(raw)
        except ValueError:
            interval = math.nan
        if not math.isfinite(interval) or interval < 0:
            _config_fallback(
                [] if faults is None else faults, REPAIR_INTERVAL_VARIABLE, raw, DEFAULT_REPAIR_INTERVAL_SECONDS
            )
            return cls(interval_seconds=DEFAULT_REPAIR_INTERVAL_SECONDS)
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


# --- Daily upkeep in the repair-authoring slot (spec Sec 4.9.2/4.9.3; execution/AGENTS.md "Daily upkeep") ---

#: `job-logs-maintain`'s own CLI defaults: 30 days of `agri.job_event` partitions kept, 7 days created ahead.
JOB_LOGS_RETENTION_DAYS: Final = 30
JOB_LOGS_FUTURE_DAYS: Final = 7
#: Bound on one job-event pass inside the tick: its DDL waits `lock_timeout` (15 s), then moves and drops rows.
JOB_LOGS_MAINTAIN_TIMEOUT_SECONDS: Final = 300.0


@dataclass(slots=True)
class DailyMaintenance:
    """The leader's once-per-UTC-day upkeep: `job-logs-maintain` (job-event retention, COV2-14) and the closed
    month's usage receipt (WQ-6). The day is process-held; a failed pass waits for the next UTC day, so a broken
    store or a busy partition lock is never retried every tick."""

    #: The executor's DSN; job-event maintenance runs on its own short-lived connection. None skips it.
    database_url: str | None = None
    #: Builds the receipt store on first use; None skips receipts.
    receipt_store_factory: Callable[[], UsageReceiptStore] | None = UsageReceiptStore.from_settings
    last_day: date | None = None
    receipt_store: UsageReceiptStore | None = None
    receipt_store_failed: bool = False

    def due(self, now: datetime) -> bool:
        return self.last_day != now.astimezone(UTC).date()

    def store(self) -> UsageReceiptStore | None:
        """The receipt store, built once; an unconfigured bucket logs once and turns receipts off for the process."""
        if self.receipt_store is None and self.receipt_store_factory is not None and not self.receipt_store_failed:
            try:
                self.receipt_store = self.receipt_store_factory()
            except Exception as error:  # an unconfigured bucket must never fail the tick
                self.receipt_store_failed = True
                logger.error("plantgeo_job_executor_usage_receipt_store_unavailable", error_type=type(error).__name__)
        return self.receipt_store


async def _maintain_job_events(database_url: str, now: datetime) -> dict[str, str | int]:
    """One `job-logs-maintain` pass on its own connection: its partition DDL never rides the leader's transaction."""
    async with local_source_loader_pool(database_url) as engine, engine.begin() as connection:
        result = await maintain_job_event_partitions(
            connection, now=now, retention_days=JOB_LOGS_RETENTION_DAYS, future_days=JOB_LOGS_FUTURE_DAYS
        )
    return result.to_dict()


async def _run_daily_maintenance(session: AsyncSession, *, now: datetime, maintenance: DailyMaintenance) -> None:
    """Job-event retention, then the closed month's receipt; each fault is logged and costs nothing but itself."""
    maintenance.last_day = now.astimezone(UTC).date()
    if maintenance.database_url is not None:
        try:
            summary = await asyncio.wait_for(
                _maintain_job_events(maintenance.database_url, now), timeout=JOB_LOGS_MAINTAIN_TIMEOUT_SECONDS
            )
        except MaintenanceBusyError:
            logger.info("plantgeo_job_executor_job_logs_maintain_busy")
        except Exception as error:  # upkeep is best-effort; the lanes never wait on it
            logger.error("plantgeo_job_executor_job_logs_maintain_failed", error_type=type(error).__name__)
        else:
            logger.info("plantgeo_job_executor_job_logs_maintained", **summary)
    store = maintenance.store()
    if store is None:
        return
    try:
        outcome, month = await write_closed_month_receipt(session, store=store, now=now)
    except Exception as error:  # the next UTC day tries again; the month's ledger rows are still there
        if isinstance(error, SQLAlchemyError) and _pinned_connection_invalidated(session):
            raise
        logger.error("plantgeo_job_executor_usage_receipt_failed", error_type=type(error).__name__)
        return
    logger.debug("plantgeo_job_executor_usage_receipt_checked", outcome=outcome, month=f"{month:%Y-%m}")


# --- Soft failure (Wave O GL-5, o5b; spec Sec 4.9.3; design record Sec 3.2A-3.4) --------------------
#
# Everything below RECORDS: one `agri.job_incident` row per streak, written through
# `execution/lane_incidents.py`'s savepoint-wrapped helpers, and it ACTS in only two ways -- repair
# withholding and the repair breaker, both by narrowing the repair planners' activation. A hold is still
# released only by an operator (`jobs-supersede-run`, or `jobs-set-lane-enabled --disabled`) until G1's
# ladder. See execution/AGENTS.md, "Soft failure".

#: `on` unset; any on-synonym keeps it on; an off-synonym OR a garbled value is HEAD planning.
SOFT_FAILURE_VARIABLE: Final = "PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE"
#: Sibling copy of `foundation/observability/bootstrap.py`'s switch synonyms (that module keeps its sets
#: private and reads only `os.environ`; this switch is also read from an explicit mapping in tests).
_SWITCH_ON_VALUES: Final = frozenset({"1", "true", "yes", "on", "enabled"})
_SWITCH_OFF_VALUES: Final = frozenset({"0", "false", "no", "off", "disabled", "none"})
#: `lane_plan_failed`, `lane_report_missing` and `executor_lease_lost` escalate to error at this streak.
STREAK_ESCALATION_COUNT: Final = 3
#: `executor_repair_authoring` escalates to error at this many consecutive failed authoring intervals.
REPAIR_AUTHORING_ESCALATION_COUNT: Final = 2
#: `fleet:<exit_class>` opens when this many lanes open holds of one class inside `FLEET_WINDOW`.
FLEET_HOLD_THRESHOLD: Final = 3
FLEET_WINDOW: Final = timedelta(hours=1)
#: `lane_incomplete`'s escalation ladder by episode age, newest step first: (state, age, level).
INCOMPLETE_ESCALATION_STEPS: Final[tuple[tuple[str, timedelta, LogLevel], ...]] = (
    ("72h", timedelta(hours=72), "error"),
    ("24h", timedelta(hours=24), "error"),
    ("6h", timedelta(hours=6), "warn"),
)
INCOMPLETE_OPEN_STATE: Final = "open"
#: Hold classes whose `lane_hold` opens at warn (spec Sec 4.9.3's table); every other class opens at error.
_WARN_HOLD_CLASSES: Final[frozenset[str]] = frozenset({"upstream", "infra"})
#: The durable "hold_chronic was logged" marker: a chronic hold's summary ends with it. `select_lane_incidents`
#: extracts only four `detail` keys, so the summary headline is the one field that carries it back.
CHRONIC_SUMMARY_SUFFIX: Final = " [chronic: chain over 72 h]"
LEASE_LOST_FLEET_SUBJECT: Final = "fleet"
#: `executor_lease_lost:fleet` needs at least this many dispatched lanes, every one of them lease-lost.
LEASE_LOST_FLEET_MIN_LANES: Final = 2
INCIDENT_SUMMARY_MAX_CHARS: Final = 500
_HOLD_PREFIX: Final = "lane_hold:"
_RESOLVED_MARKER: Final = ":resolved:"
_COUNTING_STATE_PREFIX: Final = "counting:"
_COOLDOWN_STATE: Final = "cooldown"
#: Lane-scoped kinds that resolve only when their lane plans or runs a turn: once the lane leaves the
#: allow-list (or is quarantined) they retire as `lane_inactive` instead of staying open forever.
_INACTIVE_RETIRED_KINDS: Final[frozenset[str]] = frozenset(
    {"lane_incomplete", "lane_report_missing", "executor_lease_lost", "lane_blocked", "lane_repair_failing"}
)
#: A `lane_repair_failing` row with no new settled failure this long after its breaker admits runs again
#: (cooldown lifted, or never tripped) retires: the gaps closed and no repair was authored to prove it.
REPAIR_FAILING_QUIET_PERIOD: Final = timedelta(days=7)
RESOLUTION_LANE_INACTIVE: Final = "lane_inactive"
RESOLUTION_REPAIR_QUIET: Final = "repair_quiet"
RESOLUTION_AUTHORING_DISABLED: Final = "authoring_disabled"
#: The G1 ladder's own events (design Sec 1.4); logged on a transition only, never per tick.
EVENT_HOLD_PROBE: Final = "plantgeo_job_executor_hold_probe"
EVENT_HOLD_PROBE_FAILED: Final = "plantgeo_job_executor_hold_probe_failed"
EVENT_HOLD_PROBE_INCONCLUSIVE: Final = "plantgeo_job_executor_hold_probe_inconclusive"
EVENT_HOLD_PROBE_REFUSED: Final = "plantgeo_job_executor_hold_probe_refused"
EVENT_HOLD_PROBATION: Final = "plantgeo_job_executor_hold_probation"


class _ProbeNotStartedError(RuntimeError):
    """A probe's hold write, supersession or commit did not land; the probe is rolled back and the hold stays."""


@dataclass(frozen=True, slots=True)
class ConfigFault:
    """One numeric tunable that failed to parse and fell back to its default (`executor_config:<VAR>`)."""

    variable: str
    value: str
    fallback: float | int


def _config_fallback(faults: list[ConfigFault], variable: str, raw: str, fallback: float) -> None:
    """Record one fallback and log its single `config_fallback` warning; never raises."""
    faults.append(ConfigFault(variable=variable, value=raw, fallback=fallback))
    with suppress(Exception):
        logger.warning(events.EVENT_CONFIG_FALLBACK, variable=variable, value=raw[:64], fallback=fallback)


def _tunable_float(source: Mapping[str, str], name: str, fallback: float, faults: list[ConfigFault]) -> float:
    raw = source.get(name, "").strip()
    if not raw:
        return fallback
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        _config_fallback(faults, name, raw, fallback)
        return fallback
    return value


def _tunable_lanes_per_tick(source: Mapping[str, str], faults: list[ConfigFault]) -> int:
    """An unparseable or non-positive value is the default; a positive one below the fairness floor clamps."""
    raw = source.get(MAX_LANES_PER_TICK_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_MAX_LANES_PER_TICK
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        _config_fallback(faults, MAX_LANES_PER_TICK_VARIABLE, raw, DEFAULT_MAX_LANES_PER_TICK)
        return DEFAULT_MAX_LANES_PER_TICK
    if value < MIN_LANES_PER_TICK:
        _config_fallback(faults, MAX_LANES_PER_TICK_VARIABLE, raw, MIN_LANES_PER_TICK)
        return MIN_LANES_PER_TICK
    return value


def soft_failure_enabled(environment: Mapping[str, str] | None = None) -> bool:
    """`PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE`: on when unset or blank; an unrecognised value is OFF (HEAD)."""
    source = os.environ if environment is None else environment
    raw = source.get(SOFT_FAILURE_VARIABLE)
    if raw is None or not raw.strip():
        return True
    normalized = raw.strip().casefold()
    if normalized in _SWITCH_ON_VALUES:
        return True
    if normalized not in _SWITCH_OFF_VALUES:
        with suppress(Exception):
            logger.warning(events.EVENT_CONFIG_FALLBACK, variable=SOFT_FAILURE_VARIABLE, value=raw[:64], fallback="off")
    return False


def _mode_switch(  # noqa: PLR0913 - the source, the variable and the three words that define a mode switch
    source: Mapping[str, str], variable: str, *, allowed: frozenset[str], unset: str, garbled: str, warn: bool
) -> str:
    """One S16 mode switch: blank is `unset`, a recognised word is itself, anything else is `garbled` (HEAD's)."""
    raw = source.get(variable)
    if raw is None or not raw.strip():
        return unset
    normalized = raw.strip().casefold()
    if normalized in allowed:
        return normalized
    if warn:
        with suppress(Exception):
            logger.warning(events.EVENT_CONFIG_FALLBACK, variable=variable, value=raw[:64], fallback=garbled)
    return garbled


def breaker_mode(environment: Mapping[str, str] | None = None, *, warn: bool = False) -> BreakerMode:
    """`PLANTGEO_JOB_EXECUTOR_BREAKER_MODE`: `split` unset; a garbled value is `legacy`, HEAD's breaker."""
    source = os.environ if environment is None else environment
    mode = _mode_switch(
        source, BREAKER_MODE_VARIABLE, allowed=_BREAKER_MODES, unset="split", garbled="legacy", warn=warn
    )
    return "split" if mode == "split" else "legacy"


def dispatch_mode(environment: Mapping[str, str] | None = None, *, warn: bool = False) -> DispatchMode:
    """`PLANTGEO_JOB_EXECUTOR_DISPATCH`: `queue` unset; a garbled value is `serial`, HEAD's in-tick await."""
    source = os.environ if environment is None else environment
    mode = _mode_switch(source, DISPATCH_VARIABLE, allowed=_DISPATCH_MODES, unset="queue", garbled="serial", warn=warn)
    return "queue" if mode == "queue" else "serial"


def _tunable_concurrent_lanes(source: Mapping[str, str], faults: list[ConfigFault]) -> int:
    """`MAX_CONCURRENT_LANES`: a positive integer, else the default (1) with one `config_fallback` warning."""
    raw = source.get(MAX_CONCURRENT_LANES_VARIABLE, "").strip()
    if not raw:
        return DEFAULT_MAX_CONCURRENT_LANES
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        _config_fallback(faults, MAX_CONCURRENT_LANES_VARIABLE, raw, DEFAULT_MAX_CONCURRENT_LANES)
        return DEFAULT_MAX_CONCURRENT_LANES
    return value


def hold_ladders_from_environment(environment: Mapping[str, str] | None = None) -> HoldLadders:
    """The probe ladders `CODE_PROBE_HOURS` selects; a garbled value keeps code holds operator-only, warned once."""
    source = os.environ if environment is None else environment
    raw = source.get(CODE_PROBE_HOURS_VARIABLE)
    code_hours, garbled = parse_code_probe_hours(raw)
    if garbled and raw is not None:
        with suppress(Exception):
            logger.warning(
                events.EVENT_CONFIG_FALLBACK,
                variable=CODE_PROBE_HOURS_VARIABLE,
                value=raw[:64],
                fallback="operator_only",
            )
    return HoldLadders(code_probe_hours=code_hours)


@dataclass(frozen=True, slots=True)
class ExecutorSettings:
    """Every executor tunable, parsed so that no malformed variable can stop the process (spec Sec 4.9.3)."""

    poll_seconds: float
    #: The serial dispatcher's per-tick selection bound; retired from the work queue (S15), kept for `serial`.
    max_lanes_per_tick: int
    repair_clock: RepairAuthoringClock | None
    soft_failure_enabled: bool
    config_faults: tuple[ConfigFault, ...]
    dispatch: DispatchMode = "serial"
    breaker: BreakerMode = "legacy"
    max_concurrent_lanes: int = DEFAULT_MAX_CONCURRENT_LANES
    #: The G1 hold ladder; `None` under `BREAKER_MODE=legacy` (GL-5's operator-only holds) or `SOFT_FAILURE=off`.
    hold_ladders: HoldLadders | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> ExecutorSettings:
        source = os.environ if environment is None else environment
        faults: list[ConfigFault] = []
        poll_seconds = _tunable_float(source, POLL_SECONDS_VARIABLE, DEFAULT_POLL_SECONDS, faults)
        max_lanes = _tunable_lanes_per_tick(source, faults)
        repair_clock = RepairAuthoringClock.from_environment(source, faults=faults)
        concurrent_lanes = _tunable_concurrent_lanes(source, faults)
        breaker = breaker_mode(source, warn=True)
        # One switch per shared change (S16): `BREAKER_MODE=legacy` removes the G1 ladder and keeps GL-5's incident
        # layer; `SOFT_FAILURE=off` removes GL-5. So each can be rolled back alone (review PH2).
        soft_failure = soft_failure_enabled(source)
        return cls(
            poll_seconds=poll_seconds,
            max_lanes_per_tick=max_lanes,
            repair_clock=repair_clock,
            soft_failure_enabled=soft_failure,
            config_faults=tuple(faults),
            dispatch=dispatch_mode(source, warn=True),
            breaker=breaker,
            max_concurrent_lanes=concurrent_lanes,
            hold_ladders=hold_ladders_from_environment(source) if soft_failure and breaker == "split" else None,
        )


def _quarantine_reason(lane_id: str) -> str:
    spec = LANE_SPECS.get(lane_id)
    if spec is None:
        return "unknown"
    if not spec.executable:
        return "not_executable"
    return "conflict"


def quarantine_sources(
    activation: ActivationConfig, catalogue: LaneCatalogue | None = None
) -> dict[str, tuple[str, str]]:
    """Lane id -> (the variable or file that named it, why it is quarantined), over every source.

    The allow-list (GL-5), the lane TOMLs (S8) and the CA12 kill-switch (H6/FR-11) share one rule: an id
    they cannot honour is quarantined with one warning and one `lane_quarantined` incident, never an exit.
    """
    sources = {lane_id: (ACTIVE_LANES_VARIABLE, _quarantine_reason(lane_id)) for lane_id in activation.quarantined}
    if catalogue is not None:
        for lane_id, source in catalogue.quarantine_reasons().items():
            sources.setdefault(lane_id, source)
    return sources


def log_quarantined_lanes(activation: ActivationConfig, catalogue: LaneCatalogue | None = None) -> None:
    """One `lane_quarantined` warning per quarantined id at startup; the process keeps running."""
    for lane_id, (variable, reason) in sorted(quarantine_sources(activation, catalogue).items()):
        with suppress(Exception):
            logger.warning(events.EVENT_LANE_QUARANTINED, lane_id=lane_id, reason=reason, variable=variable)


@dataclass(slots=True)
class SoftFailureState:
    """What the soft-failure layer keeps across ticks in ONE process. The durable record is the incident
    rows; everything here is either a startup fact, a memo, or the per-process fallback that keeps a log
    line from repeating every tick while its row cannot be written."""

    enabled: bool = True
    quarantined: frozenset[str] = frozenset()
    #: Lane id -> (the variable or file that named it, the reason), for ids quarantined by a source other
    #: than the allow-list (a lane TOML, the CA12 kill-switch); `quarantine_sources` builds it.
    quarantine_origins: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    config_faults: tuple[ConfigFault, ...] = ()
    startup_recorded: bool = False
    #: Statement keys whose last attempt failed: `incident_write_failed` logs once per transition.
    failing_statements: set[str] = field(default_factory=set)
    #: Held run id -> (exit class, class source); a run's final attempt never changes once it is held.
    hold_classes: dict[uuid.UUID, tuple[ExitClass, str]] = field(default_factory=dict)
    #: The per-process announcement set (`announce_operator_actions`'s `announced`), shared with the
    #: hold record so an operator action is logged once whether the row or the process remembers it.
    announced: set[tuple[str, str | None]] = field(default_factory=set)
    #: Operator actions whose OPEN hold row already records the announcement (restart-proof).
    durable_announcements: frozenset[tuple[str, str | None]] = frozenset()
    #: Other once-per-episode lines whose row could not be written (the process fallback).
    logged_once: set[str] = field(default_factory=set)
    repair_withheld: frozenset[str] = frozenset()
    chronic_lanes: tuple[str, ...] = ()
    plan_failed_lanes: tuple[str, ...] = ()
    #: The G1 hold ladder (GL-6). `None` -- the default, and `BREAKER_MODE=legacy` or `SOFT_FAILURE=off` in
    #: production -- keeps GL-5's operator-only hold byte for byte. See execution/AGENTS.md, "Holds and probes".
    ladders: HoldLadders | None = None
    #: Settled run id -> its final attempt (a settled run's attempts never change); read to judge a probe.
    final_attempts: dict[uuid.UUID, FinalAttempt | None] = field(default_factory=dict)
    #: Lane -> its hold's newest probes, newest last, for `detail.probes`. Process-held: the incident read
    #: returns no `detail` blob, so a restart starts the list again (the ladder itself rides on the row).
    hold_probes: dict[str, list[dict[str, object]]] = field(default_factory=dict)

    @classmethod
    def for_process(
        cls, activation: ActivationConfig, settings_: ExecutorSettings, catalogue: LaneCatalogue | None = None
    ) -> SoftFailureState:
        sources = quarantine_sources(activation, catalogue)
        return cls(
            enabled=settings_.soft_failure_enabled,
            quarantined=frozenset(sources),
            quarantine_origins=sources,
            config_faults=settings_.config_faults,
            ladders=settings_.hold_ladders,
        )


def _incident_fingerprint(kind: IncidentKind | BudgetIncidentKind, subject: str | None) -> str:
    return kind if subject is None else f"{kind}:{subject}"


def _incident_subject(row: LaneIncidentRow) -> str:
    return row.fingerprint[len(row.incident_type) + 1 :]


def _hold_state(row: LaneIncidentRow) -> HoldState:
    return "paused" if row.state == "paused" else "held"


def _parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else parsed.replace(tzinfo=UTC)


def _parse_count(value: str | None, *, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


def _level_of(severity: EventSeverity) -> LogLevel:
    for level, mapped in INCIDENT_SEVERITY.items():
        if mapped == severity:
            return level
    return "error"


def _acknowledged(row: LaneIncidentRow | None) -> bool:
    """An operator acknowledged this open incident in `/admin/jobs`, so its escalation events stay quiet."""
    return row is not None and row.status == IncidentState.ACKNOWLEDGED


def _log_at(level: LogLevel, event: str, **fields: object) -> None:
    with suppress(Exception):
        getattr(logger, _LANE_TURN_LOG_METHODS[level])(event, **fields)


def _repair_breaker_state(row: LaneIncidentRow | None) -> RepairBreakerState:
    """Rebuild one lane's repair breaker from its `lane_repair_failing` row.

    `detail.state` is `counting:<n>` or `cooldown`; `detail.rung` is the trip count; the cooldown ends
    `repair_breaker_cooldown_days(rung)` after the tripping upsert's `last_seen_at` (nothing upserts the
    row while it is withheld). Once the cooldown has lifted the lane gets ONE run: the state reads one
    failure short of the threshold, so that run's code-class failure re-trips at the next rung.
    """
    if row is None:
        return RepairBreakerState()
    trip_count = _parse_count(row.rung, default=0)
    state = row.state or ""
    if state == _COOLDOWN_STATE and trip_count > 0:
        cooldown_until = row.last_seen_at + timedelta(days=repair_breaker_cooldown_days(trip_count))
        return RepairBreakerState(
            consecutive_failures=REPAIR_BREAKER_TRIP_THRESHOLD - 1,
            trip_count=trip_count,
            cooldown_until=cooldown_until,
        )
    consecutive = (
        _parse_count(state[len(_COUNTING_STATE_PREFIX) :], default=0) if state.startswith(_COUNTING_STATE_PREFIX) else 0
    )
    return RepairBreakerState(consecutive_failures=consecutive, trip_count=trip_count)


def _incomplete_step(age: timedelta) -> tuple[str, LogLevel]:
    for state, threshold, level in INCOMPLETE_ESCALATION_STEPS:
        if age >= threshold:
            return state, level
    return INCOMPLETE_OPEN_STATE, "warn"


class _SoftFailureTick:
    """One leader tick's soft-failure bookkeeping over ONE incident read (design Sec 3.2A).

    Every statement goes through `_guard`: a `SQLAlchemyError` has already rolled back to its savepoint
    (`lane_incidents.py` opens one per statement), is logged once per transition as
    `incident_write_failed`, and degrades only the lane it concerned to HEAD planning; only a pinned
    connection that lost its backend re-raises. A non-SQL fault in this layer's own interpretation is
    isolated the same way (`_isolated`). If the one read fails, the whole tick plans as HEAD.
    """

    def __init__(self, state: SoftFailureState, *, activation: ActivationConfig, now: datetime) -> None:
        self.state = state
        self.activation = activation
        self.now = now
        self.read_ok = False
        self.open_rows: dict[str, LaneIncidentRow] = {}
        self.resolved_holds: list[LaneIncidentRow] = []
        #: Lane -> why this tick degraded it to HEAD planning (an incident write or interpretation fault).
        self.degraded: dict[str, str] = {}
        #: Lanes whose verdict is operator-held AND whose hold row is open: repairs are withheld for them.
        self.held_lanes: set[str] = set()
        #: Lane -> held run id for every hold open after this tick's transitions (fleet correlation).
        self.open_holds: dict[str, uuid.UUID | None] = {}
        self.opened_this_tick: set[str] = set()
        self.durable: set[tuple[str, str | None]] = set()
        self._pending_durable: set[tuple[str, str | None]] = set()
        self.chronic: set[str] = set()
        self.dispatched: list[str] = []
        self.lease_lost: list[str] = []

    # --- statement plumbing ------------------------------------------------------------------------

    async def _guard[ValueT](
        self,
        session: AsyncSession,
        statement_key: str,
        operation: Callable[[], Awaitable[ValueT]],
        *,
        lane: str | None = None,
    ) -> tuple[bool, ValueT | None]:
        try:
            value = await operation()
        except SQLAlchemyError as error:
            if _pinned_connection_invalidated(session):
                raise
            if statement_key not in self.state.failing_statements:
                self.state.failing_statements.add(statement_key)
                with suppress(Exception):
                    logger.error(
                        events.EVENT_INCIDENT_WRITE_FAILED,
                        statement=statement_key,
                        lane_id=lane,
                        error_type=type(error).__name__,
                        detail="rolled back to its savepoint; the lane plans as HEAD until the statement succeeds",
                    )
            if lane is not None:
                self.degraded.setdefault(lane, f"incident_write_failed:{type(error).__name__}")
            return False, None
        self.state.failing_statements.discard(statement_key)
        return True, value

    async def _discard(self, session: AsyncSession) -> None:
        """Roll the planning transaction back after a fault; only a dead backend escapes."""
        try:
            await _rollback_planning_transaction(session)
        except SQLAlchemyError:
            if _pinned_connection_invalidated(session):
                raise

    async def _isolated(
        self,
        session: AsyncSession,
        lane: str | None,
        body: Callable[[AsyncSession], Awaitable[None]],
    ) -> None:
        """Run one lane's (or the tick's) soft-failure step and commit it; a fault degrades only that lane."""
        self._pending_durable = set()
        try:
            await body(session)
            committed, _ = await self._guard(
                session,
                "commit" if lane is None else f"commit:{lane}",
                lambda: _commit_planning_transaction(session),
                lane=lane,
            )
            if not committed:
                await self._discard(session)
                return
        except SQLAlchemyError:
            raise  # `_guard` re-raises only for an invalidated pinned connection: today's tick-level rule
        except Exception as error:  # Wave O's interpretation must never take a lane down
            await self._discard(session)
            if lane is not None:
                self.degraded.setdefault(lane, f"soft_failure_fault:{type(error).__name__}")
            _warn_fail_open(events.EVENT_LANE_PLAN_FAILED, error, lane_id=lane, phase="soft_failure")
            return
        self.durable |= self._pending_durable

    async def _upsert(  # noqa: PLR0913 - one incident fact per keyword
        self,
        session: AsyncSession,
        *,
        kind: IncidentKind | BudgetIncidentKind,
        subject: str | None,
        level: LogLevel,
        summary: str,
        detail: Mapping[str, object],
        lane: str | None = None,
        run_id: uuid.UUID | None = None,
    ) -> UpsertedIncident | None:
        fingerprint = _incident_fingerprint(kind, subject)
        ok, upserted = await self._guard(
            session,
            fingerprint,
            lambda: upsert_lane_incident(
                session,
                fingerprint=fingerprint,
                incident_type=kind,
                severity=INCIDENT_SEVERITY[level],
                summary=redact_text(summary)[:INCIDENT_SUMMARY_MAX_CHARS],
                now=self.now,
                job_run_id=run_id,
                detail=detail,
            ),
            lane=lane,
        )
        return upserted if ok else None

    async def _resolve(
        self,
        session: AsyncSession,
        row: LaneIncidentRow,
        *,
        detail_patch: Mapping[str, object],
        lane: str | None = None,
    ) -> bool:
        ok, _ = await self._guard(
            session,
            row.fingerprint,
            lambda: resolve_lane_incident(
                session, fingerprint=row.fingerprint, now=self.now, detail_patch=detail_patch
            ),
            lane=lane,
        )
        if ok:
            self.open_rows.pop(row.fingerprint, None)
        return ok

    def _open_row(self, kind: IncidentKind | BudgetIncidentKind, subject: str | None) -> LaneIncidentRow | None:
        return self.open_rows.get(_incident_fingerprint(kind, subject))

    async def _bump_streak(  # noqa: PLR0913 - the streak's identity, its escalation point and its words
        self,
        session: AsyncSession,
        *,
        kind: IncidentKind,
        subject: str | None,
        escalate_at: int,
        summary: str,
        detail: Mapping[str, object],
        lane: str | None = None,
    ) -> tuple[int, bool] | None:
        """One more occurrence of a streak incident: `(count, escalated_now)`, or `None` if unwritten.

        The escalation is durable because it IS the row's severity: a restarted process reads `error`
        back and never logs the crossing twice (`test_escalation_survives_an_executor_restart`).
        """
        row = self._open_row(kind, subject)
        count = (0 if row is None else row.occurrence_count) + 1
        level: LogLevel = "error" if count >= escalate_at else "warn"
        upserted = await self._upsert(
            session,
            kind=kind,
            subject=subject,
            level=level,
            summary=summary,
            detail={**detail, "consecutive": count},
            lane=lane,
        )
        if upserted is None:
            return None
        escalated_now = (
            level == "error" and (row is None or row.severity != EventSeverity.ERROR) and not _acknowledged(row)
        )
        return upserted.occurrence_count, escalated_now

    # --- tick phases -------------------------------------------------------------------------------

    async def open(self, session: AsyncSession) -> None:
        """The ONE incident read per tick; a failed read plans every lane as HEAD this tick."""
        ok, rows = await self._guard(
            session, "select_lane_incidents", lambda: select_lane_incidents(session, now=self.now)
        )
        if not ok or rows is None:
            return
        self.read_ok = True
        for row in rows:
            if row.incident_type not in INCIDENT_KINDS and row.incident_type not in BUDGET_INCIDENT_KINDS:
                continue  # supersession receipts and process-start markers share the table
            if row.status == IncidentState.RESOLVED:
                if row.incident_type == "lane_hold":
                    self.resolved_holds.append(row)
                continue
            self.open_rows[row.fingerprint] = row
            if row.incident_type == "lane_hold":
                self.open_holds[_incident_subject(row)] = row.job_run_id
        if not self.state.startup_recorded:
            await self._isolated(session, None, self._record_startup)

    async def _record_startup(self, session: AsyncSession) -> None:
        """`lane_quarantined:<lane>` and `executor_config:<VAR>` open at startup and resolve on the first
        tick of a deployment without the fault."""
        quarantined = {_incident_fingerprint("lane_quarantined", lane): lane for lane in self.state.quarantined}
        faults = {_incident_fingerprint("executor_config", fault.variable): fault for fault in self.state.config_faults}
        written = True
        for fingerprint, lane in sorted(quarantined.items()):
            if fingerprint in self.open_rows:
                continue
            variable, reason = self.state.quarantine_origins.get(
                lane, (ACTIVE_LANES_VARIABLE, _quarantine_reason(lane))
            )
            upserted = await self._upsert(
                session,
                kind="lane_quarantined",
                subject=lane,
                level="warn",
                summary=f"lane {lane!r} in {variable} is quarantined ({reason}); every other lane runs",
                detail={"lane_id": lane, "reason": reason, "variable": variable},
            )
            written = written and upserted is not None
        for fingerprint, fault in sorted(faults.items()):
            if fingerprint in self.open_rows:
                continue
            upserted = await self._upsert(
                session,
                kind="executor_config",
                subject=fault.variable,
                level="warn",
                summary=f"{fault.variable} is not a valid value; the executor uses {fault.fallback}",
                detail={"variable": fault.variable, "value": redact_text(fault.value)[:64], "fallback": fault.fallback},
            )
            written = written and upserted is not None
        for fingerprint, row in list(self.open_rows.items()):
            if row.incident_type in ("lane_quarantined", "executor_config") and fingerprint not in {
                *quarantined,
                *faults,
            }:
                resolved = await self._resolve(
                    session, row, detail_patch={"resolution_reason": "absent_from_this_deployment"}
                )
                written = written and resolved
        self.state.startup_recorded = written

    async def observe_lane(self, session: AsyncSession, spec: LaneExecutionSpec, plan: _LanePlan) -> None:
        """Record one planned lane's hold and blocked state (called right after HEAD planned it)."""
        if not self.read_ok:
            return
        lane = spec.lane_id
        laddered = self.ladder_gate(lane) is not None

        async def body(active_session: AsyncSession) -> None:
            if laddered:
                await self._observe_ladder_hold(active_session, lane, plan)
            else:
                await self._observe_hold(active_session, lane, plan)
            await self._observe_blocked(active_session, lane, plan)

        await self._isolated(session, lane, body)

    async def _observe_hold(self, session: AsyncSession, lane: str, plan: _LanePlan, *, ladder: bool = False) -> None:
        """GL-5's hold record: open, reconcile, pause and resume. `ladder` keeps an episode whose held run moved
        (an inconclusive or failed probe) instead of resolving it as `held_run_changed`."""
        row = self._open_row("lane_hold", lane)
        latest = plan.latest
        if plan.operator_held and latest is not None:
            if not ladder and row is not None and row.job_run_id is not None and row.job_run_id != latest.run_id:
                # Another run holds the lane now: the old episode ended while nobody was watching.
                await self._resolve_hold(session, lane, row, released_by="reconciled", reason="held_run_changed")
                row = None
            if row is None:
                await self._open_hold(session, lane, plan)
                return
        elif row is None:
            return
        outcome = reconcile(
            state=_hold_state(row),
            verdict=HoldVerdict(
                still_held=plan.operator_held,
                definition_enabled=plan.definition_enabled,
                lane_active=True,
                lane_quarantined=False,
                superseded_by_operator=latest is not None and latest.superseded_by_operator,
                superseded_run_id=None if latest is None else latest.run_id,
            ),
        )
        action_run = None if latest is None else latest.run_id
        command = None if plan.result is None else plan.result.operator_action
        await self._apply_hold_outcome(
            session,
            lane,
            row,
            outcome,
            still_held=plan.operator_held,
            announce_run=action_run if command is not None else None,
            command=command,
        )

    async def _apply_hold_outcome(  # noqa: PLR0913 - the hold, what reconcile decided, and what it withholds
        self,
        session: AsyncSession,
        lane: str,
        row: LaneIncidentRow,
        outcome: ReconcileOutcome,
        *,
        still_held: bool,
        announce_run: uuid.UUID | None,
        command: str | None = None,
    ) -> None:
        if outcome.action == "resolve":
            await self._resolve_hold(
                session, lane, row, released_by=outcome.released_by or "reconciled", reason="verdict_no_longer_held"
            )
            return
        if outcome.action in ("pause", "resume"):
            rewritten = await self._rewrite_hold(
                session,
                lane,
                row,
                state=outcome.state,
                pause_reason=outcome.pause_reason,
                level=_level_of(row.severity),
            )
            if rewritten is None:
                return
            _log_at(
                "info",
                events.EVENT_HOLD_RECONCILED,
                lane_id=lane,
                action=outcome.action,
                state=outcome.state,
                pause_reason=outcome.pause_reason,
            )
        if still_held:
            self.held_lanes.add(lane)
        if outcome.state != "held":
            return  # paused: no announcement and no escalation (the silencers, spec Sec 4.9.3)
        if announce_run is not None:
            if outcome.action == "resume" and command is not None:
                # A hold that opened paused never announced; its first held tick is the announcement.
                classified = await self._hold_class(session, lane, row.job_run_id)
                if classified is None:
                    return
                self._announce(lane, announce_run, command, classified)
            self._pending_durable.add((lane, str(announce_run)))
        await self._escalate_chronic(session, lane, self._open_row("lane_hold", lane) or row)

    def _announce(self, lane: str, run_id: uuid.UUID, command: str, classified: tuple[ExitClass, str]) -> None:
        """`operator_action_required` once per (lane, run) per process; the hold row makes it restart-proof."""
        key = (lane, str(run_id))
        if key in self.state.announced:
            return
        self.state.announced.add(key)
        _log_at(
            "error",
            "plantgeo_job_executor_operator_action_required",
            lane_id=lane,
            run_id=key[1],
            command=command,
            exit_class=classified[0],
            class_source=classified[1],
            detail="the clock no longer releases this lane; nothing runs on it until this command is recorded",
        )

    async def _escalate_chronic(self, session: AsyncSession, lane: str, row: LaneIncidentRow) -> None:
        chain_started = _parse_instant(row.chain_first_seen_at) or row.first_seen_at
        if not is_chain_chronic(chain_started, now=self.now):
            return
        self.chronic.add(lane)
        if row.summary.endswith(CHRONIC_SUMMARY_SUFFIX):
            return  # already escalated and logged, by this process or an earlier one
        rewritten = await self._rewrite_hold(
            session, lane, row, state=row.state or "held", pause_reason=None, level="error", chronic=True
        )
        if rewritten is not None and not _acknowledged(row):
            _log_at(
                "error",
                events.EVENT_HOLD_CHRONIC,
                lane_id=lane,
                run_id=None if row.job_run_id is None else str(row.job_run_id),
                chain_first_seen_at=chain_started.isoformat(),
            )

    async def _hold_class(
        self, session: AsyncSession, lane: str, run_id: uuid.UUID | None
    ) -> tuple[ExitClass, str] | None:
        """The held run's class from its final attempt (missing, unknown or `lost` = `code`); memoized."""
        if run_id is None:
            return "code", CLASS_SOURCE_MISSING_ATTEMPT
        known = self.state.hold_classes.get(run_id)
        if known is not None:
            return known
        ok, attempt = await self._guard(
            session,
            f"select_run_final_attempt:{lane}",
            lambda: select_run_final_attempt(session, job_run_id=run_id),
            lane=lane,
        )
        if not ok:
            return None
        classified = final_attempt_exit_class(attempt)
        self.state.hold_classes[run_id] = classified
        self.state.final_attempts[run_id] = attempt
        return classified

    def _hold_detail(  # noqa: PLR0913 - the design record's `detail` shape, one key per argument
        self,
        *,
        lane: str,
        run_id: uuid.UUID | None,
        state: str,
        exit_class: ExitClass,
        class_source: str,
        chain_first_seen_at: str,
        episodes_7d: int,
        pause_reason: str | None,
        rung: int = 0,
    ) -> dict[str, object]:
        detail: dict[str, object] = {
            "lane_id": lane,
            "run_id": None if run_id is None else str(run_id),
            "state": state,
            "exit_class": exit_class,
            "class_source": class_source,
            "rung": rung,
            "chain_first_seen_at": chain_first_seen_at,
            "episodes_7d": episodes_7d,
            "probes": [],
            "clean_buckets": 0,
            "release": "operator",
            "pause_reason": pause_reason,
        }
        ladders = self.state.ladders if self.ladder_gate(lane) is not None else None
        if ladders is None:
            return detail  # GL-5's shape exactly: the hold is operator-only
        progress = HoldProgress.parse(state)
        delay = ladders.probe_delay(exit_class, rung)
        detail.update(
            probes=list(self.state.hold_probes.get(lane, ())),
            clean_buckets=progress.counter if progress.phase == "probation" else 0,
            inconclusive_probes=progress.counter if progress.phase in ("held", "probing") else 0,
            release="probe" if delay is not None else "operator",
            next_probe_at=((self.now + delay).isoformat() if delay is not None and progress.phase == "held" else None),
        )
        return detail

    def _hold_summary(self, lane: str, exit_class: str, *, chronic: bool, state: str = "held") -> str:
        ladders = self.state.ladders if self.ladder_gate(lane) is not None else None
        if ladders is None:
            summary = f"lane {lane!r} is held ({exit_class}); only a recorded supersession releases it until G1"
        elif HoldProgress.parse(state).phase == "probation":
            summary = f"lane {lane!r} is on probation after a clean probe ({exit_class} hold); buckets run once"
        elif ladders.ladder_for(exit_class) is None:
            summary = f"lane {lane!r} is held ({exit_class}); code probes are off, so only an operator releases it"
        else:
            summary = f"lane {lane!r} is held ({exit_class}); single-attempt probes re-try it on its ladder"
        return f"{summary}{CHRONIC_SUMMARY_SUFFIX}" if chronic else summary

    def _recent_resolved_holds(self, lane: str) -> list[LaneIncidentRow]:
        """This lane's hold episodes resolved within the chain window, newest first."""
        resolved_prefix = f"{_HOLD_PREFIX}{lane}{_RESOLVED_MARKER}"
        recent = [
            row
            for row in self.resolved_holds
            if row.fingerprint.startswith(resolved_prefix)
            and row.resolved_at is not None
            and self.now - row.resolved_at <= CHAIN_WINDOW
        ]
        return sorted(recent, key=lambda row: row.resolved_at or row.last_seen_at, reverse=True)

    async def _open_hold(self, session: AsyncSession, lane: str, plan: _LanePlan) -> None:
        latest = plan.latest
        if latest is None:
            return
        classified = await self._hold_class(session, lane, latest.run_id)
        if classified is None:
            return
        exit_class, class_source = classified
        recent = self._recent_resolved_holds(lane)
        episodes = 1 + len(recent)
        state: HoldState = "held" if plan.definition_enabled else "paused"
        level: LogLevel = "warn" if exit_class in _WARN_HOLD_CLASSES else "error"
        laddered = self.ladder_gate(lane) is not None
        # 7-day chaining (GL-6): a re-open inherits the prior episode's rung and chain start.
        chained = recent[0] if laddered and recent else None
        rung = 0 if chained is None else _parse_count(chained.rung, default=0)
        chain_first_seen_at = (
            chained.chain_first_seen_at if chained is not None and chained.chain_first_seen_at else self.now.isoformat()
        )
        first_flapping = is_chain_flapping(episodes) and (
            not laddered or not recent or _parse_count(recent[0].episodes_7d, default=1) < FLAPPING_EPISODE_THRESHOLD
        )
        if laddered:
            self.state.hold_probes[lane] = []
            if is_chain_flapping(episodes):
                level = "error"
        upserted = await self._upsert(
            session,
            kind="lane_hold",
            subject=lane,
            level=level,
            summary=self._hold_summary(lane, exit_class, chronic=False),
            detail=self._hold_detail(
                lane=lane,
                run_id=latest.run_id,
                state=state,
                exit_class=exit_class,
                class_source=class_source,
                chain_first_seen_at=chain_first_seen_at,
                episodes_7d=episodes,
                pause_reason=None if state == "held" else "disabled",
                rung=rung,
            ),
            lane=lane,
            run_id=latest.run_id,
        )
        if upserted is None:
            return
        self.held_lanes.add(lane)
        self.open_holds[lane] = latest.run_id
        self.opened_this_tick.add(lane)
        command = None if plan.result is None else plan.result.operator_action
        fleet_open = self._open_row("fleet", exit_class) is not None
        _log_at(
            "warn" if fleet_open else level,
            events.EVENT_HOLD_OPENED,
            lane_id=lane,
            run_id=str(latest.run_id),
            exit_class=exit_class,
            class_source=class_source,
            episodes_7d=episodes,
            state=state,
            command=command,
            rung=rung,
        )
        if command is not None and state == "held":
            self._announce(lane, latest.run_id, command, classified)
            self._pending_durable.add((lane, str(latest.run_id)))
        if first_flapping:
            _log_at("error", events.EVENT_HOLD_FLAPPING, lane_id=lane, episodes_7d=episodes)

    async def _rewrite_hold(  # noqa: PLR0913 - the hold and the facts a transition may change
        self,
        session: AsyncSession,
        lane: str,
        row: LaneIncidentRow,
        *,
        state: str,
        pause_reason: str | None,
        level: LogLevel,
        chronic: bool = False,
        rung: int | None = None,
        run_id: uuid.UUID | None = None,
    ) -> UpsertedIncident | None:
        """Re-upsert an open hold with its full `detail` (an upsert replaces `detail` whole).

        `state` is the encoded `detail.state` (`HoldProgress.encoded`); `rung` and `run_id` default to the
        row's own. The in-memory row follows the write, so a later step of the same tick reads it.
        """
        held_run = row.job_run_id if run_id is None else run_id
        classified = await self._hold_class(session, lane, held_run)
        if classified is None:
            return None
        exit_class, class_source = classified
        next_rung = _parse_count(row.rung, default=0) if rung is None else rung
        chronic_marked = chronic or row.summary.endswith(CHRONIC_SUMMARY_SUFFIX)
        upserted = await self._upsert(
            session,
            kind="lane_hold",
            subject=lane,
            level=level,
            summary=self._hold_summary(lane, exit_class, chronic=chronic_marked, state=state),
            detail=self._hold_detail(
                lane=lane,
                run_id=held_run,
                state=state,
                exit_class=exit_class,
                class_source=class_source,
                chain_first_seen_at=row.chain_first_seen_at or row.first_seen_at.isoformat(),
                episodes_7d=_parse_count(row.episodes_7d, default=1),
                pause_reason=pause_reason,
                rung=next_rung,
            ),
            lane=lane,
            run_id=held_run,
        )
        if upserted is not None:
            self.open_rows[row.fingerprint] = replace(
                row,
                state=state,
                rung=str(next_rung),
                job_run_id=held_run,
                severity=INCIDENT_SEVERITY[level],
                summary=self._hold_summary(lane, exit_class, chronic=chronic_marked, state=state),
                occurrence_count=upserted.occurrence_count,
                last_seen_at=upserted.last_seen_at,
            )
        return upserted

    async def _resolve_hold(
        self, session: AsyncSession, lane: str, row: LaneIncidentRow, *, released_by: str, reason: str
    ) -> bool:
        resolved = await self._resolve(
            session,
            row,
            detail_patch={"released_by": released_by, "resolution_reason": reason, "resolved_state": row.state},
            lane=lane,
        )
        if not resolved:
            return False
        self.open_holds.pop(lane, None)
        _log_at(
            "info",
            events.EVENT_HOLD_RELEASED,
            lane_id=lane,
            run_id=None if row.job_run_id is None else str(row.job_run_id),
            released_by=released_by,
            reason=reason,
        )
        return True

    # --- the G1 hold ladder (GL-6 folded into f1-executor; spec Sec 4.9.3; design Sec 3.2B) -----------

    def ladder_gate(self, lane: str) -> _SoftFailureTick | None:
        """This tick when the hold ladder may act on `lane`; `None` keeps GL-5's operator-only hold.

        Off when the switch is off, the ladder is unset (`BREAKER_MODE=legacy`), the incident read failed, or
        this lane degraded to HEAD planning this tick.
        """
        if not self.read_ok or not self.state.enabled or self.state.ladders is None or lane in self.degraded:
            return None
        return self

    def _hold_level(self, exit_class: str, row: LaneIncidentRow, progress: HoldProgress) -> LogLevel:
        """A hold's severity: its class's level, never lowered below an escalation it already carries."""
        if progress.phase != "probation" and row.severity == EventSeverity.ERROR:
            return "error"
        return "warn" if exit_class in _WARN_HOLD_CLASSES or progress.phase == "probation" else "error"

    def _remember_probe(self, lane: str, record: dict[str, object]) -> None:
        probes = self.state.hold_probes.setdefault(lane, [])
        probes.append(record)
        del probes[:-PROBE_HISTORY_MAX]

    def _settle_remembered_probe(self, lane: str, outcome: str) -> None:
        probes = self.state.hold_probes.get(lane)
        if probes and probes[-1].get("outcome") is None:
            probes[-1]["outcome"] = outcome
            probes[-1]["conclusive"] = outcome != "inconclusive"

    async def _final_attempt(
        self, session: AsyncSession, lane: str, run_id: uuid.UUID
    ) -> tuple[bool, FinalAttempt | None]:
        """A settled run's final attempt, memoized: `(read_ok, attempt)`."""
        if run_id in self.state.final_attempts:
            return True, self.state.final_attempts[run_id]
        ok, attempt = await self._guard(
            session,
            f"select_run_final_attempt:{lane}",
            lambda: select_run_final_attempt(session, job_run_id=run_id),
            lane=lane,
        )
        if ok:
            self.state.final_attempts[run_id] = attempt
        return ok, attempt

    async def start_probe(
        self,
        session: AsyncSession,
        spec: LaneExecutionSpec,
        definition: JobDefinitionRecord,
        latest: LatestRun,
        verdict: CheckpointVerdict,
    ) -> DueLane | None:
        """Start one single-attempt probe of a held lane when its rung is due; `None` leaves the lane held.

        The `_release_by_process_start` pattern inside a savepoint: the probe is appended to the hold row
        (`probing`), the held run is superseded as `executor:probe`, both commit together, and only then does
        the planner open the bucket (`max_attempts=1`). A crash between that commit and the open re-opens the
        same probe next tick (`shape_bucket`). Any refusal or ledger fault rolls both back and keeps the hold.
        """
        lane = spec.lane_id
        ladders = self.state.ladders
        row = self._open_row("lane_hold", lane)
        if ladders is None or row is None or not verdict.newer_bucket_exists:
            return None
        progress = HoldProgress.parse(row.state)
        if progress.phase != "held":
            return None
        classified = await self._hold_class(session, lane, row.job_run_id)
        if classified is None:
            return None
        rung = _parse_count(row.rung, default=0)
        if not probe_is_due(
            ladders,
            exit_class=classified[0],
            rung=rung,
            entered_rung_at=row.last_seen_at,
            now=self.now,
            newer_bucket_exists=verdict.newer_bucket_exists,
        ):
            return None
        record: dict[str, object] = {
            "at": self.now.isoformat(),
            "bucket": verdict.next_bucket.isoformat(),
            "superseded_run_id": str(latest.run_id),
            "rung": rung,
            "via": "ladder",
            "outcome": None,
        }
        if not await self._begin_probe(session, spec, row, progress, latest, record):
            return None
        _log_at(
            "warn",
            EVENT_HOLD_PROBE,
            lane_id=lane,
            superseded_run_id=str(latest.run_id),
            bucket=verdict.next_bucket.isoformat(),
            rung=rung,
            exit_class=classified[0],
            via="ladder",
        )
        return DueLane(
            spec=spec,
            definition=definition,
            scheduled_for=verdict.next_bucket,
            existing_run_id=None,
            last_scheduled_for=latest.scheduled_for,
            superseded_run_id=latest.run_id,
            supersession="probe",
            probe=True,
            max_attempts=1,
        )

    async def _begin_probe(  # noqa: PLR0913 - the hold, its position, the run it supersedes and the record
        self,
        session: AsyncSession,
        spec: LaneExecutionSpec,
        row: LaneIncidentRow,
        progress: HoldProgress,
        latest: LatestRun,
        record: dict[str, object],
    ) -> bool:
        """Append the probe and supersede the held run in one savepoint, then commit; `False` keeps the hold."""
        from agri_data_service.execution.job_run_supersession import (  # noqa: PLC0415 - import cycle
            SupersessionRefusal,
            supersede_failed_run,
        )

        lane = spec.lane_id
        self._remember_probe(lane, record)
        try:
            async with session.begin_nested():
                written = await self._rewrite_hold(
                    session,
                    lane,
                    row,
                    state=HoldProgress(phase="probing", counter=progress.counter).encoded(),
                    pause_reason=None,
                    level=_level_of(row.severity),
                )
                if written is None:
                    raise _ProbeNotStartedError("the hold row could not be written")
                receipt = await supersede_failed_run(
                    session,
                    spec,
                    latest.run_id,
                    ledger=_ledger_label(),
                    evidence=(
                        f"hold-ladder probe at rung {record['rung']}: one single-attempt bucket re-tries lane "
                        f"{lane}; a failure re-holds it at the next rung"
                    ),
                    operator=PROBE_OPERATOR,
                    now=self.now,
                    apply=True,
                )
                if receipt.outcome not in {"recorded", "already_superseded"}:
                    raise _ProbeNotStartedError(f"the supersession came back {receipt.outcome}")
            committed, _ = await self._guard(
                session, f"commit:{lane}", lambda: _commit_planning_transaction(session), lane=lane
            )
            if not committed:
                raise _ProbeNotStartedError("the probe could not be committed")
        except (_ProbeNotStartedError, SupersessionRefusal, SQLAlchemyError) as refusal:
            if isinstance(refusal, SQLAlchemyError) and _pinned_connection_invalidated(session):
                raise
            await self._discard(session)
            self.open_rows[row.fingerprint] = row
            probes = self.state.hold_probes.get(lane)
            if probes and probes[-1] is record:
                probes.pop()
            _log_at(
                "warn",
                EVENT_HOLD_PROBE_REFUSED,
                lane_id=lane,
                run_id=str(latest.run_id),
                reason=type(refusal).__name__,
                detail=redact_text(str(refusal))[:INCIDENT_SUMMARY_MAX_CHARS],
            )
            return False
        return True

    async def record_deploy_probe(
        self, session: AsyncSession, spec: LaneExecutionSpec, latest: LatestRun, verdict: CheckpointVerdict
    ) -> None:
        """WQ-7 fold: an opt-in process-start release of a laddered hold is recorded as a probe (`via=deploy`)."""
        lane = spec.lane_id
        row = self._open_row("lane_hold", lane)
        if row is None:
            return
        progress = HoldProgress.parse(row.state)
        if progress.phase != "held":
            return
        self._remember_probe(
            lane,
            {
                "at": self.now.isoformat(),
                "bucket": verdict.next_bucket.isoformat(),
                "superseded_run_id": str(latest.run_id),
                "rung": _parse_count(row.rung, default=0),
                "via": "deploy",
                "outcome": None,
            },
        )
        written = await self._rewrite_hold(
            session,
            lane,
            row,
            state=HoldProgress(phase="probing", counter=progress.counter).encoded(),
            pause_reason=None,
            level=_level_of(row.severity),
        )
        if written is None:
            return
        committed, _ = await self._guard(
            session, f"commit:{lane}", lambda: _commit_planning_transaction(session), lane=lane
        )
        if committed:
            _log_at("warn", EVENT_HOLD_PROBE, lane_id=lane, superseded_run_id=str(latest.run_id), via="deploy")
        else:
            self.open_rows[row.fingerprint] = row
            await self._discard(session)

    def shape_bucket(self, spec: LaneExecutionSpec, latest: LatestRun | None, due: DueLane) -> DueLane:
        """A bucket's failure budget under the ladder: a probe (re-opened or in flight) and a probation bucket
        run once; for 24 h after a hold resolves, buckets run twice (once when the chain is flapping)."""
        lane = spec.lane_id
        row = self._open_row("lane_hold", lane)
        if row is not None:
            phase = HoldProgress.parse(row.state).phase
            if phase == "probing" and latest is not None and (latest.open or latest.superseded_by_operator):
                supersession = "probe" if due.superseded_run_id is not None else due.supersession
                return replace(due, probe=True, max_attempts=1, supersession=supersession)
            if phase == "probation" or (phase == "probing" and latest is not None and latest.status == "succeeded"):
                return replace(due, max_attempts=1)
            return due
        recent = self._recent_resolved_holds(lane)
        attempts = watch_max_attempts(
            None if not recent else recent[0].resolved_at, episodes_7d=len(recent), now=self.now
        )
        return due if attempts is None else replace(due, max_attempts=attempts)

    async def _observe_ladder_hold(self, session: AsyncSession, lane: str, plan: _LanePlan) -> None:
        """One laddered hold per tick: GL-5's record for `held` and `paused`, the ladder for the rest."""
        row = self._open_row("lane_hold", lane)
        if row is None:
            await self._observe_hold(session, lane, plan, ladder=True)
            return
        progress = HoldProgress.parse(row.state)
        if not plan.definition_enabled:
            if progress.phase != "paused":
                # Disabled mid-probe or mid-probation: paused like any hold, never resolved by the brake.
                outcome = ReconcileOutcome(action="pause", state="paused", pause_reason="disabled")
                await self._apply_hold_outcome(session, lane, row, outcome, still_held=False, announce_run=None)
            return
        if progress.phase in ("held", "paused"):
            await self._observe_hold(session, lane, plan, ladder=True)
            return
        if progress.phase == "probing":
            await self._observe_probe(session, lane, plan, row, progress)
            return
        await self._observe_probation(session, lane, plan, row, progress)

    async def _observe_probe(
        self, session: AsyncSession, lane: str, plan: _LanePlan, row: LaneIncidentRow, progress: HoldProgress
    ) -> None:
        """A probe in flight withholds repairs; a settled one moves the ladder (`after_probe`)."""
        latest = plan.latest
        in_flight = (
            plan.probe_started
            or latest is None
            or latest.open
            or (latest.superseded_by_operator and latest.status in SETTLED_WITHOUT_SUCCESS)
        )
        if in_flight or latest is None:
            self.held_lanes.add(lane)
            await self._escalate_chronic(session, lane, row)
            return
        outcome: ProbeOutcome
        if latest.status == "succeeded":
            outcome = "passed"
        else:
            read, attempt = await self._final_attempt(session, lane, latest.run_id)
            if not read:
                return
            outcome = judge_probe(latest.status, attempt)
        step = after_probe(outcome, rung=_parse_count(row.rung, default=0), inconclusive_probes=progress.counter)
        held_run = latest.run_id if step.adopt_probe_class else row.job_run_id
        classified = await self._hold_class(session, lane, held_run)
        if classified is None:
            return
        self._settle_remembered_probe(lane, outcome)
        written = await self._rewrite_hold(
            session,
            lane,
            row,
            state=step.progress.encoded(),
            pause_reason=None,
            level=self._hold_level(classified[0], row, step.progress),
            rung=step.rung,
            run_id=held_run,
        )
        if written is None:
            return
        if outcome == "passed":
            _log_at("info", EVENT_HOLD_PROBATION, lane_id=lane, rung=step.rung, run_id=str(latest.run_id))
            return
        self.held_lanes.add(lane)
        if outcome == "inconclusive" and step.reason is None:
            _log_at(
                "warn",
                EVENT_HOLD_PROBE_INCONCLUSIVE,
                lane_id=lane,
                run_id=str(latest.run_id),
                inconclusive_probes=step.progress.counter,
                rung=step.rung,
            )
            return
        _log_at(
            self._hold_level(classified[0], row, step.progress),
            EVENT_HOLD_PROBE_FAILED,
            lane_id=lane,
            run_id=str(latest.run_id),
            rung=step.rung,
            exit_class=classified[0],
            reason=step.reason,
        )

    async def _observe_probation(
        self, session: AsyncSession, lane: str, plan: _LanePlan, row: LaneIncidentRow, progress: HoldProgress
    ) -> None:
        """Probation re-holds at the next rung when the ledger holds again; otherwise it ends on proof or age."""
        latest = plan.latest
        if plan.operator_held and latest is not None:
            classified = await self._hold_class(session, lane, latest.run_id)
            if classified is None:
                return
            held = HoldProgress(phase="held")
            written = await self._rewrite_hold(
                session,
                lane,
                row,
                state=held.encoded(),
                pause_reason=None,
                level=self._hold_level(classified[0], row, held),
                rung=_parse_count(row.rung, default=0) + 1,
                run_id=latest.run_id,
            )
            if written is not None:
                self.held_lanes.add(lane)
                _log_at(
                    "warn",
                    EVENT_HOLD_PROBE_FAILED,
                    lane_id=lane,
                    run_id=str(latest.run_id),
                    rung=_parse_count(row.rung, default=0) + 1,
                    exit_class=classified[0],
                    reason="failed_in_probation",
                )
            return
        verdict = judge_probation(clean_buckets=progress.counter, last_change_at=row.last_seen_at, now=self.now)
        if verdict == "resolve":
            await self._resolve_hold(session, lane, row, released_by="probe", reason="probation_clean")
        elif verdict == "expired" and await self._resolve_hold(
            session, lane, row, released_by="probe", reason="probation_expired"
        ):
            # 48 h without a failure but without proof either: a masked failure must still surface.
            await self._open_incomplete(session, lane, reason="inconclusive")

    async def _open_incomplete(self, session: AsyncSession, lane: str, *, reason: str) -> None:
        """Open or bump `lane_incomplete:<lane>` with `reason`, stepping its escalation by episode age."""
        row = self._open_row("lane_incomplete", lane)
        first_seen = self.now if row is None else row.first_seen_at
        step, level = _incomplete_step(self.now - first_seen)
        await self._upsert(
            session,
            kind="lane_incomplete",
            subject=lane,
            level=level,
            summary=f"lane {lane!r} exited 0 but owes work ({reason})",
            detail={"lane_id": lane, "reason": reason, "state": step},
            lane=lane,
        )

    async def _observe_probation_turn(self, session: AsyncSession, lane: str, verdict: _TurnVerdict) -> None:
        """A probation bucket's turn: a conclusive clean one counts toward release, a failed one restarts it."""
        row = self._open_row("lane_hold", lane)
        if self.ladder_gate(lane) is None or row is None:
            return
        progress = HoldProgress.parse(row.state)
        if progress.phase != "probation" or verdict.exit_class in ("interrupted", "lease_lost"):
            return
        if verdict.exit_class == "ok":
            if verdict.turn_outcome != "completed" or not verdict.conclusive:
                return  # an incomplete or inconclusive bucket neither proves nor fails the lane
            clean = HoldProgress(phase="probation", counter=progress.counter + 1)
            if clean.counter >= PROBATION_CLEAN_BUCKETS:
                await self._resolve_hold(session, lane, row, released_by="probe", reason="probation_clean")
                return
            next_progress = clean
        else:
            next_progress = HoldProgress(phase="probation")  # a failed bucket restarts the count and the 48 h
        await self._rewrite_hold(
            session,
            lane,
            row,
            state=next_progress.encoded(),
            pause_reason=None,
            level=_level_of(row.severity),
        )

    async def _observe_blocked(self, session: AsyncSession, lane: str, plan: _LanePlan) -> None:
        row = self._open_row("lane_blocked", lane)
        result = plan.result
        if plan.blocked and result is not None:
            once_key = f"lane_blocked:{lane}:{result.run_id}"
            if row is None:
                upserted = await self._upsert(
                    session,
                    kind="lane_blocked",
                    subject=lane,
                    level="warn",
                    summary=f"lane {lane!r} is blocked: {result.detail}",
                    detail={"lane_id": lane, "run_id": None if result.run_id is None else str(result.run_id)},
                    lane=lane,
                    run_id=result.run_id,
                )
                if upserted is not None and once_key not in self.state.logged_once:
                    self.state.logged_once.add(once_key)
                    _log_at(
                        "warn",
                        events.EVENT_LANE_BLOCKED,
                        lane_id=lane,
                        run_id=None if result.run_id is None else str(result.run_id),
                        detail=result.detail,
                    )
            return
        if row is not None and await self._resolve(
            session, row, detail_patch={"resolution_reason": "cleared"}, lane=lane
        ):
            prefix = f"lane_blocked:{lane}:"
            self.state.logged_once = {key for key in self.state.logged_once if not key.startswith(prefix)}
            _log_at("info", events.EVENT_LANE_UNBLOCKED, lane_id=lane)

    async def after_planning(self, session: AsyncSession) -> None:
        """Pause the holds of lanes this tick did not plan (inactive or quarantined), retire every other
        lane-scoped row that no longer has an exit, then correlate fleets."""
        if not self.read_ok:
            return
        holds = sorted(
            (row for row in self.open_rows.values() if row.incident_type == "lane_hold"),
            key=lambda row: row.fingerprint,
        )
        for row in holds:
            lane = _incident_subject(row)
            if not self.activation.is_active(lane):
                await self._isolated(session, lane, partial(self._pause_inactive, lane=lane, row=row))
        for row, reason in self._retirable_rows():
            lane = _incident_subject(row)
            await self._isolated(session, lane, partial(self._retire, row=row, reason=reason, lane=lane))
        await self._isolated(session, None, self._correlate_fleet)

    def _retirable_rows(self) -> list[tuple[LaneIncidentRow, str]]:
        """Open lane-scoped rows whose only exit (a planned lane, a turn, a repair success) can no longer
        come: the lane left the allow-list or was quarantined, or its repair breaker has been quiet for
        `REPAIR_FAILING_QUIET_PERIOD` past the point it admits runs again."""
        retirable: list[tuple[LaneIncidentRow, str]] = []
        for row in sorted(self.open_rows.values(), key=lambda row: row.fingerprint):
            if row.incident_type not in _INACTIVE_RETIRED_KINDS:
                continue
            lane = _incident_subject(row)
            if row.incident_type == "executor_lease_lost" and lane == LEASE_LOST_FLEET_SUBJECT:
                continue
            if not self.activation.is_active(lane):
                retirable.append((row, RESOLUTION_LANE_INACTIVE))
            elif row.incident_type == "lane_repair_failing":
                breaker = _repair_breaker_state(row)
                admits_from = breaker.cooldown_until or row.last_seen_at
                if self.now - admits_from >= REPAIR_FAILING_QUIET_PERIOD:
                    retirable.append((row, RESOLUTION_REPAIR_QUIET))
        return retirable

    async def _retire(self, session: AsyncSession, *, row: LaneIncidentRow, reason: str, lane: str | None) -> None:
        await self._resolve(session, row, detail_patch={"resolution_reason": reason}, lane=lane)

    async def retire_authoring(self, session: AsyncSession) -> None:
        """Repair authoring is switched off: an open `executor_repair_authoring` streak can never clear."""
        row = self._open_row("executor_repair_authoring", None)
        if not self.read_ok or row is None:
            return
        await self._isolated(
            session, None, partial(self._retire, row=row, reason=RESOLUTION_AUTHORING_DISABLED, lane=None)
        )

    async def _pause_inactive(self, session: AsyncSession, *, lane: str, row: LaneIncidentRow) -> None:
        """A hold on a lane outside the allow-list (or quarantined) is paused, never escalated."""
        outcome = reconcile(
            state=_hold_state(row),
            verdict=HoldVerdict(
                still_held=True,
                definition_enabled=True,
                lane_active=False,
                lane_quarantined=lane in self.activation.quarantined,
                superseded_by_operator=False,
                superseded_run_id=None,
            ),
        )
        await self._apply_hold_outcome(session, lane, row, outcome, still_held=False, announce_run=None)

    async def _correlate_fleet(self, session: AsyncSession) -> None:
        """`fleet:<exit_class>`: 3 or more lanes opening holds of one class within an hour log ONE error."""
        recent: dict[ExitClass, set[str]] = {}
        classes: dict[str, ExitClass] = {}
        every_class_known = True
        for lane, run_id in sorted(self.open_holds.items(), key=lambda item: item[0]):
            classified = await self._hold_class(session, lane, run_id)
            if classified is None:
                every_class_known = False
                continue
            classes[lane] = classified[0]
            row = self._open_row("lane_hold", lane)
            first_seen = self.now if lane in self.opened_this_tick or row is None else row.first_seen_at
            if self.now - first_seen <= FLEET_WINDOW:
                recent.setdefault(classified[0], set()).add(lane)
        for exit_class, lanes in sorted(recent.items()):
            if len(lanes) < FLEET_HOLD_THRESHOLD or self._open_row("fleet", exit_class) is not None:
                continue
            upserted = await self._upsert(
                session,
                kind="fleet",
                subject=exit_class,
                level="error",
                summary=f"{len(lanes)} lanes opened {exit_class} holds within one hour",
                detail={"exit_class": exit_class, "members": sorted(lanes)},
            )
            once_key = f"fleet:{exit_class}"
            if upserted is not None and once_key not in self.state.logged_once:
                self.state.logged_once.add(once_key)
                _log_at("error", events.EVENT_FLEET_CORRELATED, exit_class=exit_class, members=sorted(lanes))
        for row in [row for row in self.open_rows.values() if row.incident_type == "fleet"]:
            fleet_class = _incident_subject(row)
            if every_class_known and fleet_class not in classes.values():
                await self._resolve(session, row, detail_patch={"resolution_reason": "every_member_resolved"})
                self.state.logged_once.discard(f"fleet:{fleet_class}")

    def repair_withholding(self) -> dict[str, str]:
        """Lane -> why its repairs are withheld this tick; empty when the switch is off or the read failed.

        A degraded lane is never withheld: without a trustworthy row it plans repairs as HEAD did.
        """
        if not self.read_ok or not self.state.enabled:
            withheld: dict[str, str] = {}
        else:
            withheld = {lane: "lane_hold" for lane in self.held_lanes if lane not in self.degraded}
            for row in self.open_rows.values():
                if row.incident_type != "lane_repair_failing":
                    continue
                lane = _incident_subject(row)
                if lane in self.degraded:
                    continue
                if not repair_breaker_admits(_repair_breaker_state(row), now=self.now):
                    withheld.setdefault(lane, "repair_breaker")
        previous = self.state.repair_withheld
        for lane in sorted(withheld.keys() - previous):
            _log_at("info", events.EVENT_REPAIR_WITHHELD, lane_id=lane, withheld=True, reason=withheld[lane])
        for lane in sorted(previous - withheld.keys()):
            _log_at("info", events.EVENT_REPAIR_WITHHELD, lane_id=lane, withheld=False)
        self.state.repair_withheld = frozenset(withheld)
        return withheld

    def withheld_results(self, withheld: Mapping[str, str]) -> list[LaneTickResult]:
        return [
            LaneTickResult(
                lane_id=f"{lane}{REPAIR_LANE_SUFFIX}",
                state="paused",
                detail=(
                    "repairs withheld while the lane is ledger-held"
                    if reason == "lane_hold"
                    else "repairs withheld by the repair breaker until its cooldown lifts"
                ),
            )
            for lane, reason in sorted(withheld.items())
            if lane in REPAIR_LANE_IDS and self.activation.is_active(lane)
        ]

    async def observe_budget(self, session: AsyncSession, admission: AdmissionPass) -> None:
        """WQ-4's two incidents per capped pool (spec Sec 4.9.3's table; execution/AGENTS.md "Budget admission").

        `budget_deferred:<pool>` is bumped on a lane's first refusal of the UTC day (or when refusals continue
        with no open row) and resolves on a tick that admits a turn on the pool and refuses none;
        `budget_basis_suspect:<pool>` is open while suspect exceeds 10 % of charged, and resolves when the
        share falls or the UTC month rolls over. Neither escalates.
        """
        if not self.read_ok or not admission.pools:
            return

        async def body(active_session: AsyncSession) -> None:
            for pool, outcome in sorted(admission.pools.items()):
                await self._observe_budget_deferral(active_session, pool, outcome)
                await self._observe_budget_basis(active_session, pool, outcome)

        await self._isolated(session, None, body)

    async def _observe_budget_deferral(self, session: AsyncSession, pool: str, outcome: PoolAdmission) -> None:
        row = self._open_row(BUDGET_DEFERRED_KIND, pool)
        if outcome.refused and (outcome.newly_warned or row is None):
            spend = outcome.spend
            await self._upsert(
                session,
                kind=BUDGET_DEFERRED_KIND,
                subject=pool,
                level="warn",
                summary=f"the {pool} cap refused {', '.join(sorted(outcome.refused))}; no run was opened",
                detail={
                    "pool": pool,
                    "refused": dict(sorted(outcome.refused.items())),
                    "charged": None if spend is None else spend.charged,
                    "suspect": None if spend is None else spend.suspect,
                    "gap_fill_ceiling": outcome.budget.gap_fill_ceiling_calls,
                    "forward_stop": outcome.budget.forward_stop_calls,
                },
            )
        elif row is not None and outcome.admitted and not outcome.refused:
            await self._resolve(session, row, detail_patch={"resolution_reason": "admitted_turn"})

    async def _observe_budget_basis(self, session: AsyncSession, pool: str, outcome: PoolAdmission) -> None:
        spend = outcome.spend
        if spend is None:
            return  # an unread pool proves nothing either way
        row = self._open_row(BUDGET_BASIS_SUSPECT_KIND, pool)
        if row is not None and not same_utc_month(row.first_seen_at, self.now):
            await self._resolve(session, row, detail_patch={"resolution_reason": "month_rolled_over"})
            row = None
        if not spend.basis_suspect:
            if row is not None:
                await self._resolve(session, row, detail_patch={"resolution_reason": "suspect_share_below_limit"})
            return
        if row is not None:
            return
        upserted = await self._upsert(
            session,
            kind=BUDGET_BASIS_SUSPECT_KIND,
            subject=pool,
            level="warn",
            summary=f"suspect spend is over 10% of charged spend on {pool}; gap-fill is refused until it falls",
            detail={"pool": pool, "charged": spend.charged, "suspect": spend.suspect},
        )
        if upserted is not None:
            _log_at("warn", EVENT_BUDGET_BASIS_SUSPECT, pool=pool, charged=spend.charged, suspect=spend.suspect)

    async def observe_authoring(self, session: AsyncSession, *, succeeded: bool) -> None:
        if not self.read_ok:
            return

        async def body(active_session: AsyncSession) -> None:
            row = self._open_row("executor_repair_authoring", None)
            if succeeded:
                if row is not None:
                    await self._resolve(active_session, row, detail_patch={"resolution_reason": "authoring_succeeded"})
                return
            bumped = await self._bump_streak(
                active_session,
                kind="executor_repair_authoring",
                subject=None,
                escalate_at=REPAIR_AUTHORING_ESCALATION_COUNT,
                summary="the leader's repair authoring pass raised; forward lanes are unaffected",
                detail={},
            )
            if bumped is not None and bumped[1]:
                _log_at("error", events.EVENT_REPAIR_AUTHORING_FAILED, consecutive_intervals=bumped[0], escalated=True)

        await self._isolated(session, None, body)

    async def record_plan_failures(self, session: AsyncSession, results: Iterable[LaneTickResult]) -> None:
        """`lane_plan_failed:<lane>` for every lane whose planning raised or degraded; a clean pass resolves."""
        failed: dict[str, str] = {
            result.lane_id: (result.detail or "")[len(PLAN_FAILED_DETAIL_PREFIX) :].split(";", 1)[0]
            for result in results
            if result.detail is not None and result.detail.startswith(PLAN_FAILED_DETAIL_PREFIX)
        }
        for lane, reason in self.degraded.items():
            failed.setdefault(lane, reason)
        self.state.plan_failed_lanes = tuple(sorted(failed))
        if not self.read_ok:
            return

        async def body(active_session: AsyncSession) -> None:
            for lane, reason in sorted(failed.items()):
                bumped = await self._bump_streak(
                    active_session,
                    kind="lane_plan_failed",
                    subject=lane,
                    escalate_at=STREAK_ESCALATION_COUNT,
                    summary=f"planning lane {lane!r} raised or degraded ({reason}); every other lane planned",
                    detail={"lane_id": lane, "reason": reason},
                )
                if bumped is not None and bumped[1]:
                    _log_at(
                        "error", events.EVENT_LANE_PLAN_FAILED, lane_id=lane, reason=reason, consecutive_ticks=bumped[0]
                    )
            for row in [row for row in self.open_rows.values() if row.incident_type == "lane_plan_failed"]:
                if _incident_subject(row) not in failed:
                    await self._resolve(active_session, row, detail_patch={"resolution_reason": "clean_planning_pass"})

        await self._isolated(session, None, body)

    async def observe_turn(
        self,
        session: AsyncSession,
        candidate: DueLane,
        result: LaneTickResult,
        verdict: _TurnVerdict | None,
    ) -> None:
        """Fold one executed turn into its streak incidents (report missing, incomplete, lease lost, repair)."""
        if verdict is None:
            return
        lane_id = candidate.spec.lane_id
        self.dispatched.append(lane_id)
        if verdict.exit_class == "lease_lost":
            self.lease_lost.append(lane_id)
        if not self.read_ok:
            return
        if lane_id.endswith(REPAIR_LANE_SUFFIX):
            owning = lane_id[: -len(REPAIR_LANE_SUFFIX)]

            async def repair_body(active_session: AsyncSession) -> None:
                await self._observe_repair_turn(active_session, owning, result, verdict)

            await self._isolated(session, owning, repair_body)
            return

        async def forward_body(active_session: AsyncSession) -> None:
            await self._observe_report(active_session, lane_id, verdict)
            await self._observe_incomplete(active_session, lane_id, verdict)
            await self._observe_lease(active_session, lane_id, verdict)
            await self._observe_probation_turn(active_session, lane_id, verdict)

        await self._isolated(session, lane_id, forward_body)

    async def _observe_report(self, session: AsyncSession, lane: str, verdict: _TurnVerdict) -> None:
        if verdict.exit_class == "report_missing":
            bumped = await self._bump_streak(
                session,
                kind="lane_report_missing",
                subject=lane,
                escalate_at=STREAK_ESCALATION_COUNT,
                summary=f"lane {lane!r} exited 0 without a terminal report",
                detail={"lane_id": lane},
                lane=lane,
            )
            if bumped is not None:
                count, escalated = bumped
                _log_at(
                    "error" if count >= STREAK_ESCALATION_COUNT else "warn",
                    events.EVENT_LANE_REPORT_MISSING,
                    lane_id=lane,
                    consecutive_turns=count,
                    escalated=escalated,
                )
            return
        row = self._open_row("lane_report_missing", lane)
        if verdict.report_present and row is not None:
            await self._resolve(session, row, detail_patch={"resolution_reason": "report_seen"}, lane=lane)

    async def _observe_incomplete(self, session: AsyncSession, lane: str, verdict: _TurnVerdict) -> None:
        row = self._open_row("lane_incomplete", lane)
        if verdict.turn_outcome == "incomplete":
            first_seen = self.now if row is None else row.first_seen_at
            step, level = _incomplete_step(self.now - first_seen)
            reason = verdict.incomplete_reason or "inconclusive"
            upserted = await self._upsert(
                session,
                kind="lane_incomplete",
                subject=lane,
                level=level,
                summary=f"lane {lane!r} exited 0 but owes work ({reason})",
                detail={"lane_id": lane, "reason": reason, "state": step},
                lane=lane,
            )
            if (
                upserted is not None
                and row is not None
                and step != (row.state or INCOMPLETE_OPEN_STATE)
                and not _acknowledged(row)
            ):
                _log_at(level, events.EVENT_LANE_INCOMPLETE_ESCALATED, lane_id=lane, step=step, reason=reason)
            return
        # Only a CONCLUSIVE complete turn clears it (`PROGRESS_EVIDENCE`): a probation that expired unproven opens
        # this row as `inconclusive`, and another turn that proves nothing must not close it again.
        if (
            verdict.turn_outcome == "completed"
            and verdict.conclusive
            and row is not None
            and await self._resolve(session, row, detail_patch={"resolution_reason": "complete_turn"}, lane=lane)
        ):
            _log_at("info", events.EVENT_LANE_INCOMPLETE_CLEARED, lane_id=lane)

    async def _observe_lease(self, session: AsyncSession, lane: str, verdict: _TurnVerdict) -> None:
        if verdict.exit_class == "lease_lost":
            bumped = await self._bump_streak(
                session,
                kind="executor_lease_lost",
                subject=lane,
                escalate_at=STREAK_ESCALATION_COUNT,
                summary=f"lane {lane!r} lost its fenced lease while its command ran",
                detail={"lane_id": lane},
                lane=lane,
            )
            if bumped is not None and bumped[1]:
                _log_at("error", events.EVENT_LEASE_LOST_ESCALATED, lane_id=lane, consecutive=bumped[0])
            return
        row = self._open_row("executor_lease_lost", lane)
        if verdict.exit_class != "interrupted" and row is not None:
            await self._resolve(session, row, detail_patch={"resolution_reason": "attempt_kept_its_lease"}, lane=lane)

    async def _observe_repair_turn(
        self, session: AsyncSession, lane: str, result: LaneTickResult, verdict: _TurnVerdict
    ) -> None:
        """`lane_repair_failing:<lane>` and the repair breaker (spec Sec 4.9.3; never a `lane_hold`)."""
        if verdict.failure_class == "invalid_repair_request":
            return  # a malformed candidate, never a run failure: it never counts for the breaker
        row = self._open_row("lane_repair_failing", lane)
        if verdict.exit_class == "ok":
            tripped_before = row is not None and _repair_breaker_state(row).trip_count > 0
            if (
                row is not None
                and await self._resolve(session, row, detail_patch={"resolution_reason": "repair_succeeded"}, lane=lane)
                and tripped_before
            ):
                _log_at("info", events.EVENT_REPAIR_BREAKER_RELEASED, lane_id=lane)
            return
        if result.run_status not in SETTLED_WITHOUT_SUCCESS:
            return  # an attempt that will be retried is not yet a settled repair failure
        current = _repair_breaker_state(row)
        if verdict.exit_class in REPAIR_BREAKER_TRIPPING_CLASSES:
            folded = evaluate_repair_breaker(current, exit_class=verdict.exit_class, now=self.now)
            breaker, tripped = folded.state, folded.tripped_this_turn
        else:  # upstream/infra/lease: still failing, but it breaks a run of code-class failures
            breaker, tripped = replace(current, consecutive_failures=0, cooldown_until=None), False
        upserted = await self._upsert(
            session,
            kind="lane_repair_failing",
            subject=lane,
            level="error" if tripped else "warn",
            summary=f"repair runs for lane {lane!r} are settling failed ({verdict.exit_class})",
            detail={
                "lane_id": lane,
                "state": _COOLDOWN_STATE if tripped else f"{_COUNTING_STATE_PREFIX}{breaker.consecutive_failures}",
                "rung": breaker.trip_count,
                "exit_class": verdict.exit_class,
                "cooldown_until": None if breaker.cooldown_until is None else breaker.cooldown_until.isoformat(),
            },
            lane=lane,
            run_id=result.run_id,
        )
        if upserted is not None and tripped:
            _log_at(
                "error",
                events.EVENT_REPAIR_BREAKER_OPENED,
                lane_id=lane,
                trip_count=breaker.trip_count,
                cooldown_days=repair_breaker_cooldown_days(breaker.trip_count),
                cooldown_until=None if breaker.cooldown_until is None else breaker.cooldown_until.isoformat(),
            )

    async def close(self, session: AsyncSession) -> None:
        """`executor_lease_lost:fleet` (every dispatched lane lost its lease), then publish the tick's memos."""
        if self.read_ok and len(set(self.dispatched)) >= LEASE_LOST_FLEET_MIN_LANES:
            everything_lost = set(self.dispatched) <= set(self.lease_lost)

            async def body(active_session: AsyncSession) -> None:
                row = self._open_row("executor_lease_lost", LEASE_LOST_FLEET_SUBJECT)
                if everything_lost:
                    bumped = await self._bump_streak(
                        active_session,
                        kind="executor_lease_lost",
                        subject=LEASE_LOST_FLEET_SUBJECT,
                        escalate_at=STREAK_ESCALATION_COUNT,
                        summary="every dispatched lane lost its fenced lease in one tick",
                        detail={"lanes": sorted(set(self.dispatched))},
                    )
                    if bumped is not None and bumped[1]:
                        _log_at("error", events.EVENT_LEASE_LOST_ESCALATED, lane_id=LEASE_LOST_FLEET_SUBJECT)
                elif row is not None:
                    await self._resolve(
                        active_session, row, detail_patch={"resolution_reason": "a_lane_kept_its_lease"}
                    )

            await self._isolated(session, None, body)
        self.state.durable_announcements = frozenset(self.durable)
        self.state.chronic_lanes = tuple(sorted(self.chronic))


def _log_tick_partial(results: list[LaneTickResult], error: BaseException) -> None:
    """`tick_partial` before any re-raise (spec Sec 4.9.3): what this tick DID settle is never lost with it."""
    with suppress(Exception):
        logger.warning(
            events.EVENT_TICK_PARTIAL,
            error_type=type(error).__name__,
            lane_count=len(results),
            lanes=[{"lane_id": result.lane_id, "state": result.state} for result in results],
        )


def _without_lanes(activation: ActivationConfig, withheld: AbstractSet[str]) -> ActivationConfig:
    """The repair planners' activation: the same allow-list minus the lanes whose repairs are withheld.

    `plan_gap_repairs` and `_plan_repair_runs` both pass `active_lanes` straight through, so narrowing it
    here withholds a lane from authoring AND skips its already-open repair run with no edit to either.
    """
    if not withheld:
        return activation
    return replace(activation, active_lanes=activation.active_lanes - withheld)


# --- The S15 work queue (spec S15/S16, FR-7; execution/AGENTS.md, "Work queue") --------------------------


@dataclass(frozen=True, slots=True)
class DispatchedTurn:
    """One lane turn the work queue finished: what the tick reports and what its incidents fold."""

    candidate: DueLane
    result: LaneTickResult
    verdict: _TurnVerdict | None = None


async def _invalidate_connection(session: AsyncSession, event: str) -> None:
    """Drop a pinned connection that could not release its lock, so PostgreSQL frees the lock with it."""
    bind = getattr(session, "bind", None)
    if not isinstance(bind, AsyncConnection):
        return
    try:
        await bind.invalidate()
    except BaseException as error:
        logger.error(event, error_type=type(error).__name__)


async def _release_lane_lock(session: AsyncSession, definition_name: str) -> None:
    """Release a lane session's definition lock before its connection returns to the pool."""
    try:
        await session.rollback()
        released = await release_definition_lock(session, definition_name)
        await session.rollback()
    except Exception as error:  # a lock that cannot be released must never outlive this connection
        logger.error(
            "plantgeo_job_executor_lane_unlock_failed", definition=definition_name, error_type=type(error).__name__
        )
        await _invalidate_connection(session, "plantgeo_job_executor_lane_connection_invalidate_failed")
        return
    if not released:
        logger.error("plantgeo_job_executor_lane_lock_not_held", definition=definition_name)


async def _release_provider_lock(session: AsyncSession, pool: str) -> None:
    """Release a lane session's provider lock (WQ-4); a lock that cannot be released drops the connection with it."""
    try:
        await session.rollback()
        released = await release_provider_lock(session, pool)
        await session.rollback()
    except Exception as error:  # the provider lock must never outlive this connection
        logger.error("plantgeo_job_executor_provider_unlock_failed", pool=pool, error_type=type(error).__name__)
        await _invalidate_connection(session, "plantgeo_job_executor_lane_connection_invalidate_failed")
        return
    if not released:
        logger.error("plantgeo_job_executor_provider_lock_not_held", pool=pool)


async def _drive_dispatched_lane(
    candidate: DueLane,
    *,
    open_lane_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    stop: ShutdownSignal | None,
) -> DispatchedTurn:
    """One queued lane turn on its OWN session: take the definition lock, drive `run_job_slice`, release it.

    The run was already opened by the leader (`_dispatch_due_lanes`), so this only drives it. A lane-local
    fault is this lane's `failed` result and never reaches the leader; a cancellation (leader loss, shutdown)
    propagates after the lock is released, and the worker parks the shard in hand on its way out.
    """
    spec = candidate.spec
    async with open_lane_session() as session:
        await apply_statement_timeout(session)
        if not await try_definition_lock(session, spec.definition_name):
            await session.rollback()
            return DispatchedTurn(
                candidate,
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="not_due",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="another executor session holds this lane's lock; a lane never runs twice",
                ),
            )
        pool = candidate.provider_pool
        provider_locked = False
        try:
            await session.commit()  # the lock is session-level: it outlives this and every later commit
            if pool is not None:
                # WQ-4: one turn spends a capped pool at a time, across processes; a busy pool waits, never blocks.
                if not await try_provider_lock(session, pool):
                    await session.rollback()
                    return DispatchedTurn(
                        candidate,
                        LaneTickResult(
                            lane_id=spec.lane_id,
                            state="deferred_fairness",
                            scheduled_for=candidate.scheduled_for,
                            run_id=candidate.existing_run_id,
                            detail=f"another turn holds the {pool} provider lock; this run waits for the next tick",
                        ),
                    )
                provider_locked = True
                await session.commit()
            _LANE_TURN_VERDICTS.pop(spec.lane_id, None)
            result = await _execute_due_lane(session, candidate, stop=stop)
            return DispatchedTurn(candidate, result, _LANE_TURN_VERDICTS.pop(spec.lane_id, None))
        except Exception as error:  # this lane's fault only: its own session, never the leader's
            _LANE_TURN_VERDICTS.pop(spec.lane_id, None)
            logger.error("plantgeo_job_executor_lane_failed", lane_id=spec.lane_id, error_type=type(error).__name__)
            return DispatchedTurn(
                candidate,
                LaneTickResult(
                    lane_id=spec.lane_id,
                    state="failed",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail=f"scheduler lane failed ({type(error).__name__})",
                ),
            )
        finally:
            if provider_locked and pool is not None:
                await _release_provider_lock(session, pool)
            await _release_lane_lock(session, spec.definition_name)


class LaneDispatcher:
    """S15's work queue: each dispatched lane runs as its own task, on its own session and definition lock.

    The leader tick hands a lane over and returns without awaiting it; a later tick `collect`s what finished
    and folds each verdict into its incidents on the leader session. At most `max_concurrent_lanes` tasks run
    at once, and a definition already in flight is never dispatched again (its session-level lock keeps that
    true across executor processes as well). `cancel_all` is leader loss.
    """

    def __init__(
        self,
        *,
        open_lane_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
        max_concurrent_lanes: int = DEFAULT_MAX_CONCURRENT_LANES,
        stop: ShutdownSignal | None = None,
    ) -> None:
        if max_concurrent_lanes < 1:
            raise ExecutorConfigurationError("max_concurrent_lanes must be at least 1")
        self._open_lane_session = open_lane_session
        self.max_concurrent_lanes = max_concurrent_lanes
        self._stop = stop
        self._in_flight: dict[str, tuple[DueLane, asyncio.Task[DispatchedTurn]]] = {}
        self._finished: list[DispatchedTurn] = []
        #: The class of the last lane this queue dispatched; `next_lead` hands the other class the head.
        self._last_dispatched_class: LaneWorkClass | None = None

    @property
    def next_lead(self) -> LaneWorkClass:
        """The work class `fair_due_order` puts first: the one NOT dispatched last, so backlog is never starved."""
        return "backlog" if self._last_dispatched_class == "incremental" else "incremental"

    @property
    def in_flight(self) -> tuple[str, ...]:
        """The definitions whose turn is still running, in dispatch order."""
        self._reap()
        return tuple(self._in_flight)

    def is_running(self, definition_name: str) -> bool:
        self._reap()
        return definition_name in self._in_flight

    def free_slots(self) -> int:
        self._reap()
        return self.max_concurrent_lanes - len(self._in_flight)

    def dispatch(self, candidate: DueLane) -> None:
        """Start one lane's turn without awaiting it; its run must already be open (`existing_run_id`)."""
        name = candidate.spec.definition_name
        if candidate.existing_run_id is None:
            raise ExecutorConfigurationError(f"lane {candidate.spec.lane_id!r} was dispatched before its run opened")
        if self.is_running(name) or self.free_slots() <= 0:
            raise ExecutorConfigurationError(f"lane {candidate.spec.lane_id!r} has no free slot or is already running")
        task = asyncio.create_task(
            _drive_dispatched_lane(candidate, open_lane_session=self._open_lane_session, stop=self._stop),
            name=f"plantgeo-executor-lane:{name}",
        )
        self._in_flight[name] = (candidate, task)
        self._last_dispatched_class = candidate.spec.work_class

    def _reap(self) -> None:
        for name, (candidate, task) in list(self._in_flight.items()):
            if not task.done():
                continue
            del self._in_flight[name]
            self._finished.append(self._settled(candidate, task))

    @staticmethod
    def _settled(candidate: DueLane, task: asyncio.Task[DispatchedTurn]) -> DispatchedTurn:
        if task.cancelled():
            return DispatchedTurn(
                candidate,
                LaneTickResult(
                    lane_id=candidate.spec.lane_id,
                    state="deferred_shutdown",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="this lane's turn was cancelled (leader loss or shutdown); its shard was handed back",
                ),
            )
        error = task.exception()
        if error is not None:
            return DispatchedTurn(
                candidate,
                LaneTickResult(
                    lane_id=candidate.spec.lane_id,
                    state="failed",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail=f"scheduler lane failed ({type(error).__name__})",
                ),
            )
        return task.result()

    def collect(self) -> list[DispatchedTurn]:
        """Every turn that finished since the last collection, oldest dispatch first."""
        self._reap()
        finished, self._finished = self._finished, []
        return finished

    async def cancel_all(self, *, reason: str) -> list[DispatchedTurn]:
        """Leader loss: cancel every lane in flight and wait for each to hand its shard back."""
        tasks = [task for _, task in self._in_flight.values()]
        if tasks:
            logger.error("plantgeo_job_executor_lanes_cancelled", reason=reason, lanes=list(self._in_flight))
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.collect()

    async def drain(self, *, timeout_seconds: float) -> list[DispatchedTurn]:
        """Wait for in-flight lanes to finish (a stop request parks them), then cancel what is left."""
        tasks = [task for _, task in self._in_flight.values()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout_seconds)
        return await self.cancel_all(reason="drain_timeout")


async def _dispatch_due_lanes(
    session: AsyncSession,
    ordered: Iterable[DueLane],
    *,
    dispatcher: LaneDispatcher,
    stop: ShutdownSignal | None,
) -> list[LaneTickResult]:
    """Open each due lane's run on the leader session and hand it to the queue, in `fair_due_order`.

    A lane already running is reported `running` (it never runs twice); once every slot is busy the rest
    wait for the next tick (`deferred_fairness`). Opening a run is HEAD's statement: a SQL fault or a lost
    pinned connection re-raises exactly as the serial loop does, and any other fault is that lane's alone.
    """
    results: list[LaneTickResult] = []
    for candidate in ordered:
        lane_id = candidate.spec.lane_id
        if dispatcher.is_running(candidate.spec.definition_name):
            results.append(
                LaneTickResult(
                    lane_id=lane_id,
                    state="running",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="this lane's turn is still running on its own session",
                )
            )
            continue
        if stop is not None and stop.requested:
            results.append(_deferred_shutdown_result(candidate))
            continue
        if dispatcher.free_slots() <= 0:
            results.append(
                LaneTickResult(
                    lane_id=lane_id,
                    state="deferred_fairness",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail="due; every lane slot is busy, so it waits for the next free one",
                )
            )
            continue
        try:
            run_id = candidate.existing_run_id or await _open_scheduled_run(session, candidate)
        except Exception as error:  # isolate lane-local faults only while the pinned backend is intact
            await session.rollback()
            if isinstance(error, SQLAlchemyError) or _pinned_connection_invalidated(session):
                logger.error(
                    "plantgeo_job_executor_pinned_connection_lost", lane_id=lane_id, error_type=type(error).__name__
                )
                raise
            await apply_statement_timeout(session)
            logger.error("plantgeo_job_executor_lane_failed", lane_id=lane_id, error_type=type(error).__name__)
            results.append(
                LaneTickResult(
                    lane_id=lane_id,
                    state="failed",
                    scheduled_for=candidate.scheduled_for,
                    run_id=candidate.existing_run_id,
                    detail=f"scheduler lane failed ({type(error).__name__})",
                )
            )
            continue
        dispatcher.dispatch(replace(candidate, existing_run_id=run_id))
        results.append(
            LaneTickResult(
                lane_id=lane_id,
                state="dispatched",
                scheduled_for=candidate.scheduled_for,
                run_id=run_id,
                detail="probe turn handed to the work queue" if candidate.probe else "handed to the work queue",
            )
        )
    return results


@asynccontextmanager
async def _lane_session_slots(
    database_url: str, slots: int
) -> AsyncIterator[Callable[[], AbstractAsyncContextManager[AsyncSession]]]:
    """One single-connection pool per queue slot, so N lanes hold N connections and never the leader's."""
    async with AsyncExitStack() as stack:
        engines: asyncio.Queue[AsyncEngine] = asyncio.Queue()
        for _ in range(slots):
            engines.put_nowait(await stack.enter_async_context(local_source_loader_pool(database_url)))

        @asynccontextmanager
        async def open_lane_session() -> AsyncIterator[AsyncSession]:
            engine = await engines.get()
            try:
                async with (
                    engine.connect() as connection,
                    AsyncSession(bind=connection, expire_on_commit=False) as session,
                ):
                    yield session
            finally:
                engines.put_nowait(engine)

        yield open_lane_session


async def run_executor_tick(  # noqa: PLR0913, PLR0912, PLR0915 - one knob per argument; one branch per phase
    session: AsyncSession,
    *,
    activation: ActivationConfig,
    now: datetime,
    max_lanes_per_tick: int,
    stop: ShutdownSignal | None = None,
    breaker_release: ProcessStartRelease | None = None,
    repair_clock: RepairAuthoringClock | None = None,
    soft_failure: SoftFailureState | None = None,
    catalogue: LaneCatalogue | None = None,
    dispatcher: LaneDispatcher | None = None,
    budget: BudgetAdmissionState | None = None,
    maintenance: DailyMaintenance | None = None,
) -> ExecutorTickSummary:
    """Run one leader-elected, durable, fairly selected scheduler tick.

    `soft_failure` (the service loop always passes one) records every Wave O incident and applies repair
    withholding and the repair breaker; `None` is HEAD's tick exactly. Either way one lane's planning
    fault never stops another lane. See execution/AGENTS.md, "Soft failure". `catalogue` (default: this
    process's) puts every lane on one path; legacy repair planning sees the legacy path only (CA8).
    `dispatcher` is the S15 work queue: the tick opens each due lane's run and hands it over without
    awaiting it, and folds the turns that finished since the last tick first; `None` is S16's `serial`,
    HEAD's in-tick await under `max_lanes_per_tick`. See execution/AGENTS.md, "Work queue".
    `budget` is WQ-4's paid-cap admission, run on every due lane before `fair_due_order` (a refused lane is
    `deferred_budget` and opens no run); `maintenance` is the once-per-UTC-day upkeep in the repair-authoring
    slot. `None` for either is HEAD's tick. See execution/AGENTS.md, "Budget admission" and "Daily upkeep".
    """
    catalogue = _tick_catalogue() if catalogue is None else catalogue
    if max_lanes_per_tick < MIN_LANES_PER_TICK:
        raise ExecutorConfigurationError(
            f"max_lanes_per_tick must be at least {MIN_LANES_PER_TICK} to preserve class fairness"
        )
    # Wave O's tick volume bound (design Sec 1.4): these three drop to debug -- an idle executor's
    # heartbeat is ~24 lines a day, not one line of each per poll. `plantgeo_job_executor_lane_turn`
    # (per attempt) and the tick SUMMARY (on a change or hourly, `_service_loop`) carry the signal.
    logger.debug(
        "plantgeo_job_executor_tick_started",
        observed_at=now.isoformat(),
        active_lane_count=len(activation.active_lanes),
    )
    await apply_statement_timeout(session)
    if not await _try_leader_lock(session):
        await session.rollback()
        logger.debug("plantgeo_job_executor_leader_not_acquired", observed_at=now.isoformat())
        return ExecutorTickSummary(observed_at=now, leader=False, lanes=())
    logger.debug("plantgeo_job_executor_leader_acquired", observed_at=now.isoformat())
    primary_error: BaseException | None = None
    results: list[LaneTickResult] = []
    # The soft-failure layer judges "inactive" against every definition that may dispatch this tick, so a
    # config lane outside the allow-list is not mistaken for a retired one; repairs stay legacy-only.
    dispatch_activation = catalogue.dispatch_activation(activation)
    legacy_activation = catalogue.legacy_activation(activation)
    tick = None if soft_failure is None else _SoftFailureTick(soft_failure, activation=dispatch_activation, now=now)
    try:
        if tick is not None:
            await tick.open(session)
        if dispatcher is not None:
            # Turns the queue finished since the last tick land first, their verdicts on the leader session.
            for finished in dispatcher.collect():
                results.append(finished.result)
                if tick is not None:
                    await tick.observe_turn(session, finished.candidate, finished.result, finished.verdict)
        planned, due = await _plan_active_lanes(
            session, activation, now, breaker_release=breaker_release, observer=tick, catalogue=catalogue
        )
        results.extend(planned)
        repair_activation = legacy_activation
        if tick is not None:
            await tick.after_planning(session)
            withheld = tick.repair_withholding()
            repair_activation = _without_lanes(legacy_activation, frozenset(withheld))
            results.extend(tick.withheld_results(withheld))
        if repair_clock is not None and repair_clock.due(time.monotonic()):
            authored = await _author_due_repairs(session, repair_activation, now=now, clock=repair_clock)
            if tick is not None:
                await tick.observe_authoring(session, succeeded=authored is not None)
        elif repair_clock is None and tick is not None:
            await tick.retire_authoring(session)
        if maintenance is not None and maintenance.due(now) and (dispatcher is None or not dispatcher.in_flight):
            # `job-logs-maintain` holds ACCESS EXCLUSIVE on the default job-event partition, which a queued lane's
            # slice-end event write would wait behind; the day's pass waits for a tick with no lane in flight.
            await _run_daily_maintenance(session, now=now, maintenance=maintenance)
        # A forward still RUNNING on the queue counts as due: at two or more slots its repair would otherwise run
        # beside it and double that lane's egress (review PM3).
        running = () if dispatcher is None else dispatcher.in_flight
        repair_results, repair_due = await _plan_repair_runs(
            session,
            repair_activation,
            forward_due={candidate.spec.lane_id for candidate in due}
            | {name.removeprefix(EXECUTOR_DEFINITION_PREFIX) for name in running},
        )
        results.extend(repair_results)
        due.extend(repair_due)
        if tick is not None:
            await tick.record_plan_failures(session, results)
        if budget is not None:
            # WQ-4: admission BEFORE `fair_due_order`, so a refused lane never takes a selection or queue slot.
            admission = await admit_due_lanes(session, due, now=now, catalogue=catalogue, state=budget)
            results.extend(admission.refused)
            due = list(admission.admitted)
            if tick is not None:
                await tick.observe_budget(session, admission)
        ordered = fair_due_order(due, lead="incremental" if dispatcher is None else dispatcher.next_lead)
        if dispatcher is not None:
            results.extend(await _dispatch_due_lanes(session, ordered, dispatcher=dispatcher, stop=stop))
            ordered = ()
        selected = ordered[:max_lanes_per_tick]
        for index, candidate in enumerate(selected):
            if stop is not None and stop.requested:
                results.extend(_deferred_shutdown_result(deferred) for deferred in selected[index:])
                break
            _LANE_TURN_VERDICTS.pop(candidate.spec.lane_id, None)
            try:
                lane_result = await _execute_due_lane(session, candidate, stop=stop)
            except Exception as error:  # isolate lane-local faults only while the pinned backend is intact
                _LANE_TURN_VERDICTS.pop(candidate.spec.lane_id, None)
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
                continue
            results.append(lane_result)
            verdict = _LANE_TURN_VERDICTS.pop(candidate.spec.lane_id, None)
            if tick is not None:
                await tick.observe_turn(session, candidate, lane_result, verdict)
        results.extend(
            LaneTickResult(
                lane_id=candidate.spec.lane_id,
                state="deferred_fairness",
                scheduled_for=candidate.scheduled_for,
                run_id=candidate.existing_run_id,
                detail="due; another work class received this bounded tick's turn",
            )
            for candidate in ordered[max_lanes_per_tick:]
        )
        if tick is not None:
            await tick.close(session)
        return ExecutorTickSummary(
            observed_at=now,
            leader=True,
            lanes=tuple(sorted(results, key=lambda result: result.lane_id)),
        )
    except BaseException as error:
        primary_error = error
        _log_tick_partial(results, error)
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


def _warn_fail_open(event: str, error: BaseException, **fields: object) -> None:
    """One warning for a fail-open fault; never raises, since the log stream itself may be what broke."""
    with suppress(Exception):
        logger.warning(event, error_type=type(error).__name__, error=redaction.describe_error(error), **fields)


def _default_stderr_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stderr on this process's stderr, so the Railway log stream still carries it."""
    _write_through(sys.stderr, chunk)


def _default_stdout_sink(chunk: bytes) -> None:
    """Re-emit one chunk of a child's stdout on this process's stdout: the log stream keeps the JSON report."""
    _write_through(sys.stdout, chunk)


class CommandOutputTail:
    """Tee one of a child's output streams through to this process while keeping only its bounded TAIL.

    stderr: before this the child inherited it outright, its traceback reached the log stream and nothing
    else, and `agri.job_attempt.last_error_summary` read only `command exited with status 1`. The tail's
    redacted `summary()` is what `run_scheduled_command` folds into every failure reason;
    `jobs.lease.fail_work_item` then redacts and clamps it again like any other summary.

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
        self.sink_failures = 0

    @property
    def tail(self) -> bytes:
        """The bytes kept, at most `limit` of them and always the newest."""
        return bytes(self._tail)

    @property
    def truncated(self) -> bool:
        """True when the child wrote more than the tail holds, so the summary is the END of its output."""
        return self.bytes_seen > len(self._tail)

    def feed(self, chunk: bytes) -> None:
        """Fold one chunk into the bounded tail FIRST, then forward it to the sink, fail-open.

        The tail is the ledger's copy (failure reason, terminal report); the sink is only the log
        mirror. A sink fault is counted and warned about once, never raised: raising here would kill
        `_drain_stream`, leave the pipe unread and block the child until the monitor calls it a hang.
        """
        if not chunk:
            return
        self.bytes_seen += len(chunk)
        self._tail.extend(chunk)
        if len(self._tail) > self._limit:
            del self._tail[: len(self._tail) - self._limit]
        try:
            self._sink(chunk)
        except Exception as error:
            self.sink_failures += 1
            if self.sink_failures == 1:
                _warn_fail_open("plantgeo_job_executor_output_sink_failed", error)

    def summary(self, *, max_chars: int = COMMAND_STDERR_SUMMARY_CHARS) -> str | None:
        """One line: the tail's non-blank lines, each redacted, joined with ` | `, cut from the FRONT.

        Redacted per line, before joining and cutting, so a front cut can never strip the `Bearer`/
        `Authorization` word a pattern needs while leaving the credential after it, and a `[SQL: `
        cut ends with its own line instead of swallowing the exception line that follows.
        """
        text = self._tail.decode("utf-8", errors="replace")
        lines = (" ".join(redaction.redact_for_log(line).split()) for line in text.splitlines())
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
    # `return_exceptions=True`: one stream's reader fault must never stop the other stream draining.
    results = await asyncio.gather(
        _drain_stream(process.stdout, stdout), _drain_stream(process.stderr, stderr), return_exceptions=True
    )
    for result in results:
        if isinstance(result, Exception):
            _warn_fail_open("plantgeo_job_executor_stream_drain_failed", result)


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


# --- Exit classification, turn logging and the usage fold (o5a; spec Sec 4.9.1-4.9.3) -------------
#
# Every branch below is OBSERVATIONAL ONLY at GL-3 (FR-33): `exit_class`/`turn_outcome` are stamped
# into `job_attempt.metrics` and logged, but nothing here holds a lane, opens an incident, or skips a
# repair -- that is GL-5/GL-6. See execution/AGENTS.md, "Turn reports" and "Command stderr reaches
# the ledger".

#: Per lane, the newest `ExitClass` a completed handler call classified IN THIS PROCESS. Read by
#: `announce_operator_actions` so `plantgeo_job_executor_operator_action_required` can name the class
#: without a database read (o2b's `select_run_final_attempt.sql` is GL-5's job); a restart forgets it,
#: exactly like `_LANE_TURN_REPORTS` -- deliberately process-local, never the audit record.
_LANE_EXIT_CLASSES: dict[str, ExitClass] = {}

#: `classify_exit` never returns `"interrupted"` (spec Sec 4.9.3: it is not itself an evidence-driven
#: exit -- a shutdown yield or an exhausted command budget IS the interruption). `run_scheduled_command`
#: stamps that class directly for those two returns; every other class comes from `classify_exit`.
_TURN_OUTCOME_BY_EXIT_CLASS: Final[dict[ExitClass, TurnOutcome]] = {
    "ok": "completed",
    "upstream": "upstream_unavailable",
    "infra": "infra_unavailable",
    "code": "code_error",
    "hang": "timeout",
    "config": "config_error",
    "interrupted": "interrupted",
    "lease_lost": "lease_lost",
    "report_missing": "report_missing",
}

#: Base `lane_turn` level per class (spec Sec 4.9.3's table); `"ok"` is raised to `warn` at the call
#: site when the turn owes unwritten days, publication debt or a non-`ok` soil probe (`turn_outcome`
#: becomes `"incomplete"` there, never a second entry here -- `ExitClass` has no `"incomplete"` member).
_LANE_TURN_LEVEL: Final[dict[ExitClass, str]] = {
    "ok": "info",
    "upstream": "warn",
    "infra": "warn",
    "code": "error",
    "hang": "error",
    "config": "error",
    "interrupted": "warn",
    "lease_lost": "warn",
    "report_missing": "warn",
}

#: Level -> structlog method NAME, looked up on `logger` when the line is emitted, never at import:
#: `agri-service ops jobs-executor` imports this module before the CLI root calls `configure_logging`,
#: and a method read at import is bound to the unconfigured (non-JSON, unredacted) pipeline.
_LANE_TURN_LOG_METHODS: Final[dict[str, str]] = {
    "debug": "debug",
    "info": "info",
    "warn": "warning",
    "error": "error",
}

#: `ChildLogRouter`'s turn-context field per environment name. Duplicated by hand from
#: `foundation/observability/router.py::_TURN_ENV_TO_FIELD`, the same sibling-copy rule that module
#: documents for its own copy of `logging.py`'s table.
_ROUTER_TURN_FIELDS: Final[dict[str, str]] = {
    TURN_ID_ENV_VAR: "turn_id",
    LANE_ID_ENV_VAR: "lane",
    TURN_MODE_ENV_VAR: "mode",
    ATTEMPT_ENV_VAR: "attempt",
    TURN_BUCKET_ENV_VAR: "shard_key",
    TURN_PROBE_ENV_VAR: "probe",
}


@dataclass(frozen=True, slots=True)
class _Turn:
    """One `run_scheduled_command` call's identity, carried by every turn-scoped line and metric."""

    turn_id: uuid.UUID
    lane_id: str
    mode: str
    attempt: int
    #: The DEFINITION that ran (`lane_id`, or `lane_id` + `REPAIR_LANE_SUFFIX` for a repair turn): the key
    #: `_LANE_TURN_VERDICTS` is filed under, so the tick reads back the verdict of the candidate it ran.
    report_key: str | None = None
    #: A hold-ladder probe (GL-6): the child sees `PLANTGEO_TURN_PROBE=1`, and a lost fence parks the item.
    probe: bool = False


@dataclass(frozen=True, slots=True)
class _TurnVerdict:
    """What one terminal handler call decided, for the tick's incident lifecycles (o5b, GL-5).

    Process-local and consumed once: the tick pops the entry before and after it drives a candidate,
    so a verdict never outlives the turn it describes. `failure_class` is set only for a pre-spawn
    refusal (`invalid_repair_request` never counts against the repair breaker).
    """

    exit_class: ExitClass
    turn_outcome: TurnOutcome
    report_present: bool = False
    incomplete_reason: str | None = None
    failure_class: str | None = None
    #: The report is conclusive proof the lane made progress (`lane_incidents.PROGRESS_EVIDENCE`); a probation
    #: bucket counts toward release only when this holds and the turn completed clean.
    conclusive: bool = False


#: Per definition that ran (`_Turn.report_key`), the verdict of its newest terminal handler call.
_LANE_TURN_VERDICTS: dict[str, _TurnVerdict] = {}

#: `exit_classes.WRAPPER_EVIDENCE` is keyed by the short layer names its R3 rows retire under, the
#: executor by lane id; without this map R3 could never match a production lane (a GL-3 defect found
#: by the GL-5 sweep proof). See execution/AGENTS.md, "Soft failure".
_WRAPPER_EVIDENCE_KEYS: Final[Mapping[str, str]] = MappingProxyType(
    {
        DROUGHT_DIRECT_LANE_ID: "drought",
        FIRE_PERIMETERS_DIRECT_LANE_ID: "fire-perimeters",
        EVACUATION_ZONES_DIRECT_LANE_ID: "evacuation-zones",
        BURN_SEVERITY_DIRECT_LANE_ID: "burn-severity",
        FIRE_DETECTIONS_DIRECT_LANE_ID: "fire-detections",
    }
)


def _turn_mode(kind: str) -> str:
    """`forward` or `repair`, from the work item kind -- the `mode` field on every turn-scoped log line."""
    return "forward" if kind == EXECUTOR_WORK_ITEM_KIND else "repair"


def _record_lane_exit_class(lane_id: str, exit_class: ExitClass) -> None:
    _LANE_EXIT_CLASSES[lane_id] = exit_class


def _turn_context(turn: _Turn, *, bucket: str | None) -> dict[str, str]:
    """The turn context design Sec 1.2 lists, keyed by environment name.

    The child receives it as environment (`PLANTGEO_TURN_ID` alone arms `bootstrap.arm_from_environment`,
    design Sec 1.3); the executor's own `_TurnLogRouter` receives the same values directly.
    """
    context = {
        TURN_ID_ENV_VAR: str(turn.turn_id),
        LANE_ID_ENV_VAR: turn.lane_id,
        TURN_MODE_ENV_VAR: turn.mode,
        ATTEMPT_ENV_VAR: str(turn.attempt),
        TURN_PROBE_ENV_VAR: "1" if turn.probe else "0",
    }
    if bucket is not None:
        context[TURN_BUCKET_ENV_VAR] = bucket
    return context


class _TurnLogRouter(ChildLogRouter):
    """The per-turn `ChildLogRouter`: fail-open, and filled from THIS turn's context.

    See execution/AGENTS.md, "Command stderr reaches the ledger" (the fail-open and turn-context
    paragraphs).
    """

    def __init__(self, *, turn: _Turn, context: Mapping[str, str]) -> None:
        super().__init__(attempt_id=str(turn.turn_id))
        self._turn = turn
        self._turn_fields = {
            _ROUTER_TURN_FIELDS[name]: value for name, value in context.items() if name in _ROUTER_TURN_FIELDS
        }
        self.router_faults = 0

    def feed(self, stream: Stream, chunk: bytes) -> None:
        try:
            super().feed(stream, chunk)
        except Exception as error:
            self._note_fault(error)

    def flush(self) -> None:
        try:
            super().flush()
        except Exception as error:
            self._note_fault(error)

    def usage_summary(self) -> dict[str, object]:
        try:
            return super().usage_summary()
        except Exception as error:
            self._note_fault(error)
            return {"usage_complete": False, "usage_incomplete_pids": [], "last_send_outcome": None}

    def _fill_turn_context(self, parsed: dict[str, object]) -> None:
        for field_name, value in self._turn_fields.items():
            if field_name not in parsed and value:
                parsed[field_name] = value

    def _note_fault(self, error: Exception) -> None:
        self.router_faults += 1
        if self.router_faults == 1:
            _warn_fail_open(
                "plantgeo_job_executor_log_router_failed",
                error,
                lane_id=self._turn.lane_id,
                turn_id=str(self._turn.turn_id),
            )


def _emit_lane_turn(
    turn: _Turn,
    *,
    spawned: bool,
    exit_class: ExitClass,
    turn_outcome: TurnOutcome,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Exactly one `plantgeo_job_executor_lane_turn` line per terminal handler outcome (design Sec 1.4).

    "Terminal" here means `run_scheduled_command` returned `completed`/`failed`/`yielded` -- never the
    bootstrap `progressed` return that only advances the cursor to `state=ready` and has not attempted
    anything yet, so it has no class to report (FR-30/FR-33). Fail-open: the audit line's own fault
    must never fail the turn it describes.
    """
    level = _LANE_TURN_LEVEL[exit_class]
    if exit_class == "ok" and turn_outcome == "incomplete":
        level = "warn"
    fields: dict[str, object] = {
        "lane_id": turn.lane_id,
        "turn_id": str(turn.turn_id),
        "spawned": spawned,
        "exit_class": exit_class,
        "turn_outcome": turn_outcome,
        "attempt": turn.attempt,
        "mode": turn.mode,
        "probe": turn.probe,
    }
    if extra:
        fields.update(extra)
    with suppress(Exception):
        getattr(logger, _LANE_TURN_LOG_METHODS[level])(events.EVENT_JOB_EXECUTOR_LANE_TURN, **fields)


def _finish_lane_turn(  # noqa: PLR0913 - the verdict facts the tick's incident lifecycles read
    turn: _Turn,
    *,
    spawned: bool,
    exit_class: ExitClass,
    incomplete: bool = False,
    extra: Mapping[str, object] | None = None,
    report_present: bool = False,
    incomplete_reason: str | None = None,
    failure_class: str | None = None,
    conclusive: bool = False,
) -> TurnOutcome:
    """Common tail of every terminal branch: turn_outcome, the process-local caches, the one `lane_turn`
    line. `incomplete` is the ONE override the exit-class table needs (`ok` still splits into
    `completed`/`incomplete` by what the turn owes, spec Sec 4.9.3) -- every other class maps to its
    outcome one-to-one."""
    turn_outcome: TurnOutcome = (
        "incomplete" if (exit_class == "ok" and incomplete) else _TURN_OUTCOME_BY_EXIT_CLASS[exit_class]
    )
    _record_lane_exit_class(turn.lane_id, exit_class)
    _LANE_TURN_VERDICTS[turn.report_key or turn.lane_id] = _TurnVerdict(
        exit_class=exit_class,
        turn_outcome=turn_outcome,
        report_present=report_present,
        incomplete_reason=incomplete_reason if turn_outcome == "incomplete" else None,
        failure_class=failure_class,
        conclusive=conclusive,
    )
    _emit_lane_turn(turn, spawned=spawned, exit_class=exit_class, turn_outcome=turn_outcome, extra=extra)
    return turn_outcome


def _not_spawned_usage() -> dict[str, object]:
    """The `usage` block of a return that never spawned, so GL-4's rollup never reads a NULL basis."""
    return {"hosts": None, "charged_basis": "not_spawned", "charged": 0.0, "suspect": 0.0}


def _pre_spawn_failure(turn: _Turn, *, failure_class: str, reason: str) -> JobHandlerOutcome:
    """One early refusal before `create_subprocess_exec` ever runs (spec Sec 4.9.3's pre-spawn-failure
    row): always `config`/`config_error`, `spawned=false`, one `lane_turn` line, `failure_class` kept
    on the line so an operator can tell an invalid lane apart from an invalid repair request."""
    exit_class = classify_exit(return_code=None, pre_spawn=True)
    turn_outcome = _finish_lane_turn(
        turn,
        spawned=False,
        exit_class=exit_class,
        extra={"failure_class": failure_class},
        failure_class=failure_class,
    )
    return JobHandlerOutcome.failed(
        failure_class,
        reason,
        metrics={
            "turn_id": str(turn.turn_id),
            "spawned": False,
            "exit_class": exit_class,
            "turn_outcome": turn_outcome,
            "usage": _not_spawned_usage(),
        },
    )


def _interrupted_outcome(
    turn: _Turn,
    *,
    spawned: bool,
    reason: str,
    resume_from: JobInvocation | None = None,
    extra_metrics: Mapping[str, object] | None = None,
) -> JobHandlerOutcome:
    """A shutdown yield or an exhausted command budget (spec Sec 4.9.3: `interrupted`, not an attempt
    charge). `classify_exit` never returns this class (its docstring's flags all assume the command
    either ran or was stopped BY the monitor) -- it is the one class `run_scheduled_command` stamps
    directly rather than through the classifier. `resume_from` keeps that invocation's own cursor and
    progress on the yield; without it the yield carries none, exactly as before o5a."""
    exit_class: ExitClass = "interrupted"
    turn_outcome = _finish_lane_turn(turn, spawned=spawned, exit_class=exit_class)
    metrics: dict[str, object] = {
        "turn_id": str(turn.turn_id),
        "spawned": spawned,
        "exit_class": exit_class,
        "turn_outcome": turn_outcome,
    }
    if not spawned:
        metrics["usage"] = _not_spawned_usage()
    if extra_metrics:
        metrics.update(extra_metrics)
    if resume_from is None:
        return JobHandlerOutcome.yielded(reason=reason, metrics=metrics)
    return JobHandlerOutcome.yielded(
        cursor=resume_from.cursor, progress_fraction=resume_from.progress_fraction, reason=reason, metrics=metrics
    )


def _incomplete_reason(turn_report: TurnReport | None, probe_status: object) -> str | None:
    """`lane_incomplete`'s reason (spec Sec 4.9.3): `unwritten` first, then `publication_debt` (PD), then a
    soil probe that did not answer `ok` (`probe_gated`); `None` for a turn that owes nothing."""
    if turn_report is not None and turn_report.days_unwritten > 0:
        return "unwritten"
    if turn_report is not None and turn_report.publication_debt > 0:
        return "publication_debt"
    if isinstance(probe_status, str) and probe_status != "ok":
        return "probe_gated"
    return None


def _lane_turn_failure_detail(tail: CommandOutputTail) -> dict[str, object]:
    """Why a non-`ok` turn failed, on its `lane_turn` line: the bounded, redacted stderr tail.

    The router's per-attempt error ceiling can drop the final traceback from the mirror, and the
    ledger's failure reason never reaches the log stream, so without this the logs say only THAT a
    turn failed, never why.
    """
    summary = tail.summary(max_chars=LANE_TURN_STDERR_SUMMARY_CHARS)
    return {} if summary is None else {"stderr_tail": summary}


def _start_lag_seconds(ready_at: object, *, now: float) -> float | None:
    """Wall-clock seconds from the bootstrap `ready` checkpoint to this spawn, or `None` when unknown.

    Wall clock, not monotonic: the cursor is persisted, and the spawn may run in a later process or on
    another host. A lag more negative than the clock-skew allowance, or older than
    `START_LAG_MAX_SECONDS`, is not a head-of-line delay; a small negative one is skew and reads as 0.
    """
    ready_at_seconds = _finite_number(ready_at)
    if ready_at_seconds is None:
        return None
    lag = now - ready_at_seconds
    if lag < -START_LAG_CLOCK_SKEW_SECONDS or lag > START_LAG_MAX_SECONDS:
        return None
    return round(max(lag, 0.0), 3)


def _finite_number(value: object) -> float | int | None:
    """`value` when it is a real, finite int or float (never a bool), else `None`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


#: Legacy self-reported field names (spec Sec 4.9.2's map): an older writer's report still lands under
#: the current key. Applied only when the CURRENT name carries no usable value, so a writer that
#: reports both never has its own current-name value overwritten by a stale legacy one.
_LEGACY_METRIC_NAME_MAP: Final[dict[str, str]] = {
    "requests_spent": "requests",
    "rows": "rows_written",
    "bytes": "bytes_written",
    "written_bytes": "bytes_written",
}
_REPORT_COUNTER_FIELDS: Final[tuple[str, ...]] = (
    "requests",
    "weighted_calls",
    "fetch_attempts",
    "rows_written",
    "bytes_written",
)
#: Every `probe_status` a writer emits: `pipeline/direct/soil/source.py`'s probe statuses plus the
#: two `soil/forward.py::_effective_probe_status` derives. Any other value is not copied to metrics.
_PROBE_STATUSES: Final[frozenset[str]] = frozenset({"ok", "unavailable", "deferred", "invalid", "blind"})


def _report_usage_fields(report: Mapping[str, object] | None) -> dict[str, object]:
    """The turn's own self-reported counters (spec Sec 4.9.2), legacy names mapped onto current ones.

    `metrics` is stored unredacted, so only typed values cross from a child's report: a finite number
    per counter, and `probe_status` only from `_PROBE_STATUSES`.
    """
    if report is None:
        return {}
    fields: dict[str, object] = {}
    for canonical in _REPORT_COUNTER_FIELDS:
        legacy_names = [legacy for legacy, target in _LEGACY_METRIC_NAME_MAP.items() if target == canonical]
        for name in (canonical, *legacy_names):
            value = _finite_number(report.get(name))
            if value is not None:
                fields[canonical] = value
                break
    probe_status = report.get("probe_status")
    if isinstance(probe_status, str) and probe_status in _PROBE_STATUSES:
        fields["probe_status"] = probe_status
    return fields


def _json_object_lines(output_tail: bytes) -> Iterator[dict[str, object]]:
    """Every JSON-object line of a raw output tail, oldest first; a malformed or over-deep line is skipped."""
    for raw in output_tail.decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(parsed, dict):
            yield parsed


#: Per-host fields that pair into "the latest send", never summed.
_HOST_SEND_FIELDS: Final = frozenset({"last_send_outcome", "last_send_at"})


def _merge_host_counters(merged: dict[str, object], entry: Mapping[str, object]) -> None:
    """Fold one process's counters for a host into the turn's: numbers add, the latest send wins,
    labels (`provider`, `pool`) keep the first non-null value."""
    for key, value in entry.items():
        if key in _HOST_SEND_FIELDS:
            continue
        number = _finite_number(value)
        current = _finite_number(merged.get(key))
        if number is not None:
            merged[key] = number if current is None else current + number
        elif merged.get(key) is None:
            merged[key] = value
    sent_at = _finite_number(entry.get("last_send_at"))
    merged_at = _finite_number(merged.get("last_send_at"))
    outcome = entry.get("last_send_outcome")
    if outcome is not None and sent_at is not None and (merged_at is None or sent_at >= merged_at):
        merged["last_send_outcome"] = outcome
        merged["last_send_at"] = sent_at
    else:
        merged.setdefault("last_send_outcome", None)
        merged.setdefault("last_send_at", None)


def _numbers(values: Iterable[object]) -> list[float | int]:
    return [number for number in (_finite_number(value) for value in values) if number is not None]


def _fold_turn_usage(stdout_tail: bytes) -> dict[str, object] | None:
    """Every `plantgeo_turn_usage` line on the child's raw stdout tail, folded into one; `None` if none.

    A turn is not always one process: `pipeline/direct/burn_severity/daily.py` spawns a
    `multiprocessing` child that inherits `PLANTGEO_TURN_ID`, arms itself and does the fetching, and
    the PARENT's own empty-hosts line is written last. So each pid's last line is kept and host
    counters are summed across pids (`cpu_seconds` and `meter_errors` sum too; `rss_peak_kib` is the
    largest single process). Reads the SAME raw bytes `parse_terminal_report` does, never the routed
    copy: `ChildLogRouter.usage_summary()` exposes only its pairing concern, never the per-host
    breakdown this is the one reader of (spec Sec 4.9.2).
    """
    by_pid: dict[object, dict[str, object]] = {}
    for parsed in _json_object_lines(stdout_tail):
        if parsed.get("event") == events.EVENT_TURN_USAGE:
            by_pid[parsed.get("pid")] = parsed
    if not by_pid:
        return None
    lines = list(by_pid.values())
    hosts: dict[str, dict[str, object]] = {}
    for line in lines:
        line_hosts = line.get("hosts")
        if not isinstance(line_hosts, dict):
            continue
        for host, entry in line_hosts.items():
            if isinstance(host, str) and isinstance(entry, dict):
                _merge_host_counters(hosts.setdefault(host, {}), entry)
    rss_peaks = _numbers(line.get("rss_peak_kib") for line in lines)
    cpu_seconds = _numbers(line.get("cpu_seconds") for line in lines)
    return {
        "hosts": hosts,
        "rss_peak_kib": max(rss_peaks) if rss_peaks else None,
        "cpu_seconds": sum(cpu_seconds) if cpu_seconds else None,
        "meter_errors": sum(_numbers(line.get("meter_errors") for line in lines)),
    }


def _weighted_calls_metered_total(hosts: Mapping[str, object] | None) -> float:
    if not isinstance(hosts, dict):
        return 0.0
    total = 0.0
    for entry in hosts.values():
        if isinstance(entry, dict):
            value = _finite_number(entry.get("weighted_calls_metered"))
            if value is not None:
                total += float(value)
    return total


def _charging_basis(
    *,
    lane_id: str,
    spawned: bool,
    usage_reported: bool,
    report_fields: Mapping[str, object],
    hosts: Mapping[str, object] | None,
) -> dict[str, object]:
    """The per-attempt half of spec Sec 4.9.2's charging table.

    The metering EPOCH and the running/pre-epoch exclusions are read-time concerns o4's SQL rollup
    applies on top of every attempt's own `usage.charged_basis` -- this fold cannot know them (they
    depend on every OTHER attempt too), so it only ever answers "what does THIS attempt's own evidence
    support": `metered` (a usage line closed), `reported` (the report alone states a logical figure),
    `suspect` (spawned with neither) or `not_spawned`. See execution/AGENTS.md, "The usage fold".
    """
    reported_value = _finite_number(report_fields.get("weighted_calls"))
    reported_logical = None if reported_value is None else float(reported_value)
    metered_total = _weighted_calls_metered_total(hosts)
    if not spawned:
        return {"charged_basis": "not_spawned", "charged": 0.0, "suspect": 0.0}
    if usage_reported:
        use_metered = os.environ.get(CHARGE_BASIS_VARIABLE, "metered").strip().casefold() != "logical"
        if use_metered:
            charged = max(metered_total, reported_logical or 0.0)
        else:
            # A reported 0 is a real figure under `logical`, never "absent" (`is not None`, not `or`).
            charged = reported_logical if reported_logical is not None else metered_total
        return {"charged_basis": "metered", "charged": charged, "suspect": 0.0}
    if reported_logical is not None:
        return {"charged_basis": "reported", "charged": reported_logical, "suspect": 0.0}
    return {"charged_basis": "suspect", "charged": 0.0, "suspect": float(LANE_LOGICAL_CAPS.get(lane_id, 0))}


#: What a path's gates resolve a work item to: the turn identity, the definition that runs and its argv.
_ResolvedTurn = tuple[_Turn, LaneExecutionSpec, tuple[str, ...]]


def _resolve_legacy_turn(  # noqa: PLR0911 - one refusal per gate HEAD's handler had, plus CA12 and S8
    turn: _Turn, invocation: JobInvocation, catalogue: LaneCatalogue
) -> JobHandlerOutcome | _ResolvedTurn:
    """The legacy path's gates, HEAD's exactly (a registered lane, the allow-list, a command), plus CA12 and S8."""
    lane_id = invocation.payload.get("lane_id")
    if not isinstance(lane_id, str) or lane_id not in LANE_SPECS:
        return _pre_spawn_failure(
            turn if not isinstance(lane_id, str) else replace(turn, lane_id=lane_id),
            failure_class="unknown_executor_lane",
            reason="work item names no registered executor lane",
        )
    # `classify_exit`'s R1-R3 evidence rules key on the BASE lane id: a repair shares its owning lane's
    # evidence shapes, so the `REPAIR_LANE_SUFFIX` that keeps `_LANE_TURN_REPORTS` per-definition is
    # deliberately NOT applied to the turn's lane -- only to `report_key`, the definition that ran.
    report_key = lane_id if invocation.kind == EXECUTOR_WORK_ITEM_KIND else f"{lane_id}{REPAIR_LANE_SUFFIX}"
    turn = replace(turn, lane_id=lane_id, report_key=report_key)
    spec = LANE_SPECS[lane_id]
    if catalogue.kill_switch.stops(report_key):
        return _pre_spawn_failure(
            turn, failure_class="lane_stopped", reason=f"lane {lane_id!r} is named in {STOPPED_LANES_VARIABLE}"
        )
    if lane_id in catalogue.quarantined:
        return _pre_spawn_failure(
            turn, failure_class="lane_quarantined", reason=f"lanes/{lane_id}.toml is quarantined (S8)"
        )
    try:
        activation = parse_activation()
    except ExecutorConfigurationError as error:
        return _pre_spawn_failure(turn, failure_class="invalid_ownership_activation", reason=str(error))
    if not activation.is_active(lane_id):
        quarantined = lane_id in activation.quarantined
        return _pre_spawn_failure(
            turn,
            failure_class="lane_quarantined" if quarantined else "ownership_activation_removed",
            reason=(
                f"lane {lane_id!r} is quarantined in the active allow-list"
                if quarantined
                else f"lane {lane_id!r} is no longer explicitly activated"
            ),
        )
    if spec.command is None:
        return _pre_spawn_failure(
            turn, failure_class="source_specific_lane", reason=f"lane {lane_id!r} has no executor command"
        )
    try:
        command = _resolve_command(spec, invocation)
    except RepairRequestError as error:
        return _pre_spawn_failure(turn, failure_class="invalid_repair_request", reason=f"lane {lane_id!r}: {error}")
    return turn, spec, command


def _resolve_config_turn(
    turn: _Turn, invocation: JobInvocation, catalogue: LaneCatalogue
) -> JobHandlerOutcome | _ResolvedTurn:
    """The config path's gates (CA3): the TOML's `enabled`, the catalogue and the CA12 kill-switch, nothing else.

    Neither `LANE_SPECS` nor the legacy allow-list is consulted. A legacy repair item for a config lane is
    refused (CA8). A CA1-marked item names its owning lane and its mode; an unmarked scheduled item (one
    left open across a cut-over) names its definition id, `<lane>` or `<lane>:gap-fill`.
    """
    lane_id = invocation.payload.get("lane_id")
    named = lane_id if isinstance(lane_id, str) else "unknown"
    owning = owning_lane_id(named)
    if invocation.kind != EXECUTOR_WORK_ITEM_KIND:
        # `invalid_repair_request` never counts against the repair breaker: the item, not the lane, is wrong.
        return _pre_spawn_failure(
            replace(turn, lane_id=owning, report_key=f"{owning}{REPAIR_LANE_SUFFIX}"),
            failure_class="invalid_repair_request",
            reason=f"lane {owning!r} runs on the config path; a legacy repair item is never run for it (CA8)",
        )
    mode = invocation.payload.get(TURN_MODE_PAYLOAD_KEY)
    spec = catalogue.config_spec(owning, mode) if isinstance(mode, str) else catalogue.spec_named(named)
    if spec is None or not spec.is_config or spec.command is None or spec.config_mode is None:
        return _pre_spawn_failure(
            replace(turn, lane_id=owning),
            failure_class="unknown_executor_lane",
            reason="work item names no config definition in the lane catalogue",
        )
    turn = replace(turn, lane_id=owning, mode=spec.config_mode, report_key=spec.lane_id)
    refusal = catalogue.config_gate(spec)
    if refusal is not None:
        return _pre_spawn_failure(
            turn,
            failure_class="lane_stopped" if refusal == STOPPED_REASON else "config_lane_disabled",
            reason=f"lane {owning!r} ({spec.config_mode}): {refusal}",
        )
    return turn, spec, spec.command


@job_handler(EXECUTOR_HANDLER_TOKEN)
async def run_scheduled_command(  # noqa: PLR0911, PLR0912, PLR0915 - each terminal state maps to a ledger outcome
    invocation: JobInvocation,
) -> JobHandlerOutcome:
    """Execute one registry-bound command under the outer work item's fence.

    A uuid4 `turn_id` is generated FIRST (design Sec 1.2), before any validation, so even a refusal
    that never reaches a command carries one. `spawned` is `False` on every return before
    `create_subprocess_exec` has returned and `True` on every return after; every return stamps both
    into `metrics`.
    """
    turn = _Turn(
        turn_id=uuid.uuid4(),
        lane_id="unknown",
        mode=_turn_mode(invocation.kind),
        attempt=invocation.attempt_number,
        probe=_PROBE_TURN.get(),
    )
    if invocation.kind not in EXECUTOR_WORK_ITEM_KINDS:
        return _pre_spawn_failure(
            turn, failure_class="unknown_work_item_kind", reason=f"unexpected kind {invocation.kind!r}"
        )
    catalogue = _tick_catalogue()
    named_lane = invocation.payload.get("lane_id")
    config_marked = invocation.payload.get(EXECUTOR_PATH_PAYLOAD_KEY) == CONFIG_EXECUTOR
    if config_marked or (isinstance(named_lane, str) and catalogue.is_config_lane(named_lane)):
        resolved = _resolve_config_turn(turn, invocation, catalogue)
    else:
        resolved = _resolve_legacy_turn(turn, invocation, catalogue)
    if isinstance(resolved, JobHandlerOutcome):
        return resolved
    turn, spec, command = resolved
    lane_id = turn.lane_id
    report_key = turn.report_key or lane_id

    scheduled_for = invocation.payload.get("scheduled_for")
    bucket = scheduled_for if isinstance(scheduled_for, str) else invocation.shard_key
    if invocation.cursor is None:
        # NOT a terminal outcome (`JobOutcomeKind.progressed`, not `failed`/`completed`/`yielded`): this
        # call only advances the cursor to `state=ready` and has validated everything but attempted
        # nothing yet, so it gets no `exit_class` and no `lane_turn` line (design Sec 1.4's "exactly one
        # per TERMINAL handler outcome"). `turn_id`/`spawned=false` are still stamped: the worker merges
        # `{**metrics, **outcome.metrics}` across calls, so a later call's own turn_id simply wins.
        return JobHandlerOutcome.progressed(
            {"state": "ready", "scheduled_for": bucket, READY_AT_CURSOR_KEY: time.time()},
            progress_fraction=0.01,
            metrics={"command_started": False, "turn_id": str(turn.turn_id), "spawned": False},
        )
    if invocation.cursor.get("state") != "ready":
        return _pre_spawn_failure(
            turn,
            failure_class="invalid_executor_checkpoint",
            reason=f"lane {lane_id!r} cannot resume from its stored command checkpoint",
        )

    timeout = min(
        float(spec.command_timeout_seconds),
        max(invocation.seconds_remaining - COMMAND_TIMEOUT_RESERVE_SECONDS, 0.0),
    )
    if timeout <= 0:
        return _interrupted_outcome(turn, spawned=False, reason="no command budget remains in this scheduler slice")

    # The router sits IN FRONT of the log sinks (design Sec 1.1/1.6): every chunk lands in the bounded
    # RAW tail first (`tail`/`stdout`, read by `_command_failure_reason` and `parse_terminal_report`),
    # and only then is its copy for Railway routed -- reassembled, bounded, leveled and redacted,
    # fail-open. See execution/AGENTS.md, "Command stderr reaches the ledger".
    context = _turn_context(turn, bucket=bucket)
    router = _TurnLogRouter(turn=turn, context=context)
    tail = CommandOutputTail(sink=lambda chunk: router.feed("stderr", chunk))
    stdout = CommandOutputTail(limit=COMMAND_STDOUT_TAIL_BYTES, sink=lambda chunk: router.feed("stdout", chunk))
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **context},
        )
    except OSError as error:
        # Observational only: the error still propagates exactly as before (the worker's own failure
        # path and failure_class are unchanged); the turn just gets its one `lane_turn` line first.
        _finish_lane_turn(
            turn,
            spawned=False,
            exit_class=classify_exit(return_code=None, pre_spawn=True),
            extra={"spawn_error": type(error).__name__},
        )
        raise
    start_lag_seconds = _start_lag_seconds(invocation.cursor.get(READY_AT_CURSOR_KEY), now=time.time())
    drain = asyncio.create_task(_drain_both(process, stdout=stdout, stderr=tail))
    started = time.monotonic()
    try:
        monitor_state, return_code = await _monitor_subprocess(process, invocation, timeout=timeout)
    finally:
        await _finish_drain(drain)
        router.flush()
    elapsed = round(time.monotonic() - started, 3)
    # Keyed by the DEFINITION that ran (`report_key`, above), not the owning lane: a `--max-days 5` repair
    # turn is legitimately partial and must not count against the hourly lane's incomplete-bucket streak.
    raw_report = parse_terminal_report(stdout.tail)
    turn_report = record_turn_report(report_key, raw_report)

    router_usage = router.usage_summary()
    usage = _fold_turn_usage(stdout.tail)
    hosts = None if usage is None else usage["hosts"]
    report_fields = _report_usage_fields(raw_report)
    # Judged off the RAW report so an unrecognised status still reads as "not ok"; metrics get only
    # the vetted copy in `report_fields`.
    raw_probe_status = None if raw_report is None else raw_report.get("probe_status")
    incomplete = (turn_report is not None and turn_report.incomplete) or (
        isinstance(raw_probe_status, str) and raw_probe_status != "ok"
    )
    usage_metrics: dict[str, object] = {
        "report_present": raw_report is not None,
        "usage_reported": usage is not None,
        "usage_complete": router_usage["usage_complete"],
        "unwritten_known": turn_report is not None,
        "probe": turn.probe,
        "stdout_bytes": stdout.bytes_seen,
        "stdout_truncated": stdout.truncated,
        "start_lag_seconds": start_lag_seconds,
        "log_lines_dropped": router.log_lines_dropped,
        "log_router_faults": router.router_faults,
        "rss_peak_kib": None if usage is None else usage["rss_peak_kib"],
        "cpu_seconds": None if usage is None else usage["cpu_seconds"],
        "meter_errors": None if usage is None else usage["meter_errors"],
        "last_send_outcome": router_usage["last_send_outcome"],
        "usage": {
            "hosts": hosts,
            **_charging_basis(
                lane_id=lane_id,
                spawned=True,
                usage_reported=usage is not None,
                report_fields=report_fields,
                hosts=hosts if isinstance(hosts, dict) else None,
            ),
        },
        **report_fields,
    }
    metrics: dict[str, object] = {
        "turn_id": str(turn.turn_id),
        "spawned": True,
        "elapsed_seconds": elapsed,
        **tail.metrics(),
        "days_unwritten": None if turn_report is None else turn_report.days_unwritten,
        "publication_debt": None if turn_report is None else turn_report.publication_debt,
        **usage_metrics,
    }
    if monitor_state == "shutdown":
        return _interrupted_outcome(
            turn,
            spawned=True,
            reason=f"lane {lane_id!r} stopped for service shutdown before command completion",
            resume_from=invocation,
            extra_metrics=metrics,
        )
    if monitor_state == "fence_lost":
        exit_class = classify_exit(return_code=return_code, fence_lost=True)
        turn_outcome = _finish_lane_turn(
            turn,
            spawned=True,
            exit_class=exit_class,
            extra=_lane_turn_failure_detail(tail),
            report_present=raw_report is not None,
        )
        if turn.probe:
            # GL-6: a probe that lost its fence says nothing about the lane; it parks (inconclusive), never fails.
            return JobHandlerOutcome.yielded(
                reason=f"lane {lane_id!r} probe lost its fenced lease; parked as inconclusive",
                metrics={**metrics, "exit_class": exit_class, "turn_outcome": turn_outcome},
            )
        return JobHandlerOutcome.failed(
            "executor_lease_lost",
            _command_failure_reason(f"lane {lane_id!r} lost its fenced lease while the command was running", tail),
            metrics={**metrics, "exit_class": exit_class, "turn_outcome": turn_outcome},
        )
    if monitor_state == "timeout":
        exit_class = classify_exit(return_code=return_code, timed_out=True)
        turn_outcome = _finish_lane_turn(
            turn,
            spawned=True,
            exit_class=exit_class,
            extra=_lane_turn_failure_detail(tail),
            report_present=raw_report is not None,
        )
        return JobHandlerOutcome.failed(
            "scheduled_command_timeout",
            _command_failure_reason(f"lane {lane_id!r} exceeded its {int(timeout)} second command budget", tail),
            metrics={**metrics, "exit_class": exit_class, "turn_outcome": turn_outcome},
        )
    if return_code is None:  # pragma: no cover - exited always carries Process.wait's integer
        exit_class = classify_exit(return_code=None)
        turn_outcome = _finish_lane_turn(
            turn, spawned=True, exit_class=exit_class, extra=_lane_turn_failure_detail(tail)
        )
        return JobHandlerOutcome.failed(
            "scheduled_command_exit",
            f"lane {lane_id!r} returned no exit status",
            metrics={**metrics, "exit_class": exit_class, "turn_outcome": turn_outcome},
        )
    if return_code != 0:
        winning_outcome = router_usage["last_send_outcome"]
        exit_class = classify_exit(
            return_code=return_code,
            report=raw_report,
            stderr_tail=tail.tail.decode("utf-8", errors="replace"),
            last_send_outcome=winning_outcome if isinstance(winning_outcome, str) else None,
            lane_id=_WRAPPER_EVIDENCE_KEYS.get(lane_id, lane_id),
            native=spec.is_config,
        )
        turn_outcome = _finish_lane_turn(
            turn,
            spawned=True,
            exit_class=exit_class,
            extra={"exit_code": return_code, **_lane_turn_failure_detail(tail)},
            report_present=raw_report is not None,
        )
        return JobHandlerOutcome.failed(
            "scheduled_command_exit",
            _command_failure_reason(f"lane {lane_id!r} command exited with status {return_code}", tail),
            metrics={**metrics, "exit_code": return_code, "exit_class": exit_class, "turn_outcome": turn_outcome},
        )
    exit_class = classify_exit(return_code=0, report=raw_report, native=spec.is_config)
    turn_outcome = _finish_lane_turn(
        turn,
        spawned=True,
        exit_class=exit_class,
        incomplete=incomplete,
        extra=None if exit_class == "ok" else _lane_turn_failure_detail(tail),
        report_present=raw_report is not None,
        incomplete_reason=_incomplete_reason(turn_report, raw_probe_status),
        conclusive=is_conclusive(lane_id, raw_report),
    )
    if spec.is_config and exit_class == "report_missing":
        # The runner prints its S5 report from `finally` (FR-38): exit 0 without one is a runner bug, so a config
        # turn fails and its hold climbs the code ladder; a legacy writer's missing report stays a completion.
        return JobHandlerOutcome.failed(
            "report_missing",
            _command_failure_reason(f"lane {lane_id!r} runner exited 0 without its terminal report", tail),
            metrics={**metrics, "exit_code": return_code, "exit_class": exit_class, "turn_outcome": turn_outcome},
        )
    cursor = {
        "state": "completed",
        "scheduled_for": bucket,
        "completed_at": datetime.now(UTC).isoformat(),
        # The checkpoint row is where an exit-0-but-incomplete turn survives the log stream's retention.
        "turn_report": None if turn_report is None else turn_report.to_dict(),
    }
    return JobHandlerOutcome.completed(
        cursor=cursor,
        metrics={**metrics, "exit_code": return_code, "exit_class": exit_class, "turn_outcome": turn_outcome},
    )


def executor_inventory(activation: ActivationConfig, catalogue: LaneCatalogue | None = None) -> dict[str, object]:
    """The `--inventory` document: every definition on both paths, then quarantined lane TOMLs (spec §4.4)."""
    catalogue = _tick_catalogue() if catalogue is None else catalogue
    dispatch = catalogue.dispatch_activation(activation)
    return {
        "event": "plantgeo_job_executor_inventory",
        "mode": "active" if dispatch.active_lanes else "shadow",
        "activation_variables": [ACTIVE_LANES_VARIABLE, STOPPED_LANES_VARIABLE],
        "lanes": catalogue.inventory_rows(activation),
        "lane_catalogue_error": catalogue.load_error,
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


def announce_operator_actions(
    summary: ExecutorTickSummary,
    announced: set[tuple[str, str | None]],
    *,
    durable: AbstractSet[tuple[str, str | None]] = frozenset(),
) -> None:
    """Log each held lane's release command ONCE while it holds, and once more when it clears.

    The tick already printed the same command inside `blockers` every thirty seconds for a week with nothing
    consuming it; a flood is as invisible as silence. One `error`-severity event per held run, at top level
    with the exact verb to run, is what a log-based alert or a human skim can actually see. `durable` is the
    set whose OPEN `lane_hold` row already records the announcement (GL-5: de-duplicated on incident rows,
    so a restart does not re-announce); `announced` is the per-process fallback used when that row could
    not be written.
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
        if key in durable:
            continue  # the hold row carries this announcement; a restart must not repeat it
        logger.error(
            "plantgeo_job_executor_operator_action_required",
            lane_id=action.lane_id,
            run_id=key[1],
            command=action.command,
            # o5a, observational: the newest class THIS PROCESS classified for this lane, from
            # `_LANE_EXIT_CLASSES` (a restart or a fresh-process hold reads `None` here until this
            # process itself runs one turn for it -- o2b's `select_run_final_attempt.sql` is GL-5's job).
            exit_class=_LANE_EXIT_CLASSES.get(action.lane_id),
            detail="the clock no longer releases this lane; nothing runs on it until this command is recorded",
        )
    for key in [key for key in announced if key not in current]:
        announced.discard(key)
        logger.info("plantgeo_job_executor_operator_action_cleared", lane_id=key[0], run_id=key[1])


def _tick_signature(summary: ExecutorTickSummary) -> tuple[tuple[str, str, str | None], ...]:
    """(lane_id, state, run_id) per lane -- design Sec 1.4's "the tick summary prints when (lane,
    state, run) changes, and hourly"."""
    return tuple(
        (lane.lane_id, lane.state, None if lane.run_id is None else str(lane.run_id)) for lane in summary.lanes
    )


#: The "hourly" half of the tick-summary gate above.
TICK_SUMMARY_HEARTBEAT_SECONDS: Final = 3600.0

UnhealthySignature = tuple[frozenset[str], frozenset[str], frozenset[str]]


def _unhealthy_signature(summary: ExecutorTickSummary) -> UnhealthySignature:
    """(failing lanes, incomplete lanes, operator commands) of an unhealthy tick.

    `tick_unhealthy` prints whenever this CHANGES while unhealthy, not only on the healthy->unhealthy
    edge, so a new failure behind a standing hold is never hidden by the one before it.
    """
    return (
        frozenset(lane.lane_id for lane in summary.lanes if lane.state == "failed"),
        frozenset(lane.lane_id for lane in summary.incomplete_lanes),
        frozenset(action.command for action in summary.operator_actions),
    )


class _UnhealthyEdge:
    """`tick_unhealthy` on a transition (design Sec 1.4), and `tick_healthy` at debug otherwise."""

    def __init__(self) -> None:
        self._last: UnhealthySignature | None = None

    def report(self, summary: ExecutorTickSummary) -> None:
        """Log this tick's health line; an unchanged unhealthy set prints nothing again.

        GL-5's own incident rows are the durable record of an ongoing state; this line is only the edge.
        """
        if not summary.failed:
            self._last = None
            logger.debug(
                "plantgeo_job_executor_tick_healthy",
                leader=summary.leader,
                lane_count=len(summary.lanes),
                incomplete_lanes=[lane.lane_id for lane in summary.incomplete_lanes],
            )
            return
        unhealthy = _unhealthy_signature(summary)
        if unhealthy != self._last:
            failing, incomplete, operator_actions = unhealthy
            logger.error(
                "plantgeo_job_executor_tick_unhealthy",
                failing_lanes=sorted(failing),
                incomplete_lanes=sorted(incomplete),
                operator_actions=sorted(operator_actions),
            )
        self._last = unhealthy


async def _service_loop(  # noqa: PLR0913 - the process's parsed settings, one per keyword
    *,
    activation: ActivationConfig,
    poll_seconds: float,
    max_lanes_per_tick: int,
    once: bool,
    repair_clock: RepairAuthoringClock | None,
    soft_failure: SoftFailureState,
    dispatch: DispatchMode = "serial",
    max_concurrent_lanes: int = DEFAULT_MAX_CONCURRENT_LANES,
    budget: BudgetAdmissionState | None = None,
) -> int:
    """The leader loop. Under `dispatch="queue"` (S15) lanes run on their own sessions across ticks; a tick
    that finds another leader, or loses its own ledger connection, cancels them (leader loss). `--once` is
    always one serial tick, so nothing it starts outlives it. See execution/AGENTS.md, "Work queue". `--once`
    never runs the daily upkeep (job-event DDL, the monthly receipt); a continuous service does."""
    database_url = settings.require_local_source_loader_database_url()
    queued = dispatch == "queue" and not once
    maintenance = None if once else DailyMaintenance(database_url=database_url)
    async with (
        local_source_loader_pool(database_url) as loader_pool,
        shutdown_signal() as stop,
        _lane_session_slots(database_url, max_concurrent_lanes if queued else 0) as open_lane_session,
    ):
        dispatcher = (
            LaneDispatcher(open_lane_session=open_lane_session, max_concurrent_lanes=max_concurrent_lanes, stop=stop)
            if queued
            else None
        )
        try:
            return await _run_service_ticks(
                loader_pool=loader_pool,
                stop=stop,
                dispatcher=dispatcher,
                activation=activation,
                poll_seconds=poll_seconds,
                max_lanes_per_tick=max_lanes_per_tick,
                once=once,
                repair_clock=repair_clock,
                soft_failure=soft_failure,
                budget=budget,
                maintenance=maintenance,
            )
        finally:
            if dispatcher is not None:
                await dispatcher.drain(timeout_seconds=LANE_DRAIN_SECONDS)


async def _run_service_ticks(  # noqa: PLR0913 - the loop's collaborators, one per keyword
    *,
    loader_pool: AsyncEngine,
    stop: ShutdownSignal,
    dispatcher: LaneDispatcher | None,
    activation: ActivationConfig,
    poll_seconds: float,
    max_lanes_per_tick: int,
    once: bool,
    repair_clock: RepairAuthoringClock | None,
    soft_failure: SoftFailureState,
    budget: BudgetAdmissionState | None = None,
    maintenance: DailyMaintenance | None = None,
) -> int:
    """Tick until shutdown: plan and dispatch, echo the summary on a change or hourly, back off on a fault."""
    failures = 0
    breaker_release = ProcessStartRelease.from_environment(now=datetime.now(UTC))
    last_signature: tuple[tuple[str, str, str | None], ...] | None = None
    last_echo_monotonic = -TICK_SUMMARY_HEARTBEAT_SECONDS  # forces the very first tick to print
    health = _UnhealthyEdge()
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
                    soft_failure=soft_failure,
                    dispatcher=dispatcher,
                    budget=budget,
                    maintenance=maintenance,
                )
            if dispatcher is not None and not summary.leader and dispatcher.in_flight:
                # Leader loss: another process leads now, and its planner owns these lanes' next turns.
                await dispatcher.cancel_all(reason="leader_lost")
            signature = _tick_signature(summary)
            now_monotonic = time.monotonic()
            if signature != last_signature or now_monotonic - last_echo_monotonic >= TICK_SUMMARY_HEARTBEAT_SECONDS:
                # The hourly heartbeat lists chronic holds and failing planners (spec Sec 4.9.3).
                heartbeat = {
                    **summary.to_dict(),
                    "chronic_holds": list(soft_failure.chronic_lanes),
                    "plan_failed_lanes": list(soft_failure.plan_failed_lanes),
                }
                click.echo(json.dumps(heartbeat, sort_keys=True))
                last_signature = signature
                last_echo_monotonic = now_monotonic
            announce_operator_actions(summary, soft_failure.announced, durable=soft_failure.durable_announcements)
            for lane in summary.incomplete_lanes:
                if lane.turn_report is not None:
                    logger.warning(
                        "plantgeo_job_executor_lane_incomplete",
                        lane_id=lane.lane_id,
                        days_unwritten=lane.turn_report.days_unwritten,
                        consecutive_incomplete_buckets=lane.turn_report.consecutive_incomplete_buckets,
                        publication_debt=lane.turn_report.publication_debt,
                        publication_debt_counts=dict(lane.turn_report.publication_debt_counts),
                        unwritten=[dict(entry) for entry in lane.turn_report.unwritten],
                    )
            health.report(summary)
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
            if dispatcher is not None and isinstance(error, ExecutorLeaderUnlockError):
                # The leader lock may still be held by a backend this process lost: leadership is unproven.
                # Any OTHER tick fault (a planning statement timeout included) leaves running lanes alone:
                # each holds its own definition lock and fenced lease on its own session, and leadership is
                # given up at the end of every tick anyway (review H4).
                await dispatcher.cancel_all(reason="leader_connection_lost")
            if once:
                return 1
            if await _wait_for_shutdown(stop, delay):
                break
    return 0


@click.command("jobs-executor")
@click.option("--once", is_flag=True, help="Run one leader-elected scheduler tick and exit.")
@click.option("--inventory", "inventory_only", is_flag=True, help="Print the code-owned lane inventory and exit.")
def jobs_executor(once: bool, inventory_only: bool) -> None:
    """Run the single continuous PlantGeo ingestion and Parquet job service.

    No malformed executor variable stops it (spec Sec 4.9.3): an unknown or non-executable allow-list id is
    quarantined, a garbled number falls back to its default with one warning and an `executor_config`
    incident. The remaining startup exits are a missing DSN and a `Settings()` validation error.
    """
    try:
        activation = parse_activation()
        catalogue = _tick_catalogue()
        inventory = executor_inventory(activation, catalogue)
        click.echo(json.dumps(inventory, sort_keys=True))
        if inventory_only:
            return
        log_quarantined_lanes(activation, catalogue)
        executor_settings = ExecutorSettings.from_environment()
        with suppress(Exception):
            logger.info(
                "plantgeo_job_executor_runtime",
                dispatch=executor_settings.dispatch,
                breaker=executor_settings.breaker,
                max_concurrent_lanes=executor_settings.max_concurrent_lanes,
                soft_failure=executor_settings.soft_failure_enabled,
                code_probe_hours=(
                    None if executor_settings.hold_ladders is None else executor_settings.hold_ladders.code_probe_hours
                ),
            )
        exit_code = asyncio.run(
            _service_loop(
                activation=activation,
                poll_seconds=executor_settings.poll_seconds,
                max_lanes_per_tick=executor_settings.max_lanes_per_tick,
                once=once,
                repair_clock=executor_settings.repair_clock,
                soft_failure=SoftFailureState.for_process(activation, executor_settings, catalogue),
                dispatch=executor_settings.dispatch,
                max_concurrent_lanes=executor_settings.max_concurrent_lanes,
                budget=BudgetAdmissionState(),
            )
        )
    except ExecutorConfigurationError as error:
        raise click.ClickException(str(error)) from error
    if exit_code:
        raise click.exceptions.Exit(exit_code)


__all__ = [
    "ACTIVE_LANES_VARIABLE",
    "BREAKER_MODE_VARIABLE",
    "CLOCK_RELEASE_STREAK_LIMIT",
    "COMMAND_STDERR_SUMMARY_CHARS",
    "COMMAND_STDERR_TAIL_BYTES",
    "DEFAULT_MAX_CONCURRENT_LANES",
    "DISPATCH_VARIABLE",
    "EXECUTOR_DEFINITION_PREFIX",
    "EXECUTOR_DEFINITION_VERSION",
    "EXECUTOR_WORK_ITEM_KINDS",
    "FAILURE_STREAK_PROBE_LIMIT",
    "LANE_SPECS",
    "MAX_CONCURRENT_LANES_VARIABLE",
    "OPERATOR_SUPERSESSION_BLOCKER_PREFIX",
    "PLAN_FAILED_DETAIL_PREFIX",
    "PROBE_OPERATOR",
    "RUN_SUPERSESSION_FINGERPRINT_PREFIX",
    "RUN_SUPERSESSION_INCIDENT_TYPE",
    "SETTLED_WITHOUT_SUCCESS",
    "SOFT_FAILURE_VARIABLE",
    "STOPPED_LANES_VARIABLE",
    "SUPERSEDE_RUN_COMMAND",
    "ActivationConfig",
    "BreakerMode",
    "BudgetAdmissionState",
    "CheckpointVerdict",
    "CommandOutputTail",
    "CommandStderrTail",
    "ConfigFault",
    "DailyMaintenance",
    "DispatchMode",
    "DispatchedTurn",
    "DueLane",
    "ExecutorConfigurationError",
    "ExecutorLeaderUnlockError",
    "ExecutorSettings",
    "ExecutorTickSummary",
    "LaneDispatcher",
    "LaneExecutionSpec",
    "LaneTickResult",
    "LatestRun",
    "OperatorAction",
    "ProcessStartRelease",
    "RepairAuthoringClock",
    "SoftFailureState",
    "TurnReport",
    "announce_operator_actions",
    "breaker_mode",
    "bucket_after",
    "dispatch_mode",
    "ensure_lane_definition",
    "executor_inventory",
    "fair_due_order",
    "hold_ladders_from_environment",
    "jobs_executor",
    "judge_failed_checkpoint",
    "log_quarantined_lanes",
    "next_scheduled_bucket",
    "parse_activation",
    "parse_terminal_report",
    "read_lane_checkpoint",
    "record_turn_report",
    "repair_lane_spec",
    "run_executor_tick",
    "run_scheduled_command",
    "scheduled_bucket",
    "soft_failure_enabled",
    "summarize_turn_report",
    "supersession_command",
]
