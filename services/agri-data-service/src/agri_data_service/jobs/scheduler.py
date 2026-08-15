"""The HTTP trigger route and the in-process periodic drivers for durable job lanes.

Both surfaces here are thin: they open a session and hand a `lane_id` to `jobs/dispatch.py`, which
owns lane resolution, the pause switch and the slice itself. Nothing in this module knows the name
of any particular lane.

2026-08-14, replacing a fabrication. This module previously carried an `InAppScheduler` that polled
`agri.job_schedules` and an `execute_lane_job` that reported a `records_processed` count it invented
from `min(100, max_records)`. `agri.job_schedules` was never created by anything: its DDL was written
into the ROOT repo's Drizzle tree (`drizzle/0026_agri_job_schedules.sql`), which may not touch the
`agri` schema at all -- Alembic is the only component permitted to (see `jobs/AGENTS.md`) -- and it
was never applied to production. Every statement naming that table has been deleted rather than
repointed, and the pause toggle the admin panel needs now rides `agri.job_definition.enabled`, a
column the real ledger has had since `20260719_0001`.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

import structlog
from sanic import Blueprint, Request, Sanic, json
from sanic.response import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.config import settings
from agri_data_service.db.engine import async_session, receiver_writer_session
from agri_data_service.jobs import (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
)
from agri_data_service.jobs.dispatch import (
    LANE_DISPATCH,
    DispatchOutcome,
    LaneHandlerMissingError,
    UnknownDispatchableLaneError,
    dispatch_lane,
)
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS,
)

logger = structlog.get_logger(__name__)

# Relative, like every other blueprint in `routes/__init__.py`: `app.py` mounts this group under
# `/api/v1`, so an absolute prefix here produced `/api/v1/api/v1/jobs/trigger` and the documented
# route answered 404 -- which is why nothing ever observed the lane behind it working.
jobs_bp = Blueprint("jobs", url_prefix="/jobs")

# The ledger errors a trigger can legitimately raise. Degraded to a safe message before it reaches an
# HTTP response, matching ingest/commands.py::_ledger_failure -- a raw SQLAlchemyError message
# carries the whole statement and its bound parameters, which is how a DSN reaches a client.
_LEDGER_ERRORS: tuple[type[Exception], ...] = (
    JobDefinitionNotFoundError,
    JobLedgerRowError,
    JobRunError,
    JobSpecificationError,
    UnknownJobHandlerError,
)

# Everything `POST /trigger` answers rather than propagating: the two dispatch refusals, the ledger's
# own typed errors, and the driver-level fault they all sit above.
_DISPATCH_ERRORS: tuple[type[Exception], ...] = (
    UnknownDispatchableLaneError,
    LaneHandlerMissingError,
    SQLAlchemyError,
    *_LEDGER_ERRORS,
)

_TRIGGER_REQUESTED_BY = "jobs-trigger-route"


@asynccontextmanager
async def get_scheduler_session() -> AsyncIterator[AsyncSession]:
    """Open the session this profile's writes belong on."""
    if settings.service_profile == "receiver_writer":
        async with receiver_writer_session() as session:
            yield session
    else:
        async with async_session() as session:
            yield session


@jobs_bp.get("/lanes")
async def list_lanes(_request: Request) -> JSONResponse:
    """Every lane this service can run by name, with the handler token each one drives."""
    return json(
        {
            "lanes": [
                {
                    "lane_id": lane.lane_id,
                    "handler": lane.handler_token,
                    "description": lane.description,
                }
                for lane in LANE_DISPATCH.lanes()
            ]
        }
    )


def _dispatch_failure_response(lane_id: str, error: Exception) -> JSONResponse:
    """Turn one dispatch fault into the status an operator can act on, leaking no bound parameters."""
    if isinstance(error, UnknownDispatchableLaneError):
        return json(
            {
                "error": f"lane {lane_id!r} is not a dispatchable lane",
                "known_lanes": list(LANE_DISPATCH.lane_ids()),
            },
            status=404,
        )
    if isinstance(error, (LaneHandlerMissingError, *_LEDGER_ERRORS)):
        # Safe to echo: every one of these is a message this service composed itself.
        return json({"error": str(error)}, status=500)
    # A driver-level fault, whose message carries the whole statement and its bound parameters --
    # which is how a DSN reaches a client. Only the class name crosses the boundary.
    return json({"error": f"lane {lane_id!r} failed against the ledger ({type(error).__name__})"}, status=500)


