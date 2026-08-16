"""The uniform matview refresh lane: eleven views, one watermark-gated pass, one ledger row each.

Owner directive, 2026-08-15: "all 21 layers need to match to reduce memory usage as much as
possible" and "the application never runs an analytical query". Every aggregate the serving read
paths used to compute on request is now a `WITH NO DATA` matview (`drizzle/0029_pre_aggregation_layer.sql`,
a different tree from this service's own Alembic one -- see jobs/AGENTS.md "Every statement is
agri.-qualified, always" for why that split is normal here) with a unique index for `REFRESH ...
CONCURRENTLY`. This module is what keeps every one of them populated, following the same registration
shape `jobs/strategy_mv_refresh.py` already established: a `JobDefinitionSpec`, a `JobHandler` bound
with `@job_handler`, and a `trigger_matview_refresh` entry point published to `jobs/dispatch.py`.

WHY THIS IS A SEPARATE LANE RATHER THAN A WIDENED `strategy_mv_refresh`. That module's own docstring
carries an explicit guardrail: it refreshes exactly the three `geo.mv_strategy_recommendations_*`
views and nothing else, and forbids widening. Respecting that means every other matview needs a
different lane, not a longer tuple passed into the same one.

Two deliberate departures from that lane's template, both required by the scale here (eleven views
instead of three, and one of them -- `geo.mv_signal_cell_daily` -- rebuilding a ~3.2 GB rollup off a
26 GB source on every REFRESH, concurrent or not):

1. **No bucketed `logical_run_key`.** `strategy_mv_refresh.py::_run_bucket_key` mints a NEW
   `job_run_id` every `STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS`, and `.omc/RUNBOOK.md`'s "Traps
   discovered" section already documents the resulting orphan class against THIS repository's own
   production ledger: a work item stuck `running` behind a dead lease in one bucket's run is invisible
   to every later tick, because `run_job_slice`'s claim is scoped to the `job_run_id` the caller
   passes, while `reclaim_expired_leases` is scoped to the `job_definition_id` -- reclaiming the lease
   does not help once the run itself has rotated away. This lane instead opens ONE never-rotating run
   (`MATVIEW_REFRESH_RUN_KEY`) forever, via `open_job_run`'s own idempotent `ON CONFLICT DO NOTHING`,
   and fans in exactly one fresh, uniquely-keyed work item per trigger call. A shard stuck behind a
   dead lease from tick N is still inside the one run tick N+1 claims from, so the definition-scoped
   reaper can actually reach it.
2. **A watermark gate.** Refreshing every view on every tick would make the periodic full rebuild of
   `geo.mv_signal_cell_daily` -- REFRESH, concurrent or not, always rebuilds a plain matview's ENTIRE
   contents; there is no incremental refresh for a relation that is not a continuous aggregate, and
   this database has no hypertable this rollup could be built on (see the design note this lane was
   built from: `timescaledb_information.hypertables` holds exactly one row, `tracking.positions`, 0
   chunks, and it is not this table) -- the single most expensive thing this box ever runs, on a
   schedule rather than only when something actually changed. Each spec below carries an O(1)/O(small
   table) watermark query; a view whose watermark has not moved, and whose last refresh is not yet
   stale past its own ceiling, is skipped with no REFRESH statement issued at all. In the steady state
   R2 measured (the signal plane 9-13 days stale, the box idle >90% of wall-clock), this makes the
   heaviest view refresh "approximately never" rather than on every tick forever.

ONE WORK ITEM, ELEVEN VIEWS, BUDGET-AWARE -- WITH A DELIBERATE DEPARTURE FROM `has_budget_for`'S
USUAL USE. Unlike `strategy_mv_refresh`'s three views (which always finish inside one bounded call),
an unlucky tick here could need to refresh several stale views in a row, including the ~5-minute
`mv_signal_cell_daily` rebuild. `JobInvocation.seconds_remaining` is a SNAPSHOT taken once when the
call was built and does not tick down inside it (jobs/AGENTS.md "The budget-aware handler contract"
is explicit: "one bounded step per call... a handler that wants a live clock is a handler doing too
much in one call"). Calling `invocation.has_budget_for(...)` naively before each of several REFRESH
statements in the SAME call would therefore never notice time this very call already spent refreshing
an earlier view -- the frozen snapshot would keep saying yes. This handler is deliberately the "doing
too much in one call" case the docs warn about, on purpose, because eleven small watermark checks and
zero-to-a-few real refreshes is still one bounded unit of work; the fix is to track this call's own
elapsed wall-clock time explicitly (`_remaining_budget_seconds`) and subtract it from the snapshot
before every comparison, rather than to split each view into its own call. Once the adjusted
remainder can no longer safely cover `BUDGET_SAFETY_FACTOR` times a view's estimated cost, the
handler YIELDS with a cursor naming every view already refreshed this tick, rather than starting a
refresh it cannot finish inside this slice. A yield is not a failure and spends no retry budget; the
parked shard is immediately claimable, and the next tick resumes with the views already done skipped
via the cursor.

AGRI.MV_FORECAST_ML_DAILY_SERVING SELF-HEALS HERE. `pg_class.relispopulated = false` today, and the
non-negotiable this module exists to satisfy names it explicitly: a matview that is never refreshed
is the exact bug class to prevent, permanently. Every view this lane touches gets the same
unpopulated check every tick (`select_materialized_view_populated.sql`); one that fails it gets a
plain, non-concurrent first REFRESH (CONCURRENTLY is a hard error on an unpopulated matview) rather
than being skipped forever waiting for someone to notice.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.jobs.dispatch import register_dispatchable_lane
from agri_data_service.jobs.lease import apply_statement_timeout, canonical_json, fetch_row, fetch_rows, required_column
from agri_data_service.jobs.registry import (
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobWorkItemSpec,
    job_handler,
)
from agri_data_service.jobs.worker import (
    ensure_job_definition,
    open_job_run,
    run_job_slice,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.jobs.registry import JobInvocation
    from agri_data_service.jobs.worker import JobSliceSummary

logger = structlog.get_logger()

# --- Identity: what this lane is called, in the ledger and in the handler registry. ---
MATVIEW_REFRESH_DEFINITION_NAME: Final = "matview-refresh"
MATVIEW_REFRESH_DEFINITION_VERSION: Final = "v1"
MATVIEW_REFRESH_HANDLER_TOKEN: Final = "jobs.matview_refresh"
MATVIEW_REFRESH_QUEUE_NAME: Final = "matview-refresh"
MATVIEW_REFRESH_WORK_ITEM_KIND: Final = "matview_refresh_batch"

# The one, never-rotating run this lane ever opens -- see the module docstring's departure #1.
MATVIEW_REFRESH_RUN_KEY: Final = "matview-refresh:pulse"

# --- Budget: a slice may need to refresh several stale views, including the ~5-minute
# geo.mv_signal_cell_daily rebuild, in one tick. lease_seconds must exceed time_budget_seconds
# (JobDefinitionSpec's own rule); 2400 > 1800 satisfies it with headroom for the ledger writes
# around each individual REFRESH.
MATVIEW_REFRESH_MAX_ATTEMPTS: Final = 3
MATVIEW_REFRESH_LEASE_SECONDS: Final = 2_400
MATVIEW_REFRESH_TIME_BUDGET_SECONDS: Final = 1_800

# Documentary only, exactly as strategy_mv_refresh.py's own schedule constant is: nothing in this
# runtime parses cron syntax (job_definition.schedule is written and never read). This lane has no
# in-process periodic driver of its own -- it runs from jobs-pulse's dispatchable-lane pass and from
# a manual POST /api/v1/jobs/trigger -- so this cadence is a hint for an operator reading the row,
# not a knob anything obeys.
MATVIEW_REFRESH_SCHEDULE_CRON: Final = "0 * * * *"
MATVIEW_REFRESH_SCHEDULE_TIMEZONE: Final = "UTC"

# Design doc section 5.3 step 4: "if remaining < 2 x last_duration_ms, stop and defer to the next
# tick". Doubling rather than a 1x margin absorbs one view running slower than its own last
# measurement (a cold cache, a bigger delta since the last refresh) without starting a refresh this
# slice cannot finish -- an unfinished REFRESH is not resumable, unlike a checkpointed chunk.
BUDGET_SAFETY_FACTOR: Final = 2.0

# Design doc section 5.3 step 5: "One session x 2 workers x 32 MB ~= 96 MB of exposure. This is
# where raising work_mem is safe" -- the global 4 MB stays untouched (config.py owns no setting for
# it; this is a per-refresh SET LOCAL, not a server default). SET LOCAL cannot take a bind
# parameter, so both are interpolated string constants rather than query parameters -- safe because
# neither is ever derived from anything an operator or an upstream payload supplies.
MATVIEW_REFRESH_WORK_MEM: Final = "32MB"
MATVIEW_REFRESH_MAX_PARALLEL_WORKERS_PER_GATHER: Final = 1

_WORKER_ID_ENVIRONMENT_VARIABLE: Final = "RAILWAY_REPLICA_ID"
_WORKER_ID_MAX_LENGTH: Final = 255

MatviewRefreshOutcome = Literal[
    "refreshed_concurrently",
    "refreshed_full",
    "self_healed_unpopulated",
    "skipped_unchanged",
    "skipped_missing",
    "skipped_budget",
    "failed",
]

# --- Watermark queries, one per source relation a spec below points at, not one per view: several
# views share a source and therefore share a watermark. Static SQL, no interpolation, and every one
# of them lives in sql/jobs/ per sql/AGENTS.md's own rule ("The SQL lives in sql/jobs/, not
# inline") -- the rationale for each (why THAT column, why a clock component, why that relation and
# not its 26 GB sibling) is in the file's own header, which is the only place a reader looking at
# the SQL will find it. Two of the six sources have a SECOND, clock-bearing variant: the views over
# them materialise a window relative to now(), so a source-only watermark would skip a refresh they
# needed. drizzle/0029 states both as a MUST on the views themselves. ---

_WATERMARK_FEATURES_UPDATED_AT: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_features_updated_at.sql")
)

_WATERMARK_FEATURES_UPDATED_AT_HOURLY: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_features_updated_at_hourly.sql")
)

_WATERMARK_DROUGHT_AREAS: Final = text(load_query_sql("jobs/matview_refresh_watermark_drought_areas.sql"))

_WATERMARK_DROUGHT_AREAS_LIVE_EDGE: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_drought_areas_live_edge.sql")
)

_WATERMARK_SOURCE_RELEASE: Final = text(load_query_sql("jobs/matview_refresh_watermark_source_release.sql"))

_WATERMARK_SOIL_SURVEY_COVERAGE: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_soil_survey_coverage.sql")
)

_WATERMARK_WATERSHED_FEATURES: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_watershed_features.sql")
)

_WATERMARK_FORECAST_PUBLICATION: Final = text(
    load_query_sql("jobs/matview_refresh_watermark_forecast_publication.sql")
)


@dataclass(frozen=True, slots=True)
class MatviewRefreshSpec:
    """One matview under this lane's discipline: where its watermark comes from and how it is spent."""

    qualified_name: str
    watermark_sql: TextClause
    min_interval_seconds: int
    max_staleness_seconds: int
    # Lower runs first among eligible views on a budget-truncated tick: 0 = the small dictionaries,
    # 1 = the census/rollup matviews, 2 = geo.mv_signal_cell_daily alone, the one relation whose
    # REFRESH is heavy enough that starving the other ten behind it would be the wrong trade.
    priority: int
    statement_timeout_seconds: int
    # Used only when no prior duration is on record (a view's first-ever refresh); every later tick
    # estimates from agri.matview_refresh_state.duration_ms instead, which is a real measurement.
    default_estimate_seconds: float


