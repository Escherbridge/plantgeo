"""Live operator console over the agri.job_* durable backfill ledger."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any, Final

import structlog
from sanic import Blueprint, Request, html, json, raw
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.
from sqlalchemy import text

from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.db.sql_queries import load_query_sql

logger = structlog.get_logger()

ops_bp = Blueprint("ops", url_prefix="/ops")

_LANES_SQL: Final = text(load_query_sql("routes/ops_backfill_lanes.sql"))
_FAILURES_SQL: Final = text(load_query_sql("routes/ops_backfill_failures.sql"))
_DEAD_LETTER_TREND_SQL: Final = text(load_query_sql("routes/ops_backfill_dead_letter_trend.sql"))
_DATA_STREAMS_SQL: Final = text(load_query_sql("routes/ops_data_streams.sql"))

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

# The data-loads query aggregates every row of agri.signal_observation (~16M) and
# geo.features, which costs ~26 s against the production proxy. The lane query is cheap and
# wants the 5 s refresh; this one does not -- a backfill's landed coverage does not change
# meaningfully inside a minute -- so it is cached independently rather than being dragged
# along by the stream's cadence. Without this the SSE loop would re-run a 26 s scan every
# 5 s and each connected operator would hold a permanent scan open against prod.
_STREAMS_CACHE_SECONDS: Final = 300
_STREAMS_CACHE: dict[str, Any] = {}

# A stream missing more than a month of interior days is reported as severe rather than
# merely notable: at that size the hole is a lane that stopped, not an upstream that skipped
# a few publications.
_GAP_SEVERE_DAYS: Final = 30
# Below one day the median step is a rounding artefact of sub-daily observations, not a
# cadence in days, so it is named rather than printed as a misleading "0d".
_CADENCE_SUBDAY: Final = 1.0

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


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One point-in-time read of the whole backfill ledger."""

    generated_at: datetime
    throughput_window_hours: int
    lanes: list[dict[str, Any]]
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
            lane_result = await session.execute(
                _LANES_SQL,
                {"throughput_window_hours": throughput_window_hours},
            )
            lane_rows = [dict(row) for row in lane_result.mappings().all()]
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
            stream_rows = await _load_data_streams(session, generated_at)
    except Exception as error:
        logger.warning("ops_backfill_snapshot_failed", error=str(error))
        return _Snapshot(
            generated_at=generated_at,
            throughput_window_hours=throughput_window_hours,
            lanes=[],
            failures=[],
            dead_letter_trend=[],
            streams=[],
            error=f"{type(error).__name__}: {error}",
        )
    return _Snapshot(
        generated_at=generated_at,
        throughput_window_hours=throughput_window_hours,
        lanes=[_lane_record(row, generated_at, throughput_window_hours) for row in lane_rows],
        failures=failure_rows,
        dead_letter_trend=trend_rows,
        streams=stream_rows,
        error=None,
    )


async def _load_data_streams(session: Any, now: datetime) -> list[dict[str, Any]]:
    """Return the data-load states, re-reading only once the cached copy has aged out."""
    computed_at = _STREAMS_CACHE.get("computed_at")
    if computed_at is not None and (now - computed_at).total_seconds() < _STREAMS_CACHE_SECONDS:
        cached_rows: list[dict[str, Any]] = _STREAMS_CACHE["rows"]
        return cached_rows
    # Empty mapping rather than no argument: this query binds nothing, but every other
    # caller in this module passes parameters and the session contract expects them.
    result = await session.execute(_DATA_STREAMS_SQL, {})
    rows = [dict(row) for row in result.mappings().all()]
    _STREAMS_CACHE["computed_at"] = now
    _STREAMS_CACHE["rows"] = rows
    return rows


def _lane_record(row: dict[str, Any], generated_at: datetime, throughput_window_hours: int) -> dict[str, Any]:
    """Add completion, throughput and ETA to one lane row, leaving ETA None when stalled."""
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
    return {
        **row,
        "completion_percent": (100.0 * succeeded_items / total_items) if total_items else None,
        "throughput_per_hour": throughput_per_hour,
        "eta_hours": eta_hours,
        "eta_ready_at": None if eta_hours is None else generated_at + timedelta(hours=eta_hours),
    }


def _json_snapshot(snapshot: _Snapshot) -> dict[str, Any]:
    """Render the snapshot as JSON-safe primitives."""
    return {
        "generated_at": snapshot.generated_at.isoformat(),
        "throughput_window_hours": snapshot.throughput_window_hours,
        "activity_note": _ACTIVITY_NOTE,
        "error": snapshot.error,
        "lanes": [_json_safe(lane) for lane in snapshot.lanes],
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
    """Render one row per lane run: states, completion, rate, ETA, stall and activity."""
    if not snapshot.lanes:
        return '<section id="ops-lanes"><h2>lanes</h2><p class="empty">no job runs in the ledger</p></section>'
    rows = "".join(_render_lane_row(lane, snapshot.generated_at) for lane in snapshot.lanes)
    return (
        '<section id="ops-lanes"><h2>lanes</h2><div class="scroll"><table>'
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
    stalled = int(lane["stalled_lease_items"])
    dead_lettered = int(lane["dead_letter_items"])
    completion = lane["completion_percent"]
    eta_hours = lane["eta_hours"]
    enabled_mark = "" if lane["definition_enabled"] else ' <span class="pill warn">disabled</span>'
    return (
        "<tr>"
        f'<td><span class="lane">{escape(str(lane["definition_name"]))}</span>{enabled_mark}'
        f'<br><span class="dim">{escape(str(lane["logical_run_key"]))}</span></td>'
        f"<td>{_status_pill(str(lane['run_status']))}</td>"
        f'<td class="n ok">{int(lane["succeeded_items"]):,}</td>'
        f'<td class="n">{int(lane["queued_items"]):,}</td>'
        f'<td class="n">{int(lane["leased_items"]) + int(lane["running_items"]):,}</td>'
        f'<td class="n">{int(lane["retry_wait_items"]):,}</td>'
        f'<td class="n">{int(lane["deferred_items"]):,}</td>'
        f'<td class="n{" bad" if dead_lettered else ""}">{dead_lettered:,}</td>'
        f'<td class="n">{int(lane["total_items"]):,}</td>'
        f'<td class="n">{_EMPTY if completion is None else f"{completion:.1f}%"}</td>'
        f'<td class="n">{float(lane["throughput_per_hour"]):,.2f}</td>'
        f'<td class="n{"" if eta_hours is not None else " warn"}">{_format_hours(eta_hours)}</td>'
        f'<td class="n{" warn" if stalled else ""}">{stalled:,}</td>'
        f"<td>{_format_age(lane['last_recorded_activity_at'], generated_at)}"
        f'<br><span class="dim">{escape(_format_stamp(lane["last_recorded_activity_at"]))}</span></td>'
        f'<td class="dim">{escape(str(lane["oldest_outstanding_shard_key"] or "—"))}</td>'
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
    computed_at = _STREAMS_CACHE.get("computed_at")
    if computed_at is None:
        return "just now"
    return _format_age(computed_at, now)


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
    tone = {
        "succeeded": "ok",
        "running": "info",
        "queued": "info",
        "retry_wait": "warn",
        "deferred": "warn",
        "partial": "warn",
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
