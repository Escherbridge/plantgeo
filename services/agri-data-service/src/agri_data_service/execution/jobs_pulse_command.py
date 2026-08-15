"""`agri-cli jobs-pulse`: one Railway cron tick that keeps the whole in-app job runner alive.

Owner directive, 2026-08-14: "we should not need all the individual crons, maybe just one to keep a
pulse on the job runner." Before this, each durable lane -- `jobs-run --lane firms-archive`,
`jobs-run --lane streamflow-archive`, and the HTTP-triggered `strategy-mv-refresh` -- needed its own
scheduled cron service. This verb replaces that fan-out with one process that visits every lane this
service knows how to run a bounded slice of, once per tick, and reports one row per lane.

Two namespaces, visited in order, mirroring `jobs/dispatch.py`'s own split of responsibility:

1. DISPATCHABLE LANES (`jobs/dispatch.py`'s `LANE_DISPATCH` registry) -- run through the exact same
   `dispatch_lane` call `POST /api/v1/jobs/trigger` makes, so a manual trigger, a scheduled tick and
   this pulse all take one code path and honour one pause switch.
2. DURABLE ARCHIVE DEFINITIONS -- every `agri.job_definition.name` this database's ledger has ever
   written that also matches an `ingest/lanes.py` archive lane's `--lane` token, run through
   `ingest.commands.run_archive_definition_slice` -- the exact function `jobs-run` itself calls,
   extracted to a public name so this module reuses it rather than re-implementing the claim/
   checkpoint loop. A lane already covered by step 1, or one no ledger row names yet, is excluded.

A lane paused (`agri.job_definition.enabled = false` on every version of its name) is skipped, not
attempted. One lane raising or dead-lettering never stops another's turn: each lane opens and closes
its own session, and any exception is caught at that lane's boundary and reported as its own outcome.
See jobs/AGENTS.md for the runtime underneath both namespaces, and execution/AGENTS.md for this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
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
    run_archive_definition_slice,
)
from agri_data_service.ingest.lanes import BACKFILL_LANES
from agri_data_service.jobs import (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
    failure_summary,
)

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
from agri_data_service.jobs.lease import apply_statement_timeout, fetch_rows, required_column
from agri_data_service.jobs.registry import JOB_HANDLERS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.jobs.dispatch import LaneDispatchRegistry, LanePauseState
    from agri_data_service.jobs.registry import JobHandlerRegistry

# Operational telemetry to stderr, never stdout: this verb's stdout is one JSON-lines summary and
# nothing else, matching every other `jobs-*`/`ingest-*` verb in `ingest/commands.py`.
logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

FAILED_PULSE_EXIT_CODE: Final = 1

# A sensible default for a cron tick: generous enough that a healthy pulse visiting today's handful
# of lanes never trips it, short enough that a stuck lane cannot silently consume an entire cron
# cadence starting new work behind it. Overridable per invocation with `--time-budget-seconds`.
DEFAULT_PULSE_TIME_BUDGET_SECONDS: Final = 600.0

_PULSE_REQUESTED_BY: Final = "agri-cli jobs-pulse"
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

PulseLaneKind = Literal["dispatchable", "durable"]
PulseLaneOutcome = Literal["ran", "paused", "raised", "skipped_budget"]


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
class PulsePlan:
    """What this tick discovered it could run, before anything is actually dispatched or sliced."""

    dispatchable: tuple[PlannedDispatchableLane, ...]
    durable: tuple[PlannedDurableDefinition, ...]

    def to_report(self) -> dict[str, object]:
        """Render the plan for `--dry-run`: what WOULD run, without running any of it."""
        return {
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
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class PulseSummary:
    """This tick's whole report: every lane's result, in the order it was attempted."""

    lanes: tuple[PulseLaneResult, ...]

    @property
    def failed(self) -> bool:
        """True when this tick must fail the cron run: a lane raised, or a lane dead-lettered a shard.

        Matches `jobs-run`'s own exit philosophy: a paused lane, a budget-skipped lane, and a lane that
        merely left work behind are all healthy outcomes for an in-flight, multi-tick job runner.
        """
        return any(lane.outcome == "raised" or lane.dead_lettered > 0 for lane in self.lanes)

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object this verb echoes as one line, per ingest/commands.py."""
        return {
            "lanes": [lane.to_row() for lane in self.lanes],
            "lane_count": len(self.lanes),
            "ran": sum(1 for lane in self.lanes if lane.outcome == "ran"),
            "paused": sum(1 for lane in self.lanes if lane.outcome == "paused"),
            "raised": sum(1 for lane in self.lanes if lane.outcome == "raised"),
            "skipped_budget": sum(1 for lane in self.lanes if lane.outcome == "skipped_budget"),
            "dead_lettered_lanes": sum(1 for lane in self.lanes if lane.dead_lettered > 0),
        }


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