# Design doc section 14's MATVIEWS block, minus the three geo.mv_strategy_recommendations_* views
# (which strategy_mv_refresh.py keeps under its own guardrail) and their own watermark/interval
# table in section 5.2. Nine new matviews drizzle/0029 creates plus two pre-existing ones adopted
# under this lane's discipline (geo.watershed_rollup, agri.mv_forecast_ml_daily_serving) = eleven.
MATVIEW_REFRESH_SPECS: Final[tuple[MatviewRefreshSpec, ...]] = (
    MatviewRefreshSpec(
        qualified_name="geo.mv_layer_feature_stats",
        watermark_sql=_WATERMARK_FEATURES_UPDATED_AT,
        min_interval_seconds=900,
        max_staleness_seconds=21_600,
        priority=0,
        statement_timeout_seconds=60,
        default_estimate_seconds=2.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_layer_hourly_activity",
        watermark_sql=_WATERMARK_FEATURES_UPDATED_AT_HOURLY,
        min_interval_seconds=900,
        max_staleness_seconds=3_600,
        priority=0,
        statement_timeout_seconds=60,
        default_estimate_seconds=2.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_drought_release_index",
        watermark_sql=_WATERMARK_DROUGHT_AREAS,
        min_interval_seconds=3_600,
        max_staleness_seconds=86_400,
        priority=0,
        statement_timeout_seconds=60,
        default_estimate_seconds=2.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_feature_observation_day",
        watermark_sql=_WATERMARK_FEATURES_UPDATED_AT,
        # 3,600 s, not the 900 s the other geo.features-backed views use. This is the one census
        # refresh that reads the geo.features heap AND its 1,467 MB of TOAST -- both `metric_counts`
        # and `newest_observed_at` need `properties` -- and its watermark moves on every single
        # ingest, so a 900 s floor would let it run on nearly every tick.
        min_interval_seconds=3_600,
        max_staleness_seconds=21_600,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_drought_observation_day",
        watermark_sql=_WATERMARK_DROUGHT_AREAS_LIVE_EDGE,
        min_interval_seconds=3_600,
        max_staleness_seconds=86_400,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_signal_observation_day",
        watermark_sql=_WATERMARK_SOURCE_RELEASE,
        min_interval_seconds=21_600,
        max_staleness_seconds=86_400,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_soil_survey_grid",
        watermark_sql=_WATERMARK_SOIL_SURVEY_COVERAGE,
        min_interval_seconds=21_600,
        max_staleness_seconds=604_800,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_soil_survey_union",
        watermark_sql=_WATERMARK_SOIL_SURVEY_COVERAGE,
        min_interval_seconds=21_600,
        max_staleness_seconds=604_800,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.watershed_rollup",
        watermark_sql=_WATERMARK_WATERSHED_FEATURES,
        min_interval_seconds=86_400,
        max_staleness_seconds=604_800,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="agri.mv_forecast_ml_daily_serving",
        watermark_sql=_WATERMARK_FORECAST_PUBLICATION,
        min_interval_seconds=21_600,
        max_staleness_seconds=86_400,
        priority=1,
        statement_timeout_seconds=300,
        default_estimate_seconds=20.0,
    ),
    MatviewRefreshSpec(
        qualified_name="geo.mv_signal_cell_daily",
        watermark_sql=_WATERMARK_SOURCE_RELEASE,
        min_interval_seconds=86_400,
        max_staleness_seconds=259_200,
        priority=2,
        statement_timeout_seconds=1_800,
        default_estimate_seconds=300.0,
    ),
)

