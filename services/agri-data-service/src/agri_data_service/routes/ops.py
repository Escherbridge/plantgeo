"""Live operator console over the agri.job_* durable backfill ledger."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping  # noqa: TC003 - Final[Mapping[...]] is evaluated at import time.
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

import structlog
from sanic import Blueprint, Request, html, json, raw
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.
from sqlalchemy import ARRAY, Text, bindparam, text

from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.ingest.validation.models import DEFAULT_STREAM_DEFINITIONS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.elements import TextClause

logger = structlog.get_logger()

ops_bp = Blueprint("ops", url_prefix="/ops")

_LANES_SQL: Final = text(load_query_sql("routes/ops_backfill_lanes.sql"))
_WALKS_SQL: Final = text(load_query_sql("routes/ops_historical_walks.sql"))
_FAILURES_SQL: Final = text(load_query_sql("routes/ops_backfill_failures.sql"))
_DEAD_LETTER_TREND_SQL: Final = text(load_query_sql("routes/ops_backfill_dead_letter_trend.sql"))
_DATA_STREAMS_SQL: Final = text(load_query_sql("routes/ops_data_streams.sql"))
_LANE_EVIDENCE_SQL: Final = text(load_query_sql("routes/ops_lane_landed_evidence.sql")).bindparams(
    bindparam("stream_names", type_=ARRAY(Text))
)

# Every statement this page issues is bounded by the transaction-local timeout sql.md prescribes
# for direct SQL. Three of them scan whole tables and those tables only grow -- finishing the FIRMS
# archive walk this panel exists to watch multiplies its layer of geo.features roughly twentyfold --
# and `/ops` is unauthenticated, so an unbounded scan is a way for anyone who can reach the route to
# pin a production connection indefinitely. Crossing this raises; `_load_snapshot` catches it and the
# page shows the timeout in its error banner, which is a loud failure rather than a silent one.
# SET LOCAL stays inline here per sql/AGENTS.md: one line, no bind parameter (SET LOCAL cannot take
# one), and it bakes in the constant above it.
_STATEMENT_TIMEOUT_SECONDS: Final = 120
_SET_STATEMENT_TIMEOUT: Final[TextClause] = text(
    f"-- ops_statement_timeout\nSET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_SECONDS}s'"
)

# Which published stream each ledger lane fills. DERIVED from the stream catalog, which already
# ties a lane to its stream through `archive_lane_definition_name` over the registered lane
# objects, so this page and the validity report cannot spell the same lane two different ways. A
# lane absent from here simply gets no landed-evidence line, which is the honest outcome for a
# lane whose output nothing has mapped yet.
_LANE_STREAMS: Final[Mapping[str, str]] = MappingProxyType(
    {lane_name: definition.stream for definition in DEFAULT_STREAM_DEFINITIONS for lane_name in definition.lane_names}
)
_NO_LANE_EVIDENCE: Final[Mapping[str, dict[str, Any]]] = MappingProxyType({})

_DATASTAR_PATH: Final = Path(__file__).resolve().parents[1] / "static" / "datastar.js"
_DATASTAR_URL: Final = "/ops/static/datastar.js"
_JAVASCRIPT_CONTENT_TYPE: Final = "text/javascript; charset=utf-8"
_HTTP_NOT_FOUND: Final = 404

_DEFAULT_THROUGHPUT_WINDOW_HOURS: Final = 24
_MIN_THROUGHPUT_WINDOW_HOURS: Final = 1
_MAX_THROUGHPUT_WINDOW_HOURS: Final = 720
_DEFAULT_STREAM_INTERVAL_SECONDS: Final = 5
_MIN_STREAM_INTERVAL_SECONDS: Final = 2
_MAX_STREAM_INTERVAL_SECONDS: Final = 30
_FAILURE_ROW_LIMIT: Final = 40
_ERROR_SUMMARY_CHARACTERS: Final = 260
_DEAD_LETTER_TREND_DAYS: Final = 21

# Freshness bands for the data-loads table. Deliberately generous: a daily upstream that
# published this morning and one that published yesterday are both healthy, and a band that
# called the second one late would cry wolf every night.
_STREAM_FRESH_DAYS: Final = 2
_STREAM_AGING_DAYS: Final = 30

# Three of the six statements scan a whole table and cannot ride the 5 s refresh. Each is
# cached on its own clock, because "how long may this number be stale before it misleads"
# has a different answer for each of them. Without any cache the SSE loop would re-run every
# scan every 5 s and each connected operator would hold a permanent scan open against prod.
#
#   data loads    ~26 s -- aggregates all ~46M rows of agri.signal_observation and all of
#                 geo.features. A backfill's landed coverage does not change meaningfully
#                 inside a minute, let alone five.
#   walks         ~2.0 s -- the release-grain value count is an index-only scan over the same
#                 46M rows. A minute is well inside the 15-minute band that decides whether a
#                 walk reads active, and chunks land minutes apart at best.
#   lane evidence ~1.5 s -- scans the two archive layers of geo.features (~2.4M rows). A
#                 minute is far short of the six hours that make a lane's ledger read stale,
#                 so the cross-check never flips on cache age alone.
_STREAMS_CACHE_SECONDS: Final = 300
_WALKS_CACHE_SECONDS: Final = 60
_LANE_EVIDENCE_CACHE_SECONDS: Final = 60
_STREAMS_CACHE_KEY: Final = "data-streams"
# Keyed by statement (and by the window it was bound with, where that changes the answer), so
# two operators watching different rate windows never read each other's numbers.
_QUERY_CACHE: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}

# How long a non-terminal lane's ledger may stay silent before the page stops presenting its
# counters as current state. Six hours is deliberately generous: the slowest single unit of
# work either archive lane owns is a 5-day FIRMS window at the measured 11.5 minutes per
# peak-season day, i.e. about an hour, so nothing that is merely between windows can trip
# this. It is a display threshold only -- crossing it changes no number, it only stops a
# frozen number from being read as a live one.
_LEDGER_STALE_HOURS: Final = 6

# The statuses that mean the ledger has SETTLED a run and owes it no further writes. This is not a
# guess: sql/jobs/refresh_job_run_rollup.sql moves a run out of 'running' only once
# succeeded + failed >= total, and the job_run check constraint that names the statuses which must
# carry a completed_at (`terminal_run_has_completion_time`, models/jobs.py) is exactly this set. A
# settled run is entitled to a silent ledger forever, so its silence is never evidence of anything.
_TERMINAL_RUN_STATUSES: Final[frozenset[str]] = frozenset(
    {"succeeded", "partial", "failed", "dead_letter", "cancelled"}
)

# The earliest possible ordering key, for a run that carries neither a schedule nor a start time.
_BEFORE_ANY_RUN: Final = datetime.min.replace(tzinfo=UTC)

# A stream missing more than a month of interior days is reported as severe rather than
# merely notable: at that size the hole is a lane that stopped, not an upstream that skipped
# a few publications.
_GAP_SEVERE_DAYS: Final = 30
# Below one day the median step is a rounding artefact of sub-daily observations, not a
# cadence in days, so it is named rather than printed as a misleading "0d".
_CADENCE_SUBDAY: Final = 1.0

# A walk lands one release per chunk. Measured on production, chunks inside a running walk
# are 1-4 minutes apart at full tilt but tens of minutes apart when a scheduled task drives a
# continuation, so this band is deliberately the conservative one: it governs only how long
# the page keeps claiming a walk is moving, and a quieter walk reads idle with the exact age
# of its last chunk in the column beside the pill.
_WALK_ACTIVE_MINUTES: Final = 15

_SECONDS_PER_MINUTE: Final = 60
_MINUTES_PER_HOUR: Final = 60
_HOURS_PER_DAY: Final = 24
_HOURS_BEFORE_DAY_FORMAT: Final = 48
_EMPTY: Final = "&mdash;"

# The dashboard never claims to know when the cron last ran; see the "last recorded
# activity" note in the page footer and the rationale in the lane query's header.
_ACTIVITY_NOTE: Final = (
    "last recorded activity is the newest durable timestamp on this run's work items, "
    "attempts and checkpoints. It is not the last cron tick: a tick that claims nothing "
    "writes nothing to the ledger, so an idle healthy lane and a lane whose cron has not "
    "fired in days look identical from here."
)

# Shown above the lanes table only while at least one lane is actually flagged, because a
# warning that is always on the page stops being read as a warning.
_LANE_STALE_NOTE: Final = (
    "one or more lanes are marked ledger stale: their driver has stopped writing work items, "
    "so done, complete, win/h, eta and frontier are FROZEN at the timestamp beside them and are "
    "not current state. Only a lane's NEWEST run is ever checked, and only while it still owes "
    "claimable work -- a settled run, or one whose remaining items are all dead-lettered, is "
    "entitled to a silent ledger and is never flagged. The line under such a lane's name is what "
    "landed in the stream that lane fills, read straight from geo.features rather than from the "
    "ledger; it is STREAM-WIDE over the same trailing window as win/h, so a stream more than one "
    "lane writes to counts all of them, and the day pair under the frontier is the last hour only. "
    "Both readings are evidence and neither is a claim about the scheduler: rows still landing "
    "means the work is running somewhere the ledger cannot see, and no rows landing means nothing "
    "wrote recently -- not that a process died."
)

# The same honesty convention as the lane note, for the loads the ledger cannot see at all.
_WALK_NOTE: Final = (
    "plan-driven walks write no ledger item, so the lanes table above is blind to them. "
    "Everything here is derived from the source release each chunk persists: cells done "
    "counts DISTINCT lattice cells and never sums, because a continuation re-covers cells "
    "the walk already holds. State is evidence, not a claim about the scheduler -- an idle "
    "walk and a walk whose scheduled task stopped firing look identical from here, and the "
    "only thing either of them tells you is when the last chunk landed. Target and % complete "
    "are always against the full lattice a walk's cells resolve to -- a plan that deliberately "
    "covers only part of that lattice reads as low and slow-moving, including its ETA, "
    "rather than as the finished plan it may already be. Rows is the one column NOT derived "
    "from releases: it counts what those releases actually carry in agri.signal_observation, "
    "because a chunk publishes a release whether or not the upstream returned anything. A walk "
    "showing every cell and every chunk with zero rows landed nothing at all, and its releases "
    "stay on the page because they are real provenance of what was attempted -- state reads "
    "no_data rather than complete, and the two walks below that share a label and differ only "
    "in their source key are exactly that pair."
)


@dataclass(frozen=True, slots=True)
class _ScanQuery:
    """One whole-table statement and how long its answer may be reused before it misleads."""

    cache_key: str
    statement: TextClause
    parameters: dict[str, Any]
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One point-in-time read of the whole backfill ledger."""

    generated_at: datetime
    throughput_window_hours: int
    lanes: list[dict[str, Any]]
    # The plan-driven historical walks, reconstructed from source releases rather than jobs.
    walks: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    dead_letter_trend: list[dict[str, Any]]
    # Every load's landed state, including the lanes that never touch the ledger.
    streams: list[dict[str, Any]]
    error: str | None


