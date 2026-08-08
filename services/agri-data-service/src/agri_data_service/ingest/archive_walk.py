"""The archive lane walk as a durable job handler: one work item is one window, and a failed window stays visible."""

from __future__ import annotations

import math
import os
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.backfill import BackfillPlan, history_chunks, merge_backfill_results, run_source_backfill
from agri_data_service.ingest.firms import firms_archive_source
from agri_data_service.ingest.lanes import lane_windows, resolve_lane
from agri_data_service.ingest.policy import MAX_SOURCE_RECORDS_VARIABLE
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.ingest.usgs_nwis import usgs_streamflow_archive_source
from agri_data_service.jobs import (
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobWorkItemSpec,
    ensure_job_definition,
    job_handler,
    open_job_run,
)
from agri_data_service.jobs.registry import DEFAULT_RETRY_POLICY, EMPTY_JSON_OBJECT

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Mapping, Sequence

    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.ingest.lanes import BackfillLane, LaneWindow
    from agri_data_service.ingest.results import IngestionJobResult
    from agri_data_service.ingest.source import IngestionSource
    from agri_data_service.ingest.writer import FeatureWriter
    from agri_data_service.jobs import JobInvocation, OpenedJobRun

# Operational telemetry is bound to stderr by `commands.py`, never stdout; this module logs through the
# process logger so a cron tick's JSON-lines stdout stays parseable. See ingest/AGENTS.md "commands.py".
logger = structlog.get_logger()

ARCHIVE_WALK_HANDLER_TOKEN: Final = "ingest.archive_walk"
ARCHIVE_WALK_WORK_ITEM_KIND: Final = "archive_window"
ARCHIVE_WALK_DEFINITION_PREFIX: Final = "agri.ingest.archive_walk"
ARCHIVE_WALK_RUN_KEY_PREFIX: Final = "archive-walk"

# Bump when a lane's walk shape changes. `ensure_job_definition` upserts ON CONFLICT (name, version), so
# an unbumped version rewrites the live row's `parameters` under a run that is already walking. The
# executed shape is always read from `BACKFILL_LANES`, never from the stored row, so `parameters` is an
# operator-facing record of what a run was planned with -- which is exactly why it must not drift.
ARCHIVE_WALK_DEFINITION_VERSION: Final = "2026-08-08"

# These walks are hours long and must not sit behind an hourly ingest tick in the shared queue.
ARCHIVE_WALK_QUEUE_NAME: Final = "archive-backfill"

# One handler call walks ONE chunk, so the lease must cover one chunk's wall time: it cannot be extended
# from inside `fetch_history`'s single await. Worst measured chunk is 11.5 minutes (690s), the
# 2026-07-23 FIRMS day at peak PNW fire season, and 2400s is ~3.5x that. Both lanes take the FIRMS
# number deliberately: a lease that is too LONG only delays the reaper on a worker that has already
# died, while one that is too SHORT fences the live worker out of its own shard on every peak-season
# chunk, and those two costs are not comparable.
ARCHIVE_WALK_LEASE_SECONDS: Final = 2400

# 13 minutes, leaving ~2 minutes of a */15 Railway tick for container start, the definition upsert and
# the final rollup. IT DOES NOT BOUND THE TICK. `worker.py::_drive_work_item` tests its deadline BEFORE
# each handler call and a handler call is uninterruptible, so the real bound is this budget PLUS the
# chunk the tick had already started: 780 + 690 = 1470s worst case, which is 9.5 minutes past the next
# tick. Two ticks therefore CAN run against the same lane at once. That is safe -- the fencing token and
# `FOR UPDATE SKIP LOCKED` hand them different shards -- but it is not free: it doubles the concurrent
# request rate against FIRMS, which is exactly what ARCHIVE_WALK_MAX_ATTEMPTS' comment blames the
# measured 169-of-298 first-attempt failures on. `chunk_budget_seconds` is what shrinks that overrun to
# the cases nothing could have predicted: the handler declines to START a chunk `invocation.has_budget_for`
# says will not fit, so a tick only runs long when a chunk turns out slower than every chunk its own
# window has already walked.
ARCHIVE_WALK_TIME_BUDGET_SECONDS: Final = 780

# The worst chunk ever measured on either lane: 11.5 minutes for the 2026-07-23 FIRMS day at peak PNW
# fire season. It is the estimate a window's FIRST chunk is held to, because nothing better is known
# about that window yet, and it is the ceiling every later estimate is clamped to -- an estimate must
# never claim a chunk could be slower than anything ever observed. Note 780 - 690 = 90, so a window's
# first chunk is only ever started inside the first 90 seconds of a tick, and a 690s chunk there
# finishes exactly on the budget rather than past it.
ARCHIVE_WALK_WORST_CHUNK_SECONDS: Final = 690

