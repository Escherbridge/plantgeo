"""The strategy-recommendation materialized-view refresh, as a durable job on the `agri.job_*` ledger.

`drizzle/0027_ml_strategy_materialized_views.sql` (the root repo's Next.js/Drizzle migration tree,
not this service's Alembic one) defines three geo-schema matviews --
`geo.mv_strategy_recommendations_{coarse,regional,detail}` -- each with a UNIQUE index, and nothing
in this service ever refreshed them. This module is the refresh: one bounded job, registered the
same way `execution/covariate_wind_lane.py` registers its training lane, driven by
`jobs/scheduler.py`'s periodic in-process driver and its manual `/trigger` route.

Registration mirrors the runtime contract `jobs/AGENTS.md` describes: a `JobDefinitionSpec` filled
in once, a `JobHandler` bound to a handler token with `@job_handler` at import time, and a
`trigger_strategy_mv_refresh` entry point that upserts the definition, opens (or rejoins) one
window's run, and drives it through `run_job_slice` -- the same three-step shape
`covariate_wind_lane.plan_covariate_wind_lane` + a slice runner uses, collapsed into one call
because this lane has no multi-shard plan to fan out: one work item refreshes all three views.

GUARDRAIL: this lane touches ONLY the three `geo.mv_strategy_recommendations_*` views named in
`STRATEGY_RECOMMENDATION_VIEWS`. It must never be widened to cover `agri.mv_forecast_ml_daily_serving`
-- that matview has its own reviewed refresher (`cli.py`'s `forecast-refresh-ml-daily`, over
`FORECAST_MV_REFRESH_DATABASE_URL`) and this lane is not a replacement for it.
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
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.jobs.registry import JobInvocation
    from agri_data_service.jobs.worker import JobSliceSummary

logger = structlog.get_logger()

# --- Identity: what this lane is called, in the ledger and in the handler registry. ---
STRATEGY_MV_REFRESH_DEFINITION_NAME: Final = "strategy-mv-refresh"
STRATEGY_MV_REFRESH_DEFINITION_VERSION: Final = "v1"
STRATEGY_MV_REFRESH_HANDLER_TOKEN: Final = "jobs.strategy_mv_refresh"
STRATEGY_MV_REFRESH_QUEUE_NAME: Final = "strategy-mv-refresh"
STRATEGY_MV_REFRESH_WORK_ITEM_KIND: Final = "strategy_mv_refresh_batch"
STRATEGY_MV_REFRESH_RUN_KEY_PREFIX: Final = "strategy-mv-refresh"

# --- Budget: one work item refreshes all three views in one handler call, so the lease only has
# to outlive one slice, not many. 900 > 600 satisfies JobDefinitionSpec's own lease > budget rule.
STRATEGY_MV_REFRESH_MAX_ATTEMPTS: Final = 3
STRATEGY_MV_REFRESH_LEASE_SECONDS: Final = 900
STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS: Final = 600

# --- Cadence: documentary only. Nothing in this runtime parses cron syntax -- `job_definition.schedule`
# is written and never read anywhere in this service (confirmed: it carries no consumer). It is stored
# here so an operator reading `agri.job_definition` sees the intended cadence next to the row, and
# `STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS` is the actual mechanism: `jobs/scheduler.py`'s
# `StrategyMvRefreshScheduler` fires this lane on a plain asyncio interval, not a cron expression.
STRATEGY_MV_REFRESH_SCHEDULE_CRON: Final = "*/15 * * * *"
STRATEGY_MV_REFRESH_SCHEDULE_TIMEZONE: Final = "UTC"
STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS: Final = 900

_WORKER_ID_ENVIRONMENT_VARIABLE: Final = "RAILWAY_REPLICA_ID"
_WORKER_ID_MAX_LENGTH: Final = 255

# The whole guardrail, as data: this lane refreshes exactly these three views, in this order
# (coarse -> regional -> detail), and no others. `_require_known_view` enforces it defensively even
# though nothing today calls `refresh_strategy_recommendation_views` with a different sequence.
STRATEGY_RECOMMENDATION_VIEWS: Final[tuple[str, str, str]] = (
    "geo.mv_strategy_recommendations_coarse",
    "geo.mv_strategy_recommendations_regional",
    "geo.mv_strategy_recommendations_detail",
)

MaterializedViewRefreshStatus = Literal[
    "refreshed_concurrently",
    "refreshed_full",
    "skipped_missing",
    "failed",
]


class UnknownStrategyMaterializedViewError(ValueError):
    """Raised when a caller asks this lane to refresh a view outside its guardrailed list."""


class StrategyMvRefreshContextError(RuntimeError):
    """Raised when the handler runs with no bound session, which would otherwise be a silent no-op."""


@dataclass(frozen=True, slots=True)
class MaterializedViewRefreshResult:
    """What happened to one view: how it landed, whether CONCURRENTLY was used, and what it cost."""

    qualified_name: str
    status: MaterializedViewRefreshStatus
    used_concurrently: bool
    elapsed_seconds: float
    row_count: int | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class StrategyMvRefreshReport:
    """One tick's full result: all three views, in the order they were attempted."""

    results: tuple[MaterializedViewRefreshResult, ...]

    @property
    def has_failures(self) -> bool:
        """True on a real REFRESH failure, or when every guardrailed view was skipped.

        An all-skipped tick refreshed nothing -- if `drizzle/0027` were applied against this
        database the run would have found and refreshed at least one view, so three-for-three
        `skipped_missing` is a degraded outcome (a database missing the whole view family), not a
        quiet success. Reporting it as a JobHandlerOutcome.failed makes the ledger show a run that
        did no work rather than one that looks identical to a healthy refresh.
        """
        if any(result.status == "failed" for result in self.results):
            return True
        return bool(self.results) and all(result.status == "skipped_missing" for result in self.results)

    def failure_summary(self) -> str:
        """Name every view that failed, or -- on an all-skipped run -- every view that was missing."""
        failed = [result.qualified_name for result in self.results if result.status == "failed"]
        if failed:
            return f"materialized view refresh failed for: {', '.join(failed)}"
        skipped = [result.qualified_name for result in self.results if result.status == "skipped_missing"]
        return (
            "materialized view refresh found none of its guardrailed views (drizzle 0027 not "
            f"applied against this database?): {', '.join(skipped)}"
        )

    def to_metrics(self) -> dict[str, object]:
        """Render this report as the JSONB `job_attempt.metrics` shape."""
        return {
            "views": [
                {
                    "view": result.qualified_name,
                    "status": result.status,
                    "used_concurrently": result.used_concurrently,
                    "elapsed_seconds": round(result.elapsed_seconds, 3),
                    "row_count": result.row_count,
                }
                for result in self.results
            ],
        }