# --- Routes -----------------------------------------------------------------------


@ops_bp.get("/backfill")
async def backfill_dashboard(request: Request) -> HTTPResponse:
    """Serve the operator console with the current snapshot already rendered."""
    interval_seconds = _bounded_query_int(
        request.args.get("interval"),
        _DEFAULT_STREAM_INTERVAL_SECONDS,
        _MIN_STREAM_INTERVAL_SECONDS,
        _MAX_STREAM_INTERVAL_SECONDS,
    )
    throughput_window_hours = _throughput_window_from(request)
    snapshot = await _load_snapshot(throughput_window_hours)
    page = _render_page(snapshot, interval_seconds=interval_seconds)
    return html(page, headers={"Cache-Control": "no-store"})


@ops_bp.get("/backfill.json")
async def backfill_snapshot_json(request: Request) -> HTTPResponse:
    """Serve the same snapshot as JSON for scripting and curl."""
    snapshot = await _load_snapshot(_throughput_window_from(request))
    return json(_json_snapshot(snapshot), headers={"Cache-Control": "no-store"})


@ops_bp.get("/backfill/stream")
async def backfill_stream(request: Request) -> None:
    """Re-run the snapshot every interval and patch the dashboard's regions over SSE."""
    interval_seconds = _bounded_query_int(
        request.args.get("interval"),
        _DEFAULT_STREAM_INTERVAL_SECONDS,
        _MIN_STREAM_INTERVAL_SECONDS,
        _MAX_STREAM_INTERVAL_SECONDS,
    )
    throughput_window_hours = _throughput_window_from(request)
    response = await request.respond(
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    try:
        while True:
            snapshot = await _load_snapshot(throughput_window_hours)
            for selector, fragment in _regions(snapshot, interval_seconds=interval_seconds):
                await response.send(_patch_elements_event(selector, fragment))
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        logger.info("ops_backfill_stream_cancelled")
        raise
    except Exception as error:  # a dropped client surfaces here as a failed write
        logger.info("ops_backfill_stream_closed", error=str(error))


@ops_bp.get("/static/datastar.js")
async def datastar_bundle(_request: Request) -> HTTPResponse:
    """Serve the vendored Datastar bundle; nothing is fetched from a CDN at runtime."""
    if not _DATASTAR_PATH.is_file():
        return raw(b"", content_type=_JAVASCRIPT_CONTENT_TYPE, status=_HTTP_NOT_FOUND)
    return raw(
        _DATASTAR_PATH.read_bytes(),
        content_type=_JAVASCRIPT_CONTENT_TYPE,
        headers={"Cache-Control": "public, max-age=604800"},
    )


# --- Snapshot ---------------------------------------------------------------------


async def _load_snapshot(throughput_window_hours: int) -> _Snapshot:
    """Read the ledger once, returning an error-bearing snapshot rather than raising."""
    generated_at = datetime.now(UTC)
    try:
        async with receiver_writer_session() as session:
            # First statement of the transaction, so every read below inherits the bound. SET LOCAL
            # is scoped to this transaction and reverts with it, which is why the cap lives here and
            # not on the shared receiver/writer pool that ingest also draws from.
            await session.execute(_SET_STATEMENT_TIMEOUT)
            lane_result = await session.execute(
                _LANES_SQL,
                {"throughput_window_hours": throughput_window_hours},
            )
            lane_rows = [dict(row) for row in lane_result.mappings().all()]
            walk_rows = await _cached_rows(
                session,
                _ScanQuery(
                    cache_key=f"walks:{throughput_window_hours}",
                    statement=_WALKS_SQL,
                    parameters={"throughput_window_hours": throughput_window_hours},
                    ttl_seconds=_WALKS_CACHE_SECONDS,
                ),
                generated_at,
            )
            lane_evidence = await _load_lane_evidence(session, generated_at, throughput_window_hours)
            failure_result = await session.execute(
                _FAILURES_SQL,
                {"row_limit": _FAILURE_ROW_LIMIT, "error_summary_limit": _ERROR_SUMMARY_CHARACTERS},
            )
            failure_rows = [dict(row) for row in failure_result.mappings().all()]
            trend_result = await session.execute(
                _DEAD_LETTER_TREND_SQL,
                {"trend_days": _DEAD_LETTER_TREND_DAYS},
            )
            trend_rows = [dict(row) for row in trend_result.mappings().all()]
            stream_rows = await _cached_rows(
                session,
                _ScanQuery(
                    cache_key=_STREAMS_CACHE_KEY,
                    statement=_DATA_STREAMS_SQL,
                    # Empty mapping rather than no argument: this query binds nothing, but every
                    # other caller here passes parameters and the session contract expects them.
                    parameters={},
                    ttl_seconds=_STREAMS_CACHE_SECONDS,
                ),
                generated_at,
            )
    except Exception as error:
        logger.warning("ops_backfill_snapshot_failed", error=str(error))
        return _Snapshot(
            generated_at=generated_at,
            throughput_window_hours=throughput_window_hours,
            lanes=[],
            walks=[],
            failures=[],
            dead_letter_trend=[],
            streams=[],
            error=f"{type(error).__name__}: {error}",
        )
    current_run_ids = _current_run_ids(lane_rows)
    return _Snapshot(
        generated_at=generated_at,
        throughput_window_hours=throughput_window_hours,
        lanes=[
            _lane_record(
                row,
                generated_at,
                throughput_window_hours,
                lane_evidence=lane_evidence,
                is_current_run=row["job_run_id"] in current_run_ids,
            )
            for row in lane_rows
        ],
        walks=[_walk_record(row, generated_at, throughput_window_hours) for row in walk_rows],
        failures=failure_rows,
        dead_letter_trend=trend_rows,
        streams=stream_rows,
        error=None,
    )


async def _cached_rows(session: Any, query: _ScanQuery, now: datetime) -> list[dict[str, Any]]:
    """Run one scanning statement, re-reading only once the cached copy has aged past its own TTL."""
    entry = _QUERY_CACHE.get(query.cache_key)
    if entry is not None and (now - entry[0]).total_seconds() < query.ttl_seconds:
        return entry[1]
    result = await session.execute(query.statement, query.parameters)
    rows = [dict(row) for row in result.mappings().all()]
    _QUERY_CACHE[query.cache_key] = (now, rows)
    return rows


async def _load_lane_evidence(
    session: Any,
    now: datetime,
    throughput_window_hours: int,
) -> Mapping[str, dict[str, Any]]:
    """Return what actually landed per lane-filled stream, keyed by stream name.

    Skipped entirely when no lane maps to a stream, so a catalog that names none never buys the
    page a table scan it has nothing to say about.
    """
    stream_names = sorted(set(_LANE_STREAMS.values()))
    if not stream_names:
        return _NO_LANE_EVIDENCE
    rows = await _cached_rows(
        session,
        _ScanQuery(
            cache_key=f"lane-evidence:{throughput_window_hours}",
            statement=_LANE_EVIDENCE_SQL,
            parameters={"stream_names": stream_names, "throughput_window_hours": throughput_window_hours},
            ttl_seconds=_LANE_EVIDENCE_CACHE_SECONDS,
        ),
        now,
    )
    return {str(row["stream"]): row for row in rows}


def _lane_record(
    row: dict[str, Any],
    generated_at: datetime,
    throughput_window_hours: int,
    *,
    lane_evidence: Mapping[str, dict[str, Any]] = _NO_LANE_EVIDENCE,
    is_current_run: bool = True,
) -> dict[str, Any]:
    """Add completion, throughput, ETA and the ledger-staleness cross-check to one lane row.

    ETA stays None when the lane is stalled, and every landed_* key stays None when nothing
    knows which stream this lane fills -- neither is invented. `is_current_run` defaults to True
    because a row considered on its own is the only run there is; only a caller holding every
    run of a lane can say otherwise, and `_current_run_ids` is how it does.
    """
    total_items = int(row["total_items"])
    succeeded_items = int(row["succeeded_items"])
    outstanding_items = int(row["outstanding_items"])
    throughput_per_hour = int(row["succeeded_in_window"]) / throughput_window_hours
    if outstanding_items == 0:
        eta_hours: float | None = 0.0
    elif throughput_per_hour > 0:
        eta_hours = outstanding_items / throughput_per_hour
    else:
        eta_hours = None
    stream = _LANE_STREAMS.get(str(row["definition_name"]))
    evidence = lane_evidence.get(stream) if stream is not None else None
    return {
        **row,
        "completion_percent": (100.0 * succeeded_items / total_items) if total_items else None,
        "throughput_per_hour": throughput_per_hour,
        "eta_hours": eta_hours,
        "eta_ready_at": None if eta_hours is None else generated_at + timedelta(hours=eta_hours),
        "owed_items": _owed_items(row),
        "ledger_quiet_hours": _ledger_quiet_hours(row, generated_at),
        "ledger_stale": _ledger_is_stale(row, generated_at, is_current_run=is_current_run),
        "landed_stream": stream,
        "landed_window_hours": throughput_window_hours,
        "landed_last_write_at": None if evidence is None else evidence["last_write_at"],
        "landed_rows_in_window": None if evidence is None else int(evidence["rows_in_window"]),
        "landed_oldest_day": None if evidence is None else evidence["oldest_day_written_recently"],
        "landed_newest_day": None if evidence is None else evidence["newest_day_written_recently"],
    }


def _current_run_ids(lane_rows: Sequence[dict[str, Any]]) -> frozenset[Any]:
    """Name the newest run of each lane -- the only run whose ledger silence still means anything.

    The lane statement returns one row per (job definition, job run), and lowering a lane's floor
    mints a SECOND run rather than extending the first, so a superseded run sits in the ledger
    with its final counters forever. Those counters are not frozen, they are finished, and the
    landed-evidence line is keyed by stream rather than by run -- so letting an old run wear the
    stale treatment would hang a permanent warning on a closed campaign and print the CURRENT
    run's live activity beside it. Ordering is read from the row rather than trusted from the
    statement's ORDER BY, so a later edit to either cannot silently change which run is checked.
    """
    newest: dict[str, dict[str, Any]] = {}
    for row in lane_rows:
        lane = str(row["definition_name"])
        held = newest.get(lane)
        if held is None or _run_ordinal(row) > _run_ordinal(held):
            newest[lane] = row
    return frozenset(row["job_run_id"] for row in newest.values())


def _run_ordinal(row: dict[str, Any]) -> datetime:
    """Order a lane's runs by when each was meant to run, then by when it actually started."""
    scheduled: datetime | None = row["scheduled_for"] or row["run_started_at"]
    return _BEFORE_ANY_RUN if scheduled is None else scheduled.astimezone(UTC)


def _owed_items(row: dict[str, Any]) -> int:
    """Count the work items this run can still claim, which is NOT `outstanding_items`.

    `outstanding_items` is `status NOT IN ('succeeded', 'cancelled')`, so a dead-lettered window
    stays in it forever. The ledger itself disagrees: refresh_job_run_rollup.sql folds dead_letter
    into `failed` and calls a run terminal once succeeded + failed reaches total. Subtracting them
    here asks the ledger's own question -- is anything still claimable -- instead of the different
    question "is anything not succeeded", which one dead letter answers yes to for all time.
    """
    return max(int(row["outstanding_items"]) - int(row["dead_letter_items"]), 0)


def _run_is_terminal(row: dict[str, Any]) -> bool:
    """True once the ledger has settled this run, by either of the two marks it writes for that."""
    return row["run_completed_at"] is not None or str(row["run_status"]) in _TERMINAL_RUN_STATUSES


def _ledger_quiet_hours(row: dict[str, Any], now: datetime) -> float | None:
    """Say how long the ledger has recorded nothing for this run, or None when it never did.

    Falls back to the run's start because a run that fanned out and was then abandoned before
    its first item settled has no activity timestamp at all, and that is the loudest silence
    there is rather than an absence of evidence.
    """
    since: datetime | None = row["last_recorded_activity_at"] or row["run_started_at"]
    if since is None:
        return None
    quiet = now - since.astimezone(UTC)
    return quiet.total_seconds() / (_SECONDS_PER_MINUTE * _MINUTES_PER_HOUR)


def _ledger_is_stale(row: dict[str, Any], now: datetime, *, is_current_run: bool = True) -> bool:
    """True when this run still owes claimable work and its ledger went quiet long enough to mislead.

    Every guard exists to keep the flag off a run that is simply done, because a warning that is
    always on the page stops being read as a warning. A superseded run is over by construction; a
    terminal run has been settled by the ledger's own finalizer; and a run whose only unsettled
    items are dead letters can never claim another window no matter how long it is watched.
    """
    if not is_current_run or _run_is_terminal(row) or _owed_items(row) == 0:
        return False
    quiet_hours = _ledger_quiet_hours(row, now)
    return quiet_hours is not None and quiet_hours >= _LEDGER_STALE_HOURS


def _walk_record(row: dict[str, Any], generated_at: datetime, throughput_window_hours: int) -> dict[str, Any]:
    """Add completion, cell rate, ETA and state to one walk row, leaving unknowns None."""
    cells_done = int(row["cells_done"])
    # NULL when the walk's cells do not all resolve to one lattice. Without a denominator
    # there is no completion and no ETA, and both must stay None rather than be invented.
    target_cells = None if row["target_cells"] is None else int(row["target_cells"])
    cells_per_hour = int(row["cells_in_window"]) / throughput_window_hours
    outstanding_cells = None if target_cells is None else max(target_cells - cells_done, 0)
    if outstanding_cells is None:
        eta_hours: float | None = None
    elif outstanding_cells == 0:
        eta_hours = 0.0
    elif cells_per_hour > 0:
        eta_hours = outstanding_cells / cells_per_hour
    else:
        eta_hours = None
    return {
        **row,
        "target_cells": target_cells,
        "outstanding_cells": outstanding_cells,
        "completion_percent": (
            (100.0 * cells_done / target_cells) if target_cells is not None and target_cells > 0 else None
        ),
        "cells_per_hour": cells_per_hour,
        "eta_hours": eta_hours,
        "eta_ready_at": None if eta_hours is None else generated_at + timedelta(hours=eta_hours),
        "observed_value_count": int(row["observed_value_count"]),
        "state": _walk_state(
            row["last_chunk_at"],
            generated_at,
            cells_done,
            target_cells,
            int(row["observed_value_count"]),
        ),
    }


def _walk_state(
    last_chunk_at: datetime | None,
    now: datetime,
    cells_done: int,
    target_cells: int | None,
    observed_value_count: int,
) -> str:
    """Name a walk's state from landed evidence alone; see the walk note for what idle omits."""
    if observed_value_count == 0:
        # Tested BEFORE completion, because this is exactly the walk that would otherwise read
        # complete: every cell covered, every chunk published, and not one value behind them. A
        # walk whose first chunk has landed a release but no rows yet also reads no_data, which
        # is the truth at that instant and corrects itself on the next read of this statement --
        # up to _WALKS_CACHE_SECONDS away, not the page's 5 s tick, because the walk query is
        # cached.
        return "no_data"
    if target_cells is not None and cells_done >= target_cells:
        return "complete"
    if last_chunk_at is not None and now - last_chunk_at.astimezone(UTC) <= timedelta(minutes=_WALK_ACTIVE_MINUTES):
        return "active"
    return "idle"


def _json_snapshot(snapshot: _Snapshot) -> dict[str, Any]:
    """Render the snapshot as JSON-safe primitives."""
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "throughput_window_hours": snapshot.throughput_window_hours,
        "activity_note": _ACTIVITY_NOTE,
        "ledger_stale_note": _LANE_STALE_NOTE,
        "ledger_stale_hours": _LEDGER_STALE_HOURS,
        "historical_walk_note": _WALK_NOTE,
        "error": snapshot.error,
        "lanes": [_json_safe(lane) for lane in snapshot.lanes],
        "historical_walks": [_json_safe(walk) for walk in snapshot.walks],
        "failures": [_json_safe(failure) for failure in snapshot.failures],
        "dead_letter_trend": [_json_safe(entry) for entry in snapshot.dead_letter_trend],
        "data_streams": [_json_safe(stream) for stream in snapshot.streams],
    }