# The margin over the slowest chunk a window has already walked. Doubled, because the chunks of one
# window are CONSECUTIVE DAYS of one season: a neighbouring day's detection volume runs within a factor
# of two of its neighbour, not a factor of thirty. Estimating from the window's own measurement rather
# than from the archive-wide worst case is what keeps throughput: holding every chunk to 690s would mean
# one chunk per */15 tick, and FIRMS' 298 windows are 1,490 chunks.
ARCHIVE_WALK_CHUNK_ESTIMATE_MARGIN: Final = 2

# The bash gave a window 3 in-run attempts and up to 3 ledger passes -- up to 9 chances -- and measured
# 2026-08-07 over the first full pass, 169 of 298 FIRMS windows and 46 of 95 streamflow windows still
# needed more than the first. The signature was `ConnectError` with a couple of `getaddrinfo failed`:
# local socket and DNS exhaustion from firing windows back to back, not an upstream that is down, so the
# same day succeeds on a later attempt. 8 carries that budget across, and unlike the bash the 8th
# failure lands in `dead_letter`, which is a queryable row rather than a line in a file that the next
# successful run deletes. `DEFAULT_RETRY_POLICY`'s first wait is 30s, reproducing the bash's measured
# `RETRY_BACKOFF_SECONDS` drain interval exactly; the doubling to an hour is what stops a genuinely dead
# upstream from spending all 8 attempts inside one bad tick.
ARCHIVE_WALK_MAX_ATTEMPTS: Final = 8

PAYLOAD_LANE: Final = "lane"
PAYLOAD_WINDOW_START: Final = "window_start"
PAYLOAD_WINDOW_END: Final = "window_end"
PAYLOAD_BBOX: Final = "bbox"

CURSOR_NEXT_CHUNK_START: Final = "next_chunk_start"
CURSOR_RECORDS_SEEN: Final = "records_seen"
CURSOR_RECORDS_WRITTEN: Final = "records_written"
CURSOR_RECORDS_REJECTED: Final = "records_rejected"

# The slowest chunk this window has walked, in whole seconds. It lives on the cursor and not in memory
# because a window straddles ticks and every tick is a fresh one-shot container: an estimate held in a
# process variable would be discarded exactly when the next tick needs it most.
CURSOR_SLOWEST_CHUNK_SECONDS: Final = "slowest_chunk_seconds"

# The detail key `backfill._run_backfill_chunk` counts records that produced no write under.
REJECTED_DETAIL: Final = "rejected"

# A window that bit the record cap. `_truncated_chunk_reason` already names the narrower `--chunk-days`
# that would have fitted, so the reason is carried verbatim into `last_error_summary`.
TRUNCATED_FAILURE_CLASS: Final = "record_cap_truncation"
# A window the walk declined to run at all: an unconfigured `INGEST_BBOX`, or the source's typed history
# refusal. It wrote nothing, so it is a failure here even though `run_source_backfill` calls it a skip.
SKIPPED_FAILURE_CLASS: Final = "walk_skipped"
UPSTREAM_FAILURE_CLASS: Final = "upstream_unavailable"
MISSING_CREDENTIAL_FAILURE_CLASS: Final = "missing_credential"
FENCE_LOST_FAILURE_CLASS: Final = "fence_lost"

# A chunk that fetched records and rejected every one of them. `select_writes` maps each record through
# the source's `build_write` and its freshness rule, so `rejected == records_seen` means no write was
# produced at all and no reading of the write path makes that a walked chunk. This is the shape a
# renamed FIRMS CSV column takes, and the shape of FIRMS answering with its plain-text `Invalid MAP_KEY`
# body under HTTP 200: `fetch_history` yields 21,000 rows, all 21,000 are rejected, and
# `_run_backfill_chunk` still returns status="ingested" because only a bitten record cap fails it.
# Without this class every one of the 298 FIRMS windows marches to SUCCEEDED having written zero rows --
# the bash bug reincarnated one layer up, in the module that exists to make it impossible.
TOTAL_REJECTION_FAILURE_CLASS: Final = "all_records_rejected"

# The stable prefix a budget stop states. A yield is not a failure, so this never reaches
# `job_work_item.last_error_summary`: `defer_work_item` writes it to the parked attempt's
# `metrics.deferral_reason`, which is where an operator distinguishes a tick that ran out of clock from a
# shard that went wrong without having to read a failure column that should stay empty.
BUDGET_STOP_REASON: Final = "archive walk declined a chunk it cannot finish inside this tick's budget"

_UNBOUND_CONTEXT_REASON: Final = (
    "no archive walk context is bound; open `archive_walk_context()` once around the slice so a handler "
    "never opens its own ingest session per shard"
)


