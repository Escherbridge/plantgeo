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

THE REPAIR MAY NOT COST THE POLL ITS RETENTION. Running ahead of `checkpoint_current_poll` is
necessary (the retention overwrites the keys the repair reads) and it put two network phases in
front of the turn's only durable step, where from 2026-09-18 to 2026-09-19 one transient store fault
unwound to `main()`'s catch-all and a poll of a feed with no archive was neither published nor
retained. Both phases now run inside `_repair_owed_days`, which returns a `RecoveryPhase` on every
path including the failing one and spends at most `RECOVERY_PROBE_BUDGET_SHARE` of what is left of
the turn's window; the degradation is reported (`weather_observations_source_recovery_phase`, the
terminal report's `recovery_phase`, and `incomplete` via `_bucket_verdict(..., recovery_degraded=)`)
and never fatal. `_retain_current_poll` gives the retention call itself the same treatment for the
same reason. Neither is a silent swallow: the probe cannot lose a reading, and losing readings to
protect their backup is what the guards forbid.
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
    WEATHER_SUPPORT_WITNESS_LIMIT,
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
    from agri_data_service.pipeline.direct.weather_observations.source import (
        WeatherPointObservation,
        WeatherPollResult,
    )
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
#: The share of what is LEFT of the turn's time budget that the repair probe may spend before the
#: poll's own retention and publication take the rest. The probe costs one `list_partition_keys` per
#: zoom tier per selected day plus one checkpoint GET per sample point per owed day -- roughly 150
#: object reads on this lane's grid -- so an unbounded probe against a slow bucket would leave every
#: day `time_budget_exhausted`, and `_bucket_verdict` then returns exit 1, which this module's
#: docstring defines as this lane's breaker. A quarter covers the probe's ordinary cost and leaves
#: three quarters to the writes, which are the half of the turn that has no second chance.
#: Enforced in `forward.py::_repair_owed_days`, the only caller of the two probe phases
#: `forward.py::_days_owed_a_recovery` and `forward.py::_recover_owed_days`.
RECOVERY_PROBE_BUDGET_SHARE: Final = 0.25


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
    #: Whether this turn's repair probe failed or ran out of budget. Also not a day count: no day was
    #: lost to it, but a bucket an earlier poll failed to write went unrepaired while THIS poll's
    #: retention overwrote the checkpoints it would have been repaired from, so the window closed.
    recovery_degraded: bool = False

    @property
    def days_unwritten(self) -> int:
        """How many days this poll selected but did not publish, whatever the reason."""
        return len(self.unwritten)


