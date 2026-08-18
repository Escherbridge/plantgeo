"""Lane dispatch: turn one operator-supplied `lane_id` into one real slice on the `agri.job_*` ledger.

This is what `POST /api/v1/jobs/trigger` and the in-process periodic drivers both call, so a manual
trigger and a scheduled tick take the same code path, honour the same pause switch, and write the
same rows. See `jobs/AGENTS.md` for the runtime this sits on top of.

Why a lane must REGISTER itself here rather than being discovered from `JOB_HANDLERS`.
`JOB_HANDLERS` maps a stored `job_definition.handler` token to a coroutine, and that coroutine is
not callable on its own: every lane in this service resolves its side effects from a lane-bound
`ContextVar` -- `strategy_mv_refresh`'s session, `archive_walk`'s feature writer and HTTP client,
`covariate_wind_lane`'s training session -- which the ledger knows nothing about and cannot supply.
A dispatcher that walked the handler registry and called `run_job_slice` for every token in it would
therefore "support" lanes it can only crash inside, one `ContextVar` lookup deep. What a lane
publishes here instead is a `trigger`: the small entry point that binds its own context, upserts its
definition, opens or rejoins its run, and drives one bounded slice. Joining is one call at import
time, next to the lane's own `@job_handler`; the route below then needs no per-lane branch at all.

Pausing is the ledger's own `job_definition.enabled` column, not a new table. It is read here,
before a lane's trigger runs, because `ensure_job_definition` writes `enabled = EXCLUDED.enabled`
on conflict -- so letting a paused lane's trigger start would immediately un-pause it as a side
effect of its own upsert. Checking first is what makes the switch hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol

import structlog
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.jobs.lease import fetch_row, required_column
from agri_data_service.jobs.registry import JOB_HANDLERS, JobHandlerRegistry
from agri_data_service.jobs.worker import slice_summary_is_failing

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.jobs.worker import JobSliceSummary

logger = structlog.get_logger(__name__)

DispatchState = Literal["dispatched", "paused"]


class UnknownDispatchableLaneError(LookupError):
    """Raised when a caller asks to run a lane that has published no dispatch entry point."""


class LaneHandlerMissingError(LookupError):
    """Raised when a registered lane's handler token resolves to no code, which only a bad import can cause."""


class LaneTrigger(Protocol):
    """A lane's own entry point: bind its context, register it, open its run, drive one slice."""

    async def __call__(self, session: AsyncSession, *, requested_by: str) -> JobSliceSummary:
        """Run exactly one bounded slice of this lane and report what the ledger recorded."""
        ...


@dataclass(frozen=True, slots=True)
class DispatchableLane:
    """One lane an operator may trigger by name, and the entry point that actually runs it."""

    lane_id: str
    handler_token: str
    trigger: LaneTrigger
    description: str


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    """What a dispatch attempt did: it either ran a slice, or it refused because the lane is paused."""

    lane_id: str
    state: DispatchState
    summary: JobSliceSummary | None

    def to_payload(self) -> dict[str, object]:
        """Render the operator-facing JSON body, carrying the real slice summary when there is one."""
        return {
            "lane_id": self.lane_id,
            "state": self.state,
            "result": None if self.summary is None else self.summary.to_summary(),
        }


class LaneDispatchRegistry:
    """Maps an operator-facing `lane_id` to the lane that owns it; a lane joins with one call."""

    def __init__(self) -> None:
        """Start empty; every dispatchable lane registers itself at import time."""
        self._lanes: dict[str, DispatchableLane] = {}

    def register(self, lane: DispatchableLane) -> None:
        """Publish one lane, refusing a second claim on a lane_id another lane already owns."""
        existing = self._lanes.get(lane.lane_id)
        if existing is not None and existing.trigger is not lane.trigger:
            raise ValueError(f"lane_id {lane.lane_id!r} is already registered by another lane")
        self._lanes[lane.lane_id] = lane

    def lane_for(self, lane_id: str) -> DispatchableLane:
        """Resolve a lane by name, naming every lane that IS dispatchable when it resolves to nothing."""
        lane = self._lanes.get(lane_id)
        if lane is None:
            known = ", ".join(self.lane_ids()) or "nothing"
            raise UnknownDispatchableLaneError(f"no dispatchable lane named {lane_id!r}; known lanes are {known}")
        return lane

    def lane_ids(self) -> tuple[str, ...]:
        """Every dispatchable lane id, sorted, so a 404 body and the admin panel agree on the list."""
        return tuple(sorted(self._lanes))

    def lanes(self) -> tuple[DispatchableLane, ...]:
        """Every dispatchable lane, in lane_id order."""
        return tuple(self._lanes[lane_id] for lane_id in self.lane_ids())

    def __contains__(self, lane_id: object) -> bool:
        """True when this lane_id resolves to a real entry point."""
        return isinstance(lane_id, str) and lane_id in self._lanes