class ArchiveWalkPayloadError(ValueError):
    """Raised when a stored work-item payload does not name a window this walk could follow."""


class ArchiveWalkCursorError(ValueError):
    """Raised when a stored cursor names no chunk of the window it belongs to, so the record contradicts the code."""


class ArchiveWalkContextError(RuntimeError):
    """Raised when a handler runs with no bound write path, which would otherwise be a silently empty walk."""


class UnknownArchiveSourceError(LookupError):
    """Raised when a lane's source token resolves to no archive source, so the lane could never have walked."""


@dataclass(frozen=True, slots=True)
class ArchiveWalkContext:
    """The write path, upstream client and coverage box one cron tick opens once and every shard reuses."""

    write_features: FeatureWriter
    client: httpx.AsyncClient | None = None
    bbox: str | None = None


_ARCHIVE_WALK_CONTEXT: Final[ContextVar[ArchiveWalkContext | None]] = ContextVar(
    "archive_walk_context",
    default=None,
)


@asynccontextmanager
async def archive_walk_context(context: ArchiveWalkContext) -> AsyncIterator[None]:
    """Bind one tick's write path for the whole slice, so a handler never opens a session per shard.

    `ingest_session()` builds a NEW engine and a NEW connection per `async with` and disposes it in its
    `finally` (db/engine.py), so binding per work item would mean a full TCP+TLS+auth handshake against
    the Railway proxy for every window. One binding per tick, released on the way out.
    """
    token = _ARCHIVE_WALK_CONTEXT.set(context)
    try:
        yield
    finally:
        _ARCHIVE_WALK_CONTEXT.reset(token)


def current_archive_walk_context() -> ArchiveWalkContext:
    """Return the tick's bound write path, refusing in typed terms rather than walking into a no-op."""
    context = _ARCHIVE_WALK_CONTEXT.get()
    if context is None:
        raise ArchiveWalkContextError(_UNBOUND_CONTEXT_REASON)
    return context


@contextmanager
def pinned_source_record_cap(lane: BackfillLane) -> Iterator[int]:
    """Pin INGEST_MAX_SOURCE_RECORDS to the lane's declared ceiling for one walk, restoring it afterwards.

    The environment IS the seam: `policy.resolve_max_source_records` reads the variable at call time and
    `run_source_backfill` builds its own `FetchRequest` from it, so the ceiling cannot be passed as an
    argument. The bash `export`ed it for exactly this reason. Leaving it to the deployment is not
    equivalent -- an unset variable is a 10,000-record cap, and a bitten cap deletes the OLDEST days of a
    chunk whole rather than thinning it, so a walk under the default reports `ingested` for a window
    whose earliest days were never written. A slice drives one shard at a time, so this process-global
    pin is never held across two concurrent walks.
    """
    previous = os.environ.get(MAX_SOURCE_RECORDS_VARIABLE)
    os.environ[MAX_SOURCE_RECORDS_VARIABLE] = str(lane.max_source_records)
    try:
        yield lane.max_source_records
    finally:
        if previous is None:
            os.environ.pop(MAX_SOURCE_RECORDS_VARIABLE, None)
        else:
            os.environ[MAX_SOURCE_RECORDS_VARIABLE] = previous


def archive_sources() -> Mapping[str, IngestionSource]:
    """Build the archive sources a lane can walk, on demand, mirroring `commands._build_backfillable_sources`.

    Built here rather than imported from `commands.py` so the CLI can import this module without a cycle.
    `tests/test_ingest_archive_walk.py` pins every lane's token against the CLI's own registry, so the
    two cannot drift into a lane that plans windows no `--source` token can walk.
    """
    sources = (firms_archive_source(), usgs_streamflow_archive_source())
    return MappingProxyType({source.source_name: source for source in sources})


def archive_source(lane: BackfillLane) -> IngestionSource:
    """Resolve the lane's source token, naming what is buildable rather than returning None into a walk."""
    sources = archive_sources()
    source = sources.get(lane.source_name)
    if source is None:
        known = ", ".join(sorted(sources)) or "nothing"
        raise UnknownArchiveSourceError(f"lane {lane.name!r} walks unknown source {lane.source_name!r}; have {known}")
    return source


@dataclass(frozen=True, slots=True)
class ArchiveWindowRequest:
    """One work item's payload read back as typed values, rather than as an untrusted JSONB blob."""

    lane_name: str
    window: HistoryWindow
    bbox: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> ArchiveWindowRequest:
        """Read a stored payload, refusing a shape that names no walkable window."""
        bbox = payload.get(PAYLOAD_BBOX)
        if bbox is not None and (not isinstance(bbox, str) or not bbox.strip()):
            raise ArchiveWalkPayloadError(f"work item payload field {PAYLOAD_BBOX!r} must be a bbox string or absent")
        try:
            window = HistoryWindow(
                start=_payload_instant(payload, PAYLOAD_WINDOW_START),
                end=_payload_instant(payload, PAYLOAD_WINDOW_END),
            )
        except ValueError as error:
            raise ArchiveWalkPayloadError(f"work item payload names no walkable window: {error}") from error
        return cls(lane_name=_payload_text(payload, PAYLOAD_LANE), window=window, bbox=bbox)


