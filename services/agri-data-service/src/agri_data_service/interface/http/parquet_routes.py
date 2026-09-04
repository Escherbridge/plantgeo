"""The `/api/v1/parquet` blueprint: four bounded reads over the day-partitioned Parquet warehouse.

Layer L4. Every route answers all four warehouse states as HTTP 200 carrying `state`; a non-2xx is a
transport or serving fault and never a statement about content. See `AGENTS.md` in this directory.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

import duckdb
import structlog
from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.

from agri_data_service.config import settings
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.availability_coverage import (
    AvailabilityCoverageReaderHolder,
    SnapshotForwardAvailability,
    merge_direct_lane_rows,
    resolve_availability_lanes,
)
from agri_data_service.parquet_ops.coverage import CoverageCache, registered_census_lanes
from agri_data_service.parquet_ops.duckdb_session import run_serving_read
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import (
    RequestError,
    parse_calendar_day,
    parse_read_scope,
    parse_window,
)
from agri_data_service.parquet_ops.serving import resolve_day, resolve_release, resolve_window
from agri_data_service.parquet_ops.snapshot_products import (
    PRODUCT_BY_LAYER,
    ObjectStoreSnapshotStore,
    SnapshotCoverageCache,
    load_snapshot_scope_evidence,
    resolve_snapshot_evidence_day,
    resolve_snapshot_evidence_window,
    serves_from_snapshot,
)
from agri_data_service.parquet_ops.warehouse_reader import DuckDbRowReader, ObjectStoreListing
from agri_data_service.parquet_ops.wire import (
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
    WarehouseCoverage,
    render_window,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from agri_data_service.parquet_ops.availability_coverage import AvailabilityCoverageReader
    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import PartitionRowReader, WarehouseListing
    from agri_data_service.parquet_ops.wire import LaneCoverage

logger = structlog.get_logger()

parquet_bp = Blueprint("parquet", url_prefix="/parquet")

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503

# The core raises only `(code, message)`. This table is the HTTP adapter's complete transport policy.
_REFUSAL_HTTP_STATUS: Final[dict[str, int]] = {
    "partition_day_conflict": HTTP_CONFLICT,
    "partition_day_incomplete": HTTP_SERVICE_UNAVAILABLE,
    "bbox_unsupported": HTTP_CONFLICT,
    "bbox_columns_absent": HTTP_SERVICE_UNAVAILABLE,
    "absence_marker_unreadable": HTTP_SERVICE_UNAVAILABLE,
    "absence_marker_undecodable": HTTP_SERVICE_UNAVAILABLE,
    "read_timed_out": HTTP_SERVICE_UNAVAILABLE,
    "read_over_budget": HTTP_CONFLICT,
    "serving_at_capacity": HTTP_CONFLICT,
    "serving_fault": HTTP_CONFLICT,
    "object_store_session_unavailable": HTTP_SERVICE_UNAVAILABLE,
    "serving_extension_unavailable": HTTP_SERVICE_UNAVAILABLE,
    "census_budget_exhausted": HTTP_CONFLICT,
    "snapshot_unpublished": HTTP_SERVICE_UNAVAILABLE,
    "snapshot_schema_mismatch": HTTP_SERVICE_UNAVAILABLE,
    "snapshot_manifest_conflict": HTTP_CONFLICT,
}

#: Row reads finish inside the client's 15 s budget. Coverage retains a 29 s shielded build budget:
#: the client stops waiting after 8 s, reports explicit incomplete coverage, and retries the cache.
ROW_READ_TIMEOUT_SECONDS: Final = 14.0
COVERAGE_TIMEOUT_SECONDS: Final = 29.0

_coverage_cache = CoverageCache()
_snapshot_coverage_cache = SnapshotCoverageCache()
_availability_readers = AvailabilityCoverageReaderHolder()


class _CoveragePayloadCache:
    """One async refresh gate acquired before any caller can take a serving slot."""

    def __init__(self, ttl_seconds: int = 120) -> None:
        self._ttl_seconds = ttl_seconds
        self._held: tuple[datetime, dict[str, object]] | None = None
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[dict[str, object]] | None = None

    async def get(
        self,
        *,
        now: datetime,
        build: Callable[[], Awaitable[dict[str, object]]],
    ) -> dict[str, object]:
        held = self._fresh(now)
        if held is not None:
            return held
        async with self._lock:
            held = self._fresh(now)
            if held is not None:
                return held
            if self._inflight is None:
                self._inflight = asyncio.create_task(self._refresh(build))
            inflight = self._inflight
        return await asyncio.shield(inflight)

    def clear(self) -> None:
        self._held = None

    def _fresh(self, now: datetime) -> dict[str, object] | None:
        held = self._held
        if held is None or (now - held[0]).total_seconds() >= self._ttl_seconds:
            return None
        return held[1]

    async def _refresh(
        self,
        build: Callable[[], Awaitable[dict[str, object]]],
    ) -> dict[str, object]:
        try:
            built = await build()
            self._held = (datetime.now(UTC), built)
            return built
        finally:
            self._inflight = None


_coverage_payload_cache = _CoveragePayloadCache()


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


def open_snapshot_store() -> ObjectStoreSnapshotStore:
    """Reuse the process-held backend while keeping snapshot keys outside the mutable layout parser."""
    listing = _listings.get()
    return ObjectStoreSnapshotStore(backend=listing.backend, prefix=listing.prefix)


def open_availability_reader() -> AvailabilityCoverageReader:
    """Return the process's availability reader. Patched in tests to answer without a network."""
    return _availability_readers.get(settings)