def _require_known_view(qualified_name: str) -> None:
    """Refuse a view outside the guardrailed set before any SQL naming it is ever built."""
    if qualified_name not in STRATEGY_RECOMMENDATION_VIEWS:
        raise UnknownStrategyMaterializedViewError(
            f"{qualified_name!r} is not one of the guardrailed strategy recommendation views"
        )


_VIEW_EXISTS_SQL: Final = text("SELECT to_regclass(:qualified_name) IS NOT NULL AS view_exists")

_SELECT_UNIQUE_INDEX: Final = text(load_query_sql("jobs/select_materialized_view_unique_index.sql"))


async def _view_exists(session: AsyncSession, qualified_name: str) -> bool:
    """True when `qualified_name` resolves to a real relation right now."""
    result = await session.execute(_VIEW_EXISTS_SQL, {"qualified_name": qualified_name})
    row = result.mappings().first()
    return bool(row is not None and row["view_exists"])


async def _has_refreshable_unique_index(session: AsyncSession, schema_name: str, view_name: str) -> bool:
    """True when the view carries the unique, non-partial index CONCURRENTLY requires."""
    result = await session.execute(
        _SELECT_UNIQUE_INDEX,
        {"schema_name": schema_name, "view_name": view_name},
    )
    row = result.mappings().first()
    return bool(row is not None and row["has_unique_index"])


def _refresh_statement(qualified_name: str, *, concurrently: bool) -> TextClause:
    """Build the REFRESH statement text. The view name cannot be a bind parameter -- identifiers
    never can be -- so this is only safe because every caller has already passed the name through
    `_require_known_view`, which refuses anything outside the guardrailed, code-defined tuple.
    """
    keyword = "CONCURRENTLY " if concurrently else ""
    return text(f"REFRESH MATERIALIZED VIEW {keyword}{qualified_name}")


def _row_count_statement(qualified_name: str) -> TextClause:
    """Build the post-refresh row-count check, subject to the same identifier-safety note above."""
    return text(f"SELECT count(*) AS row_count FROM {qualified_name}")


async def _row_count(session: AsyncSession, qualified_name: str) -> int:
    """The view's current row count, read right after its own refresh commits."""
    result = await session.execute(_row_count_statement(qualified_name))
    row = result.mappings().first()
    return 0 if row is None else int(row["row_count"])