def _payload_text(payload: Mapping[str, object], key: str) -> str:
    """Read one required string out of a stored payload, refusing an absent or empty value."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArchiveWalkPayloadError(f"work item payload field {key!r} must be a non-empty string")
    return value.strip()


def _payload_instant(payload: Mapping[str, object], key: str) -> datetime:
    """Read one required tz-aware instant out of a stored payload, refusing a naive local guess."""
    raw = _payload_text(payload, key)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ArchiveWalkPayloadError(f"work item payload field {key!r} is not an ISO-8601 instant") from error
    if parsed.tzinfo is None:
        raise ArchiveWalkPayloadError(f"work item payload field {key!r} must name its timezone")
    return parsed.astimezone(UTC)


def archive_lane_definition_name(lane: BackfillLane) -> str:
    """The `agri.job_definition.name` one lane executes under, so a slice targets exactly one lane.

    THE SINGLE SOURCE OF TRUTH for that name. `archive_lane_definition_spec` is the only thing that
    writes `job_definition.name`, and it takes the value from here -- so anything that reads the ledger
    back (a completeness report, an operator query, a `--definition` argument) must call this function
    rather than spell the token itself. A hard-coded second spelling is not a cosmetic duplicate: it
    joins to nothing, and a completeness report that joins to nothing reports no missing shards, which
    is the same silence as a walk that never ran.
    """
    return f"{ARCHIVE_WALK_DEFINITION_PREFIX}.{lane.name}"


def archive_lane_run_key(lane: BackfillLane) -> str:
    """The lane's `logical_run_key`, which carries its FLOOR so a deeper walk cannot land in a finished run.

    The bash sentinel recorded that a walk had completed but not the floor it completed at, so a later
    decision to go deeper could never resume on its own -- `firms-archive-full.sh` exists solely to
    delete that file. Here the floor is part of the run's identity: lowering it mints a different run
    with its own counters, and the completed one stays completed instead of being quietly reopened.
    """
    return f"{ARCHIVE_WALK_RUN_KEY_PREFIX}:{lane.name}:{lane.floor_day}"


def archive_window_shard_key(lane: BackfillLane, lane_window: LaneWindow) -> str:
    """The window's run-scoped idempotency key, spelled the way the bash failure ledger spelled its lines."""
    return f"{lane.name}:{lane_window.label}"


def archive_window_payload(
    lane: BackfillLane,
    lane_window: LaneWindow,
    *,
    bbox: str | None = None,
) -> dict[str, object]:
    """Render the window a handler is handed; the bounds are stored, never re-derived from the shard key."""
    payload: dict[str, object] = {
        PAYLOAD_LANE: lane.name,
        PAYLOAD_WINDOW_START: lane_window.window.start.isoformat(),
        PAYLOAD_WINDOW_END: lane_window.window.end.isoformat(),
    }
    if bbox is not None:
        payload[PAYLOAD_BBOX] = bbox
    return payload


def archive_lane_definition_spec(lane: BackfillLane) -> JobDefinitionSpec:
    """The declarative job definition one lane runs under; every number is argued at its constant above."""
    return JobDefinitionSpec(
        name=archive_lane_definition_name(lane),
        version=ARCHIVE_WALK_DEFINITION_VERSION,
        handler=ARCHIVE_WALK_HANDLER_TOKEN,
        queue_name=ARCHIVE_WALK_QUEUE_NAME,
        # No `schedule`: Railway's `cronSchedule` is the only thing that fires a tick, and nothing in
        # this service reads `job_definition.schedule`. A string here would be a second, unenforced
        # source of truth that drifts from `infra/cron-*/railway.json` without anything noticing.
        schedule=None,
        # The bash held a per-lane `mkdir` lock so a scheduled tick landing on a running walk exited
        # rather than starting a second one. This is the declarative form of that lock. Note it is
        # advisory today: the ledger has no concurrency-key enforcement, so what actually stops two
        # workers on one shard is the fencing token, not this key.
        concurrency_key=f"{ARCHIVE_WALK_RUN_KEY_PREFIX}:{lane.name}",
        max_attempts=ARCHIVE_WALK_MAX_ATTEMPTS,
        lease_seconds=ARCHIVE_WALK_LEASE_SECONDS,
        time_budget_seconds=ARCHIVE_WALK_TIME_BUDGET_SECONDS,
        retry_policy=DEFAULT_RETRY_POLICY,
        parameters=lane.to_parameters(),
    )