def _bucket_verdict(
    results: Sequence[ForwardDayResult], *, retention_failed: int = 0, recovery_degraded: bool = False
) -> ForwardBucketVerdict:
    """Exit 1 only when NO day wrote; some written and some not is `incomplete` at exit 0 -- see module docstring.

    A RETENTION FAILURE ALSO COSTS THE TURN ITS `complete`. Until 2026-09-19 the retention counters
    were advisory: a poll whose every checkpoint failed published exactly as before and reported a
    clean success, which made writing the checkpoint before the first write buy nothing. It is not
    an exit-1 refusal, because refusing to publish rows already held in memory over a failed BACKUP
    of them destroys the very data the backup exists to protect -- and because exit 1 is this lane's
    breaker (module docstring). It is `incomplete`, on stderr, and named per unwritten day.

    A DEGRADED REPAIR PROBE COSTS THE TURN ITS `complete` FOR THE SAME REASON AND NO MORE. It does
    not touch the exit code either (`forward.py::_repair_owed_days` returns a `RecoveryPhase` on
    every path including its `except Exception`, so a probe fault
    can no longer reach `main()`'s catch-all), but it is not nothing: the probe is the
    only reader of checkpoints that this same turn's retention is about to overwrite, so a turn that
    could not probe has closed a repair window rather than postponed it.
    """
    unwritten = tuple(result for result in results if result.outcome != "written")
    days_written = len(results) - len(unwritten)
    clean = bool(results) and not unwritten and not retention_failed and not recovery_degraded
    return ForwardBucketVerdict(
        outcome=COMPLETE if clean else INCOMPLETE,
        exit_code=0 if days_written else 1,
        days_written=days_written,
        unwritten=unwritten,
        absences_overturned=tuple(result.day for result in results if result.absence_overturned is not None),
        retention_failed=retention_failed,
        recovery_degraded=recovery_degraded,
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
    if not verdict.unwritten and not verdict.retention_failed and not verdict.recovery_degraded:
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
                "recovery_degraded": verdict.recovery_degraded,
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
            "previous turn accepted and retained UNDER THE CURRENT SUPPORT GRID: every checkpoint "
            "key is bound to the digest of INGEST_BBOX and the weather sample spacing, so changing "
            "either relocates them. The turn makes no source request at all, publishes whatever "
            "part of the grid was retained through the ordinary lane-day lock and merge, and exits 1 "
            "naming the day when nothing is readable -- saying whether that is a loss, a grid change "
            "(restore the bbox and spacing and re-run), or unknown."
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


def _days_owed_a_recovery(
    store: ObjectStore, days: Sequence[date], *, probe_deadline: float | None = None
) -> tuple[tuple[date, ...], tuple[date, ...]]:
    """Of the days this poll named, the ones no complete publication exists for, and the ones the budget did not reach.

    A day already `data` at every tier is NOT probed. Its earlier instants could in principle still
    be missing, but nothing this writer can read proves that, and guessing would spend 150 object
    reads per poll to re-merge readings that are already published. `--recover-day` is the operator
    path for the case the writer cannot see; this is the case it can.

    A STANDING GOVERNED ABSENCE IS RE-PROBED EVERY POLL, AND THAT IS PRICED, NOT FREE (style review
    W10, N4). `absent` is not `data`, so a day whose every tier carries an absence marker is owed a
    recovery on this poll and on every poll for as long as the absence stands. The cost is one
    checkpoint GET per support point per poll -- ~150 here -- inside the probe's share of the turn's
    window (`forward.py::RECOVERY_PROBE_BUDGET_SHARE`), so it can delay the probe's other
    days but never the writes. It is kept deliberately rather than short-circuited: a retained body
    for an absent day is exactly the evidence that OVERTURNS the absence, which the writer reports
    as `absence_overturned` (`adapter.py::OverturnedAbsence`, carried to the terminal report through
    `ForwardDayResult.absence_overturned`). Skipping the probe would make a wrongly published
    absence permanent, which is the more expensive of the two mistakes. What is NOT claimed: that
    the re-probe is cheap, or that it usually finds anything.

    `probe_deadline` is a `time.monotonic()` stamp, checked BEFORE each day's listing rather than
    after it, so a bucket slow enough to eat the turn's window stops costing it at the first day
    that would overrun. Days past the stamp come back as DEFERRED, not as not-owed: the caller
    reports them (`_repair_owed_days`) instead of silently narrowing the repair.
    """
    owed: list[date] = []
    deferred: list[date] = []
    for day in days:
        if probe_deadline is not None and time.monotonic() >= probe_deadline:
            deferred.append(day)
            continue
        if any(status != "data" for status in _tier_statuses(store, day).values()):
            owed.append(day)
    return tuple(owed), tuple(deferred)


def _recover_owed_days(
    checkpoints: SourceResponseCheckpoints,
    days: Sequence[date],
    points: Sequence[tuple[float, float]],
    *,
    now: datetime,
    probe_deadline: float | None = None,
) -> tuple[tuple[WeatherRecoveryReport, ...], tuple[date, ...]]:
    """Reparse the retained inputs of every owed day, BEFORE this poll's own checkpoints overwrite them.

    THE ORDER IS THE WHOLE POINT. A checkpoint key is (provider, support, day, request URL) with no
    instant in it, so the next poll of the same point on the same day OVERWRITES the previous poll's
    retained body (`source_checkpoint.py::write` refreshes on a newer `retrieved_at`). Checkpointing
    this poll first would therefore destroy the only copy of the bucket the LAST poll failed to
    write -- the safety net erasing exactly what it was stretched under.

    The same `probe_deadline` bounds this phase, twice: between days here, and between sample points
    inside `recovery.py::recover_weather_day`, which reports what it did not reach as
    `unprobed_points`. Returns the reports and the days the budget never started.
    """
    reports: list[WeatherRecoveryReport] = []
    deferred: list[date] = []
    for day in days:
        if probe_deadline is not None and time.monotonic() >= probe_deadline:
            deferred.append(day)
            continue
        reports.append(recover_weather_day(day, points, checkpoints, now=now, deadline=probe_deadline))
    return tuple(reports), tuple(deferred)


@dataclass(frozen=True, slots=True)
class RecoveryPhase:
    """What this turn's repair probe managed -- and, on every failure path, an EMPTY repair rather than a dead turn.

    THIS TYPE EXISTS TO MAKE THE PROBE UNABLE TO COST THE POLL ITS RETENTION. The probe runs ahead of
    `checkpoint_current_poll` because retention overwrites the very keys it reads (see
    `_recover_owed_days`), and until 2026-09-19 it ran there unwrapped: one transient 5xx from the
    object store unwound through `run()` to `main()`'s catch-all, so a poll of a feed with NO ARCHIVE
    was neither published nor retained and the instants it held were gone for good. Every
    construction below is therefore a REPORT, never an exception: `forward.py::_repair_owed_days`
    is the only producer of a probe verdict -- `forward.py::run` binds this type only from that
    producer and for the no-day report -- and it converts a fault into `state="failed"`.
    `forward.py::_tables_after_recovery` RESTATES one through `dataclasses.replace` to add a merge
    fault to a probe verdict it does not otherwise change; that is the second and last place a
    `RecoveryPhase` comes from, and it cannot raise either.
    """

    state: str
    reports: tuple[WeatherRecoveryReport, ...] = ()
    days_owed: tuple[date, ...] = ()
    #: Owed or candidate days the probe budget did not reach. Named, not counted, because the
    #: operator's next move is `--recover-day <that day>`.
    days_deferred: tuple[date, ...] = ()
    error_type: str | None = None
    detail: str | None = None
    #: A fault folding the probe's OUTPUT into this poll's day buckets, as opposed to a fault in its
    #: I/O. Separate from `error_type` because the probe succeeded: what failed is the merge, and the
    #: turn's answer to that is to publish the poll WITHOUT the recovered readings rather than to
    #: lose the poll (`forward.py::_tables_after_recovery`).
    merge_error_type: str | None = None
    merge_detail: str | None = None

    @property
    def observations(self) -> tuple[WeatherPointObservation, ...]:
        """Every reading recovered this turn, across all owed days."""
        return tuple(reading for report in self.reports for reading in report.observations)

    @property
    def degraded(self) -> bool:
        """Did the probe fail, run out of budget, or fail to merge? Any of the three leaves a bucket unrepaired."""
        return self.state == "failed" or bool(self.days_deferred) or self.merge_error_type is not None

    def to_event(self) -> dict[str, object]:
        """Render the phase for the progress stream AND the terminal report; the readings stay out of it."""
        event: dict[str, object] = {
            "state": self.state,
            "days_owed": [day.isoformat() for day in self.days_owed],
            "days_deferred": [day.isoformat() for day in self.days_deferred],
            "days_recovered": [report.to_event() for report in self.reports],
        }
        if self.error_type is not None:
            event["error_type"] = self.error_type
        if self.detail is not None:
            event["detail"] = self.detail
        if self.merge_error_type is not None:
            event["merge_error_type"] = self.merge_error_type
        if self.merge_detail is not None:
            event["merge_detail"] = self.merge_detail
        return event


async def _repair_owed_days(  # noqa: PLR0913 - the two probe phases' inputs plus the bound they share
    store: ObjectStore,
    checkpoints: SourceResponseCheckpoints,
    days: Sequence[date],
    points: Sequence[tuple[float, float]],
    *,
    now: datetime,
    deadline: float,
) -> RecoveryPhase:
    """Run the repair probe as a bounded, non-fatal phase: it may return nothing, it may not raise.

    Two properties, both enforced here rather than at the call site, because three waves of this
    lane's history are patches applied where the review pointed instead of where the behaviour is
    defined:

    1. BOUNDED -- the probe gets `RECOVERY_PROBE_BUDGET_SHARE` of what is left of the turn's window
       and no more; the rest belongs to the writes, which have no second chance.
    2. NON-FATAL -- every exception from either phase becomes `state="failed"` with the type and
       message on the report. The caller in `run()` then proceeds to `_retain_current_poll` and the
       publish loop exactly as it would for a turn that owed no day at all.

    Not swallowed, either: the phase is emitted as `weather_observations_source_recovery_phase` and
    carried on the terminal `weather_observations_forward_complete` report, and a degraded phase
    costs the turn its `complete` through `_bucket_verdict(..., recovery_degraded=...)`.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return RecoveryPhase(state="skipped_no_budget", days_deferred=tuple(days))
    probe_deadline = time.monotonic() + remaining * RECOVERY_PROBE_BUDGET_SHARE
    try:
        owed, unlisted = await asyncio.to_thread(
            _days_owed_a_recovery, store, tuple(days), probe_deadline=probe_deadline
        )
        reports, unread = await asyncio.to_thread(
            _recover_owed_days, checkpoints, owed, points, now=now, probe_deadline=probe_deadline
        )
    except Exception as error:
        # A repair that cannot read is a repair that does not happen; it is NOT a reason to drop
        # readings this turn is holding. `main()`'s catch-all would have made it one.
        return RecoveryPhase(
            state="failed",
            days_deferred=tuple(days),
            error_type=type(error).__name__,
            detail=str(error),
        )
    return RecoveryPhase(
        state="probed",
        reports=reports,
        days_owed=owed,
        days_deferred=(*unlisted, *unread),
    )


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


def _tables_after_recovery(
    poll: WeatherPollResult,
    tables: Mapping[date, pa.Table],
    phase: RecoveryPhase,
    *,
    ingested_at: datetime,
    run_id: str,
) -> tuple[dict[date, pa.Table], RecoveryPhase]:
    """Fold the probe's OUTPUT into this poll's buckets, or publish without it -- never lose the poll.

    THE THIRD UNGUARDED STRIP, CLOSED. `_repair_owed_days` made the probe's I/O non-fatal and
    `_retain_current_poll` did the same for the retention, but the phase emit and the re-bucketing
    between them stayed outside both (verifier hotfix W10, residual 1): a recovered reading that the
    row builder refuses raises `DirectWeatherObservationsRowError` out of
    `direct_weather_observation_tables`, and the only thing downstream of that raise was `main()`'s
    catch-all -- which drops a poll of a feed with no archive, unretained, to protect a repair. The
    exposure is narrow (`recovery.py::recover_weather_day` rejects unparseable and wrong-day bodies
    per point) but narrow is not closed, and the merge is the probe's output
    rather than the poll's, so it is the probe that must pay for it.

    THE SIBLING PATHS NOW AGREE ON A MISSING DAY (style review W10, S6). `_run_recovery_turn` raises
    when its reparse names days other than the one requested (`forward.py::_run_recovery_turn`); this path
    used to filter the same condition away with `if day in repaired`, a silent narrowing sitting
    where its sibling refuses. It refuses here too now -- and because the refusal happens inside
    this guard, refusing costs the recovery and not the poll.

    The two `emit` calls are inside the guard as briefed, though they are the least of it: `emit`
    (`forward.py::emit`) is `json.dumps` over plain scalars and a `print`, so its only fault mode
    is a closed stdout -- under which the failure emit below cannot speak either and no report of
    any kind survives the turn.
    """
    try:
        emit("weather_observations_source_recovery_phase", run_id=run_id, **phase.to_event())
        for recovery in phase.reports:
            emit("weather_observations_source_recovery", run_id=run_id, **recovery.to_event())
        recovered = phase.observations
        if not recovered:
            return dict(tables), phase
        repaired = direct_weather_observation_tables(
            _with_recovered_observations(poll.observations, recovered), ingested_at=ingested_at
        )
        missing = sorted(day for day in tables if day not in repaired)
        if missing:
            raise RuntimeError(
                f"recovery merge dropped {[day.isoformat() for day in missing]} from this poll's buckets"
            )
        return {day: repaired[day] for day in tables}, phase
    except Exception as error:
        emit(
            "weather_observations_source_recovery_merge_failed",
            run_id=run_id,
            error_type=type(error).__name__,
            detail=str(error),
        )
        return dict(tables), replace(phase, merge_error_type=type(error).__name__, merge_detail=str(error))


async def _retain_current_poll(
    poll: WeatherPollResult,
    points: Sequence[tuple[float, float]],
    checkpoints: SourceResponseCheckpoints,
    *,
    run_id: str,
) -> WeatherCheckpointReport:
    """Retain this poll's parser inputs, degrading a store fault to "nothing retained" rather than losing the poll.

    Same argument as `_repair_owed_days`, one step later and older: retention is a BACKUP of rows
    this turn is already holding, so letting its transport fault unwind into `main()`'s catch-all
    would drop the rows to protect the copy of them. A wholesale failure is reported as every
    accepted point failing, which is what `WeatherCheckpointReport.retained_whole_day` then answers
    with for each day, and `_bucket_verdict(..., retention_failed=...)` turns into an `incomplete`
    bucket on stdout and on stderr. Per-point failures are already counted inside
    `recovery.py::checkpoint_current_poll`; this only covers the fault that takes the whole call.
    """
    try:
        return await asyncio.to_thread(checkpoint_current_poll, poll, points, checkpoints)
    except Exception as error:
        emit(
            "weather_observations_source_retention_failed",
            run_id=run_id,
            error_type=type(error).__name__,
            detail=str(error),
            attempted=len(poll.observations),
        )
        return WeatherCheckpointReport(attempted=len(poll.observations), retained=0, failed=len(poll.observations))


def _recovery_refusal_detail(recovery: WeatherRecoveryReport) -> str:
    """Say WHICH empty answer this is. Only one of the three is a loss, and it is the rarest.

    Style review W10, S5: this text said "the bucket is lost, not owed" for every empty recovery,
    including the one an operator causes by widening `INGEST_BBOX` or changing the sample spacing --
    both read at call time (`support.py::weather_sample_points`), neither a deploy. The bodies are
    on disk under the old digest; the claim of permanent loss is false, and it is made at the exact
    moment an operator is deciding whether to panic. `recovery.py::WeatherSupportWitness` is what
    makes the three cases distinguishable; this only has to speak them.

    EVERY BRANCH IS SELECTED BY POSITIVE EVIDENCE, AND THE DEFAULT IS THE ONE THAT CLAIMS LEAST
    (style review W11, B1). The move claim needs a witnessed other identity AND the exhausted walk that
    `recovery.py::recover_weather_day` requires before it will say `foreign_checkpoint_identity`; the
    loss claim needs both a completed empty walk and the exact ordered checkpoint keyspace on the
    record for the day (`identity_matches`). Anything else -- no witness, a legacy grid-only witness,
    an unreadable one, a report that never asked -- falls to UNKNOWN, which is what the evidence
    supports, and says what to TRY rather than what to restore: telling an operator to restore a
    bbox on evidence this thin is how a readable day gets moved out of reach.
    """
    day = recovery.day.isoformat()
    witness = recovery.witness
    if recovery.state == "foreign_checkpoint_identity" and witness is not None:
        witnessed = ", ".join(identity for _support, identity in witness.witnessed_identities)
        capped = (
            f" (the witness holds at most {WEATHER_SUPPORT_WITNESS_LIMIT} identities, so older ones may be missing)"
            if witness.truncated
            else ""
        )
        return (
            f"every support point for {day} was read under checkpoint identity "
            f"{witness.searched_checkpoint_identity_sha256} and none is retained, while the day IS "
            f"on record as polled under {witnessed}{capped}. The provider, support grid, or exact "
            "request URLs have changed, so the bucket is OWED, not lost -- restore the build identity "
            "that produced a witnessed keyspace and re-run within the 7-day checkpoint window"
        )
    if (
        recovery.state == "no_retained_capture"
        and witness is not None
        and witness.verdict == "identity_matches"
    ):
        return (
            f"no retained provider response for {day} can still be read under the exact checkpoint "
            f"identity it was polled with ({witness.searched_checkpoint_identity_sha256}), so this day "
            "cannot be republished from checkpoints. Open-Meteo's current-conditions endpoint has no "
            "archive to re-fetch it from either: the bucket is lost, not owed"
        )
    if recovery.state == "probe_budget_exhausted":
        searched_identity = (
            witness.searched_checkpoint_identity_sha256
            if witness is not None and witness.searched_checkpoint_identity_sha256
            else "unknown"
        )
        return (
            f"the recovery probe for {day} stopped with {recovery.unprobed_points} support points "
            f"unread under checkpoint identity {searched_identity}. "
            "Any witness is only context until the walk completes, so the bucket is UNKNOWN, not "
            "provably lost or moved; retry --recover-day without the poll deadline"
        )
    searched_identity = (
        witness.searched_checkpoint_identity_sha256
        if witness is not None and witness.searched_checkpoint_identity_sha256
        else "unknown"
    )
    return (
        f"no retained provider response for {day} can be read under checkpoint identity "
        f"{searched_identity}, and no conclusive "
        "witness matches it. The witness may be absent, unreadable, or from the legacy grid-only "
        "schema, so its silence is not an answer. The bucket is UNKNOWN, not provably lost: re-run "
        "--recover-day with the provider, support grid, and request-URL identity that were in effect "
        "when the day was polled, and treat it as lost only if that exact-identity turn is empty too"
    )


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
                    "detail": _recovery_refusal_detail(recovery),
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
            # The same single key every other poll report carries -- `recovery_phase` replaced a
            # separate `recovered_days` list of the same per-day events on 2026-09-19, because two
            # spellings of one fact drift. A turn that named no day probed nothing and deferred
            # nothing, and says so rather than omitting the key.
            recovery_phase=RecoveryPhase(state="no_day_to_repair").to_event(),
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
    # them, so the repair of a bucket an earlier poll failed to write has to read them first. The
    # phase is bounded and cannot raise -- both properties are enforced in `_repair_owed_days`
    # (`forward.py::_repair_owed_days`, whose every return is a `RecoveryPhase`), not here -- so the two
    # statements after it run for a failed probe exactly as for an idle one.
    recovery_phase = await _repair_owed_days(
        store, checkpoints, tuple(tables), points, now=fetched_at, deadline=deadline
    )
    # The probe's OUTPUT is folded in under its own guard too, for the same reason its I/O is: the
    # merge sat between the two guarded regions until 2026-09-19 and was the last way a repair could
    # still cost this poll its retention (`_tables_after_recovery`).
    tables, recovery_phase = _tables_after_recovery(
        poll,
        tables,
        recovery_phase,
        ingested_at=fetched_at,
        run_id=run_id,
    )
    # Retain the parser inputs BEFORE the first Parquet write: this feed keeps no archive, so a
    # checkpoint taken after a failed write would be a checkpoint that never existed when needed.
    checkpoint_report = await _retain_current_poll(poll, points, checkpoints, run_id=run_id)
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
    verdict = _bucket_verdict(
        results, retention_failed=checkpoint_report.failed, recovery_degraded=recovery_phase.degraded
    )
    emit(
        "weather_observations_forward_complete",
        run_id=run_id,
        outcome=verdict.outcome,
        days=len(results),
        recovery_phase=recovery_phase.to_event(),
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
