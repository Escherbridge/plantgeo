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
# The three backend-health panels. Each reads tables no ledger row describes, so each is loaded
# through `_optional_rows` rather than beside the ledger statements: see the savepoint note there.
_FORECAST_STATE_SQL: Final = text(load_query_sql("routes/ops_forecast_state.sql"))
_PLATFORM_ACTIVITY_SQL: Final = text(load_query_sql("routes/ops_platform_activity.sql"))
_UNARMED_SOURCES_SQL: Final = text(load_query_sql("routes/ops_unarmed_sources.sql"))

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
_NO_PANEL_ERRORS: Final[Mapping[str, str]] = MappingProxyType({})

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
#
# The three backend-health panels are the same argument at smaller cost. Measured server-side
# against production 2026-08-09 on warm buffers: forecast state 82 ms (nineteen scalar aggregates
# over tables no larger than 184k rows), platform activity 102 ms, unarmed sources 247 ms (one
# index probe per source release, 1,811 of them). None of that is expensive per read and all of it
# is pointless twelve times a minute per connected operator, which is the only reason they are
# cached at all. Their windows are chosen by how long each answer may lag before it misleads:
#
#   forecast state ~90 s -- the plane's newest write is days old. Nothing here moves inside a
#                  minute, and the panel exists to report exactly that.
#   platform       ~60 s -- this one is a liveness signal, so it is the panel that most needs to
#                  change soon after the world does; a minute is the shortest window worth the
#                  scan.
#   sources        ~60 s -- a zero-landing release must become visible while the lane that wrote
#                  it is still running, not after it finishes.
_STREAMS_CACHE_SECONDS: Final = 300
_WALKS_CACHE_SECONDS: Final = 60
_LANE_EVIDENCE_CACHE_SECONDS: Final = 60
_FORECAST_CACHE_SECONDS: Final = 90
_PLATFORM_CACHE_SECONDS: Final = 60
_SOURCES_CACHE_SECONDS: Final = 60
_STREAMS_CACHE_KEY: Final = "data-streams"
# Each panel's cache key doubles as its name in `panel_errors`, so a failed read is reported
# against the same identity the cache and the log entry use.
_FORECAST_CACHE_KEY: Final = "forecast-state"
_PLATFORM_CACHE_KEY: Final = "platform-activity"
_SOURCES_CACHE_KEY: Final = "unarmed-sources"
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

# How recently a forecast-plane stage must have written to still read as running. The bands are
# deliberately coarse, in days rather than hours, because nothing in this plane is scheduled: it
# is driven by hand, so an overnight gap is normal and only a multi-day one says anything. A stage
# with zero rows never reaches these bands at all -- it has demonstrably never executed, which is
# a different statement from "has not run lately" and gets its own state.
_FORECAST_LIVE_HOURS: Final = 24
_FORECAST_STALE_DAYS: Final = 7

# What share of iterations must carry a scored actual before the coverage reads as sound rather
# than as a gap. Half is a low bar on purpose: production sits at 7% (118 of 1,676 iterations,
# measured 2026-08-09), so anything stricter would be tuned to a number nobody has tried to move.
_SCORED_COVERAGE_HEALTHY_PERCENT: Final = 50.0

# Stage, state, rows, last write, frontier, scored. Named because the plane-band header spans them
# all, and a colspan that drifts out of step with the header row silently breaks the table layout.
_FORECAST_TABLE_COLUMNS: Final = 6

# How long since a source last wrote a release that CARRIED DATA before the page stops calling it
# fed. Two days is the fresh band because the daily upstreams here publish on a daily cadence and
# a load that ran yesterday is healthy; a week is the outer band because every remaining reading
# -- a finished archive walk, a stopped scheduled task, an upstream that has published nothing --
# looks identical from here, and the panel says so rather than picking one.
_SOURCE_FED_HOURS: Final = 48
_SOURCE_LAGGING_DAYS: Final = 7

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

# The forecast plane has no job definition, no lane and no ledger row, so every other panel on
# this page is structurally blind to it and only a direct count can say what has run.
_FORECAST_NOTE: Final = (
    "the forecast and ML plane, counted from the tables themselves. Nothing here enqueues a ledger "
    "item, so the lanes table above cannot see this plane at all and a stage that has never "
    "executed is otherwise indistinguishable from one nobody has looked at. A stage showing zero "
    "rows HAS NEVER RUN, whatever a plan or a handoff note says about it. Two clocks travel with "
    "each stage and they are not the same question: last write is when a row was written, frontier "
    "is the newest point in time the stage reasons about, and the label under the frontier says "
    "which quantity it is because a newest as-of and a newest valid time are not comparable. "
    "scored is the share of ITERATIONS carrying at least one actual, not the share of predicted "
    "values -- the two readings differ by a factor of four here, because one actual is recorded "
    "per predicted point and one iteration holds many points. Nothing on this row of the page is a "
    "claim that any forecast is operational or life-safety-valid: a plane with no backtest metric "
    "has never had a prediction compared against what actually happened, which makes its output "
    "unfalsifiable rather than merely unvalidated."
)

