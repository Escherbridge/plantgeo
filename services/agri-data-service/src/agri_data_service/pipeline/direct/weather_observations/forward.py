"""Poll Open-Meteo's current-conditions grid once and durably merge every day the poll touched.

THE CLI SHAPE FOLLOWS `climate/forward.py` / `soil/forward.py` -- `--max-days`, `--time-budget-seconds`,
`--run-id`, the bounded retry and contention knobs -- because that is the contract a reviewer expects
of every lane under this track. What `--max-days` MEANS differs, and is documented at its flag: there
is no settled-day backlog to walk (see `source.py`'s module docstring), so it caps how many of the
(at most two, see `_newest_day_buckets`) day buckets ONE poll produced are actually published, not how
many days of history this run may advance through.

THE PUBLISH LOOP FOLLOWS `pipeline/parquet/water_gauges_forward.py` -- merge, verify, retry, emit --
because this lane's incremental-accumulation shape is water-gauges' shape, not climate's. See
`pipeline/direct/AGENTS.md`, "Weather observations".
"""

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
from agri_data_service.ingest.open_meteo import OPEN_METEO_BOUNDS
from agri_data_service.pipeline.direct.weather_observations.adapter import (
    WEATHER_OBSERVATIONS_DIRECT_KIND,
    DirectWeatherObservationsForwardAdapter,
)
from agri_data_service.pipeline.direct.weather_observations.rows import (
    WEATHER_OBSERVATIONS_SOURCE_COLUMNS,
    direct_weather_observation_tables,
)
from agri_data_service.pipeline.direct.weather_observations.source import poll_current_conditions
from agri_data_service.pipeline.direct.weather_observations.support import weather_sample_points
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore, conform_to_stream_schema
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter

#: The forward writer can never see more than two named days in one poll -- an observation lands on
#: yesterday only when the poll runs within `MAX_OBSERVATION_AGE` (3h) of UTC midnight and the source
#: instant is still dated the day before. A ceiling of 2 documents that fact rather than guessing at
#: a climate/soil-style backlog depth this lane structurally has none of.
WEATHER_OBSERVATIONS_MAX_DAYS: Final = 2
#: Defaults to the full ceiling, not 1: this lane has no archive endpoint for this product (see
#: module docstring), so a bucket the default silently dropped at the UTC midnight straddle could
#: never be re-fetched. Publishing both buckets every run is the only default that cannot lose data;
#: nothing downstream reads this CLI on a schedule yet (`pipeline/direct/AGENTS.md`, "Proposed
#: executor lane -- not wired") and the upstream poll cost is independent of --max-days (one
#: current-conditions fetch either way), so there is no budget reason to prefer 1.
WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS: Final = WEATHER_OBSERVATIONS_MAX_DAYS
WEATHER_OBSERVATIONS_DEFAULT_TIME_BUDGET_SECONDS: Final = 120.0
WEATHER_OBSERVATIONS_MAX_TIME_BUDGET_SECONDS: Final = 900.0
STATEMENT_TIMEOUT_SECONDS: Final = 600
DEFAULT_MAX_DAY_ATTEMPTS: Final = 5
MAX_DAY_ATTEMPTS: Final = 10
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
MAX_RETRY_BASE_SECONDS: Final = 60.0
MAX_RETRY_DELAY_SECONDS: Final = 60.0
DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0
MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
CONTENTION_POLL_SECONDS: Final = 15.0
VERIFICATION_MISMATCH_SAMPLE: Final = 5


class WeatherObservationsForwardConfigError(ValueError):
    """Raised when the forward CLI is invoked with an out-of-range argument."""


@dataclass(frozen=True, slots=True)
class ForwardContentVerification:
    """Actual z13 evidence collected after the completion markers landed."""

    actual_rows: int
    incoming_rows_verified: int


@dataclass(frozen=True, slots=True)
class ForwardDayResult:
    """The durable publication outcome for one named day."""

    day: date
    outcome: str
    attempts: int
    incoming_rows: int
    existing_rows: int
    added_rows: int
    updated_rows: int
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


def _newest_day_buckets(tables: Mapping[date, pa.Table], *, max_days: int) -> dict[date, pa.Table]:
    """Keep the newest `max_days` day buckets a poll produced, newest first like every other lane."""
    newest_first = sorted(tables, reverse=True)[:max_days]
    return {day: tables[day] for day in newest_first}


