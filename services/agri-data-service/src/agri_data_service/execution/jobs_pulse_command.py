"""`agri-service ops jobs-pulse`: one Railway cron tick that keeps the whole in-app job runner alive.

Owner directive, 2026-08-14: "we should not need all the individual crons, maybe just one to keep a
pulse on the job runner." Before this, each durable lane -- `jobs-run --lane firms-archive`,
`jobs-run --lane streamflow-archive`, and the HTTP-triggered `strategy-mv-refresh` -- needed its own
scheduled cron service. This verb replaces that fan-out with one process that visits every lane this
service knows how to run a bounded slice of, once per tick, and reports one row per lane.

Three namespaces, visited in order, the first two mirroring `jobs/dispatch.py`'s own split of
responsibility:

1. DISPATCHABLE LANES (`jobs/dispatch.py`'s `LANE_DISPATCH` registry) -- run through the exact same
   `dispatch_lane` call `POST /api/v1/jobs/trigger` makes, so a manual trigger, a scheduled tick and
   this pulse all take one code path and honour one pause switch.
2. DURABLE ARCHIVE DEFINITIONS -- every `agri.job_definition.name` this database's ledger has ever
   written that also matches an `ingest/lanes.py` archive lane's `--lane` token, run through
   `ingest.commands.run_archive_definition_slice` -- the exact function `jobs-run` itself calls,
   extracted to a public name so this module reuses it rather than re-implementing the claim/
   checkpoint loop. A lane already covered by step 1, or one no ledger row names yet, is excluded.
3. THE DATA-QUALITY MAINTENANCE PASS -- `jobs-reconcile-lane --apply` then `jobs-plan-gaps --apply`
   for each lane step 2 discovered, then one global `validate-streams`.

Why the maintenance pass lives HERE rather than in its own cron service. The 2026-08-14 consolidation
deleted `infra/cron-maintain-*` and `infra/cron-validate`, which left `validate-streams`,
`jobs-plan-gaps` and `jobs-reconcile-lane` on no schedule at all -- so the loop that turns a DETECTED
gap into a CLAIMABLE work item was manual-only, and a lane could sit with a hole in it indefinitely
while every cron stayed green. Restoring that loop as three more cron services would have to name the
lanes in a hard-coded shell string (both verbs take a required `--lane`), and a hard-coded lane list
joins to nothing the day a lane is renamed -- the exact failure `_ARCHIVE_LANE_TOKEN_BY_DEFINITION_NAME`
below exists to avoid. This pass instead reuses the lane set step 2 already discovered from the ledger,
so a new archive lane is maintained the moment its first run is planned, with no second list to update.

These are NOT registered as dispatchable lanes. A dispatchable lane is driven by `run_job_slice`, which
takes a lease and writes `agri.job_work_item` rows of its own; reconcile and gap-planning exist to
MUTATE those same rows for other lanes, so running them inside a slice would have the ledger's own
bookkeeping and its maintenance contend for one set of rows in one transaction. They are a third pass
over the plan instead, which `--dry-run` reports alongside the other two.

ORDER WITHIN THE PASS is load-bearing. Per lane, reconcile runs before gap-planning: reconcile settles
the windows the layer already serves, so gap-planning then measures holes against the settled truth
rather than against windows that were about to be marked succeeded. `validate-streams` runs last, for
two reasons: its verdict then describes the state the tick actually leaves behind, and it is the one
step that can be dropped by the tick's time budget without leaving a lane half-maintained.

A lane paused (`agri.job_definition.enabled = false` on every version of its name) is skipped, not
attempted, in every pass. One lane raising or dead-lettering never stops another's turn: each lane
opens and closes its own session, and any exception is caught at that lane's boundary and reported as
its own outcome. See jobs/AGENTS.md for the runtime underneath the first two namespaces, and
execution/AGENTS.md for this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import click
import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.engine import ingest_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.archive_walk import archive_lane_definition_name
from agri_data_service.ingest.commands import (
    WORKER_ID_MAX_LENGTH,
    WORKER_ID_VARIABLE,
    build_stream_validation_report,
    plan_archive_lane_gaps,
    reconcile_archive_lane,
    run_archive_definition_slice,
)
from agri_data_service.ingest.lanes import BACKFILL_LANES, resolve_lane
from agri_data_service.jobs import (
    PREFLIGHT_MISSING_RELATIONS_STOP_REASON,
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
    failure_summary,
)

# Same mechanism, same reason: `register_dispatchable_lane` runs at the bottom of
# `jobs.matview_refresh` at import time. Without this import the matview-refresh lane exists in code
# but is invisible to this CLI's `LANE_DISPATCH` registry -- every matview it refreshes stays
# unscheduled, which is the exact bug this lane exists to prevent, one layer up.
from agri_data_service.jobs import matview_refresh as _matview_refresh_registers_on_import  # noqa: F401

# Importing this module is also what REGISTERS the strategy-mv-refresh dispatchable lane in THIS
# process: `register_dispatchable_lane` runs at the bottom of `jobs.strategy_mv_refresh` at import
# time, the same mechanism `jobs/scheduler.py` relies on so the HTTP trigger route can reach it. This
# CLI never imports `jobs/scheduler.py` (that module also wires a Sanic blueprint and an in-process
# asyncio loop this one-shot verb has no use for), so it imports the one module that actually calls
# `register_dispatchable_lane` instead. A future dispatchable lane must be imported here too, exactly
# as it must be added wherever `routes/__init__.py`'s import chain already reaches it for HTTP.
from agri_data_service.jobs import strategy_mv_refresh as _strategy_mv_refresh_registers_on_import  # noqa: F401
from agri_data_service.jobs.dispatch import (
    LANE_DISPATCH,
    LaneHandlerMissingError,
    UnknownDispatchableLaneError,
    dispatch_lane,
    read_lane_pause_state,
)
from agri_data_service.jobs.lease import (
    apply_statement_timeout,
    fetch_row,
    fetch_rows,
    optional_column,
    required_column,
)
from agri_data_service.jobs.registry import JOB_HANDLERS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.jobs.dispatch import LaneDispatchRegistry, LanePauseState
    from agri_data_service.jobs.registry import JobHandlerRegistry
    from agri_data_service.jobs.worker import JobSliceSummary

# Operational telemetry to stderr, never stdout: this verb's stdout is one JSON-lines summary and
# nothing else, matching every other `jobs-*`/`ingest-*` verb in `ingest/commands.py`.
logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

FAILED_PULSE_EXIT_CODE: Final = 1

# A sensible default for a cron tick: generous enough that a healthy pulse visiting today's handful
# of lanes never trips it, short enough that a stuck lane cannot silently consume an entire cron
# cadence starting new work behind it. Overridable per invocation with `--time-budget-seconds`.
DEFAULT_PULSE_TIME_BUDGET_SECONDS: Final = 600.0

_PULSE_REQUESTED_BY: Final = "agri-service ops jobs-pulse"
_WORKER_ID_PREFIX: Final = "jobs-pulse"

_SELECT_JOB_DEFINITION_REGISTRY: Final = text(load_query_sql("execution/select_job_definition_registry.sql"))

# The single source of truth for which `agri.job_definition.name` a lane token names: derived from
# `archive_lane_definition_name`, never spelled a second time, for the same reason
# `ingest/validation/constants.py::ARCHIVE_LANE_DEFINITION_NAMES` gives -- a hard-coded second
# spelling joins to nothing the day the naming changes, and a lookup that joins to nothing silently
# runs no slice while still looking healthy.
_ARCHIVE_LANE_TOKEN_BY_DEFINITION_NAME: Final[Mapping[str, str]] = MappingProxyType(
    {archive_lane_definition_name(lane): lane.name for lane in BACKFILL_LANES.values()}
)

PulseLaneKind = Literal["dispatchable", "durable", "maintenance"]

# `invalid` is deliberately distinct from `raised`: `raised` means the check itself could not run,
# while `invalid` means it ran perfectly and found rows that ARE wrong. Both fail the tick, but an
# operator reading the summary must be able to tell "the gap detector is broken" from "the gap
# detector works and is telling you something".
#
# `dead_lettered` and `standing_dead_letters` are the same distinction drawn one more time, and they exist
# because neither was distinguishable from a healthy idle tick. `dead_lettered` means THIS tick exhausted a
# work item's retries and buried it. `standing_dead_letters` means the lane is CARRYING buried work from an
# earlier tick: this tick found nothing new to bury, and `agri.job_work_item` rows are nevertheless sitting
# in 'dead_letter' for this definition right now. That second state is what let production report SUCCESS
# hourly for ~24h with two lanes fully dead: the tick that did the dead-lettering was correctly loud once,
# and every tick after it read as `stop_reason=no_claimable_work succeeded=0`, which is exactly what a
# finished lane looks like.
PulseLaneOutcome = Literal[
    "ran",
    "paused",
    "raised",
    "skipped_budget",
    "invalid",
    "dead_lettered",
    "standing_dead_letters",
    "preflight_refused",
]

# The outcomes that mean this tick found something wrong rather than found nothing to do. Named once and
# consumed by `PulseSummary.failed`, the ERROR log and the exit rule, so the three cannot disagree about
# what "a failing tick" is -- the disagreement being the whole bug this set was introduced to close.
FAILING_PULSE_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"raised", "invalid", "dead_lettered", "standing_dead_letters", "preflight_refused"}
)

# THE STANDING-FAILURE SIGNAL IS A DEAD-LETTERED WORK ITEM COUNT, NOT A RUN STATUS.
#
# It used to be `JobSliceSummary.run_status in TERMINALLY_FAILED_RUN_STATUSES`, which was wrong in both
# directions and is proved so by the ledger's own SQL rather than by argument:
#
#   It could not fire when it should have. `sql/jobs/select_open_job_run.sql` selects a run only while its
#   status is 'queued' or 'running'. The moment `refresh_job_run_rollup.sql` rolls a run up to 'failed' or
#   'partial' it stops being selectable, so the NEXT tick takes `run_job_slice`'s `run_id is None` branch,
#   which builds a summary with `stop_reason='no_open_run'` and `run_status` left at its `None` default --
#   and `None` is not a failing status. The signal therefore covered exactly ONE tick, the one on which the
#   run went terminal, and that tick was already loud through `dead_lettered > 0`. The 24-hour
#   green-while-broken hole it was written to close stayed open.
#
#   It fired when it must not have. `refresh_job_run_rollup.sql` counts a work item in 'cancelled' as
#   `failed`, and the run CASE has no 'cancelled' branch, so an OPERATOR CANCELLATION -- the only encoding
#   cancellation actually has in this ledger -- rolls the run up to 'partial' or 'failed'. Both were
#   "terminally failed". A lane holding one never-rotating run (`jobs/matview_refresh.py`, by design) keeps
#   every settled item forever, so one historical burial or cancellation would have pinned that run at
#   'partial' and redded the hourly cron permanently, with nothing an operator could do to clear it --
#   while the old detail line told them to "cancel it deliberately", which made it strictly worse.
#
# Nothing in this service ever writes 'dead_letter' or 'cancelled' to `agri.job_run.status`. Two statements
# touch that column: `insert_job_run.sql` writes 'queued', and the rollup CASE writes 'running',
# 'succeeded', 'failed' or 'partial'. Those five are the whole writable vocabulary.
#
# A dead-lettered WORK ITEM has neither defect: it is issued unconditionally, per definition, independent
# of whether a run was selected this tick; it is not produced by an operator's decision; and it IS
# operator-clearable, because requeueing or cancelling the item moves it out of 'dead_letter'.
_COUNT_DEAD_LETTERED_WORK_ITEMS: Final = text(load_query_sql("jobs/count_dead_lettered_work_items.sql"))

# The three data-quality steps, in the order one lane's turn runs them. `validate-streams` carries no
# lane of its own -- it measures every stream at once -- which is why `PlannedMaintenanceStep.lane_token`
# is optional and why the step is planned once per tick rather than once per lane.
MaintenanceStepKind = Literal["reconcile", "plan-gaps", "validate-streams"]

_GLOBAL_MAINTENANCE_STEPS: Final[tuple[MaintenanceStepKind, ...]] = ("validate-streams",)
_PER_LANE_MAINTENANCE_STEPS: Final[tuple[MaintenanceStepKind, ...]] = ("reconcile", "plan-gaps")


class MaintenanceStepContractError(RuntimeError):
    """Raised when a per-lane maintenance step was planned with no lane, which only a bad edit can cause."""


@dataclass(frozen=True, slots=True)
class PlannedDispatchableLane:
    """One dispatchable lane this tick considered, and whether the ledger says it is paused."""

    lane_id: str
    handler_token: str
    pause_state: LanePauseState


@dataclass(frozen=True, slots=True)
class PlannedDurableDefinition:
    """One durable archive definition this tick considered: its ledger name, its `--lane` token, and
    whether any version of it is still enabled."""

    definition_name: str
    lane_token: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class PlannedMaintenanceStep:
    """One data-quality step this tick considered, and whether the lane it maintains is paused."""

    step: MaintenanceStepKind
    lane_token: str | None
    enabled: bool

    @property
    def step_id(self) -> str:
        """The summary-table label: `<lane>:<step>` for a lane step, the bare step for a global one."""
        return self.step if self.lane_token is None else f"{self.lane_token}:{self.step}"

    def require_lane_token(self) -> str:
        """Return the lane this step maintains, refusing in typed terms rather than maintaining nothing."""
        if self.lane_token is None:
            raise MaintenanceStepContractError(
                f"maintenance step {self.step!r} is per-lane but was planned with no lane token"
            )
        return self.lane_token


@dataclass(frozen=True, slots=True)
class PulsePlan:
    """What this tick discovered it could run, before anything is actually dispatched or sliced."""

    dispatchable: tuple[PlannedDispatchableLane, ...]
    durable: tuple[PlannedDurableDefinition, ...]
    maintenance: tuple[PlannedMaintenanceStep, ...] = ()

    def to_report(self) -> dict[str, object]:
        """Render the plan for `--dry-run`: what WOULD run, without running any of it."""
        return {
            "maintenance": [
                {
                    "lane": entry.step_id,
                    "kind": "maintenance",
                    "step": entry.step,
                    "paused": not entry.enabled,
                    "would_run": entry.enabled,
                }
                for entry in self.maintenance
            ],
            "dispatchable": [
                {
                    "lane": entry.lane_id,
                    "kind": "dispatchable",
                    "handler": entry.handler_token,
                    "paused": entry.pause_state.paused,
                    "would_run": not entry.pause_state.paused,
                }
                for entry in self.dispatchable
            ],
            "durable": [
                {
                    "lane": entry.lane_token,
                    "kind": "durable",
                    "definition": entry.definition_name,
                    "paused": not entry.enabled,
                    "would_run": entry.enabled,
                }
                for entry in self.durable
            ],
        }


@dataclass(frozen=True, slots=True)
class PulseLaneResult:
    """One lane's outcome for this pulse tick: the compact row the summary table prints."""

    lane: str
    kind: PulseLaneKind
    outcome: PulseLaneOutcome
    seconds: float
    records: int
    dead_lettered: int = 0
    # How many of this lane's work items are sitting in 'dead_letter' RIGHT NOW, across every run its
    # definition has ever opened -- counted unconditionally, whether or not this tick drove a run. This
    # is the standing signal; `dead_lettered` above is what THIS tick buried.
    standing_dead_letters: int = 0
    detail: str | None = None

    def to_row(self) -> dict[str, object]:
        """Render one summary-table row."""
        return {
            "lane": self.lane,
            "kind": self.kind,
            "outcome": self.outcome,
            "seconds": round(self.seconds, 3),
            "records": self.records,
            "dead_lettered": self.dead_lettered,
            "standing_dead_letters": self.standing_dead_letters,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PulseSummary:
    """This tick's whole report: every lane's result, in the order it was attempted."""

    lanes: tuple[PulseLaneResult, ...]

    @property
    def failing_lanes(self) -> tuple[PulseLaneResult, ...]:
        """Every lane this tick must be reported as having failed on, in the order it was attempted."""
        return tuple(
            lane
            for lane in self.lanes
            if lane.outcome in FAILING_PULSE_OUTCOMES or lane.dead_lettered > 0 or lane.standing_dead_letters > 0
        )

    @property
    def failed(self) -> bool:
        """True when this tick must fail the cron run.

        Matches `jobs-run`'s own exit philosophy: a paused lane, a budget-skipped lane, and a lane that
        merely left work behind are all healthy outcomes for an in-flight, multi-tick job runner.

        `invalid` joins the failing set because it is `validate-streams`' own exit rule, carried through
        unchanged: a stream whose rows are WRONG fails the run, while an `incomplete` stream -- a backfill
        still in flight, which is weeks of correct operation -- does not and is reported as `ran`.

        `dead_lettered > 0` and `standing_dead_letters > 0` are kept as second, independent triggers beside
        the outcomes that name them, so a lane holding buried work still fails the tick even if some future
        edit classifies its outcome differently. Two ways to fail on the same evidence is the right
        redundancy here; the bug this guards against is a dead letter silently reading as a clean tick.
        """
        return bool(self.failing_lanes)

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object this verb echoes as one line, per ingest/commands.py."""
        return {
            "lanes": [lane.to_row() for lane in self.lanes],
            "lane_count": len(self.lanes),
            "ran": sum(1 for lane in self.lanes if lane.outcome == "ran"),
            "paused": sum(1 for lane in self.lanes if lane.outcome == "paused"),
            "raised": sum(1 for lane in self.lanes if lane.outcome == "raised"),
            "invalid": sum(1 for lane in self.lanes if lane.outcome == "invalid"),
            "skipped_budget": sum(1 for lane in self.lanes if lane.outcome == "skipped_budget"),
            "dead_lettered": sum(1 for lane in self.lanes if lane.outcome == "dead_lettered"),
            "standing_dead_letters": sum(1 for lane in self.lanes if lane.outcome == "standing_dead_letters"),
            "dead_lettered_lanes": sum(1 for lane in self.lanes if lane.dead_lettered > 0),
            "standing_dead_letter_lanes": sum(1 for lane in self.lanes if lane.standing_dead_letters > 0),
            "failed": self.failed,
            "failing_lanes": [lane.lane for lane in self.failing_lanes],
        }


def _log_tick_verdict(summary: PulseSummary) -> None:
    """Emit ONE terminal line per tick, at ERROR when anything failed and at INFO only when nothing did.

    This is the line that was missing. `jobs/dispatch.py` logs `lane_dispatched ... stop_reason=... ` at
    INFO after every dispatch, including one that buried every remaining work item, and nothing downstream
    of it ever said otherwise -- so a log scrape for ERROR over a fully dead lane found nothing at all. The
    verdict is logged here, from the summary, rather than at each lane's own boundary, because the summary
    is the only place that knows whether the WHOLE tick was healthy.

    `stop_reason` is deliberately absent from this line. It is a property of one slice's claim loop and
    `no_claimable_work` is a truthful answer even when everything is on fire; repeating it beside a verdict
    would only re-import the ambiguity this line exists to remove.
    """
    if not summary.failed:
        logger.info(
            "jobs_pulse_tick_healthy",
            lane_count=len(summary.lanes),
            ran=sum(1 for lane in summary.lanes if lane.outcome == "ran"),
        )
        return
    logger.error(
        "jobs_pulse_tick_failed",
        lane_count=len(summary.lanes),
        failing_lane_count=len(summary.failing_lanes),
        failing_lanes=[
            {
                "lane": lane.lane,
                "kind": lane.kind,
                "outcome": lane.outcome,
                "dead_lettered": lane.dead_lettered,
                "standing_dead_letters": lane.standing_dead_letters,
                "detail": lane.detail,
            }
            for lane in summary.failing_lanes
        ],
    )


def _default_worker_id() -> str:
    """Label this tick's lease owner, preferring Railway's per-container id over a random one."""
    replica = os.environ.get(WORKER_ID_VARIABLE, "").strip()
    return f"{_WORKER_ID_PREFIX}:{replica or uuid.uuid4()}"[:WORKER_ID_MAX_LENGTH]


def known_lane_tokens(registry: LaneDispatchRegistry | None = None) -> frozenset[str]:
    """Every `--lane` token this verb could ever be asked for, from static code, not the ledger.

    Used only to validate an operator's `--lane` filter early -- BEFORE any query -- exactly as
    `jobs-run`'s own `_lane_from_token` validates against `BACKFILL_LANES` rather than the ledger.
    """
    dispatch_registry = registry if registry is not None else LANE_DISPATCH
    return frozenset(BACKFILL_LANES) | frozenset(dispatch_registry.lane_ids())


def _plan_maintenance_steps(
    durable: Sequence[PlannedDurableDefinition],
    *,
    lane_filter: frozenset[str] | None,
) -> tuple[PlannedMaintenanceStep, ...]:
    """Derive the data-quality pass from the lanes the durable pass already found in the ledger.

    Reusing that list rather than walking `BACKFILL_LANES` is deliberate on both sides: a lane with no
    ledger row has no planned run to reconcile or plan into, and a lane the ledger DOES name is
    maintained without anything having to add it to a second list.

    `validate-streams` measures every stream at once, so it is planned only for an unfiltered tick --
    under `--lane` an operator has asked about one lane, and a report covering all of them would answer
    a question they did not ask and could fail the tick on a lane they excluded.
    """
    steps = [
        PlannedMaintenanceStep(step=step, lane_token=entry.lane_token, enabled=entry.enabled)
        for entry in durable
        for step in _PER_LANE_MAINTENANCE_STEPS
    ]
    if lane_filter is None:
        steps.extend(
            PlannedMaintenanceStep(step=step, lane_token=None, enabled=True) for step in _GLOBAL_MAINTENANCE_STEPS
        )
    return tuple(steps)


async def discover_pulse_plan(
    session: AsyncSession,
    *,
    lane_filter: frozenset[str] | None,
    registry: LaneDispatchRegistry | None = None,
    include_maintenance: bool = True,
) -> PulsePlan:
    """Read the dispatch registry's pause states and the ledger's own archive-definition namespace.

    Read-only. A caller that goes on to execute the plan opens its OWN session per lane -- exactly as
    `jobs-run` does -- rather than reusing this one, so a caller that only wants `--dry-run`'s report
    must roll this session back itself once it has read the result.
    """
    dispatch_registry = registry if registry is not None else LANE_DISPATCH
    await apply_statement_timeout(session)

    dispatchable: list[PlannedDispatchableLane] = []
    for lane in dispatch_registry.lanes():
        if lane_filter is not None and lane.lane_id not in lane_filter:
            continue
        pause_state = await read_lane_pause_state(session, lane.lane_id)
        dispatchable.append(
            PlannedDispatchableLane(lane_id=lane.lane_id, handler_token=lane.handler_token, pause_state=pause_state)
        )

    dispatch_ids = dispatch_registry.lane_ids()
    rows = await fetch_rows(session, _SELECT_JOB_DEFINITION_REGISTRY, {})
    durable: list[PlannedDurableDefinition] = []
    for row in rows:
        name = required_column(row, "name", str)
        lane_token = _ARCHIVE_LANE_TOKEN_BY_DEFINITION_NAME.get(name)
        if lane_token is None:
            # Not a definition this verb knows how to run a bounded slice against -- e.g. the
            # covariate-wind training lane, which is deliberately NOT `jobs-run`-shaped (it writes
            # through the evaluation-writer DSN, not the source-loader one this verb uses).
            continue
        if name in dispatch_ids:
            # Already covered by step 1. Defensive: no lane occupies both namespaces today, since
            # `archive_lane_definition_name` and a dispatchable lane_id are disjoint spellings, but a
            # future lane_id colliding with an archive definition name must not run twice.
            continue
        if lane_filter is not None and lane_token not in lane_filter:
            continue
        enabled = required_column(row, "any_version_enabled", bool)
        durable.append(PlannedDurableDefinition(definition_name=name, lane_token=lane_token, enabled=enabled))

    maintenance = _plan_maintenance_steps(durable, lane_filter=lane_filter) if include_maintenance else ()
    return PulsePlan(dispatchable=tuple(dispatchable), durable=tuple(durable), maintenance=maintenance)


def _budget_exhausted_result(lane: str, kind: PulseLaneKind) -> PulseLaneResult:
    """This lane was never started: the global tick budget was already spent when its turn came."""
    return PulseLaneResult(lane=lane, kind=kind, outcome="skipped_budget", seconds=0.0, records=0)


def _slice_outcome(summary: JobSliceSummary) -> tuple[PulseLaneOutcome, str | None]:
    """Classify what ONE slice did this tick: what it buried, or that it refused to start at all.

    Deliberately says nothing about work buried on an EARLIER tick -- see `_read_standing_dead_letters`
    and the module constant above it for why `JobSliceSummary.run_status` cannot carry that and what
    replaced it. This function reads only evidence the slice in hand actually produced.
    """
    if summary.dead_lettered > 0:
        return "dead_lettered", (
            f"dead-lettered {summary.dead_lettered} of {summary.claimed} claimed "
            f"(succeeded {summary.succeeded}, retried {summary.retried}, run_status {summary.run_status})"
        )
    if summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON:
        # The lane refused before it ever opened a run or minted a shard -- work that will not run,
        # reported as though nothing needed to run. See worker.py::preflight_required_relations and
        # jobs/AGENTS.md "Preflight".
        return "preflight_refused", (
            f"preflight refused: missing relation(s) {', '.join(summary.preflight_missing_relations)}"
        )
    return "ran", None


@dataclass(frozen=True, slots=True)
class StandingDeadLetters:
    """One lane's buried work as it stands right now, independent of what this tick did."""

    count: int
    first_shard_key: str | None = None

    @property
    def present(self) -> bool:
        """True when this lane is carrying at least one dead-lettered work item."""
        return self.count > 0

    def describe(self) -> str:
        """The operator-facing sentence, naming a shard to look at and an action that actually clears it."""
        shard = "" if self.first_shard_key is None else f", first {self.first_shard_key}"
        return (
            f"{self.count} work item(s) standing dead-lettered for this definition{shard}; "
            "the lane is not idle, its work is buried -- requeue those items, or cancel them "
            "deliberately, and the next tick goes green"
        )


async def read_standing_dead_letters(session: AsyncSession, definition_name: str) -> StandingDeadLetters:
    """Count one definition's dead-lettered work items -- the tick's standing-failure signal.

    Issued UNCONDITIONALLY per lane, whichever way that lane's own slice landed and whether or not a run
    was selected, because that independence is the whole point: the state this measures outlives the run
    that produced it and is invisible to any one slice's summary.
    """
    row = await fetch_row(session, _COUNT_DEAD_LETTERED_WORK_ITEMS, {"definition_name": definition_name})
    if row is None:  # pragma: no cover - the statement aggregates, so it always yields exactly one row
        raise JobLedgerRowError(f"the dead-letter census for {definition_name!r} returned no row")
    return StandingDeadLetters(
        count=required_column(row, "dead_lettered_work_items", int),
        first_shard_key=optional_column(row, "first_dead_lettered_shard_key", str),
    )


async def _fold_in_standing_dead_letters(result: PulseLaneResult, definition_name: str) -> PulseLaneResult:
    """Add this definition's buried-work census to one lane's already-computed result.

    Opens its OWN session rather than reusing the lane's, for two reasons: the dispatch path's session is
    already closed by the time its result exists, and the raised path has no usable session at all -- and
    a census that ran only on the healthy paths would be exactly the conditional signal this replaced.

    FAILS CLOSED. A census that cannot be read leaves the lane `raised`, never `ran`: "we do not know
    whether this lane is carrying buried work" is not the same claim as "it is not".
    """
    try:
        async with ingest_session() as session:
            await apply_statement_timeout(session)
            standing = await read_standing_dead_letters(session, definition_name)
            await session.rollback()
    except Exception as error:  # per-lane isolation: an unreadable census must not end the pulse
        logger.error("jobs_pulse_dead_letter_census_raised", lane=result.lane, error=type(error).__name__)
        detail = f"dead-letter census failed: {failure_summary(error)}"
        return replace(result, outcome="raised", detail=_join_details(result.detail, detail))
    if not standing.present:
        return result
    # A landing this tick produced outranks the standing condition in the OUTCOME, because "this happened
    # just now" is the more actionable of the two -- but the count rides along regardless, and
    # `PulseSummary.failing_lanes` triggers on it independently of whichever outcome won.
    outcome: PulseLaneOutcome = "standing_dead_letters" if result.outcome == "ran" else result.outcome
    return replace(
        result,
        outcome=outcome,
        standing_dead_letters=standing.count,
        detail=_join_details(result.detail, standing.describe()),
    )


def _join_details(existing: str | None, addition: str) -> str:
    """Keep both halves of a lane's evidence rather than letting the later one overwrite the earlier."""
    return addition if not existing else f"{existing}; {addition}"


async def _run_dispatchable_lane(
    planned: PlannedDispatchableLane,
    *,
    registry: LaneDispatchRegistry | None,
    handlers: JobHandlerRegistry,
    monotonic: Callable[[], float],
) -> PulseLaneResult:
    """Dispatch one lane through the exact path `POST /jobs/trigger` uses, isolating its own failure.

    A dispatchable lane's `lane_id` IS its `agri.job_definition.name` -- `discover_pulse_plan` already
    relies on that when it excludes a ledger definition already covered by the dispatch registry -- so it
    is what the standing dead-letter census is keyed by.
    """
    if planned.pause_state.paused:
        return PulseLaneResult(lane=planned.lane_id, kind="dispatchable", outcome="paused", seconds=0.0, records=0)
    started = monotonic()
    try:
        async with ingest_session() as session:
            outcome = await dispatch_lane(
                session,
                planned.lane_id,
                requested_by=_PULSE_REQUESTED_BY,
                registry=registry,
                handlers=handlers,
            )
    except Exception as error:  # per-lane isolation: one lane's fault must not end the pulse
        logger.error("jobs_pulse_dispatchable_lane_raised", lane_id=planned.lane_id, error=type(error).__name__)
        return await _fold_in_standing_dead_letters(
            PulseLaneResult(
                lane=planned.lane_id,
                kind="dispatchable",
                outcome="raised",
                seconds=monotonic() - started,
                records=0,
                detail=failure_summary(error),
            ),
            planned.lane_id,
        )
    elapsed = monotonic() - started
    if outcome.state == "paused":
        # A narrow race: the definition paused between discovery and this dispatch call. Report it
        # exactly as the pre-flight check above would have, rather than as a raised lane.
        return PulseLaneResult(lane=planned.lane_id, kind="dispatchable", outcome="paused", seconds=elapsed, records=0)
    summary = outcome.summary
    if summary is None:
        result = PulseLaneResult(lane=planned.lane_id, kind="dispatchable", outcome="ran", seconds=elapsed, records=0)
    else:
        lane_outcome, detail = _slice_outcome(summary)
        result = PulseLaneResult(
            lane=planned.lane_id,
            kind="dispatchable",
            outcome=lane_outcome,
            seconds=elapsed,
            records=summary.claimed,
            dead_lettered=summary.dead_lettered,
            detail=detail,
        )
    return await _fold_in_standing_dead_letters(result, planned.lane_id)


async def _run_durable_definition(
    planned: PlannedDurableDefinition,
    *,
    worker_id: str,
    monotonic: Callable[[], float],
) -> PulseLaneResult:
    """Run one bounded slice of one durable archive definition, isolating its own failure.

    Reuses `run_archive_definition_slice` verbatim -- the same function `jobs-run` calls -- so the
    claim/checkpoint loop, the session lifetime, and the SIGTERM handling around one slice are not
    re-implemented here.
    """
    if not planned.enabled:
        return PulseLaneResult(lane=planned.lane_token, kind="durable", outcome="paused", seconds=0.0, records=0)
    started = monotonic()
    try:
        summary = await run_archive_definition_slice(
            definition_name=planned.definition_name,
            worker_id=f"{worker_id}:{planned.lane_token}"[:WORKER_ID_MAX_LENGTH],
            budget_seconds=None,
        )
    except Exception as error:  # per-lane isolation: one lane's fault must not end the pulse
        logger.error("jobs_pulse_durable_definition_raised", lane=planned.lane_token, error=type(error).__name__)
        return await _fold_in_standing_dead_letters(
            PulseLaneResult(
                lane=planned.lane_token,
                kind="durable",
                outcome="raised",
                seconds=monotonic() - started,
                records=0,
                detail=failure_summary(error),
            ),
            planned.definition_name,
        )
    elapsed = monotonic() - started
    lane_outcome, detail = _slice_outcome(summary)
    return await _fold_in_standing_dead_letters(
        PulseLaneResult(
            lane=planned.lane_token,
            kind="durable",
            outcome=lane_outcome,
            seconds=elapsed,
            records=summary.claimed,
            dead_lettered=summary.dead_lettered,
            detail=detail,
        ),
        planned.definition_name,
    )


@dataclass(frozen=True, slots=True)
class MaintenanceStepReport:
    """What one data-quality step measured: how it landed, what it changed, and the one-line evidence."""

    outcome: PulseLaneOutcome
    records: int
    detail: str | None


async def _execute_maintenance_step(planned: PlannedMaintenanceStep) -> MaintenanceStepReport:
    """Run one data-quality step through the exact function its own CLI verb calls.

    Both lane steps run with `apply_changes=True`. That is the whole point of putting them on a
    schedule: a dry run detects a gap and changes nothing, which is the state this pass exists to end.

    NO CENSUS TELEMETRY IS COLLECTED HERE, deliberately. A `_probe_census_shards` helper used to time each
    shard of the observed-day census and fold `census shards=N slowest_shard_seconds=S` into every lane
    step's detail. Its entire cost was re-reading the WHOLE census a second time, per lane, per tick, to
    describe a fan-out that no longer exists -- see `ingest/reconcile.py::observed_layer_days` for the
    measurement that removed it. Anyone wanting that number back should have `reconcile_lane` report its
    own census timing rather than reading the census twice to observe it once.
    """
    if planned.step == "validate-streams":
        # `bbox=None` means "the configured INGEST_BBOX", exactly as the verb's own default does.
        report = await build_stream_validation_report(None)
        counts = dict(report.verdict_counts)
        detail = ", ".join(f"{verdict}={count}" for verdict, count in sorted(counts.items()))
        # `incomplete` is NOT a failure -- see `PulseSummary.failed` and the verb's own docstring.
        outcome: PulseLaneOutcome = "invalid" if counts.get("invalid", 0) else "ran"
        return MaintenanceStepReport(outcome=outcome, records=len(report.streams), detail=detail)

    lane_token = planned.require_lane_token()
    lane = resolve_lane(lane_token)

    if planned.step == "reconcile":
        reconciliation = await reconcile_archive_lane(lane, apply_changes=True)
        return MaintenanceStepReport(
            outcome="ran",
            records=reconciliation.marked_succeeded_count,
            detail=(
                f"settled {reconciliation.marked_succeeded_count} of {reconciliation.planned_window_count} planned"
            ),
        )

    # `end=None` measures gaps through today, matching `jobs-plan-gaps`' own `--until` default. No
    # trailing partial window is ever planned; the forward hourly ingest leg owns the present.
    gap_plan = await plan_archive_lane_gaps(lane, end=None, apply_changes=True)
    opened = gap_plan.opened_count + gap_plan.reopened_count
    return MaintenanceStepReport(
        outcome="ran",
        records=opened,
        detail=(
            f"opened {gap_plan.opened_count}, reopened {gap_plan.reopened_count}, "
            f"missing {len(gap_plan.missing_days)} day(s), unplannable {len(gap_plan.unplannable_days)}"
        ),
    )


async def _run_maintenance_step(
    planned: PlannedMaintenanceStep,
    *,
    monotonic: Callable[[], float],
) -> PulseLaneResult:
    """Run one data-quality step, isolating its own failure exactly as the two lane passes do."""
    if not planned.enabled:
        return PulseLaneResult(lane=planned.step_id, kind="maintenance", outcome="paused", seconds=0.0, records=0)
    started = monotonic()
    try:
        report = await _execute_maintenance_step(planned)
    except Exception as error:  # per-step isolation: one check's fault must not end the pulse
        logger.error("jobs_pulse_maintenance_step_raised", step=planned.step_id, error=type(error).__name__)
        return PulseLaneResult(
            lane=planned.step_id,
            kind="maintenance",
            outcome="raised",
            seconds=monotonic() - started,
            records=0,
            detail=failure_summary(error),
        )
    return PulseLaneResult(
        lane=planned.step_id,
        kind="maintenance",
        outcome=report.outcome,
        seconds=monotonic() - started,
        records=report.records,
        detail=report.detail,
    )


async def run_jobs_pulse(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single pulse tick
    *,
    lane_filter: frozenset[str] | None,
    time_budget_seconds: float,
    worker_id: str,
    registry: LaneDispatchRegistry | None = None,
    handlers: JobHandlerRegistry = JOB_HANDLERS,
    monotonic: Callable[[], float] = time.monotonic,
    include_maintenance: bool = True,
) -> PulseSummary:
    """Dispatch every dispatchable lane, slice every durable definition owed, then maintain data quality.

    `time_budget_seconds` bounds when this tick STARTS a new lane, never a lane already in hand: the
    budget is checked before each lane's own turn, exactly as `run_job_slice`'s own budget is checked
    before claiming another shard, never mid-shard. A lane's own slice keeps its own definition-level
    lease/budget regardless of how much of this tick's global budget remains.

    The maintenance pass runs LAST so that a tick whose budget was spent on real ingest work drops
    checking rather than dropping walking -- a gap that goes one hour unmeasured is recoverable, an
    hour of un-walked backfill is an hour the archive never gets back.
    """
    started = monotonic()
    deadline = started + time_budget_seconds
    async with ingest_session() as discovery_session:
        plan = await discover_pulse_plan(
            discovery_session,
            lane_filter=lane_filter,
            registry=registry,
            include_maintenance=include_maintenance,
        )
        await discovery_session.rollback()

    results: list[PulseLaneResult] = []
    for planned_lane in plan.dispatchable:
        if monotonic() >= deadline:
            results.append(_budget_exhausted_result(planned_lane.lane_id, "dispatchable"))
            continue
        results.append(
            await _run_dispatchable_lane(planned_lane, registry=registry, handlers=handlers, monotonic=monotonic)
        )
    for planned_definition in plan.durable:
        if monotonic() >= deadline:
            results.append(_budget_exhausted_result(planned_definition.lane_token, "durable"))
            continue
        results.append(await _run_durable_definition(planned_definition, worker_id=worker_id, monotonic=monotonic))
    for planned_step in plan.maintenance:
        if monotonic() >= deadline:
            results.append(_budget_exhausted_result(planned_step.step_id, "maintenance"))
            continue
        results.append(await _run_maintenance_step(planned_step, monotonic=monotonic))
    summary = PulseSummary(lanes=tuple(results))
    _log_tick_verdict(summary)
    return summary


async def _dry_run_report(lane_filter: frozenset[str] | None, *, include_maintenance: bool) -> dict[str, object]:
    """Build `--dry-run`'s report: the plan this tick would execute, without executing any of it."""
    async with ingest_session() as session:
        plan = await discover_pulse_plan(session, lane_filter=lane_filter, include_maintenance=include_maintenance)
        await session.rollback()
    return plan.to_report()


_LEDGER_ERRORS: Final[tuple[type[Exception], ...]] = (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
    UnknownDispatchableLaneError,
    LaneHandlerMissingError,
    SQLAlchemyError,
)


def _pulse_ledger_failure(exc: Exception, action: str) -> click.ClickException:
    """Degrade a ledger failure the discovery pass itself hit, matching `ingest/commands.py::_ledger_failure`."""
    reason = f"{action} failed ({exc.__class__.__name__})" if isinstance(exc, SQLAlchemyError) else str(exc)
    return click.ClickException(reason)


def _parse_lane_filter(lane_names: Sequence[str]) -> frozenset[str] | None:
    """Validate an operator's `--lane` filter against the STATIC lane universe, naming what is known."""
    if not lane_names:
        return None
    requested = frozenset(lane_names)
    known = known_lane_tokens()
    unknown = sorted(requested - known)
    if unknown:
        raise click.BadParameter(
            f"unknown lane(s) {', '.join(unknown)!r}; known lanes are {', '.join(sorted(known)) or 'nothing'}",
            param_hint="--lane",
        )
    return requested


@click.command("jobs-pulse")
@click.option(
    "--time-budget-seconds",
    type=click.FloatRange(min=0.0),
    default=DEFAULT_PULSE_TIME_BUDGET_SECONDS,
    show_default=True,
    help="Stop STARTING new lanes once this many seconds of this tick have elapsed. A lane already in "
    "hand always finishes its own bounded slice; this never kills one mid-slice.",
)
@click.option(
    "--lane",
    "lane_names",
    multiple=True,
    help="Restrict this tick to one or more lane tokens (a dispatchable lane_id, e.g. strategy-mv-refresh, "
    "or an archive --lane token, e.g. firms-archive); repeatable. Default: every lane this verb knows.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="List what this tick WOULD run -- the dispatch registry, the ledger's own due definitions, the "
    "data-quality steps, and every lane's pause state -- without dispatching, slicing or applying anything.",
)
@click.option(
    "--skip-maintenance",
    is_flag=True,
    default=False,
    help="Run only the two lane passes, omitting the reconcile/gap-plan/validate data-quality pass. For an "
    "operator draining lane work; the scheduled tick must not use it, or gaps stop becoming work items.",
)
@click.pass_context
def jobs_pulse(
    context: click.Context,
    time_budget_seconds: float,
    lane_names: tuple[str, ...],
    dry_run: bool,
    skip_maintenance: bool,
) -> None:
    """Keep the whole in-app job runner alive from ONE Railway cron, replacing a per-lane cron service.

    Three ordered passes over ONE tick: every dispatchable lane in `jobs/dispatch.py`'s `LANE_DISPATCH`
    registry (the same path `POST /api/v1/jobs/trigger` runs), then one bounded slice of every durable
    archive definition this database's ledger owns that also names an `ingest/lanes.py` `--lane` token
    and is not already covered by a dispatchable lane, then the DATA-QUALITY pass -- per lane,
    `jobs-reconcile-lane --apply` then `jobs-plan-gaps --apply`, and finally one `validate-streams`.
    A lane paused in the ledger is skipped rather than attempted, in every pass.

    The data-quality pass is what turns a detected gap into a claimable work item. Before it lived
    here those three verbs were on no schedule at all, so a hole in a layer was found by whoever
    happened to run the report by hand. See this module's docstring for why it is a pass rather than
    three more cron services or three more dispatchable lanes.

    EXIT CODES, matching `jobs-run`'s own philosophy:

      0 -- the tick ran. Some lanes may have been paused; some may have been skipped because this
           tick's own time budget was already spent starting the next one. A stream reported
           `incomplete` -- a backfill in flight -- is likewise not an incident. All of these are what
           a multi-tick, in-flight job runner working exactly as designed looks like.
      1 -- any lane DEAD-LETTERED a shard during this tick; any lane is CARRYING dead-lettered work items
           from an earlier tick (counted per definition, unconditionally, whether or not a run was driven);
           any lane's own dispatch/slice call raised; any data-quality step raised; the dead-letter census
           itself could not be read; or `validate-streams` found a stream `invalid` (rows that ARE there
           are wrong). Per-lane isolation means one such lane never stops another lane's turn; it only
           changes THIS TICK'S exit code, once every lane still due one has had its turn. A failing tick
           also emits one `jobs_pulse_tick_failed` line at ERROR naming every failing lane, so the failure
           is visible in the log and not only in the exit status.

    An OPERATOR CANCELLATION never reds a tick. Cancellation is recorded on the WORK ITEM as 'cancelled',
    which this verb does not count, and the run-level rollup it also moves is not read here at all -- see
    the standing-signal note beside `_COUNT_DEAD_LETTERED_WORK_ITEMS` for why reading that rollup made
    cancelling a lane an unclearable hourly failure.

    WHY A NON-ZERO EXIT IS SAFE HERE, and what would make it unsafe. `infra/cron-ingest/railway.json` sets
    `restartPolicyType: NEVER` alongside `cronSchedule: 0 * * * *`, and `infra/cron-ingest/Dockerfile`'s
    ENTRYPOINT already propagates this verb's status. So a non-zero exit marks ONE hourly run red and
    Railway starts nothing in its place; the next attempt is the next hour's tick. `standing_dead_letters`
    in particular is a STANDING condition that will repeat every hour until an operator requeues or cancels
    the buried items, and that is the intended behaviour -- one red run per hour, not a restart storm. If
    that restart policy is ever changed to `ON_FAILURE`, it becomes an unbounded loop of back-to-back
    600-second ticks and must be demoted to an ERROR log with a zero exit before the policy is flipped.
    """
    lane_filter = _parse_lane_filter(lane_names)
    include_maintenance = not skip_maintenance

    try:
        if dry_run:
            report = asyncio.run(_dry_run_report(lane_filter, include_maintenance=include_maintenance))
            click.echo(json.dumps(report, sort_keys=True))
            return
        summary = asyncio.run(
            run_jobs_pulse(
                lane_filter=lane_filter,
                time_budget_seconds=time_budget_seconds,
                worker_id=_default_worker_id(),
                include_maintenance=include_maintenance,
            )
        )
    except _LEDGER_ERRORS as exc:
        raise _pulse_ledger_failure(exc, "jobs-pulse") from exc
    click.echo(json.dumps(summary.to_summary(), sort_keys=True))
    if summary.failed:
        context.exit(FAILED_PULSE_EXIT_CODE)
