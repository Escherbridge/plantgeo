"""Fetch the bounded USGS NWIS IV feed and append it directly to water-gauges Parquet."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, cast

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.policy import resolve_bounded_bbox, resolve_max_source_records
from agri_data_service.ingest.usgs_nwis import NWIS_BOUNDS, build_gauge_write, fetch_streamflow_gauges
from agri_data_service.pipeline.direct.water_gauges import (
    WATER_GAUGES_SOURCE_COLUMNS,
    DirectWaterGaugesForwardAdapter,
    tables_by_publisher_day,
)
from agri_data_service.pipeline.lanes.water_gauges import WATER_GAUGES_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore, conform_to_stream_schema
from agri_data_service.warehouse.schemas.water_gauges import (
    WATER_GAUGES_GRAIN,
    WATER_GAUGES_SCHEMA,
    WATER_GAUGES_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter

KIND: Final = "observed"
STATEMENT_TIMEOUT_SECONDS: Final = 600
DEFAULT_MAX_DAY_ATTEMPTS: Final = 5
MAX_DAY_ATTEMPTS: Final = 10
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
MAX_RETRY_BASE_SECONDS: Final = 60.0
MAX_RETRY_DELAY_SECONDS: Final = 60.0
DEFAULT_CONTENTION_POLL_SECONDS: Final = 15.0
MAX_CONTENTION_POLL_SECONDS: Final = 60.0
DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0
MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
VERIFICATION_MISMATCH_SAMPLE: Final = 5


@dataclass(frozen=True, slots=True)
class ForwardContentVerification:
    """Actual z13 evidence collected after the completion markers landed."""

    actual_rows: int
    incoming_rows_verified: int


@dataclass(frozen=True, slots=True)
class ForwardDayResult:
    """The durable publication outcome for one publisher-named IV day."""

    day: date
    outcome: str
    attempts: int
    incoming_rows: int
    existing_rows: int
    added_rows: int
    updated_rows: int
    recovered_duplicate_rows: int
    merged_rows: int
    actual_z13_rows: int
    incoming_rows_verified: int
    parts: int
    rows: int
    written_bytes: int
    detail: str | None


def emit(event: str, **fields: object) -> None:
    """Write one stable JSON progress record without exposing credentials."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def _owned_publisher_tables(tables: Mapping[date, pa.Table]) -> dict[date, pa.Table]:
    """Keep only publisher days assigned to the direct writer."""
    return {
        publisher_day: table
        for publisher_day, table in tables.items()
        if publisher_day >= WATER_GAUGES_DIRECT_WRITER_START_DAY
    }