async def _refresh_one_view(session: AsyncSession, qualified_name: str) -> MaterializedViewRefreshResult:
    """Refresh one guardrailed view: skip if absent, fall back if unrefreshable concurrently, else refresh."""
    _require_known_view(qualified_name)
    schema_name, _, view_name = qualified_name.partition(".")

    if not await _view_exists(session, qualified_name):
        # Graceful skip, not a crash: drizzle/0027 is a separate migration tree from this service's
        # Alembic one and may not have run against every database this job is deployed alongside.
        logger.warning("strategy_mv_refresh_view_missing", view=qualified_name)
        return MaterializedViewRefreshResult(
            qualified_name=qualified_name,
            status="skipped_missing",
            used_concurrently=False,
            elapsed_seconds=0.0,
            row_count=None,
            detail="relation does not exist (drizzle 0027 not applied against this database)",
        )

    use_concurrently = await _has_refreshable_unique_index(session, schema_name, view_name)
    if not use_concurrently:
        # Logged loudly, per the task contract: silently downgrading to a plain REFRESH would hide
        # that readers are now locked out of the view for the whole rebuild.
        logger.warning(
            "strategy_mv_refresh_missing_unique_index_falls_back_to_full_refresh",
            view=qualified_name,
            reason="no unique, non-partial index found; REFRESH CONCURRENTLY is not legal here",
        )

    started = time.monotonic()
    try:
        await session.execute(_refresh_statement(qualified_name, concurrently=use_concurrently))
    except SQLAlchemyError as error:
        await session.rollback()
        elapsed = time.monotonic() - started
        logger.error(
            "strategy_mv_refresh_failed",
            view=qualified_name,
            used_concurrently=use_concurrently,
            elapsed_seconds=round(elapsed, 3),
            error=type(error).__name__,
        )
        return MaterializedViewRefreshResult(
            qualified_name=qualified_name,
            status="failed",
            used_concurrently=use_concurrently,
            elapsed_seconds=elapsed,
            row_count=None,
            detail=type(error).__name__,
        )

    elapsed = time.monotonic() - started
    row_count = await _row_count(session, qualified_name)
    await session.commit()
    logger.info(
        "strategy_mv_refresh_view_refreshed",
        view=qualified_name,
        used_concurrently=use_concurrently,
        elapsed_seconds=round(elapsed, 3),
        row_count=row_count,
    )
    return MaterializedViewRefreshResult(
        qualified_name=qualified_name,
        status="refreshed_concurrently" if use_concurrently else "refreshed_full",
        used_concurrently=use_concurrently,
        elapsed_seconds=elapsed,
        row_count=row_count,
    )


async def refresh_strategy_recommendation_views(
    session: AsyncSession,
    *,
    views: Sequence[str] = STRATEGY_RECOMMENDATION_VIEWS,
) -> StrategyMvRefreshReport:
    """Refresh every guardrailed view in order, one view's outcome never blocking the next one's attempt."""
    results = [await _refresh_one_view(session, qualified_name) for qualified_name in views]
    return StrategyMvRefreshReport(results=tuple(results))


# --- Ledger registration: the shape jobs/AGENTS.md and execution/covariate_wind_lane.py both use. ---


@dataclass(frozen=True, slots=True)
class StrategyMvRefreshLaneContext:
    """The one session a tick opens and this lane's single work item reuses."""

    session: AsyncSession


_LANE_CONTEXT: Final[ContextVar[StrategyMvRefreshLaneContext | None]] = ContextVar(
    "strategy_mv_refresh_lane_context",
    default=None,
)

_UNBOUND_CONTEXT_REASON: Final = (
    "no strategy mv refresh lane context is bound; the slice's session must be bound at the "
    "trigger boundary with strategy_mv_refresh_context() before run_job_slice drives a handler"
)


@asynccontextmanager
async def strategy_mv_refresh_context(context: StrategyMvRefreshLaneContext) -> AsyncIterator[None]:
    """Bind one tick's session for the whole slice; released on the way out."""
    token = _LANE_CONTEXT.set(context)
    try:
        yield
    finally:
        _LANE_CONTEXT.reset(token)


def current_strategy_mv_refresh_context() -> StrategyMvRefreshLaneContext:
    """Return the tick's bound session, refusing in typed terms rather than writing nothing."""
    context = _LANE_CONTEXT.get()
    if context is None:
        raise StrategyMvRefreshContextError(_UNBOUND_CONTEXT_REASON)
    return context