def parser() -> argparse.ArgumentParser:
    """Build the mutating, forward-only lane operator."""
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--bbox", help="west,south,east,north; defaults to INGEST_BBOX")
    built.add_argument("--max-days", type=int, default=WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS)
    built.add_argument("--time-budget-seconds", type=float, default=WEATHER_OBSERVATIONS_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=DEFAULT_MAX_DAY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=MAX_RETRY_DELAY_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def _validate_args(args: argparse.Namespace) -> None:
    """Keep every day count, budget, retry and contention wait bounded."""
    if not 1 <= args.max_days <= WEATHER_OBSERVATIONS_MAX_DAYS:
        raise WeatherObservationsForwardConfigError(f"--max-days must be between 1 and {WEATHER_OBSERVATIONS_MAX_DAYS}")
    if not 0 < args.time_budget_seconds <= WEATHER_OBSERVATIONS_MAX_TIME_BUDGET_SECONDS:
        raise WeatherObservationsForwardConfigError(
            f"--time-budget-seconds must be within (0, {WEATHER_OBSERVATIONS_MAX_TIME_BUDGET_SECONDS}]"
        )
    if not 1 <= args.retry_attempts <= MAX_DAY_ATTEMPTS:
        raise WeatherObservationsForwardConfigError(f"--retry-attempts must be between 1 and {MAX_DAY_ATTEMPTS}")
    if not 0 < args.retry_base_seconds <= MAX_RETRY_BASE_SECONDS:
        raise WeatherObservationsForwardConfigError(
            f"--retry-base-seconds must be within (0, {MAX_RETRY_BASE_SECONDS}]"
        )
    if args.retry_max_seconds < args.retry_base_seconds:
        raise WeatherObservationsForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")
    if not 0 < args.contention_timeout_seconds <= MAX_CONTENTION_TIMEOUT_SECONDS:
        raise WeatherObservationsForwardConfigError(
            f"--contention-timeout-seconds must be within (0, {MAX_CONTENTION_TIMEOUT_SECONDS}]"
        )


def _tier_statuses(store: ObjectStore, day: date) -> dict[int, str]:
    """Read the durable completion checkpoint for every weather-observations zoom tier."""
    statuses: dict[int, str] = {}
    for zoom in ZOOM_TIERS:
        keys = store.list_partition_keys(
            WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, zoom, year=day.year, month=day.month
        )
        statuses[zoom] = partition_day_statuses(
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_OBSERVATIONS_DIRECT_KIND,
            zoom=zoom,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
    return statuses


def _grain_key(row: Mapping[str, object], *, day: date, label: str) -> tuple[float, float, datetime]:
    """Return a verified base-rung grain from a post-write table row."""
    latitude = row.get(WEATHER_OBSERVATIONS_GRAIN[0])
    longitude = row.get(WEATHER_OBSERVATIONS_GRAIN[1])
    observed_at = row.get(WEATHER_OBSERVATIONS_GRAIN[2])
    if isinstance(latitude, bool) or not isinstance(latitude, int | float):
        raise RuntimeError(f"{label} z13 row has no latitude")
    if isinstance(longitude, bool) or not isinstance(longitude, int | float):
        raise RuntimeError(f"{label} z13 row has no longitude")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise RuntimeError(f"{label} z13 row has no timezone-aware observed_at")
    if row.get("observed_day") != day:
        raise RuntimeError(f"{label} z13 row does not belong to day {day.isoformat()}")
    return float(latitude), float(longitude), observed_at


def _rows_by_grain(table: pa.Table, *, day: date, label: str) -> dict[tuple[float, float, datetime], dict[str, object]]:
    """Group an exact-schema table by its base grain, refusing an internal duplicate."""
    if table.schema != WEATHER_OBSERVATIONS_SCHEMA.arrow_schema:
        raise RuntimeError(f"{label} z13 table does not match the registered weather-observations schema")
    indexed: dict[tuple[float, float, datetime], dict[str, object]] = {}
    for row in table.to_pylist():
        key = _grain_key(row, day=day, label=label)
        if key in indexed:
            raise RuntimeError(f"{label} z13 table holds duplicate grain {key!r}")
        indexed[key] = row
    return indexed


def _table_sha256(table: pa.Table) -> str:
    """Hash canonical Arrow content for bounded mismatch evidence."""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, WEATHER_OBSERVATIONS_SCHEMA.arrow_schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _sample_grains(grains: set[tuple[float, float, datetime]]) -> list[str]:
    """Render a bounded mismatch sample without dumping a whole partition."""
    ordered = sorted(grains)[:VERIFICATION_MISMATCH_SAMPLE]
    return [f"{latitude},{longitude}@{observed_at.isoformat()}" for latitude, longitude, observed_at in ordered]


def _verify_actual_z13(
    store: ObjectStore,
    *,
    day: date,
    expected: pa.Table,
    incoming: pa.Table,
) -> ForwardContentVerification:
    """Re-read z13 and prove it equals the intended merge, and carries every incoming source field."""
    actual = conform_to_stream_schema(
        store.read_partition(WEATHER_OBSERVATIONS_STREAM, WEATHER_OBSERVATIONS_DIRECT_KIND, ZOOM_TIERS[-1], day),
        WEATHER_OBSERVATIONS_SCHEMA,
    ).combine_chunks()
    intended = conform_to_stream_schema(expected, WEATHER_OBSERVATIONS_SCHEMA).combine_chunks()
    if not actual.equals(intended):
        raise RuntimeError(
            "actual z13 differs from the intended merged table: "
            f"actual_rows={actual.num_rows}, intended_rows={intended.num_rows}, "
            f"actual_sha256={_table_sha256(actual)}, intended_sha256={_table_sha256(intended)}"
        )

    actual_rows = _rows_by_grain(actual, day=day, label="actual")
    incoming_rows = _rows_by_grain(incoming, day=day, label="incoming")
    source_mismatches: list[str] = []
    for grain, incoming_row in incoming_rows.items():
        actual_row = actual_rows.get(grain)
        if actual_row is None:
            source_mismatches.extend(_sample_grains({grain}))
        else:
            mismatched_columns = [
                column for column in WEATHER_OBSERVATIONS_SOURCE_COLUMNS if actual_row[column] != incoming_row[column]
            ]
            if mismatched_columns:
                lat, lon, observed_at = grain
                source_mismatches.append(f"{lat},{lon}@{observed_at.isoformat()}:{','.join(mismatched_columns)}")
        if len(source_mismatches) >= VERIFICATION_MISMATCH_SAMPLE:
            break
    if source_mismatches:
        raise RuntimeError(f"actual z13 does not carry every incoming source field: {source_mismatches}")
    return ForwardContentVerification(actual_rows=actual.num_rows, incoming_rows_verified=incoming.num_rows)


def _result_after_failure(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    *,
    day: date,
    outcome: str,
    attempts: int,
    table: pa.Table,
    adapter: DirectWeatherObservationsForwardAdapter,
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
        merged_rows=0 if merge is None else merge.table.num_rows,
        actual_z13_rows=0,
        incoming_rows_verified=0,
        parts=parts,
        rows=rows,
        written_bytes=written_bytes,
        detail=detail,
    )


async def _publish_day(  # noqa: PLR0913 - one bounded lane-day state machine
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    table: pa.Table,
    run_id: str,
    max_day_attempts: int,
    retry_base_seconds: float,
    retry_max_seconds: float,
    contention_timeout_seconds: float,
    availability_storage: AvailabilityStorage | None = None,
    availability: AvailabilityExtensionTally | None = None,
) -> ForwardDayResult:
    """Publish one day through the shared lock/finalizer and verify its physical z13 content."""
    adapter = DirectWeatherObservationsForwardAdapter(table)
    lane = replace(LANE_REGISTRY[WEATHER_OBSERVATIONS_STREAM], adapter=cast("LaneAdapter", adapter))
    failed_attempts = 0
    attempts = 0
    contention_started = time.monotonic()
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
        tier_statuses: dict[int, str] | None = None
        content: ForwardContentVerification | None = None
        if outcome == "written":
            try:
                tier_statuses = _tier_statuses(store, day)
                incomplete_tiers = {zoom: status for zoom, status in tier_statuses.items() if status != "data"}
                if incomplete_tiers:
                    raise RuntimeError(f"completion checkpoint is not data at every tier: {incomplete_tiers}")
                if adapter.merge is None:
                    raise RuntimeError("weather-observations adapter reported written without an intended merge")
                content = _verify_actual_z13(store, day=day, expected=adapter.merge.table, incoming=table)
            except Exception as error:
                outcome = "raised"
                detail = f"post-write verification failed: {type(error).__name__}: {error}"
        emit(
            "weather_observations_forward_attempt",
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
                raise RuntimeError("weather-observations publication passed without merge/content evidence")
            return ForwardDayResult(
                day=day,
                outcome=outcome,
                attempts=attempts,
                incoming_rows=adapter.merge.incoming_rows,
                existing_rows=adapter.merge.existing_rows,
                added_rows=adapter.merge.added_rows,
                updated_rows=adapter.merge.updated_rows,
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
            await asyncio.sleep(min(CONTENTION_POLL_SECONDS, contention_timeout_seconds - waited))
            continue
        if outcome == "raised":
            failed_attempts += 1
            retryable = not detail or "DirectWeatherObservationsError" not in detail
            if retryable and failed_attempts < max_day_attempts:
                delay = min(retry_max_seconds, retry_base_seconds * (2 ** (failed_attempts - 1)))
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
    """Fetch one current-conditions poll and durably merge every day it touched, bounded by the time budget."""
    _validate_args(args)
    deadline = time.monotonic() + args.time_budget_seconds
    points = weather_sample_points(args.bbox)

    fetched_at = datetime.now(UTC)
    run_id = args.run_id or f"weather-observations-direct-forward-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}"
    availability = AvailabilityExtensionTally()
    async with upstream_client(OPEN_METEO_BOUNDS) as client:
        poll = await poll_current_conditions(client, points, now=fetched_at)

    all_tables = direct_weather_observation_tables(poll.observations, ingested_at=fetched_at)
    tables = _newest_day_buckets(all_tables, max_days=args.max_days)
    emit(
        "weather_observations_forward_fetch",
        run_id=run_id,
        fetched_at=fetched_at.isoformat(),
        points_sampled=poll.points_sampled,
        points_unavailable=poll.unavailable_points,
        observations_written=len(poll.observations),
        days_seen=[day.isoformat() for day in all_tables],
        days_selected=[day.isoformat() for day in tables],
    )
    if not tables:
        emit(
            "weather_observations_forward_complete",
            run_id=run_id,
            outcome="no_writable_observations",
            days=0,
            rows_added=0,
            rows_updated=0,
            bytes=0,
            **availability.to_summary(),
        )
        return 0

    credentials = settings.require_object_store()
    store = ObjectStore(BotoObjectStoreBackend.from_credentials(credentials), prefix=settings.object_store_prefix)
    availability_storage = BotoAvailabilityStorage.from_settings()
    database_url = settings.require_local_source_loader_database_url()
    results: list[ForwardDayResult] = []
    async with local_source_loader_session(database_url) as session:
        for day, table in tables.items():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                emit(
                    "weather_observations_forward_checkpoint",
                    run_id=run_id,
                    day=day.isoformat(),
                    outcome="time_budget_exhausted",
                    detail=f"{args.time_budget_seconds:g}s time budget spent before this day was attempted",
                )
                results.append(
                    ForwardDayResult(
                        day=day,
                        outcome="time_budget_exhausted",
                        attempts=0,
                        incoming_rows=table.num_rows,
                        existing_rows=0,
                        added_rows=0,
                        updated_rows=0,
                        merged_rows=0,
                        actual_z13_rows=0,
                        incoming_rows_verified=0,
                        parts=0,
                        rows=0,
                        written_bytes=0,
                        detail="time budget exhausted before this day was attempted",
                    )
                )
                continue
            result = await _publish_day(
                session,
                store,
                day=day,
                table=table,
                run_id=run_id,
                max_day_attempts=args.retry_attempts,
                retry_base_seconds=args.retry_base_seconds,
                retry_max_seconds=args.retry_max_seconds,
                contention_timeout_seconds=min(args.contention_timeout_seconds, max(remaining, 0.0)),
                availability_storage=availability_storage,
                availability=availability,
            )
            results.append(result)
            emit(
                "weather_observations_forward_checkpoint",
                run_id=run_id,
                namespace=f"layer={WEATHER_OBSERVATIONS_STREAM}/kind={WEATHER_OBSERVATIONS_DIRECT_KIND}",
                day=day.isoformat(),
                outcome=result.outcome,
                attempts=result.attempts,
                incoming_rows=result.incoming_rows,
                existing_rows=result.existing_rows,
                added_rows=result.added_rows,
                updated_rows=result.updated_rows,
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
        "weather_observations_forward_complete",
        run_id=run_id,
        outcome="complete" if complete else "incomplete",
        days=len(results),
        outcomes=dict(sorted(outcomes.items())),
        incoming_rows=sum(result.incoming_rows for result in results),
        rows_added=sum(result.added_rows for result in results),
        rows_updated=sum(result.updated_rows for result in results),
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
        emit("weather_observations_forward_failed", error_type=type(error).__name__, detail=str(error))
        return 1


__all__ = [
    "WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS",
    "WEATHER_OBSERVATIONS_MAX_DAYS",
    "ForwardDayResult",
    "WeatherObservationsForwardConfigError",
    "main",
    "parser",
    "run",
]
