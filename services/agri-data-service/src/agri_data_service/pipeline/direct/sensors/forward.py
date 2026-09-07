"""Poll NOAA NWS ground stations once and durably merge every day the rolling window touched.

THE CLI SHAPE FOLLOWS `climate/forward.py` / `soil/forward.py` / `weather_observations/forward.py` --
`--max-days`, `--time-budget-seconds`, `--run-id`, the bounded retry and contention knobs -- because
that is the contract a reviewer expects of every lane under this track. What `--max-days` MEANS
follows `weather_observations`, not `climate`/`soil`: there is no settled-day backlog to walk by
date (see `source.py`'s module docstring for why the floor is rolling, not fixed), so it caps how
many of the day buckets ONE poll produced are actually published, never a backlog depth.

`SENSORS_MAX_DAYS = NWS_OBSERVATION_RETENTION.days + 1` -- SEVEN, not a guessed round number. A
half-open `[now - 6 days, now)` window can straddle at most seven distinct calendar dates (six full
days plus the partial day at either end), so that is the most days one poll could ever need to
publish; the `+ 1` is derived from the SAME constant `source.py` builds the fetch window from, so the
two can never silently drift apart. The default equals the ceiling, matching
`weather_observations`' own reasoning: a day that ages out of NWS's rolling retention before the
next poll runs is gone from the source forever (`ingest/sensors.py:94-96`), and unlike a settled
archive fetch, asking for the whole window here costs no extra HTTP requests -- `observation_url`
issues ONE request per station whether `window` is `None` or set, so there is no per-day request
budget to protect by publishing fewer days.

THE PUBLISH LOOP FOLLOWS `pipeline/parquet/water_gauges_forward.py` / `weather_observations/forward.py`
-- merge, verify, retry, emit -- because this lane's incremental-accumulation shape is theirs, not
climate's. See `adapter.py`'s module docstring for why the MERGE unit differs (a whole station-day
block, not one grain) and why that changes what post-write verification can honestly claim: this
module's `_verify_actual_z13` checks physical z13 content against `adapter.merge.table` (the intended
merge) ALONE, not against every field of the raw incoming poll the way
`weather_observations/forward.py::_verify_actual_z13` does. The weather-observations check is valid
there because that lane's merge always accepts every incoming grain; this lane's merge deliberately
DISCARDS an incoming station-day block whose report is older than what is already published (see
`adapter.py`, "stale"), so asserting every incoming row survived into `actual` would fail on exactly
the rows the merge was correct to drop. Checking `actual == adapter.merge.table` byte-for-byte is the
honest, equally strong claim: it proves the write reflects PRECISELY what the merge computed, without
mischaracterizing an intentional discard as a lost row.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
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
from agri_data_service.ingest.mtbs import inline_bbox_value
from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION, OBSERVATION_BOUNDS
from agri_data_service.pipeline.direct import (
    COMPLETE,
    INCOMPLETE,
    LANE_DAY_OUTCOMES,
    NO_SUCH_DEFECT,
    NO_WRITABLE_OBSERVATIONS,
    REFUSE_UNCONFIGURED_BBOX,
    SKIP_AND_COUNT,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.sensors.adapter import SENSORS_DIRECT_KIND, DirectSensorsForwardAdapter
from agri_data_service.pipeline.direct.sensors.rows import direct_sensor_tables
from agri_data_service.pipeline.direct.sensors.source import (
    SENSORS_DEFAULT_MAX_RECORDS,
    SENSORS_MAX_MAX_RECORDS,
    SENSORS_MIN_MAX_RECORDS,
    poll_recent_sensor_readings,
)
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore, conform_to_stream_schema
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter

#: Derived, not guessed -- see module docstring. A half-open six-day rolling window can straddle at
#: most seven distinct calendar dates.
SENSORS_MAX_DAYS: Final = NWS_OBSERVATION_RETENTION.days + 1
#: Defaults to the full ceiling for the same no-extra-cost, cannot-lose-data reasoning
#: `weather_observations` states for its own default.
SENSORS_DEFAULT_MAX_DAYS: Final = SENSORS_MAX_DAYS

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
#:
#: `--max-records` IS A DIFFERENT KNOB FROM `fire_detections`' `--max-records-per-day`, and the two
#: spellings are the correct outcome rather than drift: this one caps readings across the WHOLE
#: ROSTER for one poll, so its unit is a turn; that one caps records inside ONE EXACT UTC DAY, so its
#: unit is a day. Giving them one name would make a turn-scoped ceiling look day-scoped, which is how
#: an operator sets a cap ten times smaller than they meant.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="sensors",
    identity_defect=SKIP_AND_COUNT,
    geometry_defect=NO_SUCH_DEFECT,
    unconfigured_bbox=REFUSE_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {TIME_BUDGET_EXHAUSTED, NO_WRITABLE_OBSERVATIONS, COMPLETE, INCOMPLETE},
    flags_absent_on_purpose={
        "--product": "one stream; a station reading is one series per station rather than a fan-out "
        "of several products over one fetch, so there is no second product a selector could name.",
        "--max-records-per-day": "this lane polls a ROLLING WINDOW and sorts the answer into day "
        "buckets afterwards -- it never issues a per-day request -- so a per-day ceiling would bound "
        "something no request corresponds to. `--max-records` is the turn-scoped ceiling that does.",
    },
    policy_basis="A reading whose identity will not build is counted (`source.py`'s `rejected` and "
    "`dropped`, both carried on the fetch record) rather than refusing the poll, because NWS is a "
    "rolling ~6-day window: a refused turn does not retry a day, it LOSES it once the window slides "
    "past. There is no geometry to be malformed -- the station's coordinates are provenance floats, "
    "and a shape mismatch nulls them (`rows.py:93`) rather than dropping the reading, since the "
    "reading is the observation and the position is metadata about who took it.",
)
SENSORS_DEFAULT_TIME_BUDGET_SECONDS: Final = 300.0
SENSORS_MAX_TIME_BUDGET_SECONDS: Final = 900.0
STATEMENT_TIMEOUT_SECONDS: Final = 600
DEFAULT_MAX_DAY_ATTEMPTS: Final = 5
MAX_DAY_ATTEMPTS: Final = 10
DEFAULT_RETRY_BASE_SECONDS: Final = 2.0
MAX_RETRY_BASE_SECONDS: Final = 60.0
MAX_RETRY_DELAY_SECONDS: Final = 60.0
DEFAULT_CONTENTION_TIMEOUT_SECONDS: Final = 900.0
MAX_CONTENTION_TIMEOUT_SECONDS: Final = 3_600.0
CONTENTION_POLL_SECONDS: Final = 15.0


class SensorsForwardConfigError(ValueError):
    """Raised when the forward CLI is invoked with an out-of-range argument."""


@dataclass(frozen=True, slots=True)
class ForwardContentVerification:
    """Actual z13 evidence collected after the completion markers landed."""

    actual_rows: int
    merged_rows_verified: int


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
    stale_rows: int
    merged_rows: int
    actual_z13_rows: int
    merged_rows_verified: int
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
    built.add_argument("--max-days", type=int, default=SENSORS_DEFAULT_MAX_DAYS)
    built.add_argument(
        "--max-records",
        type=int,
        default=SENSORS_DEFAULT_MAX_RECORDS,
        help=(
            "ceiling on readings fetched across the whole roster "
            "(see source.py for why this is not the shared ingest ceiling)"
        ),
    )
    built.add_argument("--time-budget-seconds", type=float, default=SENSORS_DEFAULT_TIME_BUDGET_SECONDS)
    built.add_argument("--run-id", default=None)
    built.add_argument("--retry-attempts", type=int, default=DEFAULT_MAX_DAY_ATTEMPTS)
    built.add_argument("--retry-base-seconds", type=float, default=DEFAULT_RETRY_BASE_SECONDS)
    built.add_argument("--retry-max-seconds", type=float, default=MAX_RETRY_DELAY_SECONDS)
    built.add_argument("--contention-timeout-seconds", type=float, default=DEFAULT_CONTENTION_TIMEOUT_SECONDS)
    return built


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Read the operator's argv into a namespace, surviving a bbox whose first ordinate is negative.

    `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    sees it, matching `ingest/mtbs.py::main` and `burn_severity/forward.py`. Without it argparse
    reads the leading `-125` as a second flag rather than this option's value and the documented
    operator command dies with "argument --bbox: expected one argument". The helper lives in
    `ingest/mtbs.py` because that is where the trap was first paid for; every `--bbox` writer in
    `pipeline/direct` imports the one implementation rather than restating the rewrite.

    A function rather than an inline `parser().parse_args()` in `main` so a test can exercise the
    rewrite on a real argv without spawning a process: an untested guard is the next regression.
    """
    raw = list(argv) if argv is not None else sys.argv[1:]
    return parser().parse_args(inline_bbox_value(raw))