def archive_lane_work_items(
    lane: BackfillLane,
    *,
    end: datetime | None = None,
    bbox: str | None = None,
) -> tuple[JobWorkItemSpec, ...]:
    """Fan a lane out into one work item per window, newest first, with priority carrying that ordering.

    `priority` is the window's index on the lane's floor-anchored grid rather than its position in this
    list, so a later replan never has to renumber anything: a new window at the present simply gets a
    higher index, and the claim's `ORDER BY priority DESC` keeps walking backward from the newest.
    """
    boundary = end if end is not None else datetime.now(UTC)
    return tuple(
        JobWorkItemSpec(
            shard_key=archive_window_shard_key(lane, lane_window),
            kind=ARCHIVE_WALK_WORK_ITEM_KIND,
            payload=archive_window_payload(lane, lane_window, bbox=bbox),
            priority=lane_window.grid_index,
        )
        for lane_window in lane_windows(lane, boundary)
    )


async def plan_archive_lane(
    session: AsyncSession,
    lane: BackfillLane,
    *,
    end: datetime | None = None,
    bbox: str | None = None,
    requested_by: str | None = None,
) -> OpenedJobRun:
    """Declare the lane and fan its windows out, idempotently on both unique keys. The caller commits.

    Idempotent twice over: `open_job_run` inserts the run `ON CONFLICT (logical_run_key) DO NOTHING` and
    each window `ON CONFLICT (job_run_id, shard_key) DO NOTHING`, and `lane_windows` anchors its grid at
    the floor so re-planning the same lane on the same day produces byte-identical shard keys.

    Replanning on a LATER day adds ONLY the whole windows that day completed, and re-keys nothing. There
    is no overlapping window and no window whose bounds move, because `lane_windows` plans no trailing
    partial: every planned window starts and ends on the fixed grid, so a replan on every tick of every
    day is a set of `DO NOTHING` inserts plus, at most once per `window_days`, one genuinely new shard.
    That is what stops a daily replan from both duplicating fetch work and starving the backward walk of
    the highest priority -- see `lane_windows` for the arithmetic.
    """
    definition = await ensure_job_definition(session, archive_lane_definition_spec(lane))
    target_partitions: dict[str, object] = {"lane": lane.name, "floor": lane.floor_day}
    if bbox is not None:
        target_partitions[PAYLOAD_BBOX] = bbox
    return await open_job_run(
        session,
        definition,
        logical_run_key=archive_lane_run_key(lane),
        work_items=archive_lane_work_items(lane, end=end, bbox=bbox),
        requested_by=requested_by,
        target_partitions=target_partitions,
    )


def chunk_position(cursor: Mapping[str, object] | None, chunks: Sequence[HistoryWindow]) -> int:
    """Find which chunk the stored cursor says to walk next, refusing a cursor that names none of them.

    A cursor naming no chunk of this window means the durable record and the code no longer agree -- the
    lane's `chunk_days` changed under a run in flight, most likely. Restarting from the top would be
    safe but would hide that, and the one behaviour this port exists to remove is a walk that keeps
    going once its own record has stopped making sense. Failing here is instant and costs no fetch.
    """
    if cursor is None:
        return 0
    marker = cursor.get(CURSOR_NEXT_CHUNK_START)
    if not isinstance(marker, str):
        raise ArchiveWalkCursorError(f"stored cursor field {CURSOR_NEXT_CHUNK_START!r} must be an ISO-8601 instant")
    for position, chunk in enumerate(chunks):
        if chunk.start.isoformat() == marker:
            return position
    raise ArchiveWalkCursorError(f"stored cursor {marker!r} names no chunk of this window")


def _cursor_count(cursor: Mapping[str, object] | None, key: str) -> int:
    """Read one running total off the cursor, treating an absent or non-integer value as a fresh start."""
    if cursor is None:
        return 0
    value = cursor.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


def _detail_count(result: IngestionJobResult, key: str) -> int:
    """Read one count off a chunk result's details, treating an absent entry as zero rather than raising."""
    return result.details.get(key, 0)


def chunk_budget_seconds(cursor: Mapping[str, object] | None) -> int:
    """The wall time this window's next chunk is assumed to need, measured from the chunks it already walked."""
    slowest = _cursor_count(cursor, CURSOR_SLOWEST_CHUNK_SECONDS)
    if slowest <= 0:
        return ARCHIVE_WALK_WORST_CHUNK_SECONDS
    return min(slowest * ARCHIVE_WALK_CHUNK_ESTIMATE_MARGIN, ARCHIVE_WALK_WORST_CHUNK_SECONDS)