@parquet_bp.get(f"/{ROUTE_DAY}")
async def read_day(request: Request) -> HTTPResponse:
    """One layer's rows for one day at one tier, or the state that says why there are none."""
    try:
        scope = _read_scope(request)
        day = parse_calendar_day(request.args.get(PARAM_DAY), PARAM_DAY)
    except RequestError as exc:
        return _refused(exc)

    # DAY-AWARE, not layer-aware. Six climate products are frozen only BELOW their forward first
    # day; a request at or above it is answered by the live lane like any other layer's day.
    if serves_from_snapshot(scope.layer, day):
        return await _answer(
            lambda: _run_snapshot_day(scope=scope, day=day),
            ROW_READ_TIMEOUT_SECONDS,
            route=ROUTE_DAY,
        )

    def work(reader: PartitionRowReader) -> dict[str, object]:
        return resolve_day(open_listing(), reader, scope=scope, day=day).to_wire()

    return await _answer(lambda: _run_row_read(work, route=ROUTE_DAY), ROW_READ_TIMEOUT_SECONDS, route=ROUTE_DAY)


@parquet_bp.get(f"/{ROUTE_WINDOW}")
async def read_window(request: Request) -> HTTPResponse:
    """Every day of a closed range, ascending; a gap day is stated as `day_not_written`, never omitted."""
    try:
        scope = _read_scope(request)
        first_day, last_day = parse_window(request.args.get(PARAM_FIRST_DAY), request.args.get(PARAM_LAST_DAY))
    except RequestError as exc:
        return _refused(exc)

    # Routed on the window's FIRST day: a range that starts in the closed snapshot is answered by
    # the snapshot path, which itself reads each day at or above the boundary from the live lane.
    if serves_from_snapshot(scope.layer, first_day):
        return await _answer(
            lambda: _run_snapshot_window(scope=scope, first_day=first_day, last_day=last_day),
            ROW_READ_TIMEOUT_SECONDS,
            route=ROUTE_WINDOW,
        )

    def work(reader: PartitionRowReader) -> dict[str, object]:
        days = resolve_window(open_listing(), reader, scope=scope, first_day=first_day, last_day=last_day)
        return render_window(days)

    return await _answer(
        lambda: _run_row_read(work, route=ROUTE_WINDOW),
        ROW_READ_TIMEOUT_SECONDS,
        route=ROUTE_WINDOW,
    )


@parquet_bp.get(f"/{ROUTE_RELEASE}")
async def read_release(request: Request) -> HTTPResponse:
    """The newest release at or before a day, reported at the release's OWN day and never at the day asked for."""
    try:
        scope = _read_scope(request)
        as_of = parse_calendar_day(request.args.get(PARAM_AS_OF), PARAM_AS_OF)
    except RequestError as exc:
        return _refused(exc)

    # Release carry is refused only for the FROZEN half: above the boundary the live lane owns the
    # days, and a live lane resolves release carry exactly as every other lane does.
    if serves_from_snapshot(scope.layer, as_of):
        return _refusal(
            faults.snapshot_unpublished(
                layer=scope.layer,
                snapshot_id=PRODUCT_BY_LAYER[scope.layer].snapshot_id,
                detail="daily-series snapshots do not define release carry; use the exact day route",
            ),
            ROUTE_RELEASE,
        )

    def work(reader: PartitionRowReader) -> dict[str, object]:
        return resolve_release(open_listing(), reader, scope=scope, as_of=as_of).to_wire()

    return await _answer(
        lambda: _run_row_read(work, route=ROUTE_RELEASE),
        ROW_READ_TIMEOUT_SECONDS,
        route=ROUTE_RELEASE,
    )