MATVIEW_REFRESH_QUALIFIED_NAMES: Final[frozenset[str]] = frozenset(
    spec.qualified_name for spec in MATVIEW_REFRESH_SPECS
)


class MatviewRefreshContextError(RuntimeError):
    """Raised when the handler runs with no bound session, which would otherwise be a silent no-op."""


@dataclass(frozen=True, slots=True)
class MatviewRefreshResult:
    """What happened to one view on one tick: how it landed, whether CONCURRENTLY was used, its cost."""

    qualified_name: str
    status: MatviewRefreshOutcome
    used_concurrently: bool
    elapsed_seconds: float
    row_count: int | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class MatviewRefreshReport:
    """One tick's full result: every considered view, in the order it was evaluated or attempted."""

    results: tuple[MatviewRefreshResult, ...]

    @property
    def has_failures(self) -> bool:
        """True on a real REFRESH failure, or when every ATTEMPTED view turned out missing.

        A skipped-unchanged or skipped-budget view was never attempted at all -- it is not evidence
        of anything wrong, only of the watermark gate or this tick's clock doing their job. But if
        every view this tick DID attempt came back `skipped_missing`, that is the same degraded
        signal strategy_mv_refresh.py's own report gives for an all-missing tick: drizzle/0029
        probably has not been applied against this database yet.
        """
        if any(result.status == "failed" for result in self.results):
            return True
        attempted = [
            result for result in self.results if result.status not in {"skipped_unchanged", "skipped_budget"}
        ]
        return bool(attempted) and all(result.status == "skipped_missing" for result in attempted)

    def failure_summary(self) -> str:
        """Name every view that failed, or -- on an all-attempted-missing tick -- every one that was missing."""
        failed = [result.qualified_name for result in self.results if result.status == "failed"]
        if failed:
            return f"materialized view refresh failed for: {', '.join(failed)}"
        missing = [result.qualified_name for result in self.results if result.status == "skipped_missing"]
        return (
            "materialized view refresh found none of its attempted views (drizzle 0029 not applied "
            f"against this database?): {', '.join(missing)}"
        )

    def to_metrics(self) -> dict[str, object]:
        """Render this report as the JSONB `job_attempt.metrics` shape -- the per-view cost detail
        the non-negotiable asks for, matching the census shard timings' own visibility.
        """
        return {
            "views": [
                {
                    "view": result.qualified_name,
                    "status": result.status,
                    "used_concurrently": result.used_concurrently,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "row_count": result.row_count,
                    "detail": result.detail,
                }
                for result in self.results
            ],
        }