async def discover_pulse_plan(
    session: AsyncSession,
    *,
    lane_filter: frozenset[str] | None,
    registry: LaneDispatchRegistry | None = None,
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

    return PulsePlan(dispatchable=tuple(dispatchable), durable=tuple(durable))


def _budget_exhausted_result(lane: str, kind: PulseLaneKind) -> PulseLaneResult:
    """This lane was never started: the global tick budget was already spent when its turn came."""
    return PulseLaneResult(lane=lane, kind=kind, outcome="skipped_budget", seconds=0.0, records=0)


async def _run_dispatchable_lane(
    planned: PlannedDispatchableLane,
    *,
    registry: LaneDispatchRegistry | None,
    handlers: JobHandlerRegistry,
    monotonic: Callable[[], float],
) -> PulseLaneResult:
    """Dispatch one lane through the exact path `POST /jobs/trigger` uses, isolating its own failure."""
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
        return PulseLaneResult(
            lane=planned.lane_id,
            kind="dispatchable",
            outcome="raised",
            seconds=monotonic() - started,
            records=0,
            detail=failure_summary(error),
        )
    elapsed = monotonic() - started
    if outcome.state == "paused":
        # A narrow race: the definition paused between discovery and this dispatch call. Report it
        # exactly as the pre-flight check above would have, rather than as a raised lane.
        return PulseLaneResult(lane=planned.lane_id, kind="dispatchable", outcome="paused", seconds=elapsed, records=0)
    summary = outcome.summary
    records = 0 if summary is None else summary.claimed
    dead_lettered = 0 if summary is None else summary.dead_lettered
    return PulseLaneResult(
        lane=planned.lane_id,
        kind="dispatchable",
        outcome="ran",
        seconds=elapsed,
        records=records,
        dead_lettered=dead_lettered,
    )


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
        return PulseLaneResult(
            lane=planned.lane_token,
            kind="durable",
            outcome="raised",
            seconds=monotonic() - started,
            records=0,
            detail=failure_summary(error),
        )
    elapsed = monotonic() - started
    return PulseLaneResult(
        lane=planned.lane_token,
        kind="durable",
        outcome="ran",
        seconds=elapsed,
        records=summary.claimed,
        dead_lettered=summary.dead_lettered,
    )


async def run_jobs_pulse(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single pulse tick
    *,
    lane_filter: frozenset[str] | None,
    time_budget_seconds: float,
    worker_id: str,
    registry: LaneDispatchRegistry | None = None,
    handlers: JobHandlerRegistry = JOB_HANDLERS,
    monotonic: Callable[[], float] = time.monotonic,
) -> PulseSummary:
    """Dispatch every dispatchable lane, then run one slice of every durable archive definition owed.

    `time_budget_seconds` bounds when this tick STARTS a new lane, never a lane already in hand: the
    budget is checked before each lane's own turn, exactly as `run_job_slice`'s own budget is checked
    before claiming another shard, never mid-shard. A lane's own slice keeps its own definition-level
    lease/budget regardless of how much of this tick's global budget remains.
    """
    started = monotonic()
    deadline = started + time_budget_seconds
    async with ingest_session() as discovery_session:
        plan = await discover_pulse_plan(discovery_session, lane_filter=lane_filter, registry=registry)
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
    return PulseSummary(lanes=tuple(results))


async def _dry_run_report(lane_filter: frozenset[str] | None) -> dict[str, object]:
    """Build `--dry-run`'s report: the plan this tick would execute, without executing any of it."""
    async with ingest_session() as session:
        plan = await discover_pulse_plan(session, lane_filter=lane_filter)
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
    help="List what this tick WOULD run -- the dispatch registry, the ledger's own due definitions, and "
    "every lane's pause state -- without dispatching or slicing anything.",
)
@click.pass_context
def jobs_pulse(
    context: click.Context,
    time_budget_seconds: float,
    lane_names: tuple[str, ...],
    dry_run: bool,
) -> None:
    """Keep the whole in-app job runner alive from ONE Railway cron, replacing a per-lane cron service.

    Two ordered passes over ONE tick: every dispatchable lane in `jobs/dispatch.py`'s `LANE_DISPATCH`
    registry (the same path `POST /api/v1/jobs/trigger` runs), then one bounded slice of every durable
    archive definition this database's ledger owns that also names an `ingest/lanes.py` `--lane` token
    and is not already covered by a dispatchable lane. A lane paused in the ledger is skipped rather
    than attempted, in either pass.

    EXIT CODES, matching `jobs-run`'s own philosophy:

      0 -- the tick ran. Some lanes may have been paused; some may have been skipped because this
           tick's own time budget was already spent starting the next one. Neither is an incident --
           a multi-tick, in-flight job runner working exactly as designed looks like this.
      1 -- any lane DEAD-LETTERED a shard during this tick, or any lane's own dispatch/slice call
           raised. Per-lane isolation means one such lane never stops another lane's turn; it only
           changes THIS TICK'S exit code, once every lane still due one has had its turn.
    """
    lane_filter = _parse_lane_filter(lane_names)

    try:
        if dry_run:
            report = asyncio.run(_dry_run_report(lane_filter))
            click.echo(json.dumps(report, sort_keys=True))
            return
        summary = asyncio.run(
            run_jobs_pulse(
                lane_filter=lane_filter,
                time_budget_seconds=time_budget_seconds,
                worker_id=_default_worker_id(),
            )
        )
    except _LEDGER_ERRORS as exc:
        raise _pulse_ledger_failure(exc, "jobs-pulse") from exc
    click.echo(json.dumps(summary.to_summary(), sort_keys=True))
    if summary.failed:
        context.exit(FAILED_PULSE_EXIT_CODE)