def walk_metrics(  # noqa: PLR0913 - the lane plus the five cumulative window counters, all keyword-only
    lane: BackfillLane,
    *,
    chunks_walked: int,
    chunks_total: int,
    records_seen: int,
    records_written: int,
    records_rejected: int,
) -> dict[str, object]:
    """The cumulative window counters, spelled once so a walked chunk and a budget yield report the same keys."""
    return {
        "lane": lane.name,
        "chunks_walked": chunks_walked,
        "chunks_total": chunks_total,
        "record_cap": lane.max_source_records,
        "window_records_seen": records_seen,
        "window_records_written": records_written,
        # Carried so the number `backfill_outcome` fails a window on is the number an operator reads off
        # `job_work_item.metrics`, rather than one recomputed from a different place months later.
        "window_records_rejected": records_rejected,
    }


def cursor_walk_metrics(
    lane: BackfillLane,
    cursor: Mapping[str, object] | None,
    *,
    chunks_walked: int,
    chunks_total: int,
) -> dict[str, object]:
    """What this window has walked according to its stored cursor alone, for a stop that ran no chunk itself."""
    return walk_metrics(
        lane,
        chunks_walked=chunks_walked,
        chunks_total=chunks_total,
        records_seen=_cursor_count(cursor, CURSOR_RECORDS_SEEN),
        records_written=_cursor_count(cursor, CURSOR_RECORDS_WRITTEN),
        records_rejected=_cursor_count(cursor, CURSOR_RECORDS_REJECTED),
    )


def budget_stop_outcome(
    invocation: JobInvocation,
    estimated_seconds: int,
    *,
    metrics: Mapping[str, object] = EMPTY_JSON_OBJECT,
) -> JobHandlerOutcome:
    """Yield the window to the next tick rather than starting a chunk this tick cannot finish.

    A yield and not a failure, and no longer a deferral either. Both park on
    `jobs/lease.py::defer_work_item`, which raises the item's `max_attempts` in the same statement that
    parks it, so the wait still costs none of the retry budget the measured 169-of-298 first-attempt
    failures need. What `yielded` adds is that it lands in `summary.yielded` instead of
    `summary.deferred`, and that distinction is the whole reason to prefer it: "this tick ran out of
    clock" and "upstream had nothing to give" are different operator problems, and a walk whose windows
    are 5 chunks long yields far more often than it defers, so conflating them buries the deferrals.

    It pins no `resume_at` and MAY NOT -- `JobHandlerOutcome` refuses one on a yield. That used to be the
    reason this returned a deferral parked past the tick's own deadline: `run_job_slice` claimed until its
    deadline rather than until a park, so a shard parked to `now()` was immediately claimable again by the
    same loop and would spin against its own refusal for the rest of the tick. The runtime now ENDS the
    slice on a yield (`stop_reason="time_budget_exhausted"`), so there is no loop left to respin it and
    the next tick is the earliest thing that can claim it -- which is what the old deadline arithmetic was
    approximating all along.

    `metrics` is what the window has already walked, carried so a shard that spends several ticks parked
    on the clock still reports its running totals on every parked attempt rather than only on the tick
    that finally finishes it.
    """
    return JobHandlerOutcome.yielded(
        reason=(
            f"{BUDGET_STOP_REASON}: {estimated_seconds}s estimated against "
            f"{invocation.seconds_remaining:.0f}s left of this tick"
        ),
        progress_fraction=invocation.progress_fraction,
        metrics=metrics,
    )