def parser() -> argparse.ArgumentParser:
    """Build the mutating, forward-only lane operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--bbox", help="west,south,east,north; defaults to INGEST_BBOX")
    built.add_argument("--max-day-attempts", type=int, default=DEFAULT_MAX_DAY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--contention-poll-seconds", type=float, default=DEFAULT_CONTENTION_POLL_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def _validate_args(args: argparse.Namespace) -> None:
    """Keep every retry and contention wait bounded."""
    if not 1 <= args.max_day_attempts <= MAX_DAY_ATTEMPTS:
        raise SystemExit(f"--max-day-attempts must be between 1 and {MAX_DAY_ATTEMPTS}")
    if not 0 < args.retry_base_seconds <= MAX_RETRY_BASE_SECONDS:
        raise SystemExit(f"--retry-base-seconds must be within (0, {MAX_RETRY_BASE_SECONDS}]")
    if not 0 < args.contention_poll_seconds <= MAX_CONTENTION_POLL_SECONDS:
        raise SystemExit(f"--contention-poll-seconds must be within (0, {MAX_CONTENTION_POLL_SECONDS}]")
    if not 0 < args.contention_timeout_seconds <= MAX_CONTENTION_TIMEOUT_SECONDS:
        raise SystemExit(f"--contention-timeout-seconds must be within (0, {MAX_CONTENTION_TIMEOUT_SECONDS}]")


def _tier_statuses(store: ObjectStore, day: date) -> dict[int, str]:
    """Read the durable completion checkpoint for every water-gauges zoom tier."""
    statuses: dict[int, str] = {}
    for zoom in ZOOM_TIERS:
        keys = store.list_partition_keys(WATER_GAUGES_STREAM, KIND, zoom, year=day.year, month=day.month)
        statuses[zoom] = partition_day_statuses(
            layer=WATER_GAUGES_STREAM,
            kind=KIND,
            zoom=zoom,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
    return statuses


def _grain_key(row: Mapping[str, object], *, day: date, label: str) -> tuple[str, datetime]:
    """Return a verified base-rung grain from a post-write table row."""
    site_number = row.get(WATER_GAUGES_GRAIN[0])
    observed_at = row.get(WATER_GAUGES_GRAIN[1])
    if not isinstance(site_number, str) or not site_number.strip():
        raise RuntimeError(f"{label} z13 row has no site_number")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise RuntimeError(f"{label} z13 row has no timezone-aware observed_at")
    if row.get("observed_day") != day:
        raise RuntimeError(f"{label} z13 row does not belong to publisher day {day.isoformat()}")
    return site_number, observed_at


def _rows_by_grain(table: pa.Table, *, day: date, label: str) -> dict[tuple[str, datetime], list[dict[str, object]]]:
    """Group an exact-schema table without discarding source-preserved duplicates."""
    if table.schema != WATER_GAUGES_SCHEMA.arrow_schema:
        raise RuntimeError(f"{label} z13 table does not match the registered water-gauges schema")
    indexed: dict[tuple[str, datetime], list[dict[str, object]]] = {}
    for row in table.to_pylist():
        key = _grain_key(row, day=day, label=label)
        indexed.setdefault(key, []).append(row)
    return indexed


def _table_sha256(table: pa.Table) -> str:
    """Hash canonical Arrow content for bounded mismatch evidence."""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, WATER_GAUGES_SCHEMA.arrow_schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _sample_grains(grains: set[tuple[str, datetime]]) -> list[str]:
    """Render a bounded mismatch sample without dumping a whole partition."""
    ordered = sorted(grains, key=lambda grain: (grain[0], grain[1]))[:VERIFICATION_MISMATCH_SAMPLE]
    return [f"{site}@{observed_at.isoformat()}" for site, observed_at in ordered]


def _verify_actual_z13(
    store: ObjectStore,
    *,
    day: date,
    expected: pa.Table,
    incoming: pa.Table,
) -> ForwardContentVerification:
    """Re-read z13 and prove its full merge plus every incoming source field."""
    actual = conform_to_stream_schema(
        store.read_partition(WATER_GAUGES_STREAM, KIND, ZOOM_TIERS[-1], day), WATER_GAUGES_SCHEMA
    ).combine_chunks()
    intended = conform_to_stream_schema(expected, WATER_GAUGES_SCHEMA).combine_chunks()
    if not actual.equals(intended):
        raise RuntimeError(
            "actual z13 differs from the complete intended duplicate-preserving table: "
            f"actual_rows={actual.num_rows}, intended_rows={intended.num_rows}, "
            f"actual_sha256={_table_sha256(actual)}, intended_sha256={_table_sha256(intended)}"
        )

    actual_rows = _rows_by_grain(actual, day=day, label="actual")
    incoming_rows = _rows_by_grain(incoming, day=day, label="incoming")
    duplicate_incoming = {grain for grain, rows in incoming_rows.items() if len(rows) != 1}
    if duplicate_incoming:
        raise RuntimeError(f"incoming z13 contains duplicate grains: {_sample_grains(duplicate_incoming)}")

    source_mismatches: list[str] = []
    for grain, incoming_group in incoming_rows.items():
        candidates = actual_rows.get(grain, [])
        if len(candidates) != 1:
            source_mismatches.extend(_sample_grains({grain}))
        else:
            actual_row = candidates[0]
            incoming_row = incoming_group[0]
            mismatched_columns = [
                column for column in WATER_GAUGES_SOURCE_COLUMNS if actual_row[column] != incoming_row[column]
            ]
            if mismatched_columns:
                source_mismatches.append(f"{grain[0]}@{grain[1].isoformat()}:{','.join(mismatched_columns)}")
        if len(source_mismatches) >= VERIFICATION_MISMATCH_SAMPLE:
            break
    if source_mismatches:
        raise RuntimeError(f"actual z13 does not carry every incoming source field: {source_mismatches}")
    return ForwardContentVerification(actual_rows=actual.num_rows, incoming_rows_verified=incoming.num_rows)


def _result_after_failure(  # noqa: PLR0913 - preserve bounded attempt evidence verbatim
    *,
    day: date,
    outcome: str,
    attempts: int,
    table: pa.Table,
    adapter: DirectWaterGaugesForwardAdapter,
    parts: int,
    rows: int,
    written_bytes: int,
    detail: str | None,
) -> ForwardDayResult:
    """Retain any pre-mutation merge evidence when a bounded day attempt cannot finish."""
    merge = adapter.merge
    return ForwardDayResult(
        day=day,
        outcome=outcome,
        attempts=attempts,
        incoming_rows=table.num_rows if merge is None else merge.incoming_rows,
        existing_rows=0 if merge is None else merge.existing_rows,
        added_rows=0 if merge is None else merge.added_rows,
        updated_rows=0 if merge is None else merge.updated_rows,
        recovered_duplicate_rows=0 if merge is None else merge.recovered_duplicate_rows,
        merged_rows=0 if merge is None else merge.table.num_rows,
        actual_z13_rows=0,
        incoming_rows_verified=0,
        parts=parts,
        rows=rows,
        written_bytes=written_bytes,
        detail=detail,
    )


async def _publish_day(  # noqa: PLR0912, PLR0913, PLR0915 - one bounded lane-day state machine
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    table: pa.Table,
    run_id: str,
    max_day_attempts: int,
    retry_base_seconds: float,
    contention_poll_seconds: float,
    contention_timeout_seconds: float,
    availability_storage: AvailabilityStorage | None = None,
    availability: AvailabilityExtensionTally | None = None,
) -> ForwardDayResult:
    """Publish one day through the shared lock/finalizer and verify its physical z13 content.

    `availability_storage` is threaded through rather than defaulted away: this writer OWNS every
    water-gauges day from `WATER_GAUGES_DIRECT_WRITER_START_DAY`, so a day it publishes without an
    index entry is a day `PARQUET_COVERAGE_AUTHORITY=availability` withholds from the slider.
    """
    adapter = DirectWaterGaugesForwardAdapter(table)
    lane = replace(LANE_REGISTRY[WATER_GAUGES_STREAM], adapter=cast("LaneAdapter", adapter))
    failed_attempts = 0
    attempts = 0
    contention_started = time.monotonic()
    initial_existing_rows: int | None = None
    intended_added_rows: int | None = None
    intended_updated_rows: int | None = None
    recovered_duplicate_rows: int | None = None
    while True:
        attempts += 1
        try:
            outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=datetime.now(UTC).date(),
                lane_day_lock=postgres_lane_day_lock,
                statement_timeout_seconds=STATEMENT_TIMEOUT_SECONDS,
                availability_storage=availability_storage,
                availability_tally=availability,
            )
        except Exception as error:  # advisory-lock and session failures share the bounded retry budget
            await session.rollback()
            outcome, parts, rows, written_bytes = "raised", 0, 0, 0
            detail = f"{type(error).__name__}: {error}"
        if adapter.merge is not None and initial_existing_rows is None:
            initial_existing_rows = adapter.merge.existing_rows
            intended_added_rows = adapter.merge.added_rows
            intended_updated_rows = adapter.merge.updated_rows
            recovered_duplicate_rows = adapter.merge.recovered_duplicate_rows
        tier_statuses: dict[int, str] | None = None
        content: ForwardContentVerification | None = None
        if outcome == "written":
            try:
                tier_statuses = _tier_statuses(store, day)
                incomplete_tiers = {zoom: status for zoom, status in tier_statuses.items() if status != "data"}
                if incomplete_tiers:
                    raise RuntimeError(f"completion checkpoint is not data at every tier: {incomplete_tiers}")
                if adapter.merge is None:
                    raise RuntimeError("water-gauges adapter reported written without an intended merge")
                content = _verify_actual_z13(store, day=day, expected=adapter.merge.table, incoming=table)
            except Exception as error:
                outcome = "raised"
                detail = f"post-write verification failed: {type(error).__name__}: {error}"
        emit(
            "water_gauges_forward_attempt",
            run_id=run_id,
            day=day.isoformat(),
            attempt=attempts,
            outcome=outcome,
            parts=parts,
            rows=rows,
            bytes=written_bytes,
            tier_statuses=tier_statuses,
            actual_z13_rows=None if content is None else content.actual_rows,
            incoming_rows_verified=None if content is None else content.incoming_rows_verified,
            detail=detail,
        )
        if outcome == "written":
            if adapter.merge is None or content is None:
                raise RuntimeError("water-gauges publication passed without merge/content evidence")
            return ForwardDayResult(
                day=day,
                outcome=outcome,
                attempts=attempts,
                incoming_rows=adapter.merge.incoming_rows,
                existing_rows=0 if initial_existing_rows is None else initial_existing_rows,
                added_rows=0 if intended_added_rows is None else intended_added_rows,
                updated_rows=0 if intended_updated_rows is None else intended_updated_rows,
                recovered_duplicate_rows=(0 if recovered_duplicate_rows is None else recovered_duplicate_rows),
                merged_rows=adapter.merge.table.num_rows,
                actual_z13_rows=content.actual_rows,
                incoming_rows_verified=content.incoming_rows_verified,
                parts=parts,
                rows=rows,
                written_bytes=written_bytes,
                detail=detail,
            )
        if outcome == "contended":
            waited = time.monotonic() - contention_started
            if waited >= contention_timeout_seconds:
                return _result_after_failure(
                    day=day,
                    outcome=outcome,
                    attempts=attempts,
                    table=table,
                    adapter=adapter,
                    parts=parts,
                    rows=rows,
                    written_bytes=written_bytes,
                    detail=f"contention did not clear within {contention_timeout_seconds:g}s: {detail}",
                )
            await asyncio.sleep(min(contention_poll_seconds, contention_timeout_seconds - waited))
            continue
        if outcome == "raised":
            failed_attempts += 1
            retryable = not detail or "DirectWaterGaugesError" not in detail
            if retryable and failed_attempts < max_day_attempts:
                delay = min(MAX_RETRY_DELAY_SECONDS, retry_base_seconds * (2 ** (failed_attempts - 1)))
                await asyncio.sleep(delay)
                continue
        return _result_after_failure(
            day=day,
            outcome=outcome,
            attempts=attempts,
            table=table,
            adapter=adapter,
            parts=parts,
            rows=rows,
            written_bytes=written_bytes,
            detail=detail,
        )


async def run(args: argparse.Namespace) -> int:
    """Fetch one complete IV snapshot and durably merge every publisher day it contains."""
    _validate_args(args)
    bbox = resolve_bounded_bbox(args.bbox)
    if bbox is None:
        raise SystemExit("--bbox or INGEST_BBOX is required")

    fetched_at = datetime.now(UTC)
    run_id = f"water-gauges-nwis-forward-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}"
    # ONE TALLY FOR THE WHOLE RUN, on EVERY terminal record this function emits. Without it an
    # availability verdict lands only in a day's detail string -- and `ladder_incomplete` and
    # `retry_claim_failed` both mean a day that is in the bucket and permanently outside the index.
    availability = AvailabilityExtensionTally()
    async with upstream_client(NWIS_BOUNDS) as client:
        fetched = await fetch_streamflow_gauges(client, bbox, fetched_at)

    record_limit = resolve_max_source_records()
    if len(fetched.gauges) > record_limit:
        raise RuntimeError(
            f"NWIS returned {len(fetched.gauges)} gauges, above the configured "
            f"INGEST_MAX_SOURCE_RECORDS={record_limit}; refusing a truncated Parquet checkpoint"
        )
    publisher_timestamped = [gauge for gauge in fetched.gauges if not gauge.get("updatedAtIsWallClock")]
    wall_clock_dropped = len(fetched.gauges) - len(publisher_timestamped)
    valid_records = [
        gauge for gauge in publisher_timestamped if build_gauge_write(gauge, WATER_GAUGES_STREAM) is not None
    ]
    rejected = len(publisher_timestamped) - len(valid_records)
    publisher_tables = tables_by_publisher_day(valid_records, ingested_at=fetched_at)
    tables = _owned_publisher_tables(publisher_tables)
    emit(
        "water_gauges_forward_fetch",
        run_id=run_id,
        fetched_at=fetched_at.isoformat(),
        records_seen=len(fetched.gauges),
        records_writable=len(valid_records),
        rejected=rejected,
        sentinel_sites=fetched.sentinel_sites,
        wall_clock_records_dropped=wall_clock_dropped,
        ownership_start_day=WATER_GAUGES_DIRECT_WRITER_START_DAY.isoformat(),
        publisher_days_seen=[day.isoformat() for day in publisher_tables],
        publisher_days_owned=[day.isoformat() for day in tables],
    )
    if not tables:
        emit(
            "water_gauges_forward_complete",
            run_id=run_id,
            outcome=("no_writable_records" if not publisher_tables else "outside_owned_window"),
            days=0,
            rows_added=0,
            rows_updated=0,
            bytes=0,
            **availability.to_summary(),
        )
        return 0

    credentials = settings.require_object_store()
    store = ObjectStore(
        BotoObjectStoreBackend.from_credentials(credentials),
        prefix=settings.object_store_prefix,
    )
    # Built from the SAME settings the store above already required, so it adds no failure mode of
    # its own and opens no socket: a run without object-store credentials raised four lines earlier.
    availability_storage = BotoAvailabilityStorage.from_settings()
    database_url = settings.require_local_source_loader_database_url()
    results: list[ForwardDayResult] = []
    async with local_source_loader_session(database_url) as session:
        for day, table in tables.items():
            result = await _publish_day(
                session,
                store,
                day=day,
                table=table,
                run_id=run_id,
                max_day_attempts=args.max_day_attempts,
                retry_base_seconds=args.retry_base_seconds,
                contention_poll_seconds=args.contention_poll_seconds,
                contention_timeout_seconds=args.contention_timeout_seconds,
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit(
                "water_gauges_forward_checkpoint",
                run_id=run_id,
                namespace=f"layer={WATER_GAUGES_STREAM}/kind={KIND}",
                day=day.isoformat(),
                outcome=result.outcome,
                attempts=result.attempts,
                incoming_rows=result.incoming_rows,
                existing_rows=result.existing_rows,
                added_rows=result.added_rows,
                updated_rows=result.updated_rows,
                recovered_duplicate_rows=result.recovered_duplicate_rows,
                merged_rows=result.merged_rows,
                actual_z13_rows=result.actual_z13_rows,
                incoming_rows_verified=result.incoming_rows_verified,
                parts=result.parts,
                rows=result.rows,
                bytes=result.written_bytes,
                detail=result.detail,
            )

    outcomes = Counter(result.outcome for result in results)
    complete = len(outcomes) == 1 and outcomes["written"] == len(results)
    emit(
        "water_gauges_forward_complete",
        run_id=run_id,
        outcome="complete" if complete else "incomplete",
        days=len(results),
        outcomes=dict(sorted(outcomes.items())),
        incoming_rows=sum(result.incoming_rows for result in results),
        rows_added=sum(result.added_rows for result in results),
        rows_updated=sum(result.updated_rows for result in results),
        recovered_duplicate_rows=sum(result.recovered_duplicate_rows for result in results),
        merged_rows=sum(result.merged_rows for result in results),
        actual_z13_rows=sum(result.actual_z13_rows for result in results),
        incoming_rows_verified=sum(result.incoming_rows_verified for result in results),
        parts=sum(result.parts for result in results),
        rows=sum(result.rows for result in results),
        bytes=sum(result.written_bytes for result in results),
        **availability.to_summary(),
    )
    return 0 if all(result.outcome == "written" for result in results) else 1


async def main() -> int:
    """Run the operator and emit one typed terminal fault on failure."""
    args = parser().parse_args()
    try:
        return await run(args)
    except Exception as error:
        emit("water_gauges_forward_failed", error_type=type(error).__name__, detail=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