# --- SQL: view-shape checks shared with strategy_mv_refresh.py's own conventions, plus this lane's
# own state table. ---

_VIEW_EXISTS_SQL: Final = text("SELECT to_regclass(:qualified_name) IS NOT NULL AS view_exists")

# PUBLIC because jobs/strategy_mv_refresh.py runs the same two statements against the same two
# relations and sql/AGENTS.md's LOADED-ONCE rule allows each .sql file exactly one
# `load_query_sql` call site anywhere under src/. This module owns the load; that lane imports the
# compiled clause. Two loader calls on one path is how a query quietly acquires two copies.
SELECT_MATERIALIZED_VIEW_UNIQUE_INDEX: Final = text(
    load_query_sql("jobs/select_materialized_view_unique_index.sql")
)

_SELECT_UNIQUE_INDEX: Final = SELECT_MATERIALIZED_VIEW_UNIQUE_INDEX

_SELECT_POPULATED: Final = text(load_query_sql("jobs/select_materialized_view_populated.sql"))

# reltuples, not count(*): the whole point of this lane is that the application never runs an
# analytical query, and an exact COUNT over an ~18M-row rollup after every refresh would be exactly
# that, paid by the maintenance path instead of a request but no cheaper for it. The catalog estimate
# is refreshed by the REFRESH itself (ANALYZE runs as part of a matview rebuild) and costs one index
# lookup.
_ROW_COUNT_ESTIMATE_SQL: Final = text(
    "SELECT reltuples::bigint AS row_count_estimate FROM pg_class WHERE oid = to_regclass(:qualified_name)"
)

_SELECT_MATVIEW_REFRESH_STATE: Final = text(load_query_sql("jobs/select_matview_refresh_state.sql"))

UPSERT_MATVIEW_REFRESH_STATE: Final = text(load_query_sql("jobs/upsert_matview_refresh_state.sql"))

_UPSERT_MATVIEW_REFRESH_STATE: Final = UPSERT_MATVIEW_REFRESH_STATE


# The REAL `datetime` class, captured at import. `isinstance(..., datetime)` cannot use the module
# global: the only way to control `datetime.now()` in a test is to rebind THIS module's `datetime`
# name to a frozen-clock subclass (`datetime.datetime` is an immutable C type and refuses
# `setattr` on `now`), and a genuine `datetime` returned by the driver is NOT an instance of that
# subclass. Every `refreshed_at` would then read as None, every view would look never-refreshed,
# and the watermark gate would appear to pass every view through on a tick where it should have
# skipped all of them -- the failure looks like a lane bug and is a test-harness artefact.
_DATETIME_TYPE: Final = datetime


@dataclass(frozen=True, slots=True)
class _PriorRefreshState:
    """One view's last recorded state, read back from `agri.matview_refresh_state`."""

    source_watermark: str
    refreshed_at: datetime | None
    duration_ms: int | None


async def _view_exists(session: AsyncSession, qualified_name: str) -> bool:
    """True when `qualified_name` resolves to a real relation right now."""
    row = await fetch_row(session, _VIEW_EXISTS_SQL, {"qualified_name": qualified_name})
    return bool(row is not None and row["view_exists"])