@jobs_bp.post("/trigger")
async def trigger_job(request: Request) -> JSONResponse:
    """Run one slice of any dispatchable lane, right now, through the real `agri.job_*` ledger.

    409 rather than 200 for a paused lane: the caller asked for a run and did not get one, and an
    admin panel that renders a 200 as success would show a paused lane as having just executed.
    """
    data = request.json or {}
    lane_id = data.get("lane_id")
    if not isinstance(lane_id, str) or not lane_id.strip():
        return json({"error": "lane_id is required"}, status=400)
    lane_id = lane_id.strip()

    try:
        async with get_scheduler_session() as session:
            outcome = await dispatch_lane(session, lane_id, requested_by=_TRIGGER_REQUESTED_BY)
    except _DISPATCH_ERRORS as error:
        return _dispatch_failure_response(lane_id, error)

    if outcome.state == "paused":
        return json(
            {
                "error": f"lane {lane_id!r} is paused; enable it before triggering a run",
                **outcome.to_payload(),
            },
            status=409,
        )
    return json({"message": f"Triggered execution for lane {lane_id!r}", **outcome.to_payload()})


class StrategyMvRefreshScheduler:
    """The periodic, in-process driver for the strategy-recommendation matview refresh lane.

    One lane, one plain asyncio interval -- not a general cron engine. Nothing in this service parses
    cron syntax (`job_definition.schedule` is written and never read), so a "cadence" column would be
    a second source of truth with no consumer.

    Concurrent replicas are safe by construction, not by coordination: `trigger_strategy_mv_refresh`
    buckets its run key to this class's own poll interval, and `claim_work_item`'s
    `FOR UPDATE SKIP LOCKED` fencing means two Sanic workers ticking the same window race for one
    work item and exactly one of them refreshes anything -- the loser's `run_job_slice` observes
    `no_claimable_work` and exits cleanly.

    Every tick goes through `dispatch_lane`, so the pause an operator sets in the admin panel stops
    the schedule as well as the manual button, in one place rather than two.
    """

    def __init__(self, poll_interval_seconds: int = STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS) -> None:
        """Bind the tick interval; nothing starts until `start()` is awaited."""
        self.poll_interval_seconds = poll_interval_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Begin ticking, unless this driver is already running."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("strategy_mv_refresh_scheduler_started", interval=self.poll_interval_seconds)

    async def stop(self) -> None:
        """Cancel the loop and wait for it to unwind."""
        if not self._running:
            return
        self._running = False
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        logger.info("strategy_mv_refresh_scheduler_stopped")

    async def _loop(self) -> None:
        """Tick forever, never letting one failed tick end the loop."""
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("strategy_mv_refresh_scheduler_tick_error", error=str(exc))
            await asyncio.sleep(self.poll_interval_seconds)

    async def run_once(self) -> DispatchOutcome:
        """Run exactly one tick, outside the loop too, so a test or an operator can drive one by hand."""
        async with get_scheduler_session() as session:
            return await dispatch_lane(
                session,
                STRATEGY_MV_REFRESH_DEFINITION_NAME,
                requested_by="strategy-mv-refresh-scheduler",
            )


strategy_mv_refresh_scheduler = StrategyMvRefreshScheduler()


@jobs_bp.before_server_start
async def _start_strategy_mv_refresh_scheduler(_app: Sanic[Any, Any], _loop: object) -> None:
    """Start the periodic driver when this blueprint's app boots. No app.py change needed:
    Sanic invokes a blueprint's own listeners exactly as it invokes the app's."""
    await strategy_mv_refresh_scheduler.start()


@jobs_bp.after_server_stop
async def _stop_strategy_mv_refresh_scheduler(_app: Sanic[Any, Any], _loop: object) -> None:
    """Stop the periodic driver cleanly on shutdown."""
    await strategy_mv_refresh_scheduler.stop()
