"""Poll NOAA NWS ground stations once and durably merge every day the rolling window touched.

THE CLI SHAPE FOLLOWS `climate/forward.py` / `soil/forward.py` / `weather_observations/forward.py` --
`--max-days`, `--time-budget-seconds`, `--run-id`, the bounded retry and contention knobs -- because
that is the contract a reviewer expects of every lane under this track. `--max-days` caps how many
DUE days one turn sweeps, OLDEST first (the oldest due day is the next to age out of NWS retention),
and how many day buckets it publishes; a due day past the cap is reported unwritten and stays due.

`SENSORS_MAX_DAYS = NWS_OBSERVATION_RETENTION.days + 1` -- SEVEN, not a guessed round number. A
half-open `[now - 6 days, now)` window can straddle at most seven distinct calendar dates (six full
days plus the partial day at either end), so that is the most days one poll could ever need to
publish; the `+ 1` is derived from the SAME constant `source.py` builds the fetch window from, so the
two can never silently drift apart. The default equals the ceiling, matching
`weather_observations`' own reasoning: a day that ages out of NWS's rolling retention before the
next poll runs is gone from the source forever (`ingest/sensors.py:94-96`). A day `--max-days` leaves
out is not lost: it was never written, so it stays due and the next turn asks for it again. What a
turn may ASK is bounded separately, by `source.py::SensorsFetchBudget`.

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

THE BUCKET'S EXIT STATUS FAILS ONLY WHEN NO DAY WROTE. Until 2026-09-15 `run` returned 1 unless EVERY
day was `written`, so one per-day refusal failed the whole poll: two stale absence markers on
2026-09-05/06 (`adapter.py`, "governed `absent` is reconciled") made three consecutive buckets exit 1
while five of their seven days had written cleanly, and the executor's breaker held the lane for a
week over days that were never broken. `_bucket_verdict` now separates the two claims the exit code
was conflating: `outcome` stays `complete`/`incomplete` (the word `pipeline/direct/__init__.py::INCOMPLETE`
reserves for "at least one day did not settle"), while the exit code says whether the lane can write
AT ALL -- 0 when at least one day published, 1 only when none did. A partial bucket is therefore
`incomplete` at exit 0, with every unwritten day listed by outcome and detail under `unwritten`, so it
can neither pass for a clean success nor spend the lane's run on a degradation the next hourly poll
will re-attempt from the same rolling window. The breaker exists for a lane that cannot write; a lane
writing some of its days is degraded, visibly, not broken.

A TURN THAT ADVANCED A SWEEP IT COULD NOT FINISH EXITS 0. Its saved progress (`progress.py`) is
`sweep_in_progress` on the fetch and complete records, the day is `unwritten` with its budget word, and
the next due turn asks only the stations left -- so a slow NWS costs turns, never the breaker.

A TURN WITH NOTHING DUE IS AN `idempotent_noop` AT EXIT 0. The executor runs this lane hourly, but the
product is one report per station-day, so a turn only asks NWS once a day has ended and its late-report
allowance has passed (`watermark.py`). The other turns read z13 listings and markers, emit the fetch
and complete records with zero requests, and exit. See `AGENTS.md`, "Ask once per day, after the day
has ended".

THE STDOUT REPORT NOW HAS A CONSUMER.
    As of 2026-09-18 the executor (`execution/job_executor_service.py`) parses the last JSON line of
    the child's stdout into a `TurnReport` and records `days_unwritten` and the `unwritten` list on the
    completed checkpoint cursor, so a partial bucket at exit 0 is visible in the ledger. The stderr
    `sensors_forward_bucket_incomplete` event is kept as well, because stderr is teed to the log stream on
    every exit and a log reader should not need the ledger to see the same fact.
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
from agri_data_service.foundation.geography.bounding_box import inline_bbox_value
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION
from agri_data_service.pipeline.direct import (
    COMPLETE,
    IDEMPOTENT_NOOP,
    INCOMPLETE,
    LANE_DAY_OUTCOMES,
    NO_SUCH_DEFECT,
    NO_WRITABLE_OBSERVATIONS,
    REFUSE_UNCONFIGURED_BBOX,
    REQUEST_BUDGET_EXHAUSTED,
    SKIP_AND_COUNT,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.sensors.adapter import (
    SENSORS_DIRECT_KIND,
    DirectSensorsForwardAdapter,
    merge_sensors_day,
)
from agri_data_service.pipeline.direct.sensors.progress import SWEEP_PROGRESS_ROOT, SweepProgressStore
from agri_data_service.pipeline.direct.sensors.rows import direct_sensor_tables
from agri_data_service.pipeline.direct.sensors.source import (
    BUDGET_TIME,
    SENSORS_DEFAULT_MAX_RECORDS,
    SENSORS_FETCH_TIME_SHARE,
    SENSORS_MAX_MAX_RECORDS,
    SENSORS_MIN_MAX_RECORDS,
    SENSORS_OBSERVATION_BOUNDS,
    SensorsFetchBudget,
    poll_recent_sensor_readings,
)
from agri_data_service.pipeline.parquet.availability_extension import AvailabilityExtensionTally
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore, conform_to_stream_schema
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.sensors.adapter import OverturnedAbsence
    from agri_data_service.pipeline.direct.sensors.source import SensorsPollResult
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
    turn_outcomes=LANE_DAY_OUTCOMES
    | {
        TIME_BUDGET_EXHAUSTED,
        REQUEST_BUDGET_EXHAUSTED,
        NO_WRITABLE_OBSERVATIONS,
        IDEMPOTENT_NOOP,
        COMPLETE,
        INCOMPLETE,
    },
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
    #: The governed absence this day's poll overturned, when it did; carried on failures too, since the
    #: marker is already gone by the time a later write can fail (`adapter.py`).
    absence_overturned: OverturnedAbsence | None = None
    #: True for a day that actually reached the publish loop (a real `_publish_day` write attempt, or a
    #: `time_budget_exhausted` abandoned mid-publish-loop); False for a day `_due_days_not_published`
    #: reports without ever building a table to write. A non-written result with this True is a PUBLISH
    #: FAULT -- a day the fetch phase handed over cleanly but the write path itself could not settle --
    #: and `_bucket_verdict` must never let `progressed` (a healthy FETCH-phase sweep advancing) paper
    #: over it.
    publish_attempted: bool = False


@dataclass(frozen=True, slots=True)
class ForwardBucketVerdict:
    """What one poll's day publications add up to, and the exit status that sum earns."""

    outcome: str
    exit_code: int
    days_written: int
    unwritten: tuple[ForwardDayResult, ...]
    absences_overturned: tuple[date, ...]

    @property
    def days_unwritten(self) -> int:
        """How many days this poll selected but did not publish, whatever the reason."""
        return len(self.unwritten)