@parquet_bp.get(f"/{ROUTE_COVERAGE}")
async def read_coverage(_request: Request) -> HTTPResponse:
    """The slider census: one entry per physical lane and rung, with no viewport."""

    return await _answer(
        _run_coverage_read,
        COVERAGE_TIMEOUT_SECONDS,
        route=ROUTE_COVERAGE,
    )


def _read_scope(request: Request) -> ReadScope:
    """Validate the four parameters every row read carries."""
    return parse_read_scope(
        layer=request.args.get(PARAM_LAYER),
        kind=request.args.get(PARAM_KIND),
        zoom=request.args.get(PARAM_ZOOM),
        bbox=request.args.get(PARAM_BBOX),
    )


async def _run_row_read(
    work: Callable[[PartitionRowReader], dict[str, object]],
    *,
    route: str,
) -> dict[str, object]:
    """Run one row operation through the core's admitted session runner."""
    credentials = settings.require_object_store()
    return await run_serving_read(
        credentials,
        lambda session: work(DuckDbRowReader(session=session)),
        prefix=settings.object_store_prefix,
        operation=route,
    )


async def _run_snapshot_day(*, scope: ReadScope, day: date) -> dict[str, object]:
    """Resolve immutable evidence before admitting the exact-day DuckDB read."""
    store = open_snapshot_store()
    evidence = await asyncio.to_thread(load_snapshot_scope_evidence, store, scope)
    credentials = settings.require_object_store()
    return await run_serving_read(
        credentials,
        lambda session: resolve_snapshot_evidence_day(
            store,
            session,
            evidence=evidence,
            scope=scope,
            day=day,
        ).to_wire(),
        prefix=settings.object_store_prefix,
        operation=ROUTE_DAY,
    )


async def _run_snapshot_window(*, scope: ReadScope, first_day: date, last_day: date) -> dict[str, object]:
    """Resolve immutable evidence before admitting the exact-window DuckDB read."""
    store = open_snapshot_store()
    evidence = await asyncio.to_thread(load_snapshot_scope_evidence, store, scope)
    credentials = settings.require_object_store()
    return await run_serving_read(
        credentials,
        lambda session: render_window(
            resolve_snapshot_evidence_window(
                store,
                session,
                evidence=evidence,
                scope=scope,
                first_day=first_day,
                last_day=last_day,
            )
        ),
        prefix=settings.object_store_prefix,
        operation=ROUTE_WINDOW,
    )


async def _run_coverage_read() -> dict[str, object]:
    """Wait for the one cold census before acquiring any DuckDB serving slot."""
    generated_at = datetime.now(UTC)
    return await _coverage_payload_cache.get(
        now=generated_at,
        build=lambda: _build_coverage_payload(generated_at),
    )


