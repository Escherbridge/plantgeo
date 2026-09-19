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

THE BUCKET'S EXIT STATUS FAILS ONLY WHEN NO DAY WROTE. Until 2026-09-15 `run` returned 1 unless EVERY
day was `written`, so one per-day refusal failed the whole poll -- the construct that, on
`sensors-direct-forward`, turned two stale absence markers into three consecutive exit-1 buckets and a
week-long breaker hold over days that were never broken (`sensors/forward.py`). `_bucket_verdict` now
separates the two claims the exit code was conflating: `outcome` stays `complete`/`incomplete` -- the
word `pipeline/direct/__init__.py::INCOMPLETE` reserves for a monitor,
and as of 2026-09-18 the executor (`execution/job_executor_service.py`) parses the last JSON line of the child's
stdout into a `TurnReport` and records `days_unwritten` and the `unwritten` list on the completed checkpoint cursor,
so a partial bucket at exit 0 is visible in the ledger; the stderr `weather_observations_forward_bucket_incomplete`
event is kept
because stderr is teed to the log stream on every exit -- while the exit code says whether the lane can write AT ALL:
0 when at least one day published, 1 only when none did. A partial bucket is therefore `incomplete` at
exit 0, with every unwritten day listed by outcome and detail under `unwritten`, and because exit 0
would otherwise leave the degradation invisible, `_report_bucket_incomplete` ALSO emits
`weather_observations_forward_bucket_incomplete` on stderr with the same list. A partial bucket thus
neither passes for a clean success nor spends the lane's run on a degradation the next poll will
re-attempt. THAT RE-ATTEMPT IS WHAT MAKES EXIT 0 SOUND: this lane's at-most-two buckets are today and
    (For yesterday's bucket the re-offer holds only within MAX_OBSERVATION_AGE of UTC midnight; a
    yesterday-day refused after that window is not re-offered, and exit 1 never preserved it either --
    there is no archive endpoint, so the loss is the source's, not this rule's.)
yesterday, and every poll re-buckets the same rolling instant, so an unwritten day is re-selected
automatically. The rule must not be lifted into a lane whose unwritten day is NOT re-offered by its next
turn. The breaker exists for a lane that cannot write; a lane writing some of its days is degraded,
visibly, not broken.