# Owner decision, and the reason this panel is counts-only. It is repeated on the page rather than
# left in the SQL header because the page is the surface a reader can actually check it against.
_PLATFORM_NOTE: Final = (
    "aggregate counts and timestamps only. /ops is unauthenticated, so by owner decision no email, "
    "name, organization, slug, id, address or coordinate is read by this panel or reachable from "
    "it, and no most-active or recent-activity list may be added to it. THIS DATABASE RECORDS NO "
    "REQUEST LOG, ACCESS LOG OR AUDIT TABLE, so there is no request-volume number here and any "
    "that appeared would be invented: ai messages counts assistant traffic only, and a user "
    "browsing the map all day writes nothing anywhere. Every row names the column its windows are "
    "measured on, because they are not all creation stamps -- api keys is measured on last use, so "
    "its windows count keys USED in the window rather than keys issued in it, and verified "
    "accounts is measured on the email-verification stamp rather than on the unrelated boolean "
    "beside it that no auth path ever writes. Every newest column is truncated to the hour: these "
    "are aggregates and name nobody, but at a population of one an aggregate instant is a single "
    "subject's registration time to the microsecond, and no question this panel answers turns on "
    "a sub-hour difference. Unexpired sessions "
    "is the row with no windows at all: the table holds only a token, a user and an expiry and "
    "records no creation time, so how many people signed in this week is not a question this "
    "schema can answer, and blank is the honest answer rather than zero."
)