def _json_safe(value: Any) -> Any:
    """Convert database scalars Sanic's JSON encoder cannot serialize."""
    # One branch covers both: datetime subclasses date, and each one's isoformat() already
    # renders itself correctly -- a timestamp keeps its time, a bare day stays a bare day.
    # The data-load rows carry bare dates, which a datetime-only check would let through to
    # the encoder and 500 the response.
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _throughput_window_from(request: Request) -> int:
    return _bounded_query_int(
        request.args.get("window"),
        _DEFAULT_THROUGHPUT_WINDOW_HOURS,
        _MIN_THROUGHPUT_WINDOW_HOURS,
        _MAX_THROUGHPUT_WINDOW_HOURS,
    )


def _bounded_query_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    """Clamp a query integer into range, falling back to the default when unparseable."""
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


# --- Server-sent events -----------------------------------------------------------


def _regions(snapshot: _Snapshot, *, interval_seconds: int) -> list[tuple[str, str]]:
    """Return each live region as a (CSS selector, replacement HTML) pair."""
    return [
        ("#ops-meta", _render_meta(snapshot, interval_seconds=interval_seconds)),
        ("#ops-lanes", _render_lanes(snapshot)),
        ("#ops-walks", _render_walks(snapshot)),
        ("#ops-streams", _render_streams(snapshot)),
        ("#ops-failures", _render_failures(snapshot)),
        ("#ops-deadletters", _render_dead_letter_trend(snapshot)),
    ]


