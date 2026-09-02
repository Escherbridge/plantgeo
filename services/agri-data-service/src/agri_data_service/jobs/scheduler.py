"""The manual HTTP trigger route for durable job lanes.

The route is thin: it opens a session and hands a `lane_id` to `jobs/dispatch.py`, which
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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sanic import Blueprint, Request, json
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
    LaneHandlerMissingError,
    UnknownDispatchableLaneError,
    dispatch_lane,
)

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
