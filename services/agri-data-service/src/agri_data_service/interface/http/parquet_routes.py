"""The `/api/v1/parquet` blueprint: four bounded reads over the day-partitioned Parquet warehouse.

Layer L4. Every route answers all four warehouse states as HTTP 200 carrying `state`; a non-2xx is a
transport or serving fault and never a statement about content. See `AGENTS.md` in this directory.
"""

from __future__ import annotations

import asyncio
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import duckdb
import structlog
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.

from agri_data_service.config import settings
from agri_data_service.interface.http import faults
from agri_data_service.interface.http.coverage import CoverageCache, registered_census_lanes
from agri_data_service.interface.http.duckdb_session import SERVING_MAX_CONCURRENT_READS, open_serving_session
from agri_data_service.interface.http.faults import HTTP_BAD_REQUEST, ServingRefusalError
from agri_data_service.interface.http.request_params import (
    RequestError,
    parse_calendar_day,
    parse_read_scope,
    parse_window,
)
from agri_data_service.interface.http.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.interface.http.warehouse_reader import DuckDbRowReader, ObjectStoreListing
from agri_data_service.interface.http.wire import (
    PARAM_AS_OF,
    PARAM_BBOX,
    PARAM_DAY,
    PARAM_FIRST_DAY,
    PARAM_KIND,
    PARAM_LAST_DAY,
    PARAM_LAYER,
    PARAM_ZOOM,
    ROUTE_COVERAGE,
    ROUTE_DAY,
    ROUTE_RELEASE,
    ROUTE_WINDOW,
    render_window,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from agri_data_service.interface.http.request_params import ReadScope
    from agri_data_service.interface.http.warehouse_reader import PartitionRowReader, WarehouseListing

logger = structlog.get_logger()

parquet_bp = Blueprint("parquet", url_prefix="/parquet")

HTTP_OK: Final = 200

#: Under the client's own 15 s row budget and 20 s coverage budget, so the server's error wins the
#: race and the caller learns which read failed rather than only that something timed out.
ROW_READ_TIMEOUT_SECONDS: Final = 14.0
COVERAGE_TIMEOUT_SECONDS: Final = 19.0

#: How long a read waits for a serving slot before it is refused. Short on purpose: a queue that
#: waits out the client's own budget converts contention into timeouts, and the map's `retry: 1`
#: then puts a second copy of the same read behind the first.
SLOT_WAIT_SECONDS: Final = 2.0

_coverage_cache = CoverageCache()

#: ONE bounded pool for every warehouse read. `asyncio.to_thread` dispatches to the default executor,
#: which allows `min(32, cpu + 4)` threads, and `open_row_reader` opens a session per request -- so
#: nothing bounded how many memory-capped DuckDB sessions could be alive at once, and the process
#: ceiling was 16-32 x SERVING_MEMORY_LIMIT rather than the one-query ceiling the guard implies.
#: The pool is the hard bound: at most this many `work()` calls, so at most this many sessions.
_read_pool: Final = ThreadPoolExecutor(max_workers=SERVING_MAX_CONCURRENT_READS, thread_name_prefix="parquet-read")

#: Admission control in front of the pool, one semaphore per event loop. Its job is not the ceiling
#: (the pool is) but the CLEAN FAULT: a read that cannot get a slot is refused in `SLOT_WAIT_SECONDS`
#: rather than queued behind work whose own timeout has not run out yet.
_read_slots: Final[weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def _read_slot() -> asyncio.Semaphore:
    """Return the running loop's admission semaphore, built on first use."""
    loop = asyncio.get_running_loop()
    slot = _read_slots.get(loop)
    if slot is None:
        slot = asyncio.Semaphore(SERVING_MAX_CONCURRENT_READS)
        _read_slots[loop] = slot
    return slot


class _ListingHolder:
    """One boto3-backed listing per process; building a client per request costs more than the read."""

    def __init__(self) -> None:
        self._held: ObjectStoreListing | None = None

    def get(self) -> ObjectStoreListing:
        """Return the held listing, building it from settings on first use."""
        if self._held is None:
            credentials = settings.require_object_store()
            self._held = ObjectStoreListing(
                backend=BotoObjectStoreBackend.from_credentials(credentials),
                prefix=settings.object_store_prefix,
            )
        return self._held


_listings = _ListingHolder()


def open_listing() -> WarehouseListing:
    """Return the process's object-store listing. Patched in tests to answer without a network."""
    return _listings.get()


@contextmanager
def open_row_reader() -> Iterator[PartitionRowReader]:
    """Open a memory-capped DuckDB session for the life of ONE read, then close it.

    Per request rather than pooled: the ceiling and the disabled spill are the point, and a shared
    connection would let one oversized read spend a budget the next one is relying on.
    """
    session = open_serving_session(settings.require_object_store(), prefix=settings.object_store_prefix)
    try:
        yield DuckDbRowReader(session=session)
    finally:
        session.close()


@parquet_bp.get(f"/{ROUTE_DAY}")
async def read_day(request: Request) -> HTTPResponse:
    """One layer's rows for one day at one tier, or the state that says why there are none."""
    try:
        scope = _read_scope(request)
        day = parse_calendar_day(request.args.get(PARAM_DAY), PARAM_DAY)
    except RequestError as exc:
        return _refused(exc)

    def work() -> dict[str, object]:
        with open_row_reader() as reader:
            return resolve_day(open_listing(), reader, scope=scope, day=day).to_wire()

    return await _answer(work, ROW_READ_TIMEOUT_SECONDS, route=ROUTE_DAY)


@parquet_bp.get(f"/{ROUTE_WINDOW}")
async def read_window(request: Request) -> HTTPResponse:
    """Every day of a closed range, ascending; a gap day is stated as `day_not_written`, never omitted."""
    try:
        scope = _read_scope(request)
        first_day, last_day = parse_window(request.args.get(PARAM_FIRST_DAY), request.args.get(PARAM_LAST_DAY))
    except RequestError as exc:
        return _refused(exc)

    def work() -> dict[str, object]:
        with open_row_reader() as reader:
            days = resolve_window(open_listing(), reader, scope=scope, first_day=first_day, last_day=last_day)
            return render_window(days)

    return await _answer(work, ROW_READ_TIMEOUT_SECONDS, route=ROUTE_WINDOW)


@parquet_bp.get(f"/{ROUTE_RELEASE}")
async def read_release(request: Request) -> HTTPResponse:
    """The newest release at or before a day, reported at the release's OWN day and never at the day asked for."""
    try:
        scope = _read_scope(request)
        as_of = parse_calendar_day(request.args.get(PARAM_AS_OF), PARAM_AS_OF)
    except RequestError as exc:
        return _refused(exc)

    def work() -> dict[str, object]:
        with open_row_reader() as reader:
            return resolve_release(open_listing(), reader, scope=scope, as_of=as_of).to_wire()

    return await _answer(work, ROW_READ_TIMEOUT_SECONDS, route=ROUTE_RELEASE)


@parquet_bp.get(f"/{ROUTE_COVERAGE}")
async def read_coverage(_request: Request) -> HTTPResponse:
    """The whole warehouse's census: one entry per lane, no viewport and no tier."""

    def work() -> dict[str, object]:
        census = _coverage_cache.get(open_listing(), lanes=registered_census_lanes(), now=datetime.now(UTC))
        return census.to_wire()

    return await _answer(work, COVERAGE_TIMEOUT_SECONDS, route=ROUTE_COVERAGE)


def _read_scope(request: Request) -> ReadScope:
    """Validate the four parameters every row read carries."""
    return parse_read_scope(
        layer=request.args.get(PARAM_LAYER),
        kind=request.args.get(PARAM_KIND),
        zoom=request.args.get(PARAM_ZOOM),
        bbox=request.args.get(PARAM_BBOX),
    )


async def _answer(work: Callable[[], dict[str, object]], timeout_seconds: float, *, route: str) -> HTTPResponse:
    """Run one bounded warehouse read on the bounded pool and map EVERY fault onto an honest status.

    Nothing may leave here as a generic 500: `upstream-fault.ts` classifies `status >= 500` as
    transient, so the map would retry the identical read against a process already at its ceiling.
    """
    slot = _read_slot()
    try:
        async with asyncio.timeout(SLOT_WAIT_SECONDS):
            await slot.acquire()
    except TimeoutError:
        return _refusal(faults.serving_at_capacity(route=route, concurrent_reads=SERVING_MAX_CONCURRENT_READS), route)
    try:
        async with asyncio.timeout(timeout_seconds):
            payload = await asyncio.get_running_loop().run_in_executor(_read_pool, work)
    except RequestError as exc:
        return _refused(exc)
    except Exception as exc:
        return _refusal(_as_refusal(exc, route=route, timeout_seconds=timeout_seconds), route)
    finally:
        slot.release()
    return json(payload, status=HTTP_OK)


def _as_refusal(exc: Exception, *, route: str, timeout_seconds: float) -> ServingRefusalError:
    """Map every fault a warehouse read can raise onto a refusal the client's `retry: 1` cannot amplify."""
    if isinstance(exc, ServingRefusalError):
        return exc
    if isinstance(exc, TimeoutError):
        return faults.read_timed_out(route=route, timeout_seconds=timeout_seconds)
    if isinstance(exc, duckdb.Error):
        # `OutOfMemoryException` is the guard doing its job -- the read did not fit the ceiling. It is
        # not a 500 and not retryable: the same read costs the same, against a process already loaded.
        logger.warning("parquet_read_over_budget", route=route, fault=type(exc).__name__)
        return faults.read_over_budget(route=route)
    # Everything unforeseen -- a botocore fault, a listing budget, a row this plane cannot render.
    # Logged with a traceback and answered as a refusal, because an unclassified 500 is retried.
    logger.exception("parquet_read_failed", route=route, fault=type(exc).__name__)
    return faults.serving_fault(route=route, fault=type(exc).__name__)


def _refusal(exc: ServingRefusalError, route: str) -> HTTPResponse:
    """Render one serving refusal. Never one of the four states: this is about SERVING, not content."""
    logger.warning("parquet_read_refused", route=route, code=exc.code, status=exc.status)
    return json(exc.to_wire(), status=exc.status)


def _refused(exc: RequestError) -> HTTPResponse:
    """Render a rejected request. Never one of the four states: the caller, not the warehouse, was wrong."""
    return json(
        ServingRefusalError("invalid_request", str(exc), status=HTTP_BAD_REQUEST).to_wire(),
        status=HTTP_BAD_REQUEST,
    )