async def _is_populated(session: AsyncSession, qualified_name: str) -> bool:
    """True once the view has been refreshed at least once; false for a fresh `WITH NO DATA` matview."""
    schema_name, _, view_name = qualified_name.partition(".")
    row = await fetch_row(session, _SELECT_POPULATED, {"schema_name": schema_name, "view_name": view_name})
    return bool(row is not None and row["is_populated"])


async def _has_refreshable_unique_index(session: AsyncSession, qualified_name: str) -> bool:
    """True when the view carries the unique, non-partial index CONCURRENTLY requires."""
    schema_name, _, view_name = qualified_name.partition(".")
    row = await fetch_row(session, _SELECT_UNIQUE_INDEX, {"schema_name": schema_name, "view_name": view_name})
    return bool(row is not None and row["has_unique_index"])


async def _row_count_estimate(session: AsyncSession, qualified_name: str) -> int | None:
    """The view's approximate row count, read from the catalog rather than scanned."""
    row = await fetch_row(session, _ROW_COUNT_ESTIMATE_SQL, {"qualified_name": qualified_name})
    if row is None or row.get("row_count_estimate") is None:
        return None
    # reltuples is a planner estimate and can read slightly negative immediately after a DDL change
    # on a relation ANALYZE has not yet touched; clamped rather than surfaced as a confusing negative.
    return max(0, int(row["row_count_estimate"]))


def _refresh_statement(qualified_name: str, *, concurrently: bool) -> TextClause:
    """Build the REFRESH statement text. The view name cannot be a bind parameter -- identifiers
    never can be -- so this is only safe because every caller passes a name drawn from
    `MATVIEW_REFRESH_SPECS`, a closed, code-defined tuple, never anything an operator supplies.
    """
    keyword = "CONCURRENTLY " if concurrently else ""
    return text(f"REFRESH MATERIALIZED VIEW {keyword}{qualified_name}")


async def _set_local_refresh_settings(session: AsyncSession, spec: MatviewRefreshSpec) -> None:
    """Pin this refresh's own statement timeout and memory ceiling; `SET LOCAL` dies with its transaction."""
    await session.execute(text(f"SET LOCAL statement_timeout = '{spec.statement_timeout_seconds}s'"))
    await session.execute(text(f"SET LOCAL work_mem = '{MATVIEW_REFRESH_WORK_MEM}'"))
    await session.execute(
        text(f"SET LOCAL max_parallel_workers_per_gather = {MATVIEW_REFRESH_MAX_PARALLEL_WORKERS_PER_GATHER}")
    )


async def _execute_refresh(
    session: AsyncSession,
    spec: MatviewRefreshSpec,
    *,
    concurrently: bool,
    status_on_success: MatviewRefreshOutcome,
) -> MatviewRefreshResult:
    """Run one REFRESH statement under this view's own session settings and report what happened."""
    started = time.monotonic()
    await _set_local_refresh_settings(session, spec)
    try:
        await session.execute(_refresh_statement(spec.qualified_name, concurrently=concurrently))
    except SQLAlchemyError as error:
        await session.rollback()
        elapsed = time.monotonic() - started
        logger.error(
            "matview_refresh_failed",
            view=spec.qualified_name,
            used_concurrently=concurrently,
            elapsed_seconds=round(elapsed, 3),
            error=type(error).__name__,
        )
        return MatviewRefreshResult(
            qualified_name=spec.qualified_name,
            status="failed",
            used_concurrently=concurrently,
            elapsed_seconds=elapsed,
            row_count=None,
            detail=type(error).__name__,
        )
    elapsed = time.monotonic() - started
    row_count = await _row_count_estimate(session, spec.qualified_name)
    await session.commit()
    logger.info(
        "matview_refresh_view_refreshed",
        view=spec.qualified_name,
        status=status_on_success,
        used_concurrently=concurrently,
        elapsed_seconds=round(elapsed, 3),
        row_count=row_count,
    )
    return MatviewRefreshResult(
        qualified_name=spec.qualified_name,
        status=status_on_success,
        used_concurrently=concurrently,
        elapsed_seconds=elapsed,
        row_count=row_count,
    )


async def _refresh_one_matview(session: AsyncSession, spec: MatviewRefreshSpec) -> MatviewRefreshResult:
    """Refresh one view: skip if absent, self-heal if unpopulated, fall back if unrefreshable concurrently."""
    await apply_statement_timeout(session)
    if not await _view_exists(session, spec.qualified_name):
        logger.warning("matview_refresh_view_missing", view=spec.qualified_name)
        return MatviewRefreshResult(
            qualified_name=spec.qualified_name,
            status="skipped_missing",
            used_concurrently=False,
            elapsed_seconds=0.0,
            row_count=None,
            detail="relation does not exist (drizzle 0029 not applied against this database?)",
        )
    if not await _is_populated(session, spec.qualified_name):
        # NON-NEGOTIABLE: CONCURRENTLY is a hard error on an unpopulated matview, and a matview that
        # nobody ever ran a first REFRESH against is the exact bug agri.mv_forecast_ml_daily_serving
        # shipped as. A plain, non-concurrent REFRESH here makes that state self-correcting forever
        # rather than permanent.
        logger.warning("matview_refresh_self_healing_unpopulated_view", view=spec.qualified_name)
        return await _execute_refresh(session, spec, concurrently=False, status_on_success="self_healed_unpopulated")
    use_concurrently = await _has_refreshable_unique_index(session, spec.qualified_name)
    if not use_concurrently:
        # Logged loudly rather than silently downgraded: a plain REFRESH locks the view against
        # every reader for the whole rebuild, and an operator needs to know that happened.
        logger.warning(
            "matview_refresh_missing_unique_index_falls_back_to_full_refresh",
            view=spec.qualified_name,
            reason="no unique, non-partial index found; REFRESH CONCURRENTLY is not legal here",
        )
    return await _execute_refresh(
        session,
        spec,
        concurrently=use_concurrently,
        status_on_success="refreshed_concurrently" if use_concurrently else "refreshed_full",
    )