def _bucket_verdict(
    results: Sequence[ForwardDayResult], *, progressed: bool = False, excused: frozenset[date] = frozenset()
) -> ForwardBucketVerdict:
    """Exit 1 only when NO day wrote, no sweep advanced, and some unwritten day is not `excused`.

    `excused` days were not a failure to write: NWS had nothing writable, or `--max-days` deferred them.
    Some written and some not is `incomplete` at exit 0 -- see module docstring.

    `progressed` excuses only the FETCH phase's own stall (a sweep that advanced and saved but could not
    finish this turn); it must never excuse a PUBLISH FAULT (`ForwardDayResult.publish_attempted`), a day
    the fetch phase handed over cleanly that then failed to write (raised, contended past its timeout, or
    ran out of time budget mid-publish-loop). Review 2026-10-03: before this, a turn that both advanced an
    unrelated day's sweep AND failed to publish a different, already-fetched day exited 0 on the publish
    fault alone.
    """
    unwritten = tuple(result for result in results if result.outcome != "written")
    days_written = len(results) - len(unwritten)
    stuck = any(result.day not in excused for result in unwritten)
    publish_faulted = any(result.publish_attempted for result in unwritten)
    return ForwardBucketVerdict(
        outcome=COMPLETE if results and not unwritten else INCOMPLETE,
        exit_code=0 if days_written or (not publish_faulted and (progressed or not stuck)) else 1,
        days_written=days_written,
        unwritten=unwritten,
        absences_overturned=tuple(result.day for result in results if result.absence_overturned is not None),
    )


