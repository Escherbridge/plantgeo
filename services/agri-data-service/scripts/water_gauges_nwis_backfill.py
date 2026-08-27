"""Backfill actual pre-cutover NWIS daily-value days directly into Parquet."""

from __future__ import annotations

import argparse
import asyncio
import json
import time as monotonic_time
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final, cast

from water_gauges_postgres_drain import (
    BULK_STATEMENT_TIMEOUT_SECONDS,
    KIND,
    LAYER,
    read_base_days,
    read_source_day_counts,
)

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.ingest.usgs_nwis import NWIS_ARCHIVE_BOUNDS, fetch_streamflow_history
from agri_data_service.pipeline.direct.water_gauges import (
    DirectWaterGaugesAdapter,
    tables_by_publisher_day,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter, LaneRegistration

DEFAULT_CHUNK_DAYS: Final = 10
MAX_CHUNK_DAYS: Final = 31
DEFAULT_MAX_DAY_ATTEMPTS: Final = 5
MAX_DAY_ATTEMPTS: Final = 10
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
DEFAULT_CONTENTION_POLL_SECONDS: Final = 15.0
DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0


def emit(event: str, **fields: object) -> None:
    """Write one stable JSON progress record."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def iso_day(value: str) -> date:
    """Parse one canonical CLI calendar day."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("day must be canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("day must be canonical YYYY-MM-DD")
    return parsed


def parser() -> argparse.ArgumentParser:
    """Build the lane-scoped mutating operator CLI."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--since", type=iso_day, required=True, help="first publisher-named day, inclusive")
    built.add_argument("--until", type=iso_day, required=True, help="last publisher-named day, inclusive")
    built.add_argument("--bbox", help="west,south,east,north; defaults to INGEST_BBOX")
    built.add_argument("--chunk-days", type=int, default=DEFAULT_CHUNK_DAYS)
    built.add_argument("--max-day-attempts", type=int, default=DEFAULT_MAX_DAY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--contention-poll-seconds", type=float, default=DEFAULT_CONTENTION_POLL_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def statuses_for_day(store: ObjectStore, day: date) -> dict[int, str]:
    """Read every rung without constructing a multi-decade calendar window."""
    statuses: dict[int, str] = {}
    for zoom in ZOOM_TIERS:
        keys = store.list_partition_keys(LAYER, KIND, zoom, year=day.year, month=day.month)
        statuses[zoom] = partition_day_statuses(
            layer=LAYER,
            kind=KIND,
            zoom=zoom,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
    return statuses


async def publish_with_retries(  # noqa: PLR0913 - bounded operator controls stay explicit
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    table: pa.Table,
    run_id: str,
    repair_ladder_only: bool,
    args: argparse.Namespace,
) -> tuple[str, int, int, int, str | None, int]:
    """Run one new-day write or coarse-only repair with bounded contention and R2 retries."""
    failed_attempts = 0
    attempts = 0
    contention_started = monotonic_time.monotonic()
    while True:
        attempts += 1
        detail: str | None
        try:
            if repair_ladder_only:
                async with postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
                    if not granted:
                        outcome, parts, rows, written_bytes, detail = (
                            "contended",
                            0,
                            0,
                            0,
                            f"{day.isoformat()}: another writer holds the lane-day lock",
                        )
                    else:
                        derived = derive_and_write_day_tiers(
                            store,
                            layer=LAYER,
                            kind=KIND,
                            day=day,
                            run_id=run_id,
                            now=lambda: datetime.now(UTC),
                        )
                        outcome, parts, rows, written_bytes, detail = (
                            "written",
                            derived.part_count,
                            derived.row_count,
                            derived.byte_count,
                            "; ".join(derived.notes) or None,
                        )
            else:
                write_lane = replace(
                    lane,
                    adapter=cast("LaneAdapter", DirectWaterGaugesAdapter(table)),
                )
                outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                    session,
                    store,
                    write_lane,
                    day=day,
                    run_id=run_id,
                    now=lambda: datetime.now(UTC),
                    today=datetime.now(UTC).date(),
                    lane_day_lock=postgres_lane_day_lock,
                    statement_timeout_seconds=BULK_STATEMENT_TIMEOUT_SECONDS,
                )
            if repair_ladder_only:
                await session.rollback()
        except Exception as error:
            await session.rollback()
            outcome, parts, rows, written_bytes = "raised", 0, 0, 0
            detail = f"{type(error).__name__}: {error}"

        emit(
            "water_gauges_nwis_attempt",
            run_id=run_id,
            day=day.isoformat(),
            attempt=attempts,
            repair_ladder_only=repair_ladder_only,
            outcome=outcome,
            parts=parts,
            rows=rows,
            bytes=written_bytes,
            detail=detail,
        )
        if outcome == "written":
            return outcome, parts, rows, written_bytes, detail, attempts
        if outcome == "contended":
            waited = monotonic_time.monotonic() - contention_started
            if waited >= args.contention_timeout_seconds:
                return outcome, parts, rows, written_bytes, detail, attempts
            await asyncio.sleep(min(args.contention_poll_seconds, args.contention_timeout_seconds - waited))
            continue
        failed_attempts += 1
        if outcome == "raised" and failed_attempts < args.max_day_attempts:
            await asyncio.sleep(min(60.0, args.retry_base_seconds * (2 ** (failed_attempts - 1))))
            continue
        return outcome, parts, rows, written_bytes, detail, attempts


def chunks(first_day: date, last_day: date, chunk_days: int) -> tuple[tuple[date, date], ...]:
    """Split an inclusive operator range into bounded inclusive NWIS requests."""
    found: list[tuple[date, date]] = []
    cursor = first_day
    while cursor <= last_day:
        end = min(cursor + timedelta(days=chunk_days - 1), last_day)
        found.append((cursor, end))
        cursor = end + timedelta(days=1)
    return tuple(found)


async def main() -> int:  # noqa: PLR0912, PLR0915 - one bounded backfill state machine
    """Fetch real NWIS days oldest-first and publish only uncovered z13 days."""
    args = parser().parse_args()
    if args.since > args.until:
        raise SystemExit("--since must be on or before --until")
    if args.chunk_days < 1 or args.chunk_days > MAX_CHUNK_DAYS:
        raise SystemExit(f"--chunk-days must be between 1 and {MAX_CHUNK_DAYS}")
    if not 1 <= args.max_day_attempts <= MAX_DAY_ATTEMPTS:
        raise SystemExit(f"--max-day-attempts must be between 1 and {MAX_DAY_ATTEMPTS}")
    if args.retry_base_seconds <= 0 or args.contention_poll_seconds <= 0 or args.contention_timeout_seconds <= 0:
        raise SystemExit("retry and contention seconds must all be positive")
    bbox = resolve_bounded_bbox(args.bbox)
    if bbox is None:
        raise SystemExit("--bbox or INGEST_BBOX is required")

    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStore(backend, prefix=settings.object_store_prefix)
    database_url = settings.require_local_source_loader_database_url()
    registered_lane = LANE_REGISTRY[LAYER]
    run_id = f"water-gauges-nwis-backfill-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    totals: Counter[str] = Counter()

    async with local_source_loader_session(database_url) as session:
        source_counts = await read_source_day_counts(session)
        source_days = read_base_days(backend, store, source_counts)
        if any(item.status != "data" or item.parquet_rows != item.source_rows for item in source_days):
            raise RuntimeError(
                "Postgres-to-Parquet water-gauges parity is incomplete; run water_gauges_postgres_drain.py first"
            )
        earliest_postgres_day = min(source_counts)
        if args.until >= earliest_postgres_day:
            raise RuntimeError(
                f"--until must be before PostgreSQL's earliest water-gauges day "
                f"{earliest_postgres_day.isoformat()}; archive rows may never replace PostgreSQL-covered data"
            )

        requested_chunks = chunks(args.since, args.until, args.chunk_days)
        async with upstream_client(NWIS_ARCHIVE_BOUNDS) as client:
            for chunk_index, (chunk_start, chunk_end) in enumerate(requested_chunks, start=1):
                fetched_at = datetime.now(UTC)
                window = HistoryWindow(
                    start=datetime.combine(chunk_start, time.min, tzinfo=UTC),
                    end=datetime.combine(chunk_end + timedelta(days=1), time.min, tzinfo=UTC),
                )
                records = await fetch_streamflow_history(client, bbox, window)
                tables = tables_by_publisher_day(records, ingested_at=fetched_at)
                unexpected_days = [day for day in tables if not chunk_start <= day <= chunk_end]
                if unexpected_days:
                    raise RuntimeError(
                        "NWIS returned publisher days outside the requested chunk: "
                        f"{[day.isoformat() for day in unexpected_days[:10]]}"
                    )
                emit(
                    "water_gauges_nwis_chunk",
                    run_id=run_id,
                    index=chunk_index,
                    total=len(requested_chunks),
                    since=chunk_start.isoformat(),
                    until=chunk_end.isoformat(),
                    source_days=len(tables),
                    source_rows=sum(table.num_rows for table in tables.values()),
                )
                for day, table in tables.items():
                    current = statuses_for_day(store, day)
                    if all(status == "data" for status in current.values()):
                        totals["preserved_existing"] += 1
                        continue
                    base_status = current[BASE_ZOOM_TIER]
                    if base_status in {"absent", "conflict"}:
                        raise RuntimeError(
                            f"{day.isoformat()} z{BASE_ZOOM_TIER} is {base_status}; "
                            "refusing to retract governed object-store evidence"
                        )
                    repair_ladder_only = base_status == "data"
                    outcome, parts, rows, written_bytes, detail, attempts = await publish_with_retries(
                        session,
                        store,
                        registered_lane,
                        day=day,
                        table=table,
                        run_id=run_id,
                        repair_ladder_only=repair_ladder_only,
                        args=args,
                    )
                    totals[outcome] += 1
                    final_statuses = statuses_for_day(store, day)
                    emit(
                        "water_gauges_nwis_day",
                        run_id=run_id,
                        day=day.isoformat(),
                        previous_statuses=current,
                        final_statuses=final_statuses,
                        source_rows=table.num_rows,
                        repair_ladder_only=repair_ladder_only,
                        attempts=attempts,
                        outcome=outcome,
                        parts=parts,
                        rows=rows,
                        bytes=written_bytes,
                        detail=detail,
                    )
                    if outcome != "written" or any(status != "data" for status in final_statuses.values()):
                        raise RuntimeError(
                            f"{day.isoformat()} did not finish at every tier: "
                            f"outcome={outcome}, statuses={final_statuses}, detail={detail}"
                        )

    emit("water_gauges_nwis_complete", run_id=run_id, outcomes=dict(sorted(totals.items())))
    return 1 if any(totals[outcome] for outcome in ("raised", "blocked", "contended")) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