def _watermark_signature(row: Mapping[str, object] | None) -> str:
    """A canonical, comparable identity for one watermark read; a matching-nothing query reads as `{}`."""
    return canonical_json({str(key): value for key, value in (row or {}).items()})


async def _evaluate_watermark(session: AsyncSession, spec: MatviewRefreshSpec) -> str:
    """Read this view's watermark and render it as the string `agri.matview_refresh_state` compares."""
    row = await fetch_row(session, spec.watermark_sql, {})
    return _watermark_signature(row)


async def _read_prior_refresh_state(session: AsyncSession) -> dict[str, _PriorRefreshState]:
    """Every view this service has ever recorded a refresh outcome for, keyed by qualified name."""
    rows = await fetch_rows(session, _SELECT_MATVIEW_REFRESH_STATE, {})
    return {
        required_column(row, "view_name", str): _PriorRefreshState(
            source_watermark=required_column(row, "source_watermark", str),
            refreshed_at=row.get("refreshed_at") if isinstance(row.get("refreshed_at"), _DATETIME_TYPE) else None,
            duration_ms=row.get("duration_ms") if isinstance(row.get("duration_ms"), int) else None,
        )
        for row in rows
    }


# Outcomes that mean the view's contents actually moved -- the only ones that advance refreshed_at.
_SUCCESSFUL_REFRESH_OUTCOMES: Final[frozenset[str]] = frozenset(
    {"refreshed_concurrently", "refreshed_full", "self_healed_unpopulated"}
)


async def _write_refresh_state(
    session: AsyncSession,
    spec: MatviewRefreshSpec,
    result: MatviewRefreshResult,
    *,
    current_watermark: str,
) -> None:
    """Persist one attempted view's outcome; see upsert_matview_refresh_state.sql for the NULL/COALESCE rule."""
    await apply_statement_timeout(session)
    refreshed_at = datetime.now(UTC) if result.status in _SUCCESSFUL_REFRESH_OUTCOMES else None
    await fetch_row(
        session,
        _UPSERT_MATVIEW_REFRESH_STATE,
        {
            "view_name": spec.qualified_name,
            "source_watermark": current_watermark,
            "refreshed_at": refreshed_at,
            "duration_ms": round(result.elapsed_seconds * 1000),
            "row_count": result.row_count,
            "outcome": result.status,
        },
    )
    await session.commit()


# NULLS-FIRST sort key for a view with no recorded refresh: sorts before every real timestamp, which
# is what "never refreshed" should mean for (priority, refreshed_at ASC NULLS FIRST) ordering.
_NEVER_REFRESHED_SORT_KEY: Final = datetime.min.replace(tzinfo=UTC)