def strategy_mv_refresh_definition_spec() -> JobDefinitionSpec:
    """The lane's declared shape. Name/version/handler match what a run's job_run row will record."""
    return JobDefinitionSpec(
        name=STRATEGY_MV_REFRESH_DEFINITION_NAME,
        version=STRATEGY_MV_REFRESH_DEFINITION_VERSION,
        handler=STRATEGY_MV_REFRESH_HANDLER_TOKEN,
        queue_name=STRATEGY_MV_REFRESH_QUEUE_NAME,
        schedule=STRATEGY_MV_REFRESH_SCHEDULE_CRON,
        schedule_timezone=STRATEGY_MV_REFRESH_SCHEDULE_TIMEZONE,
        max_attempts=STRATEGY_MV_REFRESH_MAX_ATTEMPTS,
        lease_seconds=STRATEGY_MV_REFRESH_LEASE_SECONDS,
        time_budget_seconds=STRATEGY_MV_REFRESH_TIME_BUDGET_SECONDS,
        parameters={
            "lane": "strategy_mv_refresh",
            "views": list(STRATEGY_RECOMMENDATION_VIEWS),
        },
    )


@job_handler(STRATEGY_MV_REFRESH_HANDLER_TOKEN)
async def strategy_mv_refresh_handler(invocation: JobInvocation) -> JobHandlerOutcome:
    """Refresh all three guardrailed views in one bounded step; this lane fans out no further shards."""
    del invocation  # no shard-specific payload -- one work item drives the whole batch
    session = current_strategy_mv_refresh_context().session
    report = await refresh_strategy_recommendation_views(session)
    metrics = report.to_metrics()
    if report.has_failures:
        return JobHandlerOutcome.failed("strategy_mv_refresh_failed", report.failure_summary(), metrics=metrics)
    return JobHandlerOutcome.completed(metrics=metrics)


def _default_worker_id() -> str:
    """Label this tick's lease owner, preferring Railway's per-container id over a random one."""
    replica = os.environ.get(_WORKER_ID_ENVIRONMENT_VARIABLE, "").strip()
    return f"strategy-mv-refresh:{replica or uuid.uuid4()}"[:_WORKER_ID_MAX_LENGTH]


def _run_bucket_key(moment: datetime) -> str:
    """Round `moment` down to the poll interval, so two triggers inside one window share a run.

    A periodic tick and an operator's manual POST landing seconds apart must not refresh the same
    three views twice; bucketing the run key is what makes `open_job_run`'s
    `ON CONFLICT (logical_run_key) DO NOTHING` collapse them into one run.
    """
    epoch_seconds = int(moment.timestamp())
    bucket_start = epoch_seconds - (epoch_seconds % STRATEGY_MV_REFRESH_POLL_INTERVAL_SECONDS)
    return datetime.fromtimestamp(bucket_start, tz=UTC).strftime("%Y%m%dT%H%M%SZ")


async def trigger_strategy_mv_refresh(
    session: AsyncSession,
    *,
    requested_by: str,
    now: datetime | None = None,
) -> JobSliceSummary:
    """Upsert the definition, open (or rejoin) this window's run, and drive one bounded slice through it.

    The caller owns the session and its engine's lifetime; this function commits the plan, then binds
    the session for the handler's own commits inside `run_job_slice`.
    """
    definition = await ensure_job_definition(session, strategy_mv_refresh_definition_spec())
    moment = now if now is not None else datetime.now(UTC)
    bucket = _run_bucket_key(moment)
    run_key = f"{STRATEGY_MV_REFRESH_RUN_KEY_PREFIX}:{bucket}"
    opened = await open_job_run(
        session,
        definition,
        logical_run_key=run_key,
        work_items=(
            JobWorkItemSpec(
                shard_key=run_key,
                kind=STRATEGY_MV_REFRESH_WORK_ITEM_KIND,
                payload={},
            ),
        ),
        requested_by=requested_by,
        target_partitions={"lane": "strategy_mv_refresh", "window": bucket},
    )
    await session.commit()
    async with strategy_mv_refresh_context(StrategyMvRefreshLaneContext(session=session)):
        return await run_job_slice(
            session,
            definition_name=STRATEGY_MV_REFRESH_DEFINITION_NAME,
            worker_id=_default_worker_id(),
            job_run_id=opened.job_run_id,
        )


# Publishing this lane to `jobs/dispatch.py` is what makes `POST /api/v1/jobs/trigger` able to run
# it by name -- the route holds no branch for this lane, or for any other. Registration sits here,
# beside the `@job_handler` above, because both bind the same fact at import time: this module is
# what knows how to bind this lane's context before its handler is ever driven.
register_dispatchable_lane(
    lane_id=STRATEGY_MV_REFRESH_DEFINITION_NAME,
    handler_token=STRATEGY_MV_REFRESH_HANDLER_TOKEN,
    trigger=trigger_strategy_mv_refresh,
    description="Refresh the three geo.mv_strategy_recommendations_* materialized views.",
)