def _patch_elements_event(selector: str, fragment: str) -> str:
    """Frame one fragment as a Datastar v1 element-patch event."""
    lines = [
        "event: datastar-patch-elements",
        f"data: selector {selector}",
        "data: mode outer",
    ]
    lines.extend(f"data: elements {line}" for line in fragment.split("\n"))
    return "\n".join(lines) + "\n\n"


# --- Rendering --------------------------------------------------------------------


def _render_page(snapshot: _Snapshot, *, interval_seconds: int) -> str:
    """Render the shell with the snapshot baked in and the SSE stream wired up."""
    stream_url = escape(f"/ops/backfill/stream?interval={interval_seconds}&window={snapshot.throughput_window_hours}")
    regions = "".join(fragment for _, fragment in _regions(snapshot, interval_seconds=interval_seconds))
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>agri backfill lanes</title>"
        f"<style>{_STYLE}</style>"
        f'<script type="module" src="{_DATASTAR_URL}"></script>'
        "</head><body>"
        "<header><h1>agri backfill lanes</h1>"
        '<span class="sub">agri.job_* durable execution ledger</span></header>'
        # &#39; is an apostrophe the HTML parser decodes before Datastar reads the attribute.
        f'<main><div id="ops-stream" hidden data-init="@get(&#39;{stream_url}&#39;)"></div>'
        f"{regions}</main>"
        f"<footer><p><b>last recorded activity</b> &mdash; {escape(_ACTIVITY_NOTE)}</p>"
        "<p>/ops is not authenticated. Do not expose this service publicly until it is.</p>"
        "</footer></body></html>"
    )