def _eligibility(
    spec: MatviewRefreshSpec,
    prior: _PriorRefreshState | None,
    current_watermark: str,
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Design doc section 5.2's watermark gate: unchanged-and-fresh skips, changed-or-stale attempts.

    Clock skew here is tolerated deliberately, unlike a lease deadline (jobs/AGENTS.md "every
    timestamp comes from the database"): the thresholds this gate compares against are measured in
    minutes to days, and a lease loses correctness over seconds of skew while this gate only loses a
    few seconds of scheduling precision, so a Python `datetime.now(UTC)` is used rather than a second
    database round trip purely to fetch `now()`.
    """
    if prior is None or prior.refreshed_at is None:
        return True, "never successfully refreshed"
    elapsed_seconds = (now - prior.refreshed_at).total_seconds()
    watermark_changed = current_watermark != prior.source_watermark
    if watermark_changed:
        if elapsed_seconds < spec.min_interval_seconds:
            return False, (
                f"watermark changed but only {elapsed_seconds:.0f}s since the last refresh "
                f"(min_interval_seconds={spec.min_interval_seconds})"
            )
        return True, "watermark changed"
    if elapsed_seconds >= spec.max_staleness_seconds:
        return True, (
            f"watermark unchanged but {elapsed_seconds:.0f}s stale (max_staleness_seconds={spec.max_staleness_seconds})"
        )
    return False, "watermark unchanged"


@dataclass(frozen=True, slots=True)
class _PlannedRefresh:
    """One spec's watermark read and eligibility verdict for this tick, before any REFRESH runs."""

    spec: MatviewRefreshSpec
    current_watermark: str
    eligible: bool
    reason: str
    prior: _PriorRefreshState | None


async def _plan_refreshes(
    session: AsyncSession,
    specs: Sequence[MatviewRefreshSpec],
    prior_by_view: Mapping[str, _PriorRefreshState],
    *,
    now: datetime,
) -> tuple[_PlannedRefresh, ...]:
    """Evaluate every spec's watermark and eligibility, in declared order, before any REFRESH runs.

    Every spec is read here, whether or not it turns out eligible: the watermark queries are the
    entire cost of the gate (each O(1) or O(small table) per design), and reading all eleven up front
    is what lets the handler report a full picture of what it considered, not only what it refreshed.
    """
    plans = []
    for spec in specs:
        current_watermark = await _evaluate_watermark(session, spec)
        prior = prior_by_view.get(spec.qualified_name)
        eligible, reason = _eligibility(spec, prior, current_watermark, now=now)
        plans.append(
            _PlannedRefresh(
                spec=spec, current_watermark=current_watermark, eligible=eligible, reason=reason, prior=prior
            )
        )
    return tuple(plans)


def _estimate_seconds(spec: MatviewRefreshSpec, prior: _PriorRefreshState | None) -> float:
    """This view's next refresh, estimated from its own last measured duration when one is on record."""
    if prior is not None and prior.duration_ms is not None and prior.duration_ms > 0:
        return prior.duration_ms / 1000.0
    return spec.default_estimate_seconds


def _budget_skipped_result(spec: MatviewRefreshSpec) -> MatviewRefreshResult:
    """This view was eligible but this tick's remaining budget could not safely cover it."""
    return MatviewRefreshResult(
        qualified_name=spec.qualified_name,
        status="skipped_budget",
        used_concurrently=False,
        elapsed_seconds=0.0,
        row_count=None,
        detail="this tick's remaining budget did not cover twice this view's estimated refresh time",
    )


def _remaining_budget_seconds(invocation: JobInvocation, *, handler_started: float, monotonic_now: float) -> float:
    """This call's OWN remaining budget: the frozen snapshot minus wall-clock time this call has spent.

    `invocation.seconds_remaining` is a snapshot from when the call was built and never ticks down on
    its own -- see the module docstring's "WITH A DELIBERATE DEPARTURE" note. `monotonic_now` is
    passed rather than read here so a caller can snapshot it once per loop iteration on the same clock
    `handler_started` was measured against.
    """
    return max(0.0, invocation.seconds_remaining - (monotonic_now - handler_started))


def _cursor_completed_views(cursor: Mapping[str, object] | None) -> tuple[str, ...]:
    """Read a yielded shard's own cursor back: which views this run already finished before it parked."""
    if cursor is None:
        return ()
    raw = cursor.get("completed_views")
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw)


# --- Ledger registration: the shape jobs/AGENTS.md and jobs/strategy_mv_refresh.py both use. ---


@dataclass(frozen=True, slots=True)
class MatviewRefreshLaneContext:
    """The one session a tick opens and this lane's single work item reuses."""

    session: AsyncSession


_LANE_CONTEXT: Final[ContextVar[MatviewRefreshLaneContext | None]] = ContextVar(
    "matview_refresh_lane_context",
    default=None,
)

_UNBOUND_CONTEXT_REASON: Final = (
    "no matview refresh lane context is bound; the slice's session must be bound at the trigger "
    "boundary with matview_refresh_context() before run_job_slice drives a handler"
)


@asynccontextmanager
async def matview_refresh_context(context: MatviewRefreshLaneContext) -> AsyncIterator[None]:
    """Bind one tick's session for the whole slice; released on the way out."""
    token = _LANE_CONTEXT.set(context)
    try:
        yield
    finally:
        _LANE_CONTEXT.reset(token)


def current_matview_refresh_context() -> MatviewRefreshLaneContext:
    """Return the tick's bound session, refusing in typed terms rather than running against nothing."""
    context = _LANE_CONTEXT.get()
    if context is None:
        raise MatviewRefreshContextError(_UNBOUND_CONTEXT_REASON)
    return context


def matview_refresh_definition_spec() -> JobDefinitionSpec:
    """The lane's declared shape. Name/version/handler match what a run's job_run row will record."""
    return JobDefinitionSpec(
        name=MATVIEW_REFRESH_DEFINITION_NAME,
        version=MATVIEW_REFRESH_DEFINITION_VERSION,
        handler=MATVIEW_REFRESH_HANDLER_TOKEN,
        queue_name=MATVIEW_REFRESH_QUEUE_NAME,
        schedule=MATVIEW_REFRESH_SCHEDULE_CRON,
        schedule_timezone=MATVIEW_REFRESH_SCHEDULE_TIMEZONE,
        max_attempts=MATVIEW_REFRESH_MAX_ATTEMPTS,
        lease_seconds=MATVIEW_REFRESH_LEASE_SECONDS,
        time_budget_seconds=MATVIEW_REFRESH_TIME_BUDGET_SECONDS,
        parameters={
            "lane": "matview_refresh",
            "views": [spec.qualified_name for spec in MATVIEW_REFRESH_SPECS],
        },
    )


@job_handler(MATVIEW_REFRESH_HANDLER_TOKEN)
async def matview_refresh_handler(invocation: JobInvocation) -> JobHandlerOutcome:
    """Evaluate every view's watermark, refresh what is eligible in priority order, yield if the clock runs out."""
    handler_started = time.monotonic()
    session = current_matview_refresh_context().session
    completed_already = frozenset(_cursor_completed_views(invocation.cursor))
    prior_by_view = await _read_prior_refresh_state(session)
    now = datetime.now(UTC)
    plans = await _plan_refreshes(session, MATVIEW_REFRESH_SPECS, prior_by_view, now=now)

    results: list[MatviewRefreshResult] = [
        MatviewRefreshResult(
            qualified_name=plan.spec.qualified_name,
            status="skipped_unchanged",
            used_concurrently=False,
            elapsed_seconds=0.0,
            row_count=None,
            detail=plan.reason,
        )
        for plan in plans
        if not plan.eligible
    ]

    eligible = sorted(
        (plan for plan in plans if plan.eligible),
        key=lambda plan: (
            plan.spec.priority,
            plan.prior.refreshed_at if plan.prior is not None and plan.prior.refreshed_at is not None
            else _NEVER_REFRESHED_SORT_KEY,
        ),
    )
    remaining = [plan for plan in eligible if plan.spec.qualified_name not in completed_already]
    completed_now = list(completed_already)
    budget_exhausted = False
    for plan in remaining:
        if budget_exhausted:
            results.append(_budget_skipped_result(plan.spec))
            continue
        estimate = _estimate_seconds(plan.spec, plan.prior)
        remaining_budget_seconds = _remaining_budget_seconds(
            invocation, handler_started=handler_started, monotonic_now=time.monotonic()
        )
        if remaining_budget_seconds < BUDGET_SAFETY_FACTOR * estimate:
            budget_exhausted = True
            results.append(_budget_skipped_result(plan.spec))
            continue
        result = await _refresh_one_matview(session, plan.spec)
        await _write_refresh_state(session, plan.spec, result, current_watermark=plan.current_watermark)
        results.append(result)
        completed_now.append(plan.spec.qualified_name)

    report = MatviewRefreshReport(results=tuple(results))
    metrics = report.to_metrics()
    if budget_exhausted:
        return JobHandlerOutcome.yielded(cursor={"completed_views": completed_now}, metrics=metrics)
    if report.has_failures:
        return JobHandlerOutcome.failed("matview_refresh_failed", report.failure_summary(), metrics=metrics)
    return JobHandlerOutcome.completed(metrics=metrics)


def _default_worker_id() -> str:
    """Label this tick's lease owner, preferring Railway's per-container id over a random one."""
    replica = os.environ.get(_WORKER_ID_ENVIRONMENT_VARIABLE, "").strip()
    return f"matview-refresh:{replica or uuid.uuid4()}"[:_WORKER_ID_MAX_LENGTH]


def _work_item_shard_key(moment: datetime) -> str:
    """One fresh, claimable shard per trigger call, in the ONE persistent run this lane ever opens.

    Unlike `strategy_mv_refresh._run_bucket_key`, which mints a NEW `job_run_id` per polling window,
    this lane never rotates its run -- see the module docstring's departure #1 for why a rotating run
    would strand a shard behind a dead lease. Microsecond resolution makes two calls in the same
    second distinct rather than colliding on `insert_job_work_items.sql`'s own idempotency key.
    """
    return f"matview-refresh:{moment.strftime('%Y%m%dT%H%M%S%f')}Z"


async def trigger_matview_refresh(
    session: AsyncSession,
    *,
    requested_by: str,
    now: datetime | None = None,
) -> JobSliceSummary:
    """Upsert the definition, add one fresh shard to this lane's one persistent run, drive one slice.

    The caller owns the session and its engine's lifetime; this function commits the plan, then binds
    the session for the handler's own commits inside `run_job_slice`.
    """
    definition = await ensure_job_definition(session, matview_refresh_definition_spec())
    moment = now if now is not None else datetime.now(UTC)
    opened = await open_job_run(
        session,
        definition,
        logical_run_key=MATVIEW_REFRESH_RUN_KEY,
        work_items=(
            JobWorkItemSpec(
                shard_key=_work_item_shard_key(moment),
                kind=MATVIEW_REFRESH_WORK_ITEM_KIND,
                payload={},
            ),
        ),
        requested_by=requested_by,
        target_partitions={"lane": "matview_refresh"},
    )
    await session.commit()
    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        return await run_job_slice(
            session,
            definition_name=MATVIEW_REFRESH_DEFINITION_NAME,
            worker_id=_default_worker_id(),
            job_run_id=opened.job_run_id,
        )


# Publishing this lane to `jobs/dispatch.py` is what makes `POST /api/v1/jobs/trigger` able to run it
# by name, and what puts it on jobs-pulse's dispatchable-lane pass the moment this module is imported
# -- see execution/jobs_pulse_command.py and routes/__init__.py, both of which import this module for
# exactly that side effect.
register_dispatchable_lane(
    lane_id=MATVIEW_REFRESH_DEFINITION_NAME,
    handler_token=MATVIEW_REFRESH_HANDLER_TOKEN,
    trigger=trigger_matview_refresh,
    description="Refresh every geo./agri. matview under the pre-aggregation layer's watermark-gated discipline.",
)