def _validate_args(args: argparse.Namespace) -> None:
    """Keep every day count, budget, retry and contention wait bounded."""
    if not 1 <= args.max_days <= SENSORS_MAX_DAYS:
        raise SensorsForwardConfigError(f"--max-days must be between 1 and {SENSORS_MAX_DAYS}")
    if not SENSORS_MIN_MAX_RECORDS <= args.max_records <= SENSORS_MAX_MAX_RECORDS:
        raise SensorsForwardConfigError(
            f"--max-records must be between {SENSORS_MIN_MAX_RECORDS} and {SENSORS_MAX_MAX_RECORDS}"
        )
    if not 0 < args.time_budget_seconds <= SENSORS_MAX_TIME_BUDGET_SECONDS:
        raise SensorsForwardConfigError(f"--time-budget-seconds must be within (0, {SENSORS_MAX_TIME_BUDGET_SECONDS}]")
    if not 1 <= args.retry_attempts <= MAX_DAY_ATTEMPTS:
        raise SensorsForwardConfigError(f"--retry-attempts must be between 1 and {MAX_DAY_ATTEMPTS}")
    if not 0 < args.retry_base_seconds <= MAX_RETRY_BASE_SECONDS:
        raise SensorsForwardConfigError(f"--retry-base-seconds must be within (0, {MAX_RETRY_BASE_SECONDS}]")
    if args.retry_max_seconds < args.retry_base_seconds:
        raise SensorsForwardConfigError("--retry-max-seconds must be at least --retry-base-seconds")
    if not 0 < args.contention_timeout_seconds <= MAX_CONTENTION_TIMEOUT_SECONDS:
        raise SensorsForwardConfigError(
            f"--contention-timeout-seconds must be within (0, {MAX_CONTENTION_TIMEOUT_SECONDS}]"
        )