def _render_meta(snapshot: _Snapshot, *, interval_seconds: int) -> str:
    """Render the header strip: snapshot age, totals, and any read failure."""
    outstanding = sum(int(lane["outstanding_items"]) for lane in snapshot.lanes)
    dead_lettered = sum(int(lane["dead_letter_items"]) for lane in snapshot.lanes)
    stalled = sum(int(lane["stalled_lease_items"]) for lane in snapshot.lanes)
    throughput = sum(float(lane["throughput_per_hour"]) for lane in snapshot.lanes)
    banner = (
        f'<div class="banner bad">ledger read failed &mdash; {escape(snapshot.error)}</div>' if snapshot.error else ""
    )
    tiles = "".join(
        [
            _tile("lanes", str(len(snapshot.lanes)), ""),
            _tile("outstanding windows", f"{outstanding:,}", ""),
            _tile("dead-lettered", f"{dead_lettered:,}", "bad" if dead_lettered else ""),
            _tile("stalled leases", f"{stalled:,}", "warn" if stalled else ""),
            _tile("windows/hour", f"{throughput:,.2f}", "" if throughput > 0 else "warn"),
        ]
    )
    return (
        '<section id="ops-meta">'
        f"{banner}"
        f'<div class="tiles">{tiles}</div>'
        '<div class="stamp">'
        f"snapshot {escape(_format_stamp(snapshot.generated_at))} UTC"
        f" &middot; refresh {interval_seconds}s"
        f" &middot; rate window {snapshot.throughput_window_hours}h"
        "</div></section>"
    )


def _tile(label: str, value: str, tone: str) -> str:
    tone_class = f" {tone}" if tone else ""
    return (
        f'<div class="tile{tone_class}">'
        f'<span class="label">{escape(label)}</span>'
        f'<span class="value">{value}</span></div>'
    )