def _unwritten_event(result: ForwardDayResult) -> dict[str, object]:
    """Name one unpublished day by what stopped it, so the terminal report needs no per-day log walk."""
    return {
        "day": result.day.isoformat(),
        "outcome": result.outcome,
        "attempts": result.attempts,
        "incoming_rows": result.incoming_rows,
        "detail": result.detail,
    }


def _emit_bucket_incomplete(verdict: ForwardBucketVerdict, *, run_id: str) -> None:
    """Put a partial bucket on stderr, the only stream the executor tees today -- see module docstring."""
    if not verdict.unwritten:
        return
    print(
        json.dumps(
            {
                "event": "sensors_forward_bucket_incomplete",
                "run_id": run_id,
                "outcome": verdict.outcome,
                "exit_code": verdict.exit_code,
                "days_written": verdict.days_written,
                "days_unwritten": verdict.days_unwritten,
                "unwritten": [_unwritten_event(result) for result in verdict.unwritten],
            },
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )


def emit(event: str, **fields: object) -> None:
    """Write one stable JSON progress record without exposing credentials."""
    print(json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True), flush=True)


def _publication_order(
    tables: Mapping[date, pa.Table], *, swept: frozenset[date], max_days: int
) -> dict[date, pa.Table]:
    """Swept due days first, then gap-walked done days, each oldest first, capped at `max_days`.

    A due day has nothing published yet; a gap-walked day already serves every other station.
    """
    ordered = sorted(tables, key=lambda day: (day not in swept, day))[:max_days]
    return {day: tables[day] for day in ordered}


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
    `foundation/geography/bounding_box.py` (extracted 2026-09-18 from `ingest/mtbs.py`, where the
    trap was first paid for); every `--bbox` writer in `pipeline/direct` imports the one
    implementation rather than restating the rewrite.

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
        absence_overturned=adapter.absence_overturned,
        publish_attempted=True,
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
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
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
                now=clock,
                today=clock().date(),
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
            absence_overturned=None if adapter.absence_overturned is None else adapter.absence_overturned.as_event(),
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
                absence_overturned=adapter.absence_overturned,
                publish_attempted=True,
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


def _unwritten_day(day: date, *, outcome: str, detail: str) -> ForwardDayResult:
    """A due day this turn did not attempt, with every write counter zeroed."""
    return ForwardDayResult(
        day=day,
        outcome=outcome,
        attempts=0,
        incoming_rows=0,
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
        detail=detail,
    )


def _due_days_not_published(
    poll: SensorsPollResult, *, selected: frozenset[date], max_days: int
) -> list[ForwardDayResult]:
    """Every due day this turn will not publish, named by why, so no due day vanishes from the turn report."""
    budget_word = TIME_BUDGET_EXHAUSTED if poll.budget_exhausted == BUDGET_TIME else REQUEST_BUDGET_EXHAUSTED
    saved = {entry.day: entry for entry in poll.sweep_in_progress}
    results: list[ForwardDayResult] = []
    for day in poll.days_unswept:
        entry = saved.get(day)
        asked = (
            "no new station was asked for it this turn"
            if entry is None
            else f"{entry.stations_asked} of {entry.stations_total} stations asked and saved; the next due turn "
            "asks only the rest"
        )
        results.append(
            _unwritten_day(
                day,
                outcome=budget_word,
                detail=f"the turn's {poll.budget_exhausted} budget ran out before every station was asked for this "
                f"day ({asked}); nothing was written and the day stays due",
            )
        )
    results.extend(
        _unwritten_day(
            day,
            # `--max-days` bounds the turn's request quota in whole days (one request per roster station).
            outcome=REQUEST_BUDGET_EXHAUSTED,
            detail=f"--max-days {max_days} let this turn sweep only the {max_days} oldest due day(s); this day "
            "was not asked and stays due",
        )
        for day in poll.days_deferred
    )
    results.extend(
        _unwritten_day(
            day,
            outcome=NO_WRITABLE_OBSERVATIONS,
            detail="every roster station was asked; NWS returned no writable report for this day, so nothing was "
            "written and the day stays due",
        )
        for day in poll.days_swept
        if day not in selected
    )
    return sorted(results, key=lambda result: result.day)