def _tier_statuses(store: ObjectStore, day: date) -> dict[int, str]:
    """Read the durable completion checkpoint for every sensors zoom tier."""
    statuses: dict[int, str] = {}
    for zoom in ZOOM_TIERS:
        keys = store.list_partition_keys(SENSORS_STREAM, SENSORS_DIRECT_KIND, zoom, year=day.year, month=day.month)
        statuses[zoom] = partition_day_statuses(
            layer=SENSORS_STREAM, kind=SENSORS_DIRECT_KIND, zoom=zoom, first_day=day, last_day=day, keys=keys
        )[day]
    return statuses


def _table_sha256(table: pa.Table) -> str:
    """Hash canonical Arrow content for bounded mismatch evidence."""
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, SENSORS_SCHEMA.arrow_schema) as writer:
        writer.write_table(table.combine_chunks())
    return hashlib.sha256(sink.getvalue().to_pybytes()).hexdigest()


def _verify_actual_z13(store: ObjectStore, *, day: date, expected: pa.Table) -> ForwardContentVerification:
    """Re-read z13 and prove it equals the intended merge byte-for-byte -- see module docstring for why
    this checks the INTENDED MERGE rather than the raw incoming poll (`adapter.py`'s stale-block discard).
    """
    actual = conform_to_stream_schema(
        store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, ZOOM_TIERS[-1], day), SENSORS_SCHEMA
    ).combine_chunks()
    intended = conform_to_stream_schema(expected, SENSORS_SCHEMA).combine_chunks()
    if not actual.equals(intended):
        raise RuntimeError(
            "actual z13 differs from the intended merged table: "
            f"actual_rows={actual.num_rows}, intended_rows={intended.num_rows}, "
            f"actual_sha256={_table_sha256(actual)}, intended_sha256={_table_sha256(intended)}"
        )
    return ForwardContentVerification(actual_rows=actual.num_rows, merged_rows_verified=intended.num_rows)