def backfill_outcome(
    result: IngestionJobResult,
    *,
    next_cursor: Mapping[str, object] | None,
    progress_fraction: float,
    metrics: Mapping[str, object],
) -> JobHandlerOutcome:
    """Map one folded chunk result to a runtime outcome, refusing to call a chunk that wrote nothing done.

    `ingested` is the ONLY status allowed to advance the shard, and it is not sufficient on its own.
    `failed` carries `result.reason` verbatim, because `backfill._truncated_chunk_reason` already names
    the narrower `--chunk-days` that would have fitted under the cap, and re-deriving that text here
    would let the two spellings drift. `skipped` is a FAILURE here even though `run_source_backfill`
    calls it a skip: an unconfigured `INGEST_BBOX` and a typed history refusal both mean this window
    wrote nothing, and completing a window that wrote nothing is precisely the silent hole this whole
    port exists to make impossible.

    The three ways a chunk can end at zero rows are deliberately NOT treated alike:

    - `records_seen == 0` stays a success. FIRMS publishes nothing for a day no product covers and a
      winter day genuinely holds no detections; failing those would turn most of the archive red and
      teach an operator to ignore the colour.
    - `records_written == 0` on its own stays a success, and cannot be the trigger. `FeatureWriter`
      returns "rows inserted plus genuinely-changed refreshes" (writer.py), so re-walking a window that
      already landed writes zero rows ON PURPOSE -- which is the very thing that makes a replan free.
    - `rejected == records_seen`, with records seen, is a FAILURE. Every record failed
      `source.build_write` or the source's freshness rule, so `select_writes` produced no writes at all
      and the writer was never handed anything to be idempotent about.

    Freshness cannot raise a false alarm on either archive lane: both declare
    `FreshnessRule(max_observation_age=None, accepts_undated_records=False)` (firms.py, usgs_nwis.py) so
    a walk's own window IS its age bound and `accepts` returns True for a dated record of any age. An
    OLD window can therefore never be rejected for being old. The one thing the rule refuses is an
    undated record, which for these two producers means one that did not parse.
    """
    if result.status != "ingested":
        failure_class = (
            TRUNCATED_FAILURE_CLASS
            if result.truncated
            else (SKIPPED_FAILURE_CLASS if result.status == "skipped" else UPSTREAM_FAILURE_CLASS)
        )
        return JobHandlerOutcome.failed(
            failure_class, result.reason or f"{result.source} wrote nothing", metrics=metrics
        )
    rejected = _detail_count(result, REJECTED_DETAIL)
    if result.records_seen > 0 and rejected >= result.records_seen:
        return JobHandlerOutcome.failed(
            TOTAL_REJECTION_FAILURE_CLASS,
            f"{result.source} fetched {result.records_seen} records for this chunk and rejected all "
            f"{rejected} of them before the write path, so the chunk wrote nothing and the window is "
            f"not walked",
            metrics=metrics,
        )
    if next_cursor is None:
        return JobHandlerOutcome.completed(metrics=metrics)
    return JobHandlerOutcome.progressed(next_cursor, progress_fraction=progress_fraction, metrics=metrics)


async def walk_archive_chunk(
    lane: BackfillLane,
    chunk: HistoryWindow,
    *,
    bbox: str | None = None,
) -> IngestionJobResult:
    """Walk exactly one chunk through `run_source_backfill`, under the lane's declared record ceiling."""
    context = current_archive_walk_context()
    plan = BackfillPlan(
        window=chunk,
        chunk=lane.chunk,
        bbox=bbox if bbox is not None else context.bbox,
        client=context.client,
    )
    with pinned_source_record_cap(lane):
        results = await run_source_backfill(archive_source(lane), context.write_features, plan)
    return merge_backfill_results(lane.source_name, results)