LANE_DISPATCH: Final = LaneDispatchRegistry()


def register_dispatchable_lane(
    *,
    lane_id: str,
    handler_token: str,
    trigger: LaneTrigger,
    description: str,
    registry: LaneDispatchRegistry | None = None,
) -> DispatchableLane:
    """Publish one lane as triggerable by name; call this at import time beside its `@job_handler`."""
    lane = DispatchableLane(
        lane_id=lane_id,
        handler_token=handler_token,
        trigger=trigger,
        description=description,
    )
    (registry if registry is not None else LANE_DISPATCH).register(lane)
    return lane


_SELECT_PAUSE_STATE: Final = text(load_query_sql("jobs/select_job_definition_pause_state.sql"))


@dataclass(frozen=True, slots=True)
class LanePauseState:
    """Whether a lane is registered in the ledger at all, and whether any version of it may run."""

    registered: bool
    paused: bool


async def read_lane_pause_state(session: AsyncSession, lane_id: str) -> LanePauseState:
    """Read the ledger's own pause switch for one lane; an unregistered lane is not paused."""
    row = await fetch_row(session, _SELECT_PAUSE_STATE, {"name": lane_id})
    if row is None:  # pragma: no cover - the query aggregates, so it always yields exactly one row
        return LanePauseState(registered=False, paused=False)
    registered = required_column(row, "definition_count", int) > 0
    enabled_somewhere = required_column(row, "any_version_enabled", bool)
    return LanePauseState(registered=registered, paused=registered and not enabled_somewhere)


async def dispatch_lane(
    session: AsyncSession,
    lane_id: str,
    *,
    requested_by: str,
    registry: LaneDispatchRegistry | None = None,
    handlers: JobHandlerRegistry = JOB_HANDLERS,
) -> DispatchOutcome:
    """Run one slice of `lane_id` through the ledger, unless an operator has paused it.

    Raises `UnknownDispatchableLaneError` for a lane nothing published, and `LaneHandlerMissingError`
    for a lane whose handler token no longer resolves -- an import-order fault, not an operator one.
    """
    lane = (registry if registry is not None else LANE_DISPATCH).lane_for(lane_id)
    if lane.handler_token not in handlers:
        raise LaneHandlerMissingError(
            f"lane {lane_id!r} declares handler {lane.handler_token!r}, which is registered by nothing; "
            "its module was not imported before dispatch"
        )
    pause_state = await read_lane_pause_state(session, lane_id)
    if pause_state.paused:
        logger.info("lane_dispatch_skipped_paused", lane_id=lane_id, requested_by=requested_by)
        return DispatchOutcome(lane_id=lane_id, state="paused", summary=None)
    summary = await lane.trigger(session, requested_by=requested_by)
    # Severity follows the outcome, not the log level every dispatch used to share: a fully
    # dead-lettered lane's next tick claims nothing and reports `stop_reason=no_claimable_work
    # succeeded=0`, which is numerically identical to a finished lane -- exactly what let production
    # read SUCCESS hourly for ~24h with two lanes fully dead. `slice_summary_is_failing` is the single
    # predicate `execution/jobs_pulse_command.py`'s `_slice_outcome`/`FAILING_PULSE_OUTCOMES` also read,
    # so the two can never quietly disagree about what "failing" means. See jobs/AGENTS.md "Preflight"
    # and execution/AGENTS.md "Outcomes and the exit rule".
    log = logger.error if slice_summary_is_failing(summary) else logger.info
    log(
        "lane_dispatched",
        lane_id=lane_id,
        requested_by=requested_by,
        job_run_id=None if summary.job_run_id is None else str(summary.job_run_id),
        stop_reason=summary.stop_reason,
        succeeded=summary.succeeded,
        dead_lettered=summary.dead_lettered,
        run_status=summary.run_status,
    )
    return DispatchOutcome(lane_id=lane_id, state="dispatched", summary=summary)