# The same honesty convention as the walk note, for the half of a source's state that lives in an
# infrastructure config this database cannot read.
_SOURCE_NOTE: Final = (
    "every catalogued upstream, judged on what it landed rather than on its is_active flag -- that "
    "flag is a hand-written policy statement and is not evidence that anything is loading the "
    "source. LANDED MEANS THREE TABLES, and which one a source uses is a property of that source, "
    "not a fallback: the warehouse signal plane, the normalized feature plane, and the governed "
    "forecast-input plane the Sentinel-2 NDVI registration path writes to and which never touches "
    "the signal plane at all. The line under each source name says which planes it actually lands "
    "in, because an audit that checks the wrong one reports a healthy lane as stone dead -- that "
    "mistake was made on this data, and this column is what makes it self-correcting. "
    "What a row here measures is the GOVERNED path, releases and the rows behind them, not raw "
    "ingest: a promotion step can be unarmed while the ingest beneath it is perfectly healthy, and "
    "a source going stale here while its upstream is current is exactly that shape. "
    "THIS PANEL CANNOT SEE A SCHEDULER: no table here records which crons or scheduled tasks "
    "exist, so unarmed means only that no release carrying data has been written recently, and an "
    "unscheduled promotion step, a scheduled task that stopped firing, a finished archive walk and "
    "an upstream that simply published nothing all look identical from here. The reverse direction "
    "is solid and is the useful one: a recent landed release proves something is feeding this "
    "source, whatever that something is. zero-landing counts releases that published provenance "
    "and wrote no row in ANY of the three planes -- a load that ran clean and landed nothing. "
    "Those releases are immutable provenance and are deliberately never deleted, so counting them "
    "is the only thing that stops them reading as healthy work, and the age beside the count is "
    "what separates a closed chapter from a lane failing right now. Nothing here arms anything: "
    "registering a forward load is an infrastructure decision with a human owner and is out of "
    "this page's scope."
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
    # What the forecast/ML plane has actually executed, one row per pipeline stage.
    forecast_stages: list[dict[str, Any]]
    # Aggregate-only product usage. Counts and timestamps, never identity; see _PLATFORM_NOTE.
    platform_activity: list[dict[str, Any]]
    # Every catalogued upstream and whether anything is still feeding it.
    sources: list[dict[str, Any]]
    error: str | None
    # Keyed by panel cache key. A panel whose own statement failed reports itself here and leaves
    # the rest of the page intact; `error` above stays reserved for a read that failed wholesale.
    panel_errors: Mapping[str, str] = _NO_PANEL_ERRORS


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
            panel_errors: dict[str, str] = {}
            forecast_rows = await _optional_rows(
                session,
                _ScanQuery(
                    cache_key=_FORECAST_CACHE_KEY,
                    statement=_FORECAST_STATE_SQL,
                    parameters={},
                    ttl_seconds=_FORECAST_CACHE_SECONDS,
                ),
                generated_at,
                panel_errors,
            )
            platform_rows = await _optional_rows(
                session,
                _ScanQuery(
                    cache_key=_PLATFORM_CACHE_KEY,
                    statement=_PLATFORM_ACTIVITY_SQL,
                    parameters={},
                    ttl_seconds=_PLATFORM_CACHE_SECONDS,
                ),
                generated_at,
                panel_errors,
            )
            source_rows = await _optional_rows(
                session,
                _ScanQuery(
                    cache_key=_SOURCES_CACHE_KEY,
                    statement=_UNARMED_SOURCES_SQL,
                    parameters={},
                    ttl_seconds=_SOURCES_CACHE_SECONDS,
                ),
                generated_at,
                panel_errors,
            )
    except Exception as error:
        # The same security boundary as the panel path, for the same reason. This string is
        # rendered into the page banner by `_render_meta` and mirrored into `/backfill.json`, and
        # the statements that reach here carry their own clause-by-clause headers -- publishing the
        # message published the whole lane statement plus `[parameters: (24,)]`. Nothing exotic is
        # needed to trigger it: one unreadable agri.job_* relation on a partially migrated or
        # permission-restricted database, or a statement timeout on the 46M-row lane read. Full
        # text goes to the log line above, which is private; only the class is published.
        logger.warning("ops_backfill_snapshot_failed", error=str(error))
        return _Snapshot(
            generated_at=generated_at,
            throughput_window_hours=throughput_window_hours,
            lanes=[],
            walks=[],
            failures=[],
            dead_letter_trend=[],
            streams=[],
            forecast_stages=[],
            platform_activity=[],
            sources=[],
            error=_panel_error_summary(error),
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
        forecast_stages=[_forecast_stage_record(row, generated_at) for row in forecast_rows],
        platform_activity=platform_rows,
        sources=[_source_record(row, generated_at) for row in source_rows],
        error=None,
        panel_errors=panel_errors,
    )


def _cached_entry(query: _ScanQuery, now: datetime) -> list[dict[str, Any]] | None:
    """Return this statement's cached answer while it is still inside its own TTL, else None.

    The single definition of "is the cached copy still good". Both readers below consult it, so the
    TTL rule cannot come to mean two different things depending on which one a panel goes through.
    """
    entry = _QUERY_CACHE.get(query.cache_key)
    if entry is None or (now - entry[0]).total_seconds() >= query.ttl_seconds:
        return None
    return entry[1]


async def _cached_rows(session: Any, query: _ScanQuery, now: datetime) -> list[dict[str, Any]]:
    """Run one scanning statement, re-reading only once the cached copy has aged past its own TTL."""
    cached = _cached_entry(query, now)
    if cached is not None:
        return cached
    result = await session.execute(query.statement, query.parameters)
    rows = [dict(row) for row in result.mappings().all()]
    _QUERY_CACHE[query.cache_key] = (now, rows)
    return rows


async def _optional_rows(
    session: Any,
    query: _ScanQuery,
    now: datetime,
    panel_errors: dict[str, str],
) -> list[dict[str, Any]]:
    """Run one panel's statement inside a savepoint, so its failure costs only that panel.

    Caching is `_cached_rows`' contract exactly; the difference is the blast radius of a failure.
    The ledger statements above read tables this service's own migrations create, so a failure
    there means the read is broken and the whole page should say so. These three do not: the
    platform panel reads `public` tables another application's migrations own and which need not
    exist on an agri-only database at all, and the forecast and source panels read planes that a
    partially migrated environment may be missing. Letting an UndefinedTable on one of those blank
    the lanes, walks, streams and failures panels would trade a small honest gap for a total
    outage of the page an operator opened to diagnose something else.

    The savepoint is what makes that possible rather than merely desirable. A failed statement
    poisons its whole transaction -- every following read returns InFailedSqlTransaction instead
    of data -- so catching the exception alone would still lose every panel after it. `begin_nested`
    issues SAVEPOINT, and rolling back to it leaves the outer transaction usable. The
    transaction-local statement_timeout was pinned BEFORE this savepoint, so it survives the
    rollback and still bounds everything after it.

    A failed panel is recorded rather than swallowed. An empty table and an unreadable one look
    identical on the page otherwise, and those are opposite findings. What is recorded is the
    failure's CLASS and nothing else; `_panel_error_summary` says why.

    The TTL check is `_cached_entry` and the read itself is `_cached_rows`, so this function adds a
    savepoint and an except clause to the shared path rather than restating it. The guard runs
    before the savepoint deliberately: a cache hit is a dictionary lookup and must not buy the
    connection a SAVEPOINT/RELEASE round trip on every five-second tick.
    """
    cached = _cached_entry(query, now)
    if cached is not None:
        return cached
    try:
        async with session.begin_nested():
            return await _cached_rows(session, query, now)
    except Exception as error:
        # The full text -- driver message, failing relation, and the entire statement -- goes to
        # the log, which is private. Only the class reaches `panel_errors`, which is not.
        logger.warning("ops_backfill_panel_read_failed", panel=query.cache_key, error=str(error))
        panel_errors[query.cache_key] = _panel_error_summary(error)
        return []


def _panel_error_summary(error: Exception) -> str:
    """Name the KIND of failure without publishing one byte of the statement that failed.

    The single sink for every error string this page publishes -- the per-panel banners and the
    whole-read banner both come through here. This is a security boundary, not a formatting
    preference. `/ops/backfill` is unauthenticated, and SQLAlchemy's StatementError.__str__ appends
    `[SQL: <the entire statement>]` and `[parameters: ...]` to its message. Rendering that verbatim
    published roughly 11,800 characters of internal SQL to anonymous visitors -- including this
    service's own header comments, which enumerate exactly which tables hold email addresses,
    names, slugs and coordinates. Neither trigger is exotic: for the panels it is the environment
    `_optional_rows` exists for, an agri-only database with no `public` application tables, where
    the leak was the page's DEFAULT rendering; for the whole read it is one unreadable agri.job_*
    relation or a statement timeout on the lane query.

    WHAT THIS FUNCTION MAY EVALUATE, WHICH IS THE ENTIRE SECURITY ARGUMENT

    Every expression below is `type(x).__name__`. A Python class name is an identifier -- letters,
    digits and underscores -- so nothing this returns can carry a statement, a parameter, a row or
    a message, whatever it is handed. It never reads `str()`, `repr()`, `.statement`, `.params`,
    `.detail`, `.args` or `__context__`. The guarantee is therefore structural rather than a
    property of the exception shapes anyone here has happened to see, and it keeps holding against
    driver classes nobody has met. Any future edit that reads a VALUE off an exception rather than
    its type breaks that argument and needs the same scrutiny this function got.

    WHY IT UNWRAPS EXACTLY ONE LEVEL

    `orig` is SQLAlchemy's handle on the driver exception, but for asyncpg it is the DIALECT's own
    wrapper rather than the condition. Measured against the live driver, a missing relation arrives
    as ProgrammingError whose `orig` is asyncpg's ProgrammingError, so the pair read
    "ProgrammingError: ProgrammingError" -- a tautology saying nothing `type(error).__name__` alone
    did not. The real condition, UndefinedTableError, hangs off that wrapper's `__cause__`. One
    level is unwrapped and no more: one level is what the dialect adds, and a loop would chase a
    chain of unknown depth for no gain. An error raised outside the driver has no `orig` at all and
    reports its own class alone.
    """
    origin = getattr(error, "orig", None)
    if origin is None:
        return type(error).__name__
    condition = getattr(origin, "__cause__", None) or origin
    return f"{type(error).__name__}: {type(condition).__name__}"


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


def _forecast_stage_record(row: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    """Add the executed/stale state and the scored-coverage share to one pipeline stage."""
    covered = row["covered_count"]
    coverage_of = row["coverage_of_count"]
    return {
        **row,
        "state": _forecast_stage_state(row, generated_at),
        # None rather than zero for every stage that carries no ratio at all: a stage with nothing
        # to divide has no coverage, which is not the same statement as coverage of nothing.
        "coverage_percent": (
            (100.0 * int(covered) / int(coverage_of)) if covered is not None and coverage_of else None
        ),
    }


def _forecast_stage_state(row: dict[str, Any], now: datetime) -> str:
    """Name a stage's state from its own rows alone; zero rows is never a staleness question.

    `never` is the load-bearing one and it is tested first: a stage holding no rows has not merely
    gone quiet, it has never executed once, and collapsing that into "stale" would let a plane
    nothing has ever run read as a plane that is simply between runs.
    """
    if int(row["row_count"]) == 0:
        return "never"
    last_activity_at: datetime | None = row["last_activity_at"]
    if last_activity_at is None:
        # Rows with no write stamp: the table cannot say when it last moved, so nothing is claimed.
        return "undated"
    age = now - last_activity_at.astimezone(UTC)
    if age <= timedelta(hours=_FORECAST_LIVE_HOURS):
        return "live"
    if age <= timedelta(days=_FORECAST_STALE_DAYS):
        return "stale"
    return "dormant"


def _source_record(row: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    """Add the fed/unarmed state to one catalogued source, derived from landed releases only."""
    return {
        **row,
        "has_ever_landed": int(row["landed_release_count"]) > 0,
        "state": _source_state(row, generated_at),
    }


def _source_state(row: dict[str, Any], now: datetime) -> str:
    """Name a source's state from landed evidence; see the source note for what unarmed omits.

    Order is the whole design. `inactive` comes first because a source policy has withdrawn is not
    a fault however long it stays silent, and flagging it would put a permanent warning on the page.
    `never` comes before any age test for the same reason it does in the forecast panel: a source
    that has published releases and landed nothing in ANY observation plane is not stale, it has
    never worked. Everything past those gates is a question about age alone, which is why it lives
    in its own function rather than lengthening the ladder here.
    """
    if not row["is_active"]:
        return "inactive"
    if int(row["release_count"]) == 0:
        return "no_releases"
    if int(row["landed_release_count"]) == 0:
        return "never"
    return _source_feed_state(row["last_landed_release_at"], now)


def _source_feed_state(last_landed_at: datetime | None, now: datetime) -> str:
    """Band a fed source by how long ago it last wrote a release that actually carried data."""
    if last_landed_at is None:
        return "undated"
    age = now - last_landed_at.astimezone(UTC)
    if age <= timedelta(hours=_SOURCE_FED_HOURS):
        return "fed"
    return "lagging" if age <= timedelta(days=_SOURCE_LAGGING_DAYS) else "unarmed"


def _json_snapshot(snapshot: _Snapshot) -> dict[str, Any]:
    """Render the snapshot as JSON-safe primitives."""
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "throughput_window_hours": snapshot.throughput_window_hours,
        "activity_note": _ACTIVITY_NOTE,
        "ledger_stale_note": _LANE_STALE_NOTE,
        "ledger_stale_hours": _LEDGER_STALE_HOURS,
        "historical_walk_note": _WALK_NOTE,
        "forecast_state_note": _FORECAST_NOTE,
        "platform_activity_note": _PLATFORM_NOTE,
        "source_note": _SOURCE_NOTE,
        "error": snapshot.error,
        # A panel that failed its own read reports itself here rather than as an empty list, so a
        # scripted reader can tell "nothing to report" from "could not be read" the same way the
        # page can.
        "panel_errors": dict(snapshot.panel_errors),
        "lanes": [_json_safe(lane) for lane in snapshot.lanes],
        "historical_walks": [_json_safe(walk) for walk in snapshot.walks],
        "failures": [_json_safe(failure) for failure in snapshot.failures],
        "dead_letter_trend": [_json_safe(entry) for entry in snapshot.dead_letter_trend],
        "data_streams": [_json_safe(stream) for stream in snapshot.streams],
        "forecast_state": [_json_safe(stage) for stage in snapshot.forecast_stages],
        "platform_activity": [_json_safe(metric) for metric in snapshot.platform_activity],
        "sources": [_json_safe(source) for source in snapshot.sources],
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
        ("#ops-unarmed", _render_sources(snapshot)),
        ("#ops-forecast", _render_forecast(snapshot)),
        ("#ops-activity", _render_activity(snapshot)),
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


def _panel_banner(snapshot: _Snapshot, panel: str) -> str:
    """Say that one panel's own read failed, so an unreadable table never renders as an empty one."""
    error = snapshot.panel_errors.get(panel)
    if error is None:
        return ""
    return f'<div class="banner bad">{escape(panel)} read failed &mdash; {escape(error)}</div>'


def _render_sources(snapshot: _Snapshot) -> str:
    """Render every catalogued upstream and whether landed evidence says anything still feeds it."""
    banner = _panel_banner(snapshot, _SOURCES_CACHE_KEY)
    if not snapshot.sources:
        return (
            '<section id="ops-unarmed"><h2>upstream sources</h2>'
            f'{banner}<p class="empty">no catalogued sources readable</p></section>'
        )
    rows = "".join(_render_source_row(source, snapshot.generated_at) for source in snapshot.sources)
    return (
        '<section id="ops-unarmed"><h2>upstream sources</h2>'
        f'{banner}<p class="note">{escape(_SOURCE_NOTE)}</p>'
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>source</th><th>state</th>"
        '<th class="n">releases</th><th class="n">landed</th><th class="n">zero-landing</th>'
        "<th>last landed release</th><th>newest observation</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_source_row(source: dict[str, Any], generated_at: datetime) -> str:
    """Render one source. A source that has never landed anything must read louder than a stale one."""
    landed_releases = int(source["landed_release_count"])
    inactive_mark = "" if source["is_active"] else ' <span class="pill dim">inactive</span>'
    return (
        "<tr>"
        f'<td><span class="lane">{escape(str(source["source_key"]))}</span>{inactive_mark}'
        f'<br><span class="dim">{escape(str(source["source_name"]))}</span>'
        f'<br><span class="dim">review {escape(str(source["review_state"]))}</span>'
        f"{_render_landing_planes(source)}</td>"
        f"<td>{_status_pill(str(source['state']))}</td>"
        f'<td class="n">{int(source["release_count"]):,}</td>'
        f'<td class="n{"" if landed_releases else " bad"}">{landed_releases:,}</td>'
        f'<td class="n">{_zero_landing_cell(source, generated_at)}</td>'
        f"<td>{_format_age(source['last_landed_release_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(source["last_landed_release_at"]))}</span></td>'
        f'<td class="dim">{_format_day(source["newest_observation_at"])}</td>'
        "</tr>"
    )


def _render_landing_planes(source: dict[str, Any]) -> str:
    """Name the observation planes this source lands in, because "landed" is not one table.

    A source that lands nowhere prints nothing here: its state pill already says `never`, and a
    second empty statement beside it would only dilute the first. What this line is for is the
    opposite case -- a source that IS healthy but lands somewhere an audit did not think to look.
    """
    planes = source["landing_planes"] or []
    if not planes:
        return ""
    listed = ", ".join(str(plane) for plane in planes)
    return f'<br><span class="dim">lands in {escape(listed)}</span>'


def _zero_landing_cell(source: dict[str, Any], generated_at: datetime) -> str:
    """Tint the zero-landing count and date it, because age is what separates the two readings.

    Zero is the good case and must read as such rather than as absence. Any non-zero count is a
    confirmed instance of the ran-clean-and-wrote-nothing bug class, so it tones bad outright with
    no warning band: one is already one too many, and the age beneath it is what says whether it is
    a closed chapter or a lane failing right now.
    """
    zero_landing = int(source["zero_landing_releases"])
    if zero_landing == 0:
        return '<span class="ok">0</span>'
    newest = _format_age(source["newest_zero_landing_release_at"], generated_at)
    return f'<span class="bad">{zero_landing:,}</span><br><span class="dim">newest {newest}</span>'


def _render_forecast(snapshot: _Snapshot) -> str:
    """Render one row per forecast/ML stage, with the never-executed count as the headline."""
    banner = _panel_banner(snapshot, _FORECAST_CACHE_KEY)
    if not snapshot.forecast_stages:
        return (
            '<section id="ops-forecast"><h2>forecast / ml plane</h2>'
            f'{banner}<p class="empty">no forecast plane readable</p></section>'
        )
    never_executed = sum(1 for stage in snapshot.forecast_stages if int(stage["row_count"]) == 0)
    tiles = f'<div class="tiles">{_render_forecast_tiles(snapshot, never_executed)}</div>'
    return (
        '<section id="ops-forecast"><h2>forecast / ml plane</h2>'
        f'{banner}<p class="note">{escape(_FORECAST_NOTE)}</p>{tiles}'
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>stage</th><th>state</th>"
        '<th class="n">rows</th><th>last write</th><th>frontier</th><th class="n">scored</th>'
        "</tr></thead>"
        f"<tbody>{_render_forecast_bands(snapshot)}</tbody></table></div></section>"
    )


def _render_forecast_bands(snapshot: _Snapshot) -> str:
    """Break the stage table into one band per plane, headed by that plane's own tally.

    The nineteen stages are four coherent waves -- the evidence that feeds the plane, the iteration
    method that actually runs, the governed pipeline above it, and the recommendation plane above
    that -- and the whole finding is WHERE the boundary between "has run" and "has never run"
    falls. Flat, with the plane repeated in a column, a reader has to scan all nineteen rows to
    locate a waterline that a band header states outright.
    """
    fragments: list[str] = []
    for plane, stages in _stages_by_plane(snapshot.forecast_stages):
        fragments.append(_render_plane_band(plane, stages))
        fragments.extend(_render_forecast_row(stage, snapshot.generated_at) for stage in stages)
    return "".join(fragments)


def _stages_by_plane(stages: Sequence[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group stages into RUNS of the same plane, in statement order, without re-sorting them.

    Contiguous runs rather than a dictionary of planes, deliberately. The statement returns its
    stages in pipeline order and its planes already fall in contiguous blocks; grouping by key
    would impose Python's insertion or alphabetical order on top of the pipeline's own, and would
    silently stitch two separated blocks of one plane into a single band. Grouping by run keeps
    the page a faithful picture of what the statement said: if a plane ever does appear in two
    places, it renders as two bands, which is the honest way to show it.
    """
    grouped: list[tuple[str, list[dict[str, Any]]]] = []
    for stage in stages:
        plane = str(stage["plane"])
        if not grouped or grouped[-1][0] != plane:
            grouped.append((plane, []))
        grouped[-1][1].append(stage)
    return grouped


def _render_plane_band(plane: str, stages: list[dict[str, Any]]) -> str:
    """Head one plane's rows with how many of its stages have ever executed.

    Toned on the tally rather than on the plane's name: a plane where nothing has ever run is the
    finding and reads bad, a fully executed one reads ok, and a partially executed one warns. That
    is what puts the waterline on the page instead of leaving it to be counted by eye.
    """
    executed = sum(1 for stage in stages if int(stage["row_count"]) > 0)
    if executed == 0:
        tone = "bad"
    elif executed == len(stages):
        tone = "ok"
    else:
        tone = "warn"
    return (
        f'<tr class="band"><td colspan="{_FORECAST_TABLE_COLUMNS}">'
        f"<b>{escape(plane)}</b> "
        f'<span class="{tone}">{executed} of {len(stages)} stages have ever run</span>'
        "</td></tr>"
    )


def _render_forecast_tiles(snapshot: _Snapshot, never_executed: int) -> str:
    """Put the two numbers that are the finding above the table that evidences them."""
    scored = _scored_stage(snapshot)
    coverage = None if scored is None else scored["coverage_percent"]
    return "".join(
        [
            _tile("pipeline stages", str(len(snapshot.forecast_stages)), ""),
            _tile("never executed", f"{never_executed:,}", "bad" if never_executed else ""),
            _tile(
                "iterations scored",
                _EMPTY if coverage is None else f"{coverage:.1f}%",
                "" if coverage is None else _coverage_tone(coverage),
            ),
        ]
    )


def _scored_stage(snapshot: _Snapshot) -> dict[str, Any] | None:
    """Find the one stage that carries a coverage ratio, without naming it in Python.

    The stage list lives in the SQL, so hunting for the row that HAS a ratio keeps this side from
    holding a second, silently divergent copy of which stage that is. That search is only sound
    while exactly one stage carries a ratio, which is an invariant of the statement rather than
    something this side can enforce -- so a second ratio row is logged rather than ignored. It is
    logged and not raised on purpose: this is a display path on a page whose entire value is
    staying up during an incident, and a 500 over an ambiguous tile would be a worse outcome than
    a tile showing the first of two. The table below still renders both rows in full either way.
    """
    scored = [stage for stage in snapshot.forecast_stages if stage["coverage_percent"] is not None]
    if not scored:
        return None
    if len(scored) > 1:
        logger.warning(
            "ops_forecast_multiple_coverage_stages",
            stages=[str(stage["stage"]) for stage in scored],
        )
    return scored[0]


def _coverage_tone(coverage: float) -> str:
    """Tone a coverage share. Nothing scored at all is a different finding from a thin sample."""
    if coverage <= 0.0:
        return "bad"
    return "warn" if coverage < _SCORED_COVERAGE_HEALTHY_PERCENT else "ok"


def _render_forecast_row(stage: dict[str, Any], generated_at: datetime) -> str:
    """Render one stage. A zero row count is tinted bad, because it is the finding, not a gap."""
    row_count = int(stage["row_count"])
    return (
        "<tr>"
        f'<td><span class="lane">{escape(str(stage["stage"]))}</span>'
        f'<br><span class="dim">{escape(str(stage["relation"]))}</span>'
        f"{_render_stage_breadth(stage)}</td>"
        # No plane column: the band header above this row already names it, and repeating it on
        # every row is what made the waterline between the planes invisible.
        f"<td>{_status_pill(str(stage['state']))}</td>"
        f'<td class="n{"" if row_count else " bad"}">{row_count:,}</td>'
        f"<td>{_format_age(stage['last_activity_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(stage["last_activity_at"]))}</span></td>'
        f"<td>{_render_stage_frontier(stage)}</td>"
        f'<td class="n">{_render_scored_cell(stage)}</td>'
        "</tr>"
    )


def _render_stage_breadth(stage: dict[str, Any]) -> str:
    """Say how broad a stage is, and nothing at all where the answer is not defined.

    The list is capped in the statement, so a stage that one day holds hundreds of methods puts a
    handful on the page beside its real count rather than all of them.
    """
    values = stage["breadth"] or []
    label = stage["breadth_label"]
    if not values or label is None:
        return ""
    listed = ", ".join(str(value) for value in values)
    return f'<br><span class="dim">{escape(str(label))}: {escape(listed)}</span>'


def _render_stage_frontier(stage: dict[str, Any]) -> str:
    """Print the newest time a stage reasons about, always labelled with which time that is."""
    frontier_at = stage["frontier_at"]
    if frontier_at is None:
        return _EMPTY
    return f'{escape(_format_stamp(frontier_at))}<br><span class="dim">{escape(str(stage["frontier_label"]))}</span>'


def _render_scored_cell(stage: dict[str, Any]) -> str:
    """Render the actuals-coverage share, with its two terms under it so the ratio is checkable."""
    coverage = stage["coverage_percent"]
    if coverage is None:
        return _EMPTY
    covered = int(stage["covered_count"])
    coverage_of = int(stage["coverage_of_count"])
    return (
        f'<span class="{_coverage_tone(coverage)}">{coverage:.1f}%</span>'
        f'<br><span class="dim">{covered:,}/{coverage_of:,} {escape(str(stage["coverage_label"]))}</span>'
    )


def _render_activity(snapshot: _Snapshot) -> str:
    """Render the aggregate platform counters. Counts and timestamps only; see _PLATFORM_NOTE."""
    banner = _panel_banner(snapshot, _PLATFORM_CACHE_KEY)
    if not snapshot.platform_activity:
        return (
            '<section id="ops-activity"><h2>platform activity</h2>'
            f'{banner}<p class="empty">no platform tables readable</p></section>'
        )
    rows = "".join(_render_activity_row(metric, snapshot.generated_at) for metric in snapshot.platform_activity)
    return (
        '<section id="ops-activity"><h2>platform activity</h2>'
        f'{banner}<p class="note">{escape(_PLATFORM_NOTE)}</p>'
        '<div class="scroll"><table>'
        "<thead><tr>"
        "<th>area</th><th>metric</th>"
        '<th class="n">total</th><th class="n">24h</th><th class="n">7d</th>'
        "<th>newest</th><th>measured on</th>"
        "</tr></thead>"
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _render_activity_row(metric: dict[str, Any], generated_at: datetime) -> str:
    """Render one aggregate counter. No column here identifies a person or an organization."""
    total = int(metric["total_count"] or 0)
    basis = metric["window_basis"]
    # A row with no basis has no creation stamp behind it at all, which is why its windows are
    # blank. Saying so in the row keeps the reason next to the blank instead of only in the note.
    basis_cell = '<span class="warn">no creation stamp</span>' if basis is None else escape(str(basis))
    return (
        "<tr>"
        f'<td class="dim">{escape(str(metric["section"]))}</td>'
        f"<td><b>{escape(str(metric['metric']))}</b>"
        f'<br><span class="dim">{escape(str(metric["relation"]))}</span></td>'
        f'<td class="n{"" if total else " dim"}">{total:,}</td>'
        f'<td class="n">{_window_cell(metric["last_24h"])}</td>'
        f'<td class="n">{_window_cell(metric["last_7d"])}</td>'
        f"<td>{_format_age(metric['last_activity_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(metric["last_activity_at"]))}</span></td>'
        f'<td class="dim">{basis_cell}</td>'
        "</tr>"
    )


def _window_cell(value: Any) -> str:
    """Tint a trailing-window count. An unmeasurable window is blank, never zero."""
    if value is None:
        return _EMPTY
    count = int(value)
    if count == 0:
        return '<span class="dim">0</span>'
    return f'<span class="ok">{count:,}</span>'


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
    # Ledger run/item statuses, the historical-walk states, the forecast-stage states and the
    # source states all share one map because their vocabularies are disjoint -- no word below is
    # produced by more than one of them, which is what lets one tone table serve all four without
    # a state from one panel silently borrowing another's colour. An unknown status tones dim
    # rather than raising, so a state added upstream degrades to grey instead of a 500.
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
        # Forecast-plane stages. `never` is bad and not merely warn: it is the finding.
        "never": "bad",
        "live": "ok",
        "stale": "warn",
        "dormant": "bad",
        "undated": "dim",
        # Catalogued sources. `unarmed` is warn rather than bad because this page cannot see a
        # scheduler, so it is an inference; `never` and `no_releases` are read straight off what
        # landed and are not.
        "fed": "ok",
        "lagging": "warn",
        "unarmed": "warn",
        "no_releases": "bad",
        "inactive": "dim",
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
tr.band td{background:#0d131d;color:var(--dim);font-size:11px;letter-spacing:.06em;
text-transform:uppercase;border-top:1px solid var(--line)}
tr.band:hover td{background:#0d131d}
tr.band b{color:var(--ink);font-weight:600}
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