THE RE-OFFER IS WHAT `recovery.py` REPAIRS WHEN IT DOES NOT HOLD. The rule above rests on the next
poll re-bucketing the same rolling instant -- but it re-buckets the CURRENT instant, not the one the
failed write held, so the readings a failed bucket carried are gone even though the day comes back.
Every turn therefore reparses the retained provider responses of the days it selected that carry no
complete publication, BEFORE its own checkpoints overwrite them, and merges those readings into the
same bucket (`_days_owed_a_recovery`, `_recover_owed_days`). The merge is by grain, so recovering a
day the current poll also answered is an append of what is missing, never a second claim about it.
`--recover-day` is the operator's version for a day this turn's poll does not name at all.
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
from agri_data_service.ingest.open_meteo import OPEN_METEO_BOUNDS
from agri_data_service.pipeline.direct import (
    COMPLETE,
    INCOMPLETE,
    LANE_DAY_OUTCOMES,
    NO_SUCH_DEFECT,
    NO_WRITABLE_OBSERVATIONS,
    REFUSE_UNCONFIGURED_BBOX,
    REFUSE_WHOLE_RELEASE,
    TIME_BUDGET_EXHAUSTED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.weather_observations.adapter import (
    WEATHER_OBSERVATIONS_DIRECT_KIND,
    DirectWeatherObservationsForwardAdapter,
)
from agri_data_service.pipeline.direct.weather_observations.recovery import (
    WeatherCheckpointReport,
    checkpoint_current_poll,
    recover_weather_day,
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
from agri_data_service.pipeline.parquet.source_checkpoint import CHECKPOINT_MAX_AGE, SourceResponseCheckpoints
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_GRAIN,
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.weather_observations.adapter import OverturnedAbsence
    from agri_data_service.pipeline.direct.weather_observations.recovery import WeatherRecoveryReport
    from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation
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

#: What this writer promises about its own failure policy, CLI surface and reported words; see
#: `pipeline/direct/__init__.py` for the axes and `tests/direct/test_direct_writer_contract.py` for
#: the table all eleven are read as.
WRITER_CONTRACT: Final = DirectWriterContract(
    slug="weather-observations",
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=NO_SUCH_DEFECT,
    unconfigured_bbox=REFUSE_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {TIME_BUDGET_EXHAUSTED, NO_WRITABLE_OBSERVATIONS, COMPLETE, INCOMPLETE},
    flags_absent_on_purpose={
        "--product": "one stream; each sample point carries one current-conditions record, so there "
        "is no second product a selector could name.",
        "--max-records": "the record count is the sample-point count, computed from the bbox and a "
        "fixed spacing by `support.py::weather_sample_points`. `--bbox` is therefore already the "
        "record-count knob, and a second one could only disagree with it.",
        "--max-records-per-day": "same reason as `--max-records`; the poll is a current-conditions "
        "snapshot sorted into day buckets afterwards, so no request is scoped to a day.",
    },
    policy_basis="Unlike its closest sibling `sensors/`, which counts a bad reading and publishes, a "
    "point whose `observedAt` will not parse REFUSES here (`rows.py:70`). The reason is the support: "
    "this lane's population is a computed grid where every point is expected to answer, so a dropped "
    "point silently shrinks the grid a day was written against -- the same comparability argument the "
    "lattice writers make. `sensors/` polls a roster it does not control, where a station going quiet "
    "is ordinary. There is no geometry column on either.",
)
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
    #: The governed absence this day's poll overturned, when it did; carried on failures too, since the
    #: marker is already gone by the time a later write can fail (`adapter.py`).
    absence_overturned: OverturnedAbsence | None = None
    #: Whether every row this day's bucket was built from has its parser input retained and read
    #: back. `None` means the question was not asked (no retention step ran for this day). On an
    #: UNWRITTEN day this is the difference between a bucket the next turn can still recover and one
    #: the rolling feed has lost for good, so it travels out on `_unwritten_event`.
    source_retained: bool | None = None


@dataclass(frozen=True, slots=True)
class ForwardBucketVerdict:
    """What one poll's day publications add up to, and the exit status that sum earns."""

    outcome: str
    exit_code: int
    days_written: int
    unwritten: tuple[ForwardDayResult, ...]
    absences_overturned: tuple[date, ...]
    #: Accepted points whose parser input could not be proven retained. NOT folded into the day
    #: counts: a retention failure is a failure of the lane's ONLY retry, not of a publication.
    retention_failed: int = 0

    @property
    def days_unwritten(self) -> int:
        """How many days this poll selected but did not publish, whatever the reason."""
        return len(self.unwritten)


def _bucket_verdict(
    results: Sequence[ForwardDayResult], *, retention_failed: int = 0
) -> ForwardBucketVerdict:
    """Exit 1 only when NO day wrote; some written and some not is `incomplete` at exit 0 -- see module docstring.

    A RETENTION FAILURE ALSO COSTS THE TURN ITS `complete`. Until 2026-09-19 the retention counters
    were advisory: a poll whose every checkpoint failed published exactly as before and reported a
    clean success, which made writing the checkpoint before the first write buy nothing. It is not
    an exit-1 refusal, because refusing to publish rows already held in memory over a failed BACKUP
    of them destroys the very data the backup exists to protect -- and because exit 1 is this lane's
    breaker (module docstring). It is `incomplete`, on stderr, and named per unwritten day.
    """
    unwritten = tuple(result for result in results if result.outcome != "written")
    days_written = len(results) - len(unwritten)
    return ForwardBucketVerdict(
        outcome=COMPLETE if results and not unwritten and not retention_failed else INCOMPLETE,
        exit_code=0 if days_written else 1,
        days_written=days_written,
        unwritten=unwritten,
        absences_overturned=tuple(result.day for result in results if result.absence_overturned is not None),
        retention_failed=retention_failed,
    )


def _unwritten_event(result: ForwardDayResult) -> dict[str, object]:
    """Name one unpublished day by what stopped it, so the terminal report needs no per-day log walk.

    `source_retained` appears only when retention was measured for this day -- absent means the
    question was not asked, which is honestly different from asked and answered no.
    """
    event: dict[str, object] = {
        "day": result.day.isoformat(),
        "outcome": result.outcome,
        "attempts": result.attempts,
        "incoming_rows": result.incoming_rows,
        "detail": result.detail,
    }
    if result.source_retained is not None:
        event["source_retained"] = result.source_retained
    return event


def _report_bucket_incomplete(run_id: str, verdict: ForwardBucketVerdict) -> None:
    """Announce every unwritten day on stderr, where an exit-0 partial bucket is otherwise silent (module docstring)."""
    if not verdict.unwritten and not verdict.retention_failed:
        return
    print(
        json.dumps(
            {
                "event": "weather_observations_forward_bucket_incomplete",
                "layer": WEATHER_OBSERVATIONS_STREAM,
                "run_id": run_id,
                "outcome": verdict.outcome,
                "exit_code": verdict.exit_code,
                "days_written": verdict.days_written,
                "days_unwritten": verdict.days_unwritten,
                "unwritten": [_unwritten_event(result) for result in verdict.unwritten],
                "source_checkpoints_failed": verdict.retention_failed,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )


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
    built.add_argument(
        "--recover-day",
        type=date.fromisoformat,
        default=None,
        help=(
            "ISO date of ONE past day to republish from RETAINED PROVIDER RESPONSES instead of "
            f"polling. This feed keeps no archive, so recovery is only possible within the "
            f"{CHECKPOINT_MAX_AGE.days}-day checkpoint retention window, and only for readings a "
            "previous turn accepted and retained. The turn makes no source request at all, publishes "
            "whatever part of the grid was retained through the ordinary lane-day lock and merge, "
            "and exits 1 naming the day when nothing is retained for it."
        ),
    )
    return built


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Read the operator's argv into a namespace, surviving a bbox whose first ordinate is negative.

    `inline_bbox_value` rewrites `--bbox -125,42,...` to `--bbox=-125,42,...` before argparse ever
    sees it. Without it argparse reads the leading `-125` as a second flag and the documented
    operator command dies with "argument --bbox: expected one argument" -- and EVERY bbox this
    warehouse is configured with starts with a negative longitude, so the failure was total rather
    than occasional.

    This writer accepted `--bbox` without the rewrite until 2026-09-07: `main` called
    `parser().parse_args()` with no argv and no normalisation, alone among the seven `--bbox`
    writers. Matches `sensors/forward.py::parse_args` exactly, down to being a function rather than
    an inline call in `main`, so a test can exercise the rewrite on a real argv without spawning a
    process -- an untested guard is the next regression.
    """
    raw = list(argv) if argv is not None else sys.argv[1:]
    return parser().parse_args(inline_bbox_value(raw))


def _validate_args(args: argparse.Namespace, *, today: date | None = None) -> None:
    """Keep every day count, budget, retry and contention wait bounded."""
    recover_day = getattr(args, "recover_day", None)
    if recover_day is not None:
        named_today = today if today is not None else datetime.now(UTC).date()
        if recover_day > named_today:
            raise WeatherObservationsForwardConfigError(
                f"--recover-day {recover_day.isoformat()} is in the future; this feed has no forecast to recover"
            )
        if recover_day < named_today - CHECKPOINT_MAX_AGE:
            raise WeatherObservationsForwardConfigError(
                f"--recover-day {recover_day.isoformat()} is older than the {CHECKPOINT_MAX_AGE.days}-day "
                "source-checkpoint retention window, so no retained response for it can still be read"
            )
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
        absence_overturned=adapter.absence_overturned,
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
            absence_overturned=None if adapter.absence_overturned is None else adapter.absence_overturned.as_event(),
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
                absence_overturned=adapter.absence_overturned,
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


def _days_owed_a_recovery(store: ObjectStore, days: Sequence[date]) -> tuple[date, ...]:
    """Of the days this poll named, the ones no complete publication exists for -- the only repairable ones.

    A day already `data` at every tier is NOT probed. Its earlier instants could in principle still
    be missing, but nothing this writer can read proves that, and guessing would spend 150 object
    reads per poll to re-merge readings that are already published. `--recover-day` is the operator
    path for the case the writer cannot see; this is the case it can.
    """
    return tuple(day for day in days if any(status != "data" for status in _tier_statuses(store, day).values()))


def _recover_owed_days(
    checkpoints: SourceResponseCheckpoints,
    days: Sequence[date],
    points: Sequence[tuple[float, float]],
    *,
    now: datetime,
) -> tuple[WeatherRecoveryReport, ...]:
    """Reparse the retained inputs of every owed day, BEFORE this poll's own checkpoints overwrite them.

    THE ORDER IS THE WHOLE POINT. A checkpoint key is (provider, support, day, request URL) with no
    instant in it, so the next poll of the same point on the same day OVERWRITES the previous poll's
    retained body (`source_checkpoint.py::write` refreshes on a newer `retrieved_at`). Checkpointing
    this poll first would therefore destroy the only copy of the bucket the LAST poll failed to
    write -- the safety net erasing exactly what it was stretched under.
    """
    return tuple(recover_weather_day(day, points, checkpoints, now=now) for day in days)


def _with_recovered_observations(
    polled: Sequence[WeatherPointObservation], recovered: Sequence[WeatherPointObservation]
) -> tuple[WeatherPointObservation, ...]:
    """Add retained readings this poll does not already carry; a repeated grain keeps the LIVE one.

    Deduplicated on the published grain `(latitude, longitude, observedAt)` because
    `merge_weather_observations_day` refuses a poll that offers one grain twice -- and a rolling feed
    re-serving the same instant to two consecutive polls is ordinary, not exceptional.
    """
    seen = {(reading.latitude, reading.longitude, reading.observation.get("observedAt")) for reading in polled}
    extra = [
        reading
        for reading in recovered
        if (reading.latitude, reading.longitude, reading.observation.get("observedAt")) not in seen
    ]
    return (*polled, *extra)


async def _run_recovery_turn(args: argparse.Namespace, *, run_id: str, now: datetime) -> int:
    """Republish ONE named day from retained provider responses, making no source request at all."""
    day: date = args.recover_day
    deadline = time.monotonic() + args.time_budget_seconds
    points = weather_sample_points(args.bbox)
    credentials = settings.require_object_store()
    store = ObjectStore(BotoObjectStoreBackend.from_credentials(credentials), prefix=settings.object_store_prefix)
    availability_storage = BotoAvailabilityStorage.from_settings()
    availability = AvailabilityExtensionTally()
    recovery = await asyncio.to_thread(
        recover_weather_day, day, points, SourceResponseCheckpoints(availability_storage), now=now
    )
    emit("weather_observations_source_recovery", run_id=run_id, **recovery.to_event())
    if not recovery.observations:
        emit(
            "weather_observations_forward_complete",
            run_id=run_id,
            outcome=INCOMPLETE,
            recovered_day=day.isoformat(),
            recovery_state=recovery.state,
            days=0,
            rows_added=0,
            rows_updated=0,
            bytes=0,
            exit_code=1,
            days_written=0,
            days_unwritten=1,
            unwritten=[
                {
                    "day": day.isoformat(),
                    # The lane's existing word, not a new one: a recovery turn with nothing retained
                    # has exactly the same thing to report as a poll that returned nothing writable.
                    # `recovery_state` above says which of the two this was.
                    "outcome": NO_WRITABLE_OBSERVATIONS,
                    "attempts": 0,
                    "incoming_rows": 0,
                    "detail": (
                        f"no retained provider response for {day.isoformat()} can still be read, so this day "
                        "cannot be republished from checkpoints. Open-Meteo's current-conditions endpoint has "
                        "no archive to re-fetch it from either: the bucket is lost, not owed"
                    ),
                    "source_retained": False,
                }
            ],
            absences_overturned=[],
            **WeatherCheckpointReport().to_summary(),
            **availability.to_summary(),
        )
        return 1

    tables = direct_weather_observation_tables(recovery.observations, ingested_at=now)
    if set(tables) != {day}:
        raise RuntimeError(f"recovery for {day.isoformat()} reparsed rows for {sorted(tables)}")
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session:
        result = await _publish_day(
            session,
            store,
            day=day,
            table=tables[day],
            run_id=run_id,
            max_day_attempts=args.retry_attempts,
            retry_base_seconds=args.retry_base_seconds,
            retry_max_seconds=args.retry_max_seconds,
            contention_timeout_seconds=min(args.contention_timeout_seconds, max(deadline - time.monotonic(), 0.0)),
            availability_storage=availability_storage,
            availability=availability,
        )
    verdict = _bucket_verdict([result])
    emit(
        "weather_observations_forward_complete",
        run_id=run_id,
        outcome=verdict.outcome,
        recovered_day=day.isoformat(),
        recovery_state=recovery.state,
        days=1,
        outcomes={result.outcome: 1},
        incoming_rows=result.incoming_rows,
        rows_added=result.added_rows,
        rows_updated=result.updated_rows,
        merged_rows=result.merged_rows,
        actual_z13_rows=result.actual_z13_rows,
        incoming_rows_verified=result.incoming_rows_verified,
        parts=result.parts,
        rows=result.rows,
        bytes=result.written_bytes,
        exit_code=verdict.exit_code,
        days_written=verdict.days_written,
        days_unwritten=verdict.days_unwritten,
        unwritten=[_unwritten_event(unwritten) for unwritten in verdict.unwritten],
        absences_overturned=[overturned.isoformat() for overturned in verdict.absences_overturned],
        **WeatherCheckpointReport().to_summary(),
        **availability.to_summary(),
    )
    _report_bucket_incomplete(run_id, verdict)
    return verdict.exit_code


async def run(args: argparse.Namespace) -> int:
    """Fetch one current-conditions poll and durably merge every day it touched, bounded by the time budget."""
    _validate_args(args)
    fetched_at = datetime.now(UTC)
    run_id = args.run_id or f"weather-observations-direct-forward-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}"
    if getattr(args, "recover_day", None) is not None:
        return await _run_recovery_turn(args, run_id=run_id, now=fetched_at)
    deadline = time.monotonic() + args.time_budget_seconds
    points = weather_sample_points(args.bbox)

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
        # No recovery here, deliberately: a poll that named no day gives this turn no day to repair,
        # and probing the whole rolling window on every provider outage would spend one object read
        # per point per candidate day to usually find nothing. `--recover-day` is that path, and it
        # is the operator's because only the operator knows which day is in doubt.
        emit(
            "weather_observations_forward_complete",
            run_id=run_id,
            outcome="no_writable_observations",
            recovered_days=[],
            days=0,
            rows_added=0,
            rows_updated=0,
            bytes=0,
            exit_code=0,
            days_written=0,
            days_unwritten=0,
            unwritten=[],
            absences_overturned=[],
            **WeatherCheckpointReport().to_summary(),
            **availability.to_summary(),
        )
        return 0

    credentials = settings.require_object_store()
    store = ObjectStore(BotoObjectStoreBackend.from_credentials(credentials), prefix=settings.object_store_prefix)
    availability_storage = BotoAvailabilityStorage.from_settings()
    checkpoints = SourceResponseCheckpoints(availability_storage)
    # RECOVER BEFORE RETAINING. Both steps touch the same keys and this poll's retention overwrites
    # them, so the repair of a bucket an earlier poll failed to write has to read them first.
    owed = await asyncio.to_thread(_days_owed_a_recovery, store, tuple(tables))
    recoveries = await asyncio.to_thread(_recover_owed_days, checkpoints, owed, points, now=fetched_at)
    for recovery in recoveries:
        emit("weather_observations_source_recovery", run_id=run_id, **recovery.to_event())
    recovered = tuple(reading for recovery in recoveries for reading in recovery.observations)
    if recovered:
        repaired = direct_weather_observation_tables(
            _with_recovered_observations(poll.observations, recovered), ingested_at=fetched_at
        )
        tables = {day: repaired[day] for day in tables if day in repaired}
    # Retain the parser inputs BEFORE the first Parquet write: this feed keeps no archive, so a
    # checkpoint taken after a failed write would be a checkpoint that never existed when needed.
    checkpoint_report = await asyncio.to_thread(checkpoint_current_poll, poll, points, checkpoints)
    emit("weather_observations_source_retention", run_id=run_id, **checkpoint_report.to_summary())
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
                        source_retained=checkpoint_report.retained_whole_day(day),
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
            result = replace(result, source_retained=checkpoint_report.retained_whole_day(day))
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
                absence_overturned=None if result.absence_overturned is None else result.absence_overturned.as_event(),
            )

    outcomes = Counter(result.outcome for result in results)
    verdict = _bucket_verdict(results, retention_failed=checkpoint_report.failed)
    emit(
        "weather_observations_forward_complete",
        run_id=run_id,
        outcome=verdict.outcome,
        days=len(results),
        recovered_days=[recovery.to_event() for recovery in recoveries],
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
        **checkpoint_report.to_summary(),
        exit_code=verdict.exit_code,
        days_written=verdict.days_written,
        days_unwritten=verdict.days_unwritten,
        unwritten=[_unwritten_event(result) for result in verdict.unwritten],
        absences_overturned=[day.isoformat() for day in verdict.absences_overturned],
        **availability.to_summary(),
    )
    _report_bucket_incomplete(run_id, verdict)
    return verdict.exit_code


async def main(argv: Sequence[str] | None = None) -> int:
    """Run the operator and emit one typed terminal fault on failure."""
    args = parse_args(argv)
    try:
        return await run(args)
    except Exception as error:
        emit("weather_observations_forward_failed", error_type=type(error).__name__, detail=str(error))
        return 1


__all__ = [
    "WEATHER_OBSERVATIONS_DEFAULT_MAX_DAYS",
    "WEATHER_OBSERVATIONS_MAX_DAYS",
    "ForwardBucketVerdict",
    "ForwardDayResult",
    "WeatherObservationsForwardConfigError",
    "main",
    "parse_args",
    "parser",
    "run",
]