def _result_after_failure(  # noqa: PLR0913 - one caller-supplied coordinate per arg
    *,
    day: date,
    outcome: str,
    attempts: int,
    table: pa.Table,
    adapter: DirectSensorsForwardAdapter,
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
        stale_rows=0 if merge is None else merge.stale_rows,
        merged_rows=0 if merge is None else merge.table.num_rows,
        actual_z13_rows=0,
        merged_rows_verified=0,
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
    adapter = DirectSensorsForwardAdapter(table)
    lane = replace(LANE_REGISTRY[SENSORS_STREAM], adapter=cast("LaneAdapter", adapter))
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
                    raise RuntimeError("sensors adapter reported written without an intended merge")
                content = _verify_actual_z13(store, day=day, expected=adapter.merge.table)
            except Exception as error:
                outcome = "raised"
                detail = f"post-write verification failed: {type(error).__name__}: {error}"
        emit(
            "sensors_forward_attempt",
            run_id=run_id,
            day=day.isoformat(),
            attempt=attempts,
            outcome=outcome,
            parts=parts,
            rows=rows,
            bytes=written_bytes,
            tier_statuses=tier_statuses,
            actual_z13_rows=None if content is None else content.actual_rows,
            merged_rows_verified=None if content is None else content.merged_rows_verified,
            detail=detail,
        )
        if outcome == "written":
            if adapter.merge is None or content is None:
                raise RuntimeError("sensors publication passed without merge/content evidence")
            return ForwardDayResult(
                day=day,
                outcome=outcome,
                attempts=attempts,
                incoming_rows=adapter.merge.incoming_rows,
                existing_rows=adapter.merge.existing_rows,
                added_rows=adapter.merge.added_rows,
                updated_rows=adapter.merge.updated_rows,
                stale_rows=adapter.merge.stale_rows,
                merged_rows=adapter.merge.table.num_rows,
                actual_z13_rows=content.actual_rows,
                merged_rows_verified=content.merged_rows_verified,
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
            retryable = not detail or "DirectSensorsError" not in detail
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
    """Fetch one rolling-window poll and durably merge every day it touched, bounded by the time budget."""
    _validate_args(args)
    deadline = time.monotonic() + args.time_budget_seconds

    fetched_at = datetime.now(UTC)
    run_id = args.run_id or f"sensors-direct-forward-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}"
    availability = AvailabilityExtensionTally()
    async with upstream_client(OBSERVATION_BOUNDS) as client:
        poll = await poll_recent_sensor_readings(client, args.bbox, now=fetched_at, max_records=args.max_records)

    all_tables = direct_sensor_tables(poll.writes)
    tables = _newest_day_buckets(all_tables, max_days=args.max_days)
    emit(
        "sensors_forward_fetch",
        run_id=run_id,
        fetched_at=fetched_at.isoformat(),
        stations_polled=poll.stations_polled,
        records_seen=poll.records_seen,
        writes_selected=len(poll.writes),
        rejected=poll.rejected,
        dropped=poll.dropped,
        days_seen=[day.isoformat() for day in all_tables],
        days_selected=[day.isoformat() for day in tables],
    )
    if not tables:
        emit(
            "sensors_forward_complete",
            run_id=run_id,
            outcome="no_writable_observations",
            days=0,
            rows_added=0,
            rows_updated=0,
            rows_stale=0,
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
                    "sensors_forward_checkpoint",
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
                        stale_rows=0,
                        merged_rows=0,
                        actual_z13_rows=0,
                        merged_rows_verified=0,
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
                "sensors_forward_checkpoint",
                run_id=run_id,
                namespace=f"layer={SENSORS_STREAM}/kind={SENSORS_DIRECT_KIND}",
                day=day.isoformat(),
                outcome=result.outcome,
                attempts=result.attempts,
                incoming_rows=result.incoming_rows,
                existing_rows=result.existing_rows,
                added_rows=result.added_rows,
                updated_rows=result.updated_rows,
                stale_rows=result.stale_rows,
                merged_rows=result.merged_rows,
                actual_z13_rows=result.actual_z13_rows,
                merged_rows_verified=result.merged_rows_verified,
                parts=result.parts,
                rows=result.rows,
                bytes=result.written_bytes,
                detail=result.detail,
            )

    outcomes = Counter(result.outcome for result in results)
    complete = len(outcomes) == 1 and outcomes["written"] == len(results)
    emit(
        "sensors_forward_complete",
        run_id=run_id,
        outcome="complete" if complete else "incomplete",
        days=len(results),
        outcomes=dict(sorted(outcomes.items())),
        incoming_rows=sum(result.incoming_rows for result in results),
        rows_added=sum(result.added_rows for result in results),
        rows_updated=sum(result.updated_rows for result in results),
        rows_stale=sum(result.stale_rows for result in results),
        merged_rows=sum(result.merged_rows for result in results),
        actual_z13_rows=sum(result.actual_z13_rows for result in results),
        merged_rows_verified=sum(result.merged_rows_verified for result in results),
        parts=sum(result.parts for result in results),
        rows=sum(result.rows for result in results),
        bytes=sum(result.written_bytes for result in results),
        **availability.to_summary(),
    )
    return 0 if all(result.outcome == "written" for result in results) else 1


async def main(argv: Sequence[str] | None = None) -> int:
    """Run the operator and emit one typed terminal fault on failure."""
    args = parse_args(argv)
    try:
        return await run(args)
    except Exception as error:
        emit("sensors_forward_failed", error_type=type(error).__name__, detail=str(error))
        return 1


__all__ = [
    "SENSORS_DEFAULT_MAX_DAYS",
    "SENSORS_MAX_DAYS",
    "ForwardDayResult",
    "SensorsForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run",
]