def _unchanged_on_z13(store: ObjectStore, *, day: date, table: pa.Table) -> bool:
    """True when merging `table` into a done day would leave z13 exactly as it is, so it is not re-published.

    A read or merge that raises answers False: the ordinary publish path then surfaces the same fault
    with its retry and its per-day report, rather than this check hiding it.
    """
    try:
        existing = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, ZOOM_TIERS[-1], day)
        merged = merge_sensors_day(existing, table, day=day)
    except Exception:
        return False
    return bool(merged.table.equals(existing))


def _emit_fetch(
    run_id: str,
    poll: SensorsPollResult,
    *,
    seen: Sequence[date],
    selected: Sequence[date],
    unchanged: Sequence[date],
) -> None:
    """Report what this turn asked NWS and what it will publish, before any day is written."""
    plan = poll.plan
    emit(
        "sensors_forward_fetch",
        run_id=run_id,
        fetched_at=poll.fetched_at.isoformat(),
        gate="nothing_due" if plan.nothing_due else "due",
        days_due=[day.isoformat() for day in plan.due_days],
        days_done=len(plan.done_days),
        days_waiting=[day.isoformat() for day in plan.waiting_days],
        days_blocked=[day.isoformat() for day in plan.blocked_days],
        days_swept=[day.isoformat() for day in poll.days_swept],
        days_unswept=[day.isoformat() for day in poll.days_unswept],
        days_deferred=[day.isoformat() for day in poll.days_deferred],
        days_resumed=[day.isoformat() for day in poll.days_resumed],
        sweep_in_progress=[entry.as_event() for entry in poll.sweep_in_progress],
        progress_discarded=[{"day": day.isoformat(), "reason": reason} for day, reason in poll.progress_discarded],
        days_backfilled=[day.isoformat() for day in poll.days_backfilled],
        days_unchanged=[day.isoformat() for day in unchanged],
        days_unreadable=poll.days_unreadable,
        stations_polled=poll.stations_polled,
        stations_gap_walked=poll.stations_gap_walked,
        stations_deferred=poll.stations_deferred,
        stations_unavailable=poll.stations_unavailable,
        roster_states_unavailable=list(poll.roster_states_unavailable),
        requests=poll.requests,
        bytes_in=poll.bytes_in,
        budget_exhausted=poll.budget_exhausted,
        records_seen=poll.records_seen,
        writes_selected=len(poll.writes),
        rejected=poll.rejected,
        dropped=poll.dropped,
        days_seen=[day.isoformat() for day in seen],
        days_selected=[day.isoformat() for day in selected],
    )


def _emit_nothing_written(run_id: str, *, outcome: str, availability: AvailabilityExtensionTally) -> None:
    """The terminal record of a turn that wrote nothing and owes nothing: exit 0."""
    emit(
        "sensors_forward_complete",
        run_id=run_id,
        outcome=outcome,
        days=0,
        rows_added=0,
        rows_updated=0,
        rows_stale=0,
        bytes=0,
        exit_code=0,
        days_written=0,
        days_unwritten=0,
        unwritten=[],
        absences_overturned=[],
        **availability.to_summary(),
    )


