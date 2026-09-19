"""The `/api/v1/ml` blueprint: four bounded reads, each answered or refused with a stable code.

Layer L4. Which refusals are a 200 carrying `error` and which are a transport status, and why a
read takes a serving slot before it opens a DuckDB session, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog
from pydantic import BaseModel
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002  # sanic-ext evaluates handler annotations at runtime

from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSessionError, open_session
from plantgeo_ml_service.pipeline.object_store import (
    BotoObjectStoreBackend,
    ReadOnlyObjectStore,
    ReadOnlyObjectStoreView,
)
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.analog_reads import read_analogs
from plantgeo_ml_service.planes.artifact_reads import list_artifacts
from plantgeo_ml_service.planes.fire_risk_reads import read_fire_risk_point
from plantgeo_ml_service.planes.forecast_reads import read_forecast_summary
from plantgeo_ml_service.planes.query_models import (
    AnalogsQuery,
    ArtifactKindPath,
    FireRiskQuery,
    ForecastSummaryQuery,
    ValidationError,
    field_errors,
)
from plantgeo_ml_service.planes.wire import (
    BASE_PATH,
    ROUTE_ANALOGS,
    ROUTE_ARTIFACTS,
    ROUTE_FIRE_RISK,
    ROUTE_FORECAST_SUMMARY,
    ClaimProvenance,
    answer,
    unresolved_claim,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from concurrent.futures import Future

    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession

logger = structlog.get_logger()

machine_learning_bp = Blueprint("machine_learning", url_prefix=BASE_PATH)

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503

#: A read finishes inside the web client's own budget, or it is a fault rather than a slow answer.
ROW_READ_TIMEOUT_SECONDS: Final = 14.0

#: How many reads may hold a memory-capped DuckDB session at once. Refused rather than queued: each
#: slot holds its own ceiling, and a queue would let the process exceed the sum of them.
#:
#: This is the WORKER count of the pool that runs the reads, not a counter beside it. A semaphore
#: released when the awaiting coroutine unwinds bounds how many callers are waiting, which under a
#: timeout is not how many DuckDB sessions are open; the pool bounds the work itself.
MAX_CONCURRENT_READS: Final = 4

#: Codes that state something about CONTENT. Answered 200 with `error` set, because "this day was
#: never written" is a fact about the warehouse and not a transport failure, and a client retrying
#: a 5xx would re-ask a question whose answer cannot change until a run writes something.
CONTENT_REFUSAL_CODES: Final[frozenset[str]] = frozenset(
    {
        refusals.PARTITION_DAY_NOT_WRITTEN,
        refusals.PARTITION_DAY_GOVERNED_ABSENCE,
        refusals.AVAILABILITY_UNPUBLISHED,
        refusals.AVAILABILITY_DAY_NOT_COVERED,
        refusals.AVAILABILITY_NO_PUBLISHED_DAY,
        refusals.FORECAST_WINDOW_UNWRITTEN,
        refusals.CELL_NOT_COVERED,
        refusals.CELL_SERIES_ABSENT,
    }
)

#: Everything else is about SERVING, and carries a transport status. An unmapped code is logged and
#: answered 409, never 500: a 500 is what a client retries against a process already at its ceiling.
REFUSAL_HTTP_STATUS: Final[Mapping[str, int]] = {
    refusals.PARTITION_DAY_INCOMPLETE: HTTP_SERVICE_UNAVAILABLE,
    refusals.PARTITION_DAY_CONFLICT: HTTP_CONFLICT,
    refusals.AVAILABILITY_MALFORMED: HTTP_SERVICE_UNAVAILABLE,
    refusals.ARTIFACT_KIND_UNKNOWN: HTTP_BAD_REQUEST,
    refusals.ARTIFACT_UNREADABLE: HTTP_SERVICE_UNAVAILABLE,
    refusals.LANE_UNKNOWN: HTTP_BAD_REQUEST,
    refusals.LANE_NOT_FORECAST: HTTP_BAD_REQUEST,
    refusals.READ_OVER_BUDGET: HTTP_CONFLICT,
    refusals.READ_TIMED_OUT: HTTP_SERVICE_UNAVAILABLE,
    refusals.SERVING_AT_CAPACITY: HTTP_CONFLICT,
    refusals.SERVING_FAULT: HTTP_CONFLICT,
    refusals.OBJECT_STORE_UNCONFIGURED: HTTP_SERVICE_UNAVAILABLE,
    refusals.INVALID_REQUEST: HTTP_BAD_REQUEST,
}


@dataclass(frozen=True, slots=True)
class ServingContext:
    """One read's bucket and, when the read touches a partition, its bounded DuckDB session."""

    store: ReadOnlyObjectStore
    #: `None` for a read that touches no partition, so an artifact listing never opens a session.
    session: DuckDbSession | None

    def require_session(self) -> DuckDbSession:
        """Return the session, refusing loudly rather than reading a partition without one."""
        if self.session is None:
            raise refusals.serving_fault(operation="serving_context", fault="partition read with no open session")
        return self.session


