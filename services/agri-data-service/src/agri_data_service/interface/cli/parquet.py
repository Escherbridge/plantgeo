"""Thin CLI adapters over the shared bounded Parquet read core."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import click
import duckdb

from agri_data_service.config import settings
from agri_data_service.parquet_ops import faults
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
    resolve_snapshot_product,
    resolve_snapshot_window,
)
from agri_data_service.parquet_ops.warehouse_reader import DuckDbRowReader, ObjectStoreListing
from agri_data_service.parquet_ops.wire import (
    ROUTE_COVERAGE,
    ROUTE_DAY,
    ROUTE_RELEASE,
    ROUTE_WINDOW,
    LaneCoverage,
    WarehouseCoverage,
    render_window,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from agri_data_service.parquet_ops.request_params import ReadScope
    from agri_data_service.parquet_ops.warehouse_reader import PartitionRowReader

_ROW_READ_TIMEOUT_SECONDS = 14.0
_COVERAGE_TIMEOUT_SECONDS = 29.0


@click.group()
def parquet() -> None:
    """Read governed Parquet day, window, release, and coverage envelopes."""


def _scope_options(function: click.decorators.FC) -> click.decorators.FC:
    function = click.option("--bbox", help="Optional west,south,east,north viewport.")(function)
    function = click.option("--zoom", required=True, help="Plain map zoom integer, never a padded tier.")(function)
    function = click.option("--kind", default="observed", show_default=True)(function)
    return click.option("--layer", required=True)(function)


@parquet.command("day")
@_scope_options
@click.option("--day", required=True, help="Publisher-named YYYY-MM-DD calendar day.")
def read_day(layer: str, kind: str, zoom: str, bbox: str | None, day: str) -> None:
    """Read one exact day and print its shared wire envelope."""
    scope = _parse_scope(layer=layer, kind=kind, zoom=zoom, bbox=bbox)
    parsed_day = _parse(lambda: parse_calendar_day(day, "day"))
    if scope.layer in PRODUCT_BY_LAYER:
        read = _snapshot_day_read(scope=scope, day=parsed_day)
    else:
        read = _row_read(
            lambda listing, reader: resolve_day(listing, reader, scope=scope, day=parsed_day).to_wire(),
            ROUTE_DAY,
        )
    _emit(
        read,
        operation=ROUTE_DAY,
        timeout_seconds=_ROW_READ_TIMEOUT_SECONDS,
    )


@parquet.command("window")
@_scope_options
@click.option("--first-day", required=True)
@click.option("--last-day", required=True)
def read_window(  # noqa: PLR0913
    layer: str,
    kind: str,
    zoom: str,
    bbox: str | None,
    first_day: str,
    last_day: str,
) -> None:
    """Read a closed, bounded day range and print its shared wire envelope."""
    scope = _parse_scope(layer=layer, kind=kind, zoom=zoom, bbox=bbox)
    first, last = _parse(lambda: parse_window(first_day, last_day))
    if scope.layer in PRODUCT_BY_LAYER:
        read = _snapshot_window_read(scope=scope, first_day=first, last_day=last)
    else:
        read = _row_read(
            lambda listing, reader: render_window(
                resolve_window(listing, reader, scope=scope, first_day=first, last_day=last)
            ),
            ROUTE_WINDOW,
        )
    _emit(
        read,
        operation=ROUTE_WINDOW,
        timeout_seconds=_ROW_READ_TIMEOUT_SECONDS,
    )


@parquet.command("release")
@_scope_options
@click.option("--as-of", required=True, help="Publisher-named YYYY-MM-DD release boundary.")
def read_release(layer: str, kind: str, zoom: str, bbox: str | None, as_of: str) -> None:
    """Read the newest release at or before a day and print its shared wire envelope."""
    scope = _parse_scope(layer=layer, kind=kind, zoom=zoom, bbox=bbox)
    parsed_as_of = _parse(lambda: parse_calendar_day(as_of, "as_of"))
    if scope.layer in PRODUCT_BY_LAYER:
        refusal = faults.snapshot_unpublished(
            layer=scope.layer,
            snapshot_id=PRODUCT_BY_LAYER[scope.layer].snapshot_id,
            detail="daily-series snapshots do not define release carry; use the exact day command",
        )
        raise click.ClickException(f"{refusal.code}: {refusal.message}")
    _emit(
        _row_read(
            lambda listing, reader: resolve_release(listing, reader, scope=scope, as_of=parsed_as_of).to_wire(),
            ROUTE_RELEASE,
        ),
        operation=ROUTE_RELEASE,
        timeout_seconds=_ROW_READ_TIMEOUT_SECONDS,
    )


@parquet.command("coverage")
def read_coverage() -> None:
    """Read the bounded whole-warehouse coverage census."""

    async def read() -> dict[str, object]:
        credentials = settings.require_object_store()
        backend = BotoObjectStoreBackend.from_credentials(credentials)
        listing = ObjectStoreListing(
            backend=backend,
            prefix=settings.object_store_prefix,
        )
        snapshot_store = ObjectStoreSnapshotStore(backend=backend, prefix=settings.object_store_prefix)
        generated_at = datetime.now(UTC)

        def census() -> dict[str, object]:
            return _merged_coverage(
                CoverageCache().get(listing, lanes=registered_census_lanes(), now=generated_at),
                SnapshotCoverageCache().get(snapshot_store, now=generated_at).lanes,
            )

        async with asyncio.timeout(_COVERAGE_TIMEOUT_SECONDS):
            return await asyncio.to_thread(census)

    _emit(read(), operation=ROUTE_COVERAGE, timeout_seconds=_COVERAGE_TIMEOUT_SECONDS)


def _parse_scope(*, layer: str, kind: str, zoom: str, bbox: str | None) -> ReadScope:
    return _parse(lambda: parse_read_scope(layer=layer, kind=kind, zoom=zoom, bbox=bbox))


def _parse[T](work: Callable[[], T]) -> T:
    try:
        return work()
    except RequestError as exc:
        raise click.ClickException(f"invalid_request: {exc}") from exc


async def _row_read(
    work: Callable[[ObjectStoreListing, PartitionRowReader], dict[str, object]],
    operation: str,
) -> dict[str, object]:
    credentials = settings.require_object_store()
    listing = ObjectStoreListing(
        backend=BotoObjectStoreBackend.from_credentials(credentials),
        prefix=settings.object_store_prefix,
    )
    async with asyncio.timeout(_ROW_READ_TIMEOUT_SECONDS):
        return await run_serving_read(
            credentials,
            lambda session: work(listing, DuckDbRowReader(session=session)),
            prefix=settings.object_store_prefix,
            operation=operation,
        )


async def _snapshot_day_read(*, scope: ReadScope, day: date) -> dict[str, object]:
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStoreSnapshotStore(backend=backend, prefix=settings.object_store_prefix)
    async with asyncio.timeout(_ROW_READ_TIMEOUT_SECONDS):
        return await run_serving_read(
            credentials,
            lambda session: resolve_snapshot_product(store, session, scope=scope, day=day).to_wire(),
            prefix=settings.object_store_prefix,
            operation=ROUTE_DAY,
        )


async def _snapshot_window_read(*, scope: ReadScope, first_day: date, last_day: date) -> dict[str, object]:
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStoreSnapshotStore(backend=backend, prefix=settings.object_store_prefix)
    async with asyncio.timeout(_ROW_READ_TIMEOUT_SECONDS):
        return await run_serving_read(
            credentials,
            lambda session: render_window(
                resolve_snapshot_window(
                    store,
                    session,
                    scope=scope,
                    first_day=first_day,
                    last_day=last_day,
                )
            ),
            prefix=settings.object_store_prefix,
            operation=ROUTE_WINDOW,
        )


def _merged_coverage(direct: WarehouseCoverage, snapshot: tuple[LaneCoverage, ...]) -> dict[str, object]:
    return WarehouseCoverage(
        generated_at=direct.generated_at,
        evaluated_through_day=direct.evaluated_through_day,
        lanes=direct.lanes + snapshot,
    ).to_wire()


def _emit(
    read: Coroutine[object, object, dict[str, object]],
    *,
    operation: str,
    timeout_seconds: float,
) -> None:
    try:
        payload: dict[str, object] = asyncio.run(read)
    except ServingRefusalError as exc:
        raise click.ClickException(f"{exc.code}: {exc.message}") from exc
    except TimeoutError as exc:
        refusal = faults.read_timed_out(operation=operation, timeout_seconds=timeout_seconds)
        raise click.ClickException(f"{refusal.code}: {refusal.message}") from exc
    except duckdb.Error as exc:
        refusal = faults.read_over_budget(operation=operation)
        raise click.ClickException(f"{refusal.code}: {refusal.message}") from exc
    except Exception as exc:
        refusal = faults.serving_fault(operation=operation, fault=type(exc).__name__)
        raise click.ClickException(f"{refusal.code}: {refusal.message}") from exc
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