async def run(args: argparse.Namespace, *, clock: Callable[[], datetime] | None = None) -> int:
    """Ask NWS only for the days this turn owes, then durably merge them, bounded by the time budget.

    `clock` is the turn's wall clock (UTC); tests pass a fixed one. Monotonic budgets stay real.
    """
    _validate_args(args)
    now_of = clock if clock is not None else (lambda: datetime.now(UTC))
    started = time.monotonic()
    deadline = started + args.time_budget_seconds

    fetched_at = now_of()
    run_id = args.run_id or f"sensors-direct-forward-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}"
    availability = AvailabilityExtensionTally()
    # Opened before the poll: the day plan is read from z13 (`watermark.py`), so a run with no
    # object-store credentials fails before it calls NWS at all.
    credentials = settings.require_object_store()
    backend = BotoObjectStoreBackend.from_credentials(credentials)
    store = ObjectStore(backend, prefix=settings.object_store_prefix)
    progress = SweepProgressStore(backend, root_key=store.key_for(SWEEP_PROGRESS_ROOT))
    budget = SensorsFetchBudget(deadline=started + args.time_budget_seconds * SENSORS_FETCH_TIME_SHARE)
    async with upstream_client(SENSORS_OBSERVATION_BOUNDS) as client:
        poll = await poll_recent_sensor_readings(
            client,
            args.bbox,
            now=fetched_at,
            max_records=args.max_records,
            store=store,
            budget=budget,
            progress=progress,
            max_days=args.max_days,
        )

    all_tables = direct_sensor_tables(poll.writes)
    publishable = {day: table for day, table in all_tables.items() if day in poll.publishable_days}
    swept = frozenset(poll.days_swept)
    backfilled_only = frozenset(poll.days_backfilled) - swept
    unchanged = sorted(
        day
        for day, table in publishable.items()
        if day in backfilled_only and _unchanged_on_z13(store, day=day, table=table)
    )
    tables = _publication_order(
        {day: table for day, table in publishable.items() if day not in unchanged}, swept=swept, max_days=args.max_days
    )
    _emit_fetch(run_id, poll, seen=list(all_tables), selected=list(tables), unchanged=unchanged)
    if poll.plan.nothing_due:
        _emit_nothing_written(run_id, outcome=IDEMPOTENT_NOOP, availability=availability)
        return 0

    results = _due_days_not_published(poll, selected=frozenset(tables), max_days=args.max_days)

    if tables:
        availability_storage = BotoAvailabilityStorage.from_settings()
        database_url = settings.require_local_source_loader_database_url()
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
                            publish_attempted=True,
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
                    clock=now_of,
                )
                results.append(result)
                if result.outcome == "written" and day in swept:
                    progress.clear(day)
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
                    absence_overturned=None
                    if result.absence_overturned is None
                    else result.absence_overturned.as_event(),
                )

    outcomes = Counter(result.outcome for result in results)
    excused = frozenset(poll.days_deferred) | (frozenset(poll.days_swept) - frozenset(tables))
    verdict = _bucket_verdict(results, progressed=bool(poll.sweep_in_progress), excused=excused)
    emit(
        "sensors_forward_complete",
        run_id=run_id,
        outcome=verdict.outcome,
        sweep_in_progress=[entry.as_event() for entry in poll.sweep_in_progress],
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
        exit_code=verdict.exit_code,
        days_written=verdict.days_written,
        days_unwritten=verdict.days_unwritten,
        unwritten=[_unwritten_event(result) for result in verdict.unwritten],
        absences_overturned=[day.isoformat() for day in verdict.absences_overturned],
        **availability.to_summary(),
    )
    _emit_bucket_incomplete(verdict, run_id=run_id)
    return verdict.exit_code


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
    "ForwardBucketVerdict",
    "ForwardDayResult",
    "SensorsForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run",
]