class _StoreHolder:
    """One read-only facade per process; building an S3 client per request costs more than the read.

    The facade, not an `ObjectStore`: a serving plane that holds a writer is one edit away from
    being a writer, and nothing behind `/api/v1/ml` has a reason to put or delete an object.
    """

    def __init__(self) -> None:
        self._held: ReadOnlyObjectStoreView | None = None

    def get(self) -> ReadOnlyObjectStoreView:
        """Return the held facade, building it from settings on first use."""
        if self._held is None:
            from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - settings resolve per process

            settings = get_settings()
            credentials = settings.require_object_store()
            self._held = ReadOnlyObjectStoreView(
                backend=BotoObjectStoreBackend.from_credentials(credentials),
                prefix=settings.object_store_prefix,
            )
        return self._held

    def clear(self) -> None:
        """Drop the held facade, so a test or a credential change builds the next one."""
        self._held = None


class ServingPool:
    """The bounded pool that RUNS the reads, and therefore the ceiling on how many are in flight.

    A claim is taken before the work is submitted and released by the worker itself, so a caller
    that walked away on a timeout does not hand its slot back while its query is still running.
    """

    def __init__(self, max_workers: int) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ml-serving-read")
        self._lock = threading.Lock()
        self._in_flight = 0
        self._capacity = max_workers

    @property
    def capacity(self) -> int:
        """Return how many reads may run at once."""
        return self._capacity

    @property
    def in_flight(self) -> int:
        """Return how many reads currently hold a slot."""
        with self._lock:
            return self._in_flight

    def claim(self) -> bool:
        """Take one slot, or report that every one of them is busy."""
        with self._lock:
            if self._in_flight >= self._capacity:
                return False
            self._in_flight += 1
            return True

    def submit[ResultT](self, work: Callable[[], ResultT]) -> Future[ResultT]:
        """Run one CLAIMED piece of work on the pool, releasing its slot when the work ends."""

        def run_and_release() -> ResultT:
            try:
                return work()
            finally:
                with self._lock:
                    self._in_flight -= 1

        return self._executor.submit(run_and_release)

    def shutdown(self) -> None:
        """Release the pool's threads. For a test that built its own; a process has exactly one."""
        self._executor.shutdown(wait=False, cancel_futures=True)


class ReadCancellation:
    """The handle a timed-out read reaches its running DuckDB session through.

    Binding is racy by nature -- the worker may not have opened its session yet when the timeout
    fires -- so the cancelled flag is remembered and applied to whatever binds afterwards.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: DuckDbSession | None = None
        self._cancelled = False

    def bind(self, session: DuckDbSession) -> None:
        """Register the session this read runs on, interrupting it at once if the read already timed out."""
        with self._lock:
            self._session = session
            cancelled = self._cancelled
        if cancelled:
            session.interrupt()

    def release(self) -> None:
        """Forget the session, so nothing interrupts a connection this read no longer owns."""
        with self._lock:
            self._session = None

    def cancel(self) -> None:
        """Stop this read's query now, or as soon as it has one to stop."""
        with self._lock:
            self._cancelled = True
            session = self._session
        if session is not None:
            session.interrupt()


_stores = _StoreHolder()
_serving_pool: ServingPool | None = None


def open_store() -> ReadOnlyObjectStore:
    """Return the process's read-only bucket facade. Patched in tests to answer without a network."""
    return _stores.get()


