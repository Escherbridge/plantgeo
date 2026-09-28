"""Reads o5a's usage fold off `agri.job_attempt.metrics` into the operator report
`agri-service ops jobs-usage-report` prints (spec Sec 4.9.2, design Sec 2.2-2.6, plan 0W.4). See
execution/AGENTS.md, "The usage fold", for how that fold got written in the first place.

Three fault-isolated sections, in the style of `scripts/readiness.py` (each runs inside its own
savepoint, so one section's failing query never poisons the read-only transaction for the others):

1. **Month to date per pool** -- `month_to_date(session, *, pool, now)` is the ONLY loader of
   `sql/execution/select_provider_month_to_date.sql`; `execution/provider_budget.py` (G1,
   f1-executor) imports this function directly for admission and must never load that file a
   second time (`test_month_to_date_is_the_only_loader`).
2. **Per lane x host** -- `provider_usage_rows` loads `sql/execution/select_provider_usage.sql`'s
   row-level feed; `group_usage_rows` does every rate, percentile and outcome-count computation in
   Python, keyed by the CLI's `--by pool|lane|host|day`.
3. **Open incidents** -- every non-resolved `agri.job_incident` row, of every kind.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Final, cast

import click
from sqlalchemy import ARRAY, Text, bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.engine import ingest_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.observability.usage import METERING_ONLY_POOL_LABELS
from agri_data_service.foundation.observability.vocabulary import LANE_LOGICAL_CAPS, POOL_LABELS
from agri_data_service.jobs.lease import canonical_json, fetch_row, fetch_rows

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

# spec Sec 4.9.2 / plan 0W.4: "a read-only transaction, statement_timeout = 30s". SET LOCAL stays
# inline here per sql/AGENTS.md ("one line, no bind parameter, bakes in a module constant"), the
# same shape `routes/ops.py::_SET_STATEMENT_TIMEOUT` and `jobs/lease.py::_STATEMENT_TIMEOUT` use.
STATEMENT_TIMEOUT_SECONDS: Final = 30
_SET_STATEMENT_TIMEOUT: Final = text(
    f"-- usage_report_statement_timeout\nSET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_SECONDS}s'"
)
# `transaction_read_only` (a GUC settable with SET LOCAL) rather than `SET TRANSACTION READ ONLY`
# (which must be the transaction's very first statement) -- this can then sit next to the timeout
# SET LOCAL above in either order. Every query this report issues is already a SELECT; this is
# defense in depth against a future edit accidentally adding a write, not a functional requirement.
# Written as an f-string (no interpolation needed) to match `_SET_STATEMENT_TIMEOUT` right above and
# `jobs/lease.py::_STATEMENT_TIMEOUT` / `routes/ops.py::_SET_STATEMENT_TIMEOUT`: the marker comment
# makes the literal span two lines the same way theirs does, and `tests/test_no_new_inline_sql.py`'s
# walker (by its own docstring) matches only a bare string literal, never an f-string.
_SET_READ_ONLY: Final = text(f"-- usage_report_read_only\nSET LOCAL transaction_read_only = on")  # noqa: F541

# Mirrors `foundation/observability/usage.py::_WEIGHTED_POOLS` by hand: that name is private to its
# own module (the same "sibling copy, never a shared import" rule `job_executor_service.py::
# _ROUTER_TURN_FIELDS` documents for its own hand-copy of a router table). Both Open-Meteo pools are
# the complete set until a second weighted provider lands (usage.py's own comment says the same).
WEIGHTED_POOLS: Final[frozenset[str]] = frozenset({"open-meteo-paid", "open-meteo-free"})

# WQ-4 (spec Sec 4.9.2, FR-37): the paid Open-Meteo cap, as the owner settled it. Since G1 the report and
# admission both read `lanes/_providers/open-meteo.toml [budget]` (`provider_budget.py::budget_for_pool`); these
# three are the FALLBACK used only when that file cannot load, pinned equal to it by
# `tests/lane_config/test_provider_hosts.py::test_the_toml_budget_agrees_with_the_hand_copied_usage_report_constants`.
PAID_MONTHLY_BUDGET: Final = 5_000_000
GAP_FILL_CEILING_FRACTION: Final = 0.60
FORWARD_STOP_FRACTION: Final = 0.95

BY_CHOICES: Final = ("pool", "lane", "host", "day")
#: `--pool` choices: the budget pools the month-to-date section walks, plus the metering-only labels
#: (`usage.py::METERING_ONLY_POOL_LABELS`) a host of a keyless provider resolves to (review finding 5a, f1-config).
POOL_CHOICES: Final[tuple[str, ...]] = tuple(sorted(POOL_LABELS | METERING_ONLY_POOL_LABELS))
FORMAT_CHOICES: Final = ("table", "json")
_GROUP_COLUMN: Final[dict[str, str]] = {"pool": "pool", "lane": "lane_id", "host": "host", "day": "started_on"}
DEFAULT_WINDOW_DAYS: Final = 1

_SELECT_PROVIDER_USAGE: Final = text(load_query_sql("execution/select_provider_usage.sql")).bindparams(
    bindparam("lane_ids", type_=ARRAY(Text))
)
_SELECT_MONTH_TO_DATE: Final = text(load_query_sql("execution/select_provider_month_to_date.sql")).bindparams(
    bindparam("weighted_pools", type_=ARRAY(Text))
)
_SELECT_OPEN_INCIDENTS: Final = text(load_query_sql("execution/select_open_incidents.sql"))


def _number(value: object) -> float | int | None:
    """Coerce a jsonb-derived numeric (often a `Decimal`, from Postgres's `numeric` cast) to float/int."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (float, Decimal, str)):
        return float(value)
    raise TypeError(f"_number given an unexpected type {type(value)!r}: {value!r}")


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


async def apply_report_bounds(session: AsyncSession) -> None:
    """Pin the read-only, timeout-bounded transaction every section of this report runs inside."""
    await session.execute(_SET_STATEMENT_TIMEOUT)
    await session.execute(_SET_READ_ONLY)


# --- Section 1: month to date -----------------------------------------------------------------


async def month_to_date(session: AsyncSession, *, pool: str, now: datetime) -> dict[str, object]:
    """One pool's month-to-date rollup; the ONLY loader of select_provider_month_to_date.sql.

    f1-executor's admission (G1, `execution/provider_budget.py`) imports this function directly and
    must never load that SQL file a second time (spec Sec 4.9.2; `test_loaded_exactly_once`,
    `test_month_to_date_is_the_only_loader`).
    """
    row = await fetch_row(
        session,
        _SELECT_MONTH_TO_DATE,
        {
            "pool": pool,
            "now": now,
            "logical_caps": canonical_json(dict(LANE_LOGICAL_CAPS)),
            "weighted_pools": sorted(WEIGHTED_POOLS),
        },
    )
    if row is None:  # pragma: no cover - a fixed 3-way CROSS JOIN of one-row CTEs always answers
        row = {
            "pool": pool,
            "epoch_at": None,
            "metered_count": 0,
            "reported_count": 0,
            "suspect_basis_count": 0,
            "not_spawned_count": 0,
            "lost_count": 0,
            "charged": 0,
            "suspect": 0,
        }
    budget = None
    if pool in WEIGHTED_POOLS:
        # The provider file's own `[budget]`, the numbers admission enforces (one source for both).
        from agri_data_service.execution.provider_budget import budget_for_pool  # noqa: PLC0415 - import cycle

        capped = budget_for_pool(pool)
        budget = {
            "monthly_cap": None if capped is None else capped.weighted_calls,
            "gap_fill_ceiling": None if capped is None else capped.gap_fill_ceiling_calls,
            "forward_stop": None if capped is None else capped.forward_stop_calls,
        }
    return {
        "pool": row["pool"],
        "epoch_at": _iso(row["epoch_at"]),
        "charged": _number(row["charged"]),
        "suspect": _number(row["suspect"]),
        "basis_split": {
            "metered": row["metered_count"],
            "reported": row["reported_count"],
            "suspect": row["suspect_basis_count"],
            "not_spawned": row["not_spawned_count"],
            "lost": row["lost_count"],
        },
        "budget": budget,
    }


# --- Section 2: per lane x host -----------------------------------------------------------------


async def provider_usage_rows(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    lane_ids: Sequence[str] | None,
    pool: str | None,
) -> Sequence[Mapping[str, object]]:
    """The row-level feed `group_usage_rows` rolls up; the only loader of select_provider_usage.sql."""
    return await fetch_rows(
        session,
        _SELECT_PROVIDER_USAGE,
        {
            "since": since,
            "until": until,
            "lane_ids": list(lane_ids) if lane_ids else None,
            "pool": pool,
        },
    )


def _percentiles(values: Sequence[float]) -> tuple[float | None, float | None]:
    """(p50, p95) over real per-attempt values; (None, None) on an empty series."""
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return None, None
    if len(clean) == 1:
        return clean[0], clean[0]
    quantiles = statistics.quantiles(clean, n=100, method="inclusive")
    return quantiles[49], quantiles[94]


def _summarize_bucket(key: object, column: str, rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """One summary row for a single `--by` bucket's slice of `select_provider_usage.sql`'s rows."""
    # Only rows that actually touched a host carry send/429/5xx/bytes counters -- a hostless
    # attempt's placeholder row (see the SQL header) must never contribute a phantom zero-count host.
    host_rows = [row for row in rows if row.get("host") is not None]
    seen_attempts: set[object] = set()
    attempt_rows: list[Mapping[str, object]] = []
    for row in rows:
        attempt_id = row["attempt_id"]
        if attempt_id not in seen_attempts:
            seen_attempts.add(attempt_id)
            attempt_rows.append(row)

    def host_sum(field: str) -> float:
        return sum(_number(row.get(field)) or 0 for row in host_rows)

    def attempt_sum(field: str) -> float:
        return sum(_number(row.get(field)) or 0 for row in attempt_rows)

    # A bucket's own attempts' TOTAL metered spend, summed across every host row visible in THIS
    # bucket. Grouping by pool/lane/day keeps every host of a matching attempt together, so this is
    # exact there; grouping by host it is necessarily a per-host share, a documented limitation of a
    # single-attempt fan-out across hosts (soil, this codebase's only weighted lane, only ever hits
    # Open-Meteo -- one host per attempt in practice).
    metered_by_attempt: dict[object, float] = defaultdict(float)
    for row in rows:
        metered_by_attempt[row["attempt_id"]] += _number(row.get("weighted_calls_metered")) or 0

    http_requests = host_sum("http_requests")
    requests_total = attempt_sum("requests")
    backoff_total = host_sum("backoff_seconds")
    elapsed_total = attempt_sum("elapsed_seconds")
    rows_written_total = attempt_sum("rows_written")
    bytes_written_total = attempt_sum("bytes_written")
    elapsed_p50, elapsed_p95 = _percentiles(
        [value for row in attempt_rows if (value := _number(row.get("elapsed_seconds"))) is not None]
    )
    start_lag_p50, start_lag_p95 = _percentiles(
        [value for row in attempt_rows if (value := _number(row.get("start_lag_seconds"))) is not None]
    )
    rss_peaks = [value for row in attempt_rows if (value := _number(row.get("rss_peak_kib"))) is not None]

    outcome_counts: dict[str, int] = defaultdict(int)
    exit_class_counts: dict[str, int] = defaultdict(int)
    probe_status_counts: dict[str, int] = defaultdict(int)
    for row in attempt_rows:
        if row.get("turn_outcome"):
            outcome_counts[str(row["turn_outcome"])] += 1
        if row.get("exit_class"):
            exit_class_counts[str(row["exit_class"])] += 1
        if row.get("probe_status"):
            probe_status_counts[str(row["probe_status"])] += 1

    # "runaway": this attempt's OWN metered spend, visible in this bucket, exceeds its lane's
    # logical cap (`LANE_LOGICAL_CAPS`) -- a lane spending past the figure G0's own cap pins it to.
    # Undefined precisely by spec Sec 4.9.2 ("flag columns including runaway"); this is the most
    # literal reading of "runaway" against the one number this codebase already treats as a ceiling.
    runaway = sum(
        1
        for row in attempt_rows
        if row.get("lane_id") in LANE_LOGICAL_CAPS
        and metered_by_attempt[row["attempt_id"]] > LANE_LOGICAL_CAPS[str(row["lane_id"])]
    )

    return {
        column: key,
        "attempts": len(attempt_rows),
        "http_requests": http_requests,
        "http_429": host_sum("http_429"),
        "http_5xx": host_sum("http_5xx"),
        "transport_failures": host_sum("transport_failures"),
        "bytes_in": host_sum("bytes_in"),
        "backoff_seconds": backoff_total,
        "weighted_calls_metered": host_sum("weighted_calls_metered"),
        "http_429_rate": (host_sum("http_429") / http_requests) if http_requests else None,
        "http_5xx_rate": (host_sum("http_5xx") / http_requests) if http_requests else None,
        "transport_failure_rate": (host_sum("transport_failures") / http_requests) if http_requests else None,
        "retry_amplification": (http_requests / requests_total) if requests_total else None,
        "backoff_share": (backoff_total / elapsed_total) if elapsed_total else None,
        "bytes_written_per_row": (bytes_written_total / rows_written_total) if rows_written_total else None,
        "elapsed_seconds_p50": elapsed_p50,
        "elapsed_seconds_p95": elapsed_p95,
        "start_lag_seconds_p50": start_lag_p50,
        "start_lag_seconds_p95": start_lag_p95,
        "rss_peak_kib_max": max(rss_peaks) if rss_peaks else None,
        "charged": attempt_sum("charged"),
        "suspect": attempt_sum("suspect"),
        "turn_outcome_counts": dict(outcome_counts),
        "exit_class_counts": dict(exit_class_counts),
        "probe_status_counts": dict(probe_status_counts),
        "flags": {
            "runaway": runaway,
            "report_missing": outcome_counts.get("report_missing", 0),
            "usage_reported_false": sum(1 for row in attempt_rows if row.get("usage_reported") is False),
            "usage_complete_false": sum(1 for row in attempt_rows if row.get("usage_complete") is False),
            "unwritten_known_false": sum(1 for row in attempt_rows if row.get("unwritten_known") is False),
            "meter_errors_gt0": sum(1 for row in attempt_rows if (_number(row.get("meter_errors")) or 0) > 0),
            "publication_debt_gt0": sum(1 for row in attempt_rows if (_number(row.get("publication_debt")) or 0) > 0),
        },
    }


def group_usage_rows(rows: Sequence[Mapping[str, object]], *, by: str) -> list[dict[str, object]]:
    """Roll `select_provider_usage.sql`'s row-level feed up to one summary row per `by` bucket."""
    if by not in _GROUP_COLUMN:
        raise ValueError(f"unknown --by dimension {by!r}; choose one of {sorted(_GROUP_COLUMN)}")
    column = _GROUP_COLUMN[by]
    buckets: dict[object, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        buckets[row.get(column)].append(row)
    summaries = [_summarize_bucket(key, column, bucket_rows) for key, bucket_rows in buckets.items()]
    summaries.sort(key=lambda summary: (summary[column] is None, str(summary[column])))
    return summaries


# --- Section 3: open incidents --------------------------------------------------------------------


async def open_incidents(session: AsyncSession) -> list[dict[str, object]]:
    rows = await fetch_rows(session, _SELECT_OPEN_INCIDENTS, {})
    return [
        {
            "id": str(row["id"]),
            "fingerprint": row["fingerprint"],
            "incident_type": row["incident_type"],
            "severity": row["severity"],
            "status": row["status"],
            "summary": row["summary"],
            "occurrence_count": row["occurrence_count"],
            "first_seen_at": _iso(row["first_seen_at"]),
            "last_seen_at": _iso(row["last_seen_at"]),
            "cooldown_until": _iso(row["cooldown_until"]),
            "owner": row["owner"],
            "acknowledged_at": _iso(row["acknowledged_at"]),
            "acknowledged_by": row["acknowledged_by"],
            "state": row["state"],
            "rung": row["rung"],
            "exit_class": row["exit_class"],
            "chain_first_seen_at": row["chain_first_seen_at"],
        }
        for row in rows
    ]


# --- The window, the orchestrator and the CLI verb ------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportWindow:
    """The half-open [since, until) span section 2 reads over."""

    since: datetime
    until: datetime
    label: str


def resolve_window(*, days: int | None, since: str | None, until: str | None, now: datetime) -> ReportWindow:
    """`--days N` XOR `--since D --until D`; neither given defaults to the trailing day."""
    if (since is None) != (until is None):
        raise click.BadParameter("--since and --until must be given together")
    if since is not None and days is not None:
        raise click.BadParameter("choose --days N or --since/--until, not both")
    if since is not None and until is not None:
        try:
            since_day = date.fromisoformat(since)
            until_day = date.fromisoformat(until)
        except ValueError as error:
            raise click.BadParameter("--since/--until must be YYYY-MM-DD") from error
        if until_day < since_day:
            raise click.BadParameter("--until must not be before --since")
        since_at = datetime.combine(since_day, datetime.min.time(), tzinfo=UTC)
        until_at = datetime.combine(until_day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
        return ReportWindow(since_at, until_at, f"{since_day.isoformat()}..{until_day.isoformat()}")
    span_days = days if days is not None else DEFAULT_WINDOW_DAYS
    until_at = now
    since_at = now - timedelta(days=span_days)
    return ReportWindow(since_at, until_at, f"trailing {span_days}d")


async def _run_section(session: AsyncSession, factory: Callable[[], Awaitable[object]]) -> object:
    """Run one section inside its own savepoint; a failure there never poisons the others.

    Mirrors `scripts/readiness.py::run_warehouse_checks`'s per-check try/except and
    `routes/ops.py::_cached_or_read`'s savepoint, adapted for a SQLAlchemy-managed transaction: a
    raising statement leaves the OUTER transaction unusable until rolled back to a savepoint, which
    `session.begin_nested()` opens and (on the exception here) automatically rolls back to, leaving
    later sections free to run in the same transaction. Catches `Exception`, not only
    `SQLAlchemyError`, on the same reasoning `routes/ops.py` states: the Python-side grouping and
    percentile math in `usage_section` above can itself raise on a row shape this fold never
    anticipated, and that failure must stay scoped to its own section too.
    """
    try:
        async with session.begin_nested():
            return await factory()
    except Exception as error:  # deliberately broad, see the docstring above (BLE001 is not enabled here)
        return {"error": f"{type(error).__name__}: {error}"}


async def build_report(  # noqa: PLR0913 - one full report needs the window, every filter, `by` and `now`
    *,
    days: int | None,
    since: str | None,
    until: str | None,
    lane_ids: Sequence[str] | None,
    pool: str | None,
    by: str,
    now: datetime | None = None,
) -> dict[str, object]:
    """Assemble the full three-section report inside one read-only, timeout-bounded transaction."""
    now = now or datetime.now(UTC)
    window = resolve_window(days=days, since=since, until=until, now=now)
    pools = [pool] if pool else sorted(POOL_LABELS)
    report: dict[str, object] = {
        "event": "plantgeo_usage_report",
        "generated_at": now.isoformat(),
        "window": {"since": window.since.isoformat(), "until": window.until.isoformat(), "label": window.label},
        "lane_filter": list(lane_ids) if lane_ids else None,
        "pool_filter": pool,
        "by": by,
    }
    async with ingest_session() as session:
        await apply_report_bounds(session)

        async def month_to_date_section() -> dict[str, object]:
            return {one_pool: await month_to_date(session, pool=one_pool, now=now) for one_pool in pools}

        async def usage_section() -> list[dict[str, object]]:
            rows = await provider_usage_rows(
                session, since=window.since, until=window.until, lane_ids=lane_ids, pool=pool
            )
            return group_usage_rows(rows, by=by)

        async def incidents_section() -> list[dict[str, object]]:
            return await open_incidents(session)

        report["month_to_date"] = await _run_section(session, month_to_date_section)
        report["usage"] = await _run_section(session, usage_section)
        report["open_incidents"] = await _run_section(session, incidents_section)
        await session.rollback()
    return report


def _render_table(report: Mapping[str, object]) -> str:
    """A compact human-readable rendering; JSON (the default) is the report's primary output."""
    window = cast("Mapping[str, object]", report["window"])
    lines = [f"jobs-usage-report  window={window['label']}  by={report['by']}"]
    lines.append("\n== month_to_date")
    month_to_date_section = report["month_to_date"]
    if isinstance(month_to_date_section, dict):
        lines.extend(
            f"  {pool_name}: {json.dumps(pool_report, sort_keys=True, default=str)}"
            for pool_name, pool_report in month_to_date_section.items()
        )
    else:
        lines.append(f"  {month_to_date_section}")
    lines.append("\n== usage")
    usage_section = report["usage"]
    if isinstance(usage_section, list):
        lines.extend("  " + "  ".join(f"{key}={value}" for key, value in bucket.items()) for bucket in usage_section)
    else:
        lines.append(f"  {usage_section}")
    lines.append("\n== open_incidents")
    incidents_section = report["open_incidents"]
    if isinstance(incidents_section, list):
        lines.extend(f"  {incident.get('incident_type')}: {incident.get('summary')}" for incident in incidents_section)
    else:
        lines.append(f"  {incidents_section}")
    return "\n".join(lines)


@click.command("jobs-usage-report")
@click.option("--days", type=click.IntRange(min=1), default=None, help="Trailing N days; the default window shape.")
@click.option("--since", default=None, help="Window start, YYYY-MM-DD (UTC); requires --until.")
@click.option("--until", default=None, help="Window end, YYYY-MM-DD (UTC, inclusive); requires --since.")
@click.option("--lane", "lanes", multiple=True, help="Restrict to one or more lane ids; repeatable.")
@click.option("--pool", default=None, type=click.Choice(POOL_CHOICES), help="Restrict to one metering pool.")
@click.option("--by", type=click.Choice(BY_CHOICES), default="lane", show_default=True, help="Section 2's grouping.")
@click.option("--format", "output_format", type=click.Choice(FORMAT_CHOICES), default="json", show_default=True)
def jobs_usage_report(  # noqa: PLR0913 - click binds one parameter per CLI option; this verb has seven
    days: int | None,
    since: str | None,
    until: str | None,
    lanes: tuple[str, ...],
    pool: str | None,
    by: str,
    output_format: str,
) -> None:
    """Print the source-usage audit: month-to-date spend, per-lane x host rates, open incidents.

    Read-only transaction, statement_timeout = 30s, through LOCAL_SOURCE_LOADER_DATABASE_URL. Each
    of the three sections is fault-isolated; a failing section reports its own error and the other
    two still print (spec Sec 4.9.2; execution/AGENTS.md "The usage fold").
    """
    try:
        report = asyncio.run(
            build_report(days=days, since=since, until=until, lane_ids=lanes or None, pool=pool, by=by)
        )
    except click.BadParameter:
        raise
    except SQLAlchemyError as error:
        raise click.ClickException(f"jobs-usage-report could not open its read-only transaction: {error}") from error
    if output_format == "json":
        click.echo(json.dumps(report, sort_keys=True, default=str))
    else:
        click.echo(_render_table(report))