@job_handler(ARCHIVE_WALK_HANDLER_TOKEN)
async def archive_walk_handler(invocation: JobInvocation) -> JobHandlerOutcome:
    """Walk ONE chunk of one archive window, leaving every loop, retry and landing to the runtime.

    One work item is one window and one handler call is one chunk. The bash cursor file is now the set of
    work items still in a non-terminal state and the bash failure ledger is the set in `retry_wait` or
    `dead_letter`; both are rows an operator can query, which neither file ever was.

    The one thing this handler does NOT leave entirely to the runtime is the time budget, and it cannot:
    the runtime tests its deadline before each call and a call is uninterruptible, so only the handler is
    in a position to refuse to start a chunk that would run past it. See `chunk_budget_seconds`.
    """
    request = ArchiveWindowRequest.from_payload(invocation.payload)
    lane = resolve_lane(request.lane_name)
    missing_credential = lane.missing_credential()
    if missing_credential is not None:
        # A deployment fault, not a transient one, and deliberately loud: it burns the attempt budget and
        # dead-letters rather than parking, because a window parked forever on a missing variable is the
        # same invisible hole as a window that completed without writing.
        return JobHandlerOutcome.failed(
            MISSING_CREDENTIAL_FAILURE_CLASS,
            f"lane {lane.name} needs {missing_credential} and the environment does not set it",
        )
    chunks = history_chunks(request.window, lane.chunk)
    if not chunks:  # pragma: no cover - HistoryWindow refuses start >= end, so a stored window always cuts.
        # A typed refusal rather than the `chunks[position]` IndexError it would otherwise be: an
        # IndexError names nothing an operator can act on and still spends all eight attempts, 30s
        # doubling to an hour, before the shard dead-letters.
        raise ArchiveWalkPayloadError(
            f"work item payload names a window that cuts into no chunk: "
            f"{request.window.start.isoformat()}..{request.window.end.isoformat()}"
        )
    position = chunk_position(invocation.cursor, chunks)
    estimated_seconds = chunk_budget_seconds(invocation.cursor)
    if not invocation.has_budget_for(estimated_seconds):
        # Declined BEFORE the heartbeat and before any fetch. The runtime tests its deadline before
        # calling a handler and a handler call cannot be interrupted, so the decision to START a chunk is
        # the only place a tick's budget can be honoured at all.
        return budget_stop_outcome(
            invocation,
            estimated_seconds,
            metrics=cursor_walk_metrics(
                lane,
                invocation.cursor,
                chunks_walked=position,
                chunks_total=len(chunks),
            ),
        )
    if not await invocation.heartbeat():
        # The runtime abandons a shard whose fence moved whatever we return; returning instead of
        # fetching is what stops a superseded worker from spending 11 minutes writing under a dead lease.
        return JobHandlerOutcome.failed(FENCE_LOST_FAILURE_CLASS, "another worker owns this window")

    chunk = chunks[position]
    chunk_label = f"{chunk.start.date().isoformat()}..{chunk.end.date().isoformat()}"
    started_at = time.monotonic()
    result = await walk_archive_chunk(lane, chunk, bbox=request.bbox)
    # Floored at one second: the value is the bound the NEXT chunk is held to, not a metric anyone
    # reports, and a zero would read as "this window has measured nothing" on the way back out.
    chunk_seconds = max(math.ceil(time.monotonic() - started_at), 1)

    next_position = position + 1
    chunk_rejected = _detail_count(result, REJECTED_DETAIL)
    records_seen = _cursor_count(invocation.cursor, CURSOR_RECORDS_SEEN) + result.records_seen
    records_written = _cursor_count(invocation.cursor, CURSOR_RECORDS_WRITTEN) + result.records_written
    records_rejected = _cursor_count(invocation.cursor, CURSOR_RECORDS_REJECTED) + chunk_rejected
    slowest_chunk_seconds = max(_cursor_count(invocation.cursor, CURSOR_SLOWEST_CHUNK_SECONDS), chunk_seconds)
    next_cursor = (
        None
        if next_position >= len(chunks)
        else {
            CURSOR_NEXT_CHUNK_START: chunks[next_position].start.isoformat(),
            CURSOR_RECORDS_SEEN: records_seen,
            CURSOR_RECORDS_WRITTEN: records_written,
            CURSOR_RECORDS_REJECTED: records_rejected,
            CURSOR_SLOWEST_CHUNK_SECONDS: slowest_chunk_seconds,
        }
    )
    metrics: dict[str, object] = {
        **walk_metrics(
            lane,
            chunks_walked=next_position,
            chunks_total=len(chunks),
            records_seen=records_seen,
            records_written=records_written,
            records_rejected=records_rejected,
        ),
        # Only a call that actually walked something can name which chunk it was and how long it took, so
        # these two keys are the difference between this metrics shape and a budget yield's.
        "chunk": chunk_label,
        "chunk_seconds": chunk_seconds,
    }
    logger.info(
        "archive_walk_chunk",
        lane=lane.name,
        shard_key=invocation.shard_key,
        chunk=chunk_label,
        status=result.status,
        records_seen=result.records_seen,
        records_written=result.records_written,
        records_rejected=chunk_rejected,
        chunk_seconds=chunk_seconds,
    )
    return backfill_outcome(
        result,
        next_cursor=next_cursor,
        progress_fraction=next_position / len(chunks),
        metrics=metrics,
    )


__all__ = [
    "ARCHIVE_WALK_CHUNK_ESTIMATE_MARGIN",
    "ARCHIVE_WALK_DEFINITION_VERSION",
    "ARCHIVE_WALK_HANDLER_TOKEN",
    "ARCHIVE_WALK_LEASE_SECONDS",
    "ARCHIVE_WALK_MAX_ATTEMPTS",
    "ARCHIVE_WALK_QUEUE_NAME",
    "ARCHIVE_WALK_TIME_BUDGET_SECONDS",
    "ARCHIVE_WALK_WORK_ITEM_KIND",
    "ARCHIVE_WALK_WORST_CHUNK_SECONDS",
    "ArchiveWalkContext",
    "ArchiveWalkContextError",
    "ArchiveWalkCursorError",
    "ArchiveWalkPayloadError",
    "ArchiveWindowRequest",
    "UnknownArchiveSourceError",
    "archive_lane_definition_name",
    "archive_lane_definition_spec",
    "archive_lane_run_key",
    "archive_lane_work_items",
    "archive_source",
    "archive_sources",
    "archive_walk_context",
    "archive_walk_handler",
    "archive_window_payload",
    "archive_window_shard_key",
    "backfill_outcome",
    "budget_stop_outcome",
    "chunk_budget_seconds",
    "chunk_position",
    "current_archive_walk_context",
    "cursor_walk_metrics",
    "pinned_source_record_cap",
    "plan_archive_lane",
    "walk_archive_chunk",
    "walk_metrics",
]