def open_serving_session() -> DuckDbSession:
    """Open one bounded, SEALED session against the bucket. Patched in tests to skip the network."""
    from plantgeo_ml_service.config import get_settings  # noqa: PLC0415 - see `_StoreHolder.get`

    settings = get_settings()
    return open_session(
        settings.require_object_store(), settings=settings, prefix=settings.object_store_prefix, serving=True
    )


@machine_learning_bp.get(f"/{ROUTE_FIRE_RISK}")
async def read_fire_risk(request: Request) -> HTTPResponse:
    """One cell's fire-risk answer for one valid day, or the reason there is none."""
    try:
        query = _parsed(FireRiskQuery, request)
    except (ValidationError, refusals.MachineLearningRefusalError) as error:
        return _rejected(error, ROUTE_FIRE_RISK)

    def work(context: ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
        point = read_fire_risk_point(
            context.store, context.require_session(), longitude=query.lon, latitude=query.lat, day=query.day
        )
        return (point.to_wire(), point.claim())

    return await bounded_read(work, route=ROUTE_FIRE_RISK)


@machine_learning_bp.get(f"/{ROUTE_ANALOGS}")
async def read_analog_ensemble(request: Request) -> HTTPResponse:
    """One signal cell's analog forecast issued from one origin day, p10/p50/p90 by horizon."""
    try:
        query = _parsed(AnalogsQuery, request)
    except (ValidationError, refusals.MachineLearningRefusalError) as error:
        return _rejected(error, ROUTE_ANALOGS)

    def work(context: ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
        found = read_analogs(context.store, context.require_session(), cell_id=query.cell_id, origin=query.origin)
        return (found.to_wire(), found.claim)

    return await bounded_read(work, route=ROUTE_ANALOGS)


@machine_learning_bp.get(f"/{ROUTE_FORECAST_SUMMARY}")
async def read_forecast(request: Request) -> HTTPResponse:
    """One lane's rows for one point and one valid day, on whichever path that lane publishes on."""
    try:
        query = _parsed(ForecastSummaryQuery, request)
    except (ValidationError, refusals.MachineLearningRefusalError) as error:
        return _rejected(error, ROUTE_FORECAST_SUMMARY)

    def work(context: ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
        summary = read_forecast_summary(
            context.store,
            context.require_session(),
            layer=query.layer,
            longitude=query.lon,
            latitude=query.lat,
            day=query.day,
        )
        return (summary.to_wire(), summary.claim)

    return await bounded_read(work, route=ROUTE_FORECAST_SUMMARY)


@machine_learning_bp.get(f"/{ROUTE_ARTIFACTS}/<kind:str>")
async def read_artifacts(_request: Request, kind: str) -> HTTPResponse:
    """Every artifact one model kind has published, bounded, with digests and training windows."""
    try:
        path = ArtifactKindPath.model_validate({"kind": kind})
    except ValidationError as error:
        return _invalid(error)

    def work(context: ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
        listing = list_artifacts(context.store, model_kind=path.kind)
        return (listing.to_wire(), listing.claim)

    return await bounded_read(work, route=ROUTE_ARTIFACTS, needs_session=False)


async def bounded_read(
    work: Callable[[ServingContext], tuple[dict[str, object], ClaimProvenance]],
    *,
    route: str,
    needs_session: bool = True,
) -> HTTPResponse:
    """Run one bounded read on a serving slot and map every outcome onto an honest body.

    The timeout does three things in order, and all three matter: it stops waiting, it INTERRUPTS
    the DuckDB query the worker is still running, and only then answers `read_timed_out`. Without
    the middle step the answer is a lie by omission -- the read is not over, it is unwatched, and
    its slot stays held until whatever it was doing finishes on its own.

    Nothing leaves here as a generic 500: an unclassified 500 is exactly what a client retries,
    against a process that would fail the identical read the identical way.
    """
    pool = serving_pool()
    if not pool.claim():
        return _refused(refusals.serving_at_capacity(operation=route, concurrent_reads=pool.capacity), route)
    cancellation = ReadCancellation()
    running = pool.submit(lambda: _run(work, needs_session, cancellation))
    try:
        async with asyncio.timeout(ROW_READ_TIMEOUT_SECONDS):
            payload, claim = await asyncio.wrap_future(running)
    except refusals.MachineLearningRefusalError as error:
        return _refused(error, route)
    except TimeoutError:
        cancellation.cancel()
        return _refused(refusals.read_timed_out(operation=route, timeout_seconds=ROW_READ_TIMEOUT_SECONDS), route)
    except DuckDbSessionError as error:
        return _refused(refusals.serving_fault(operation=route, fault=type(error).__name__), route)
    except Exception as error:  # botocore, pyarrow and duckdb each raise their own families
        logger.exception("machine_learning_read_failed", route=route, fault=type(error).__name__)
        return _refused(refusals.serving_fault(operation=route, fault=type(error).__name__), route)
    return json(answer(payload, claim=claim), status=HTTP_OK)


def _run(
    work: Callable[[ServingContext], tuple[dict[str, object], ClaimProvenance]],
    needs_session: bool,
    cancellation: ReadCancellation,
) -> tuple[dict[str, object], ClaimProvenance]:
    """Run one read with a session whose lifetime is exactly this read, on a pool worker."""
    try:
        store = open_store()
        session = open_serving_session() if needs_session else None
    except ValueError as error:
        # The names of the variables still missing are an operator fact, logged rather than served.
        logger.error("machine_learning_object_store_unconfigured", detail=str(error))
        raise refusals.object_store_unconfigured() from error
    if session is None:
        return work(ServingContext(store=store, session=None))
    with session:
        cancellation.bind(session)
        try:
            return work(ServingContext(store=store, session=session))
        finally:
            cancellation.release()


def serving_pool() -> ServingPool:
    """Return the process's serving pool, built on first use."""
    global _serving_pool  # noqa: PLW0603 - one pool per process, built lazily so import costs no threads
    if _serving_pool is None:
        _serving_pool = ServingPool(MAX_CONCURRENT_READS)
    return _serving_pool


def _parsed[QueryModelT: BaseModel](model: type[QueryModelT], request: Request) -> QueryModelT:
    """Validate one request's query string against its model, refusing a repeated parameter first.

    A repeated parameter is refused rather than resolved: taking the first or the last value picks
    a side of a question the caller asked twice, and both sides look like a successful read.
    """
    collected: dict[str, str] = {}
    for name, values in request.args.items():
        if len(values) != 1:
            raise refusals.invalid_request(
                detail=f"query parameter {name!r} was given {len(values)} times; it is read exactly once"
            )
        collected[name] = values[0]
    return model.model_validate(collected)


def _rejected(error: ValidationError | refusals.MachineLearningRefusalError, route: str) -> HTTPResponse:
    """Render whichever way a request was rejected, without losing which one it was."""
    if isinstance(error, ValidationError):
        return _invalid(error)
    return _refused(error, route)


def _invalid(error: ValidationError) -> HTTPResponse:
    """Render a rejected request: the caller, not the warehouse, was wrong."""
    body = {
        **unresolved_claim().to_wire(),
        "error": {
            "code": refusals.INVALID_REQUEST,
            "message": "one or more query parameters were rejected",
            "fields": field_errors(error),
        },
    }
    return json(body, status=HTTP_BAD_REQUEST)


def _refused(error: refusals.MachineLearningRefusalError, route: str) -> HTTPResponse:
    """Render one refusal at the status its code earns, with the claim block still attached."""
    if error.code in CONTENT_REFUSAL_CODES:
        status = HTTP_OK
    else:
        status = REFUSAL_HTTP_STATUS.get(error.code, HTTP_CONFLICT)
        if error.code not in REFUSAL_HTTP_STATUS:
            logger.error("machine_learning_refusal_status_unmapped", route=route, code=error.code)
    logger.warning("machine_learning_read_refused", route=route, code=error.code, status=status)
    return json({**unresolved_claim().to_wire(), **error.to_wire()}, status=status)


__all__ = [
    "CONTENT_REFUSAL_CODES",
    "MAX_CONCURRENT_READS",
    "REFUSAL_HTTP_STATUS",
    "ROW_READ_TIMEOUT_SECONDS",
    "ReadCancellation",
    "ServingContext",
    "ServingPool",
    "bounded_read",
    "machine_learning_bp",
    "open_serving_session",
    "open_store",
    "read_analog_ensemble",
    "read_artifacts",
    "read_fire_risk",
    "read_forecast",
    "serving_pool",
]