async def _build_coverage_payload(generated_at: datetime) -> dict[str, object]:
    """Build one merged metadata-only coverage answer outside the DuckDB serving pool.

    Availability is asked FIRST and the census is asked only for the lanes availability did not
    answer. Under `PARQUET_COVERAGE_AUTHORITY=availability` no TIME-BEARING lane is listed and no
    snapshot product's forward edge is listed either -- the products' forward halves come from the
    same availability reader, through `SnapshotForwardAvailability`.

    TWO KINDS OF LISTING SURVIVE UNDER `availability`, AND BOTH ARE NAMED HERE RATHER THAN GLOSSED.
    A `static_lookup` lane has no time axis, therefore owns no index (`layer-lanes.md` 4a), and stays
    on the listing census under both policies: four lanes, four prefixes. Beside them,
    `snapshot_products._verified_lane_daily_receipts` lists `<data-root>/_verification/` once per
    `daily`-layout forward product on a snapshot-coverage cache MISS -- two products, two bounded
    listings, over a manifest-declared marker count rather than a day range. Every other daily and
    release lane is answered from one pointer GET and one generation GET.
    """

    def work() -> dict[str, object]:
        lanes = registered_census_lanes()
        policy = settings.parquet_coverage_authority
        reader = open_availability_reader()
        resolution = resolve_availability_lanes(
            reader,
            lanes=lanes,
            policy=policy,
            now=generated_at,
        )
        for lane in resolution.withheld:
            logger.warning(
                "availability_coverage_withheld",
                layer=lane.layer,
                kind=lane.kind,
                code=lane.reason,
                reason=lane.detail,
            )
        census_rows: tuple[LaneCoverage, ...] = ()
        evaluated_through_day = generated_at.astimezone(UTC).date()
        if resolution.census_lanes:
            direct = _coverage_cache.get(open_listing(), lanes=resolution.census_lanes, now=generated_at)
            census_rows = direct.lanes
            evaluated_through_day = direct.evaluated_through_day
        snapshot = _snapshot_coverage_cache.get(
            open_snapshot_store(),
            now=generated_at,
            policy=policy,
            forward_availability=SnapshotForwardAvailability(reader=reader, now=generated_at),
        )
        for withheld in snapshot.withheld:
            logger.warning(
                "snapshot_coverage_withheld",
                layer=withheld.layer,
                code=withheld.code,
                reason=withheld.message,
            )
        direct_rows = merge_direct_lane_rows(lanes=lanes, resolution=resolution, census_rows=census_rows)
        return WarehouseCoverage(
            generated_at=generated_at,
            evaluated_through_day=evaluated_through_day,
            lanes=direct_rows + snapshot.lanes,
        ).to_wire()

    return await asyncio.to_thread(work)


async def _answer(
    read: Callable[[], Awaitable[dict[str, object]]],
    timeout_seconds: float,
    *,
    route: str,
) -> HTTPResponse:
    """Run one bounded warehouse read on the bounded pool and map EVERY fault onto an honest status.

    Nothing may leave here as a generic 500: `upstream-fault.ts` classifies `status >= 500` as
    transient, so the map would retry the identical read against a process already at its ceiling.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            payload = await read()
    except RequestError as exc:
        return _refused(exc)
    except Exception as exc:
        return _refusal(_as_refusal(exc, route=route, timeout_seconds=timeout_seconds), route)
    return json(payload, status=HTTP_OK)


def _as_refusal(exc: Exception, *, route: str, timeout_seconds: float) -> ServingRefusalError:
    """Map every fault a warehouse read can raise onto a refusal the client's `retry: 1` cannot amplify."""
    if isinstance(exc, ServingRefusalError):
        return exc
    if isinstance(exc, TimeoutError):
        return faults.read_timed_out(operation=route, timeout_seconds=timeout_seconds)
    if isinstance(exc, duckdb.Error):
        # `OutOfMemoryException` is the guard doing its job -- the read did not fit the ceiling. It is
        # not a 500 and not retryable: the same read costs the same, against a process already loaded.
        logger.warning("parquet_read_over_budget", route=route, fault=type(exc).__name__)
        return faults.read_over_budget(operation=route)
    # Everything unforeseen -- a botocore fault, a listing budget, a row this plane cannot render.
    # Logged with a traceback and answered as a refusal, because an unclassified 500 is retried.
    logger.exception("parquet_read_failed", route=route, fault=type(exc).__name__)
    return faults.serving_fault(operation=route, fault=type(exc).__name__)


def _refusal(exc: ServingRefusalError, route: str) -> HTTPResponse:
    """Render one serving refusal. Never one of the four states: this is about SERVING, not content."""
    status = _REFUSAL_HTTP_STATUS.get(exc.code, HTTP_CONFLICT)
    if exc.code not in _REFUSAL_HTTP_STATUS:
        logger.error("parquet_refusal_status_unmapped", route=route, code=exc.code)
    logger.warning("parquet_read_refused", route=route, code=exc.code, status=status)
    return json(_refusal_body(exc), status=status)


def _refused(exc: RequestError) -> HTTPResponse:
    """Render a rejected request. Never one of the four states: the caller, not the warehouse, was wrong."""
    return json(
        _refusal_body(ServingRefusalError("invalid_request", str(exc))),
        status=HTTP_BAD_REQUEST,
    )


def _refusal_body(exc: ServingRefusalError) -> dict[str, object]:
    """Render a core refusal in this adapter's error-envelope protocol."""
    return {"error": {"code": exc.code, "message": exc.message}}