def _render_lanes(snapshot: _Snapshot) -> str:
    """Render one row per lane run, prefaced by the stale-ledger note only while one applies."""
    if not snapshot.lanes:
        return '<section id="ops-lanes"><h2>lanes</h2><p class="empty">no job runs in the ledger</p></section>'
    rows = "".join(_render_lane_row(lane, snapshot.generated_at) for lane in snapshot.lanes)
    note = (
        f'<p class="note">{escape(_LANE_STALE_NOTE)}</p>'
        if any(lane["ledger_stale"] for lane in snapshot.lanes)
        else ""
    )
    return (
        f'<section id="ops-lanes"><h2>lanes</h2>{note}<div class="scroll"><table>'
        "<thead><tr>"
        "<th>lane / run</th><th>state</th>"
        '<th class="n">done</th><th class="n">queued</th><th class="n">leased</th>'
        '<th class="n">retry</th><th class="n">defer</th><th class="n">dead</th>'
        '<th class="n">total</th><th class="n">complete</th><th class="n">win/h</th>'
        '<th class="n">eta</th><th class="n">stalled</th><th>last recorded activity</th>'
        "<th>frontier</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_lane_row(lane: dict[str, Any], generated_at: datetime) -> str:
    """Render one lane, dimming every counter the ledger has stopped moving rather than hiding it."""
    stalled = int(lane["stalled_lease_items"])
    dead_lettered = int(lane["dead_letter_items"])
    completion = lane["completion_percent"]
    eta_hours = lane["eta_hours"]
    enabled_mark = "" if lane["definition_enabled"] else ' <span class="pill warn">disabled</span>'
    # A frozen counter keeps its value -- it is the ledger's real last word and deleting it would
    # lose evidence -- but loses the tone that makes it read as live, and gains the landed line.
    stale = bool(lane["ledger_stale"])
    frozen = " dim" if stale else ""
    stale_pill = ' <span class="pill warn">ledger stale</span>' if stale else ""
    return (
        "<tr>"
        f'<td><span class="lane">{escape(str(lane["definition_name"]))}</span>{enabled_mark}'
        f'<br><span class="dim">{escape(str(lane["logical_run_key"]))}</span>'
        f'<br><span class="dim">started {_format_age(lane["run_started_at"], generated_at)}</span>'
        f"{_render_lane_evidence(lane, generated_at)}</td>"
        f"<td>{_status_pill(str(lane['run_status']))}{stale_pill}</td>"
        f'<td class="n{frozen or " ok"}">{int(lane["succeeded_items"]):,}</td>'
        f'<td class="n">{int(lane["queued_items"]):,}</td>'
        f'<td class="n">{int(lane["leased_items"]) + int(lane["running_items"]):,}</td>'
        f'<td class="n">{int(lane["retry_wait_items"]):,}</td>'
        f'<td class="n">{int(lane["deferred_items"]):,}</td>'
        f'<td class="n{" bad" if dead_lettered else ""}">{dead_lettered:,}</td>'
        f'<td class="n">{int(lane["total_items"]):,}</td>'
        f'<td class="n{frozen}">{_EMPTY if completion is None else f"{completion:.1f}%"}</td>'
        f'<td class="n{frozen}">{float(lane["throughput_per_hour"]):,.2f}</td>'
        f'<td class="n{frozen or ("" if eta_hours is not None else " warn")}">{_format_hours(eta_hours)}</td>'
        f'<td class="n{" warn" if stalled else ""}">{stalled:,}</td>'
        f'<td class="{"dim" if stale else ""}">{_format_age(lane["last_recorded_activity_at"], generated_at)}'
        f'<br><span class="dim">{escape(_format_stamp(lane["last_recorded_activity_at"]))}</span></td>'
        f'<td class="dim">{escape(str(lane["oldest_outstanding_shard_key"] or "—"))}'
        f"{_render_lane_written_days(lane)}</td>"
        "</tr>"
    )


def _render_lane_evidence(lane: dict[str, Any], generated_at: datetime) -> str:
    """Say what landed in the stream a quiet lane fills, and nothing at all otherwise.

    A healthy lane's ledger already answers the question, so repeating the store's answer beside
    it would only invite two numbers to be read as a disagreement. The count is deliberately
    worded STREAM-WIDE and carries its window: it is keyed by stream, not by run, and several
    lanes may write the same stream, so it must never be read as this run's own output.
    """
    if not lane["ledger_stale"]:
        return ""
    quiet = _format_hours(lane["ledger_quiet_hours"])
    stream = lane["landed_stream"]
    if stream is None:
        landed = "no stream is mapped to this lane, so nothing here can say whether it is still working"
        return f'<br><span class="warn">ledger quiet {quiet}</span> <span class="dim">&mdash; {landed}</span>'
    window = f"last {int(lane['landed_window_hours'])}h"
    rows_in_window = lane["landed_rows_in_window"]
    if rows_in_window:
        landed = (
            f'<span class="ok">{rows_in_window:,} rows</span> landed stream-wide in '
            f"{escape(str(stream))} ({window}), newest {_format_age(lane['landed_last_write_at'], generated_at)}"
        )
    else:
        landed = f'<span class="warn">no rows</span> landed stream-wide in {escape(str(stream))} ({window})'
    return f'<br><span class="warn">ledger quiet {quiet}</span> <span class="dim">&mdash; {landed}</span>'


def _render_lane_written_days(lane: dict[str, Any]) -> str:
    """Put the days a stale lane's stream is really writing under its frozen ledger frontier.

    Labelled with its own window because it is NOT the window the row count beside it uses: dating
    a row costs about twenty times what counting one does, so the statement reads these two columns
    over a fixed hour. Two spans an hour and a day apart, printed unlabelled in the same row, would
    read as one measurement.
    """
    oldest = lane["landed_oldest_day"]
    newest = lane["landed_newest_day"]
    if not lane["ledger_stale"] or oldest is None or newest is None:
        return ""
    return f'<br><span class="ok">writing (last hour) {_format_day(oldest)} &rarr; {_format_day(newest)}</span>'


def _render_walks(snapshot: _Snapshot) -> str:
    """Render one row per plan-driven historical walk, with both elapsed clocks in full."""
    if not snapshot.walks:
        return (
            '<section id="ops-walks"><h2>historical walks</h2>'
            '<p class="empty">no historical source releases yet</p></section>'
        )
    rows = "".join(_render_walk_row(walk, snapshot.generated_at) for walk in snapshot.walks)
    return (
        '<section id="ops-walks"><h2>historical walks</h2>'
        f'<p class="note">{escape(_WALK_NOTE)}</p>'
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>walk</th><th>state</th><th>observed window</th>"
        '<th class="n">cells</th><th class="n">complete</th><th class="n">chunks</th>'
        '<th class="n">rows</th><th class="n">cells/h</th><th class="n">eta</th>'
        "<th>started</th><th>last chunk</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_walk_row(walk: dict[str, Any], generated_at: datetime) -> str:
    """Render one walk. Started and last chunk each carry an age and the stamp behind it."""
    cells_done = int(walk["cells_done"])
    target_cells = walk["target_cells"]
    completion = walk["completion_percent"]
    eta_hours = walk["eta_hours"]
    observed_values = int(walk["observed_value_count"])
    parameter_names = walk["parameters"] or []
    parameters = escape(", ".join(str(name) for name in parameter_names)) if parameter_names else _EMPTY
    return (
        "<tr>"
        f'<td><span class="lane">{escape(str(walk["walk_label"]))}</span>'
        # The source key is what separates two walks that share a label, which is exactly the
        # shape a lane pointed at the wrong upstream model leaves behind. Without it on the page
        # the good release set and the empty one are the same row twice.
        f'<br><span class="dim">{escape(str(walk["data_source_key"]))}</span>'
        f'<br><span class="dim">{parameters}</span></td>'
        f"<td>{_status_pill(str(walk['state']))}</td>"
        f'<td class="dim">{_format_day(walk["observed_from"])} &rarr; {_format_day(walk["observed_to"])}</td>'
        f'<td class="n">{cells_done:,}'
        f'<span class="dim">/{_EMPTY if target_cells is None else f"{int(target_cells):,}"}</span></td>'
        f'<td class="n">{_EMPTY if completion is None else f"{completion:.1f}%"}</td>'
        f'<td class="n">{int(walk["chunks_landed"]):,}</td>'
        f'<td class="n{" bad" if observed_values == 0 else ""}">{observed_values:,}</td>'
        f'<td class="n">{float(walk["cells_per_hour"]):,.2f}</td>'
        f'<td class="n{"" if eta_hours is not None else " warn"}">{_format_hours(eta_hours)}</td>'
        f"<td>{_format_age(walk['started_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(walk["started_at"]))}</span></td>'
        f"<td>{_format_age(walk['last_chunk_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(walk["last_chunk_at"]))}</span></td>'
        "</tr>"
    )


def _render_streams(snapshot: _Snapshot) -> str:
    """Render every data load's landed state, ledger-backed or not."""
    if not snapshot.streams:
        return '<section id="ops-streams"><h2>data loads</h2><p class="empty">no streams readable</p></section>'
    rows = "".join(_render_stream_row(stream) for stream in snapshot.streams)
    return (
        '<section id="ops-streams"><h2>data loads</h2>'
        '<p class="note">what actually landed, across the warehouse, the map layers and the '
        "on-demand caches. A stream absent from <em>lanes</em> above is not idle: most loads "
        "write straight to their store without ever enqueuing a ledger item. "
        f"Recomputed at most every {_STREAMS_CACHE_SECONDS // _SECONDS_PER_MINUTE} min "
        f"(full-table scan); measured {_streams_cache_age(snapshot.generated_at)}.</p>"
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>kind</th><th>stream</th>"
        '<th class="n">rows</th><th class="n">coverage</th>'
        "<th>from</th><th>through</th>"
        '<th class="n">days</th><th class="n">missing</th><th class="n">largest gap</th>'
        '<th class="n">cadence</th><th>freshness</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _streams_cache_age(now: datetime) -> str:
    """Say how old the cached scan is, so a stale number is never read as a live one."""
    entry = _QUERY_CACHE.get(_STREAMS_CACHE_KEY)
    if entry is None:
        return "just now"
    return _format_age(entry[0], now)


def _render_stream_row(stream: dict[str, Any]) -> str:
    """Render one stream, tinting freshness rather than declaring a stream broken."""
    rows = int(stream["rows"] or 0)
    coverage = int(stream["coverage"] or 0)
    stale_days = stream["stale_days"]
    return (
        "<tr>"
        f'<td class="dim">{escape(str(stream["kind"]))}</td>'
        f"<td><b>{escape(str(stream['stream']))}</b></td>"
        f'<td class="n">{rows:,}</td>'
        f'<td class="n">{coverage:,} <span class="dim">{escape(str(stream["coverage_label"]))}</span></td>'
        f"<td>{_format_day(stream['from_day'])}</td>"
        f"<td>{_format_day(stream['to_day'])}</td>"
        f'<td class="n">{_format_count(stream["observed_days"])}'
        f'<span class="dim">/{_format_count(stream["span_days"])}</span></td>'
        f'<td class="n">{_gap_cell(stream["missing_days"])}</td>'
        f'<td class="n">{_gap_cell(stream["largest_gap_days"])}</td>'
        f'<td class="n">{_format_cadence(stream["median_step_days"])}</td>'
        f"<td>{_freshness_pill(rows, stale_days)}</td>"
        "</tr>"
    )


def _gap_cell(value: Any) -> str:
    """Tint a gap count. Zero is the good case and must read as such, not as absence."""
    if value is None:
        return _EMPTY
    missing = int(value)
    if missing == 0:
        return '<span class="ok">0</span>'
    tone = "bad" if missing >= _GAP_SEVERE_DAYS else "warn"
    return f'<span class="{tone}">{missing:,}</span>'


def _format_count(value: Any) -> str:
    """Render a day count, or an em dash for a stream with no time axis at all."""
    return _EMPTY if value is None else f"{int(value):,}"


def _format_cadence(value: Any) -> str:
    """Render the observed refresh interval in days, the median step between present days."""
    if value is None:
        return _EMPTY
    cadence = float(value)
    if cadence < _CADENCE_SUBDAY:
        return '<span class="dim">sub-daily</span>'
    return f"{cadence:g}d"


def _freshness_pill(rows: int, stale_days: int | None) -> str:
    """Tone a stream by age. Empty and undated are distinct states, not both 'no data'."""
    if rows == 0:
        return '<span class="pill bad">empty</span>'
    if stale_days is None:
        # Reference layers (soil survey, gauge sites) carry no observation day at all;
        # that is their nature, not a fault, so it must not read as a failure.
        return '<span class="pill dim">undated</span>'
    days = int(stale_days)
    if days <= _STREAM_FRESH_DAYS:
        return f'<span class="pill ok">{days}d</span>'
    if days <= _STREAM_AGING_DAYS:
        return f'<span class="pill warn">{days}d</span>'
    return f'<span class="pill bad">{days}d</span>'


def _format_day(value: Any) -> str:
    """Render a date column, distinguishing a missing day from an empty stream."""
    return _EMPTY if value is None else escape(str(value)[:10])


def _render_failures(snapshot: _Snapshot) -> str:
    """Render the newest dead-lettered and retry-waiting windows with their error text."""
    if not snapshot.failures:
        return (
            '<section id="ops-failures"><h2>failures</h2>'
            '<p class="empty">no dead-lettered or retry-waiting windows</p></section>'
        )
    rows = "".join(_render_failure_row(failure, snapshot.generated_at) for failure in snapshot.failures)
    return (
        '<section id="ops-failures"><h2>failures</h2><div class="scroll"><table>'
        "<thead><tr>"
        "<th>lane</th><th>window</th><th>state</th>"
        '<th class="n">attempts</th><th>class</th><th>when</th><th>error</th>'
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_failure_row(failure: dict[str, Any], generated_at: datetime) -> str:
    occurred_at = failure["completed_at"] or failure["attempt_finished_at"] or failure["next_attempt_at"]
    failure_class = failure["attempt_failure_class"] or failure["last_error_class"] or "unknown"
    summary = failure["attempt_error_summary"] or failure["last_error_summary"] or ""
    return (
        "<tr>"
        f'<td class="lane">{escape(str(failure["definition_name"]))}</td>'
        f"<td>{escape(str(failure['shard_key']))}</td>"
        f"<td>{_status_pill(str(failure['item_status']))}</td>"
        f'<td class="n">{int(failure["attempt_count"])}/{int(failure["max_attempts"])}</td>'
        f'<td class="dim">{escape(str(failure_class))}</td>'
        f"<td>{_format_age(occurred_at, generated_at)}</td>"
        f'<td class="err">{escape(str(summary))}</td>'
        "</tr>"
    )


def _render_dead_letter_trend(snapshot: _Snapshot) -> str:
    """Render the daily dead-letter counts and their running total per lane."""
    if not snapshot.dead_letter_trend:
        return (
            '<section id="ops-deadletters"><h2>dead-letter trend</h2>'
            f'<p class="empty">no dead letters in the last {_DEAD_LETTER_TREND_DAYS} days</p></section>'
        )
    peak = max(int(entry["dead_lettered"]) for entry in snapshot.dead_letter_trend)
    rows = "".join(_render_trend_row(entry, peak) for entry in snapshot.dead_letter_trend)
    return (
        '<section id="ops-deadletters"><h2>dead-letter trend</h2><div class="scroll"><table>'
        '<thead><tr><th>lane</th><th>day</th><th class="n">dead</th>'
        '<th class="n">cumulative</th><th>shape</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_trend_row(entry: dict[str, Any], peak: int) -> str:
    dead_lettered = int(entry["dead_lettered"])
    width = 100.0 * dead_lettered / peak if peak else 0.0
    return (
        "<tr>"
        f'<td class="lane">{escape(str(entry["definition_name"]))}</td>'
        f'<td class="dim">{escape(_format_stamp(entry["day"])[:10])}</td>'
        f'<td class="n bad">{dead_lettered:,}</td>'
        f'<td class="n">{int(entry["cumulative_dead_lettered"]):,}</td>'
        f'<td><span class="bar" style="width:{width:.1f}%"></span></td>'
        "</tr>"
    )


def _status_pill(status: str) -> str:
    # Ledger run/item statuses and the three historical-walk states share one map because
    # their vocabularies are disjoint; an unknown status tones dim rather than raising.
    tone = {
        "succeeded": "ok",
        "complete": "ok",
        "active": "info",
        "idle": "warn",
        "running": "info",
        "queued": "info",
        "retry_wait": "warn",
        "deferred": "warn",
        "partial": "warn",
        "no_data": "bad",
        "failed": "bad",
        "dead_letter": "bad",
        "cancelled": "dim",
    }.get(status, "dim")
    return f'<span class="pill {tone}">{escape(status)}</span>'


# --- Formatting -------------------------------------------------------------------


def _format_stamp(value: datetime | None) -> str:
    """Render a UTC timestamp to minute precision, or an em dash when absent."""
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _format_age(value: datetime | None, now: datetime) -> str:
    """Render how long ago a timestamp was, honestly blank when the ledger has none."""
    if value is None:
        return _EMPTY
    seconds = int((now - value.astimezone(UTC)).total_seconds())
    if seconds < 0:
        return f"in {_format_duration(-seconds)}"
    return f"{_format_duration(seconds)} ago"


def _format_duration(seconds: int) -> str:
    if seconds < _SECONDS_PER_MINUTE:
        return f"{seconds}s"
    minutes = seconds // _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes}m"
    hours = minutes // _MINUTES_PER_HOUR
    if hours < _HOURS_BEFORE_DAY_FORMAT:
        return f"{hours}h{minutes % _MINUTES_PER_HOUR:02d}m"
    return f"{hours // _HOURS_PER_DAY}d{hours % _HOURS_PER_DAY:02d}h"


def _format_hours(hours: float | None) -> str:
    """Render an ETA in hours, or an em dash when throughput is zero and it is unknowable."""
    if hours is None:
        return _EMPTY
    return _format_duration(int(hours * _MINUTES_PER_HOUR * _SECONDS_PER_MINUTE))


_STYLE: Final = """
:root{color-scheme:dark;--bg:#0a0d12;--panel:#111722;--line:#1e2735;--ink:#c9d4e2;
--dim:#6c7c91;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--info:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace}
header{display:flex;align-items:baseline;gap:12px;padding:14px 18px;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:15px;font-weight:600;letter-spacing:.04em}
header .sub{color:var(--dim);font-size:12px}
main{padding:14px 18px 4px}
section{margin-bottom:20px}
h2{margin:0 0 8px;font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--dim)}
.tiles{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:8px 12px;min-width:130px}
.tile .label{display:block;color:var(--dim);font-size:11px;letter-spacing:.06em}
.tile .value{display:block;font-size:19px;font-variant-numeric:tabular-nums;margin-top:2px}
.tile.warn .value{color:var(--warn)}
.tile.bad .value{color:var(--bad)}
.stamp{color:var(--dim);font-size:11px}
.banner{border-radius:4px;padding:8px 12px;margin-bottom:8px;border:1px solid var(--line)}
.banner.bad{background:#2a1416;border-color:#5c2427;color:var(--bad)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:4px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:12px}
th,td{padding:6px 10px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;white-space:nowrap}
th{color:var(--dim);font-weight:600;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
position:sticky;top:0;background:var(--panel)}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#151d2b}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:var(--ok)}
.warn{color:var(--warn)}
.bad{color:var(--bad)}
td.n.ok{color:var(--ok)}
td.n.warn{color:var(--warn)}
td.n.bad{color:var(--bad)}
td.err{white-space:normal;max-width:520px;color:#e0b3b3;font-size:11px;line-height:1.35}
.lane{color:#e6edf6}
.dim{color:var(--dim)}
.empty{color:var(--dim);margin:0;padding:10px 0}
.note{color:var(--dim);margin:0 0 10px;max-width:80ch;font-size:.9em}
.pill{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;
border:1px solid currentColor;line-height:1.5}
.pill.ok{color:var(--ok)}
.pill.warn{color:var(--warn)}
.pill.bad{color:var(--bad)}
.pill.info{color:var(--info)}
.pill.dim{color:var(--dim)}
.bar{display:block;height:8px;min-width:2px;background:var(--bad);border-radius:2px;opacity:.75}
footer{padding:10px 18px 22px;border-top:1px solid var(--line);color:var(--dim);font-size:11px}
footer p{margin:0 0 4px;max-width:80ch}
footer b{color:var(--ink);font-weight:600}
"""
