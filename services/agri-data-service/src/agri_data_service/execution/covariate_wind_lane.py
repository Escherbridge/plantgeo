"""The covariate wind training run as a durable job lane: one work item is one (cell, origin batch).

Why this lane exists, its work-item grain, and what a dead-lettered batch means live in
`AGENTS.md` (this directory) under `covariate_wind_model.py`. The runtime contract it fills in
is `jobs/AGENTS.md`.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.execution.covariate_wind_model import (
    DEFAULT_CALIBRATION_DAYS,
    DEFAULT_HORIZON_COUNT,
    DEFAULT_RIDGE_ALPHA,
    SCHEMA_VERSION,
    OriginNotEvaluableError,
)
from agri_data_service.execution.covariate_wind_persist import (
    EVALUATION_LABEL,
    TRAINING_DEFINITION_NAME,
    TRAINING_DEFINITION_VERSION,
    TRAINING_HANDLER_TOKEN,
    TRAINING_LEASE_SECONDS,
    TRAINING_MAX_ATTEMPTS,
    TRAINING_QUEUE_NAME,
    TRAINING_TIME_BUDGET_SECONDS,
    ForecastTrainingPersistError,
    WindTrainingRequest,
    run_covariate_wind_training,
)
from agri_data_service.jobs import (
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobWorkItemSpec,
    ensure_job_definition,
    job_handler,
    open_job_run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.jobs import JobInvocation, OpenedJobRun

# Operational telemetry is bound to stderr by the CLI; this module logs through the process
# logger so a cron tick's JSON-lines stdout stays parseable.
logger = structlog.get_logger()

COVARIATE_WIND_WORK_ITEM_KIND: Final = "covariate_wind_training_batch"
COVARIATE_WIND_RUN_KEY_PREFIX: Final = "covariate-wind-train"

# What one batch is estimated to cost before anything has measured it. A batch fits one ridge
# per (origin, horizon) over a few thousand rows, which is seconds of numpy on top of two bounded
# reads -- but the first call has no measurement to reason from, so it starts pessimistic and the
# cursor carries the real figure forward.
DEFAULT_BATCH_ESTIMATE_SECONDS: Final = 180

CURSOR_MEASURED_SECONDS: Final = "measured_seconds"

BUDGET_STOP_REASON: Final = "covariate wind batch does not fit this tick"
FENCE_LOST_FAILURE_CLASS: Final = "fence_lost"
NOT_EVALUABLE_FAILURE_CLASS: Final = "covariate_batch_not_evaluable"
GOVERNANCE_FAILURE_CLASS: Final = "forecast_training_governance"
PAYLOAD_FAILURE_CLASS: Final = "covariate_batch_payload"


class CovariateWindPayloadError(ValueError):
    """Raised when a stored work-item payload cannot be read as a training batch."""


class CovariateWindContextError(RuntimeError):
    """Raised when a handler runs with no bound session, which would otherwise be a silent no-op."""


@dataclass(frozen=True, slots=True)
class CovariateWindLaneContext:
    """The one session a tick opens and every shard of that tick reuses."""

    session: AsyncSession


_LANE_CONTEXT: Final[ContextVar[CovariateWindLaneContext | None]] = ContextVar(
    "covariate_wind_lane_context",
    default=None,
)

_UNBOUND_CONTEXT_REASON: Final = (
    "no covariate wind lane context is bound; the slice's session must be bound at the process "
    "boundary with covariate_wind_lane_context() before run_job_slice drives a handler"
)


@asynccontextmanager
async def covariate_wind_lane_context(context: CovariateWindLaneContext) -> AsyncIterator[None]:
    """Bind one tick's session for the whole slice, so a handler never opens a session per shard.

    The session factories build a NEW engine and connection per `async with` and dispose it in
    their `finally` (db/engine.py), so binding per work item would be a full TCP+TLS+auth
    handshake per batch. One binding per tick, released on the way out -- the same arrangement
    `ingest/archive_walk.py` uses, and for the same reason.
    """
    token = _LANE_CONTEXT.set(context)
    try:
        yield
    finally:
        _LANE_CONTEXT.reset(token)


def current_covariate_wind_context() -> CovariateWindLaneContext:
    """Return the tick's bound session, refusing in typed terms rather than writing nothing."""
    context = _LANE_CONTEXT.get()
    if context is None:
        raise CovariateWindContextError(_UNBOUND_CONTEXT_REASON)
    return context


def _required_text(payload: Mapping[str, object], key: str) -> str:
    """Read one required string out of an untrusted JSONB payload, refusing anything else."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CovariateWindPayloadError(f"work item payload needs a non-empty {key}")
    return value.strip()


def _required_day(payload: Mapping[str, object], key: str) -> date:
    """Read one required ISO calendar day out of an untrusted JSONB payload."""
    try:
        return date.fromisoformat(_required_text(payload, key))
    except ValueError as error:
        raise CovariateWindPayloadError(f"work item payload {key} is not a YYYY-MM-DD day") from error


def _required_instant(payload: Mapping[str, object], key: str) -> datetime:
    """Read one required timezone-aware instant, refusing a naive value the gate would misread."""
    try:
        parsed = datetime.fromisoformat(_required_text(payload, key))
    except ValueError as error:
        raise CovariateWindPayloadError(f"work item payload {key} is not an ISO-8601 instant") from error
    if parsed.tzinfo is None:
        raise CovariateWindPayloadError(f"work item payload {key} must carry a timezone")
    return parsed


def _optional_int(payload: Mapping[str, object], key: str, fallback: int) -> int:
    """Read one optional integer, refusing a bool or a non-number rather than coercing it."""
    value = payload.get(key)
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int):
        raise CovariateWindPayloadError(f"work item payload {key} must be an integer")
    return value


def _optional_float(payload: Mapping[str, object], key: str, fallback: float) -> float:
    """Read one optional number, refusing a bool or a non-number rather than coercing it."""
    value = payload.get(key)
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CovariateWindPayloadError(f"work item payload {key} must be a number")
    return float(value)


@dataclass(frozen=True, slots=True)
class WindTrainingTarget:
    """One entity this lane trains for: its covariate cell and the forecast series it scores against."""

    cell_id: str
    series_id: str


@dataclass(frozen=True, slots=True)
class CovariateWindLanePlan:
    """The declared shape of one planned lane: which entities, which origin batches, which knobs.

    `as_of_time` is PINNED in the plan rather than taken from each tick's clock. Two things turn
    on that: the availability gate must not move under a run in flight, and every identity key a
    batch derives (training key, snapshot key, run key) folds the as-of instant in through the
    parameter checksum -- so an unpinned instant would make a re-claimed shard write a second
    receipt instead of resolving the first.
    """

    targets: tuple[WindTrainingTarget, ...]
    history_start: date
    history_end: date
    newest_origin: date
    as_of_time: datetime
    quality_policy_key: str
    batch_count: int = 1
    origins_per_batch: int = 4
    origin_stride_days: int | None = None
    horizon_count: int = DEFAULT_HORIZON_COUNT
    calibration_days: int = DEFAULT_CALIBRATION_DAYS
    alpha: float = DEFAULT_RIDGE_ALPHA
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Refuse a plan that could not fan out into a claimable shard, before it reaches the ledger."""
        if not self.targets:
            raise ValueError("a covariate wind lane plan needs at least one target cell")
        if self.history_start >= self.history_end:
            raise ValueError("history_start must precede history_end")
        if self.batch_count < 1:
            raise ValueError("batch_count must be at least one batch")
        if self.origins_per_batch < 1:
            raise ValueError("origins_per_batch must be at least one origin")
        if self.as_of_time.utcoffset() is None:
            raise ValueError("as_of_time must include a timezone")

    @property
    def effective_stride_days(self) -> int:
        """Days between rolling origins; defaults to the horizon count so target spans do not overlap."""
        return self.origin_stride_days if self.origin_stride_days is not None else self.horizon_count

    @property
    def batch_span_days(self) -> int:
        """Calendar days one batch's origins span, which is also the gap between two batches."""
        return self.origins_per_batch * self.effective_stride_days

    def batch_origins(self) -> tuple[date, ...]:
        """The newest origin of each batch, newest first, walking backwards from the frontier.

        Newest first is deliberate: the ledger claims by priority then insertion, and the batch
        nearest the frontier is the one whose evidence is worth having first.
        """
        return tuple(
            self.newest_origin - timedelta(days=self.batch_span_days * step) for step in range(self.batch_count)
        )

    @property
    def logical_run_key(self) -> str:
        """Stable run identity: replanning the same declared shape re-opens the same run."""
        return (
            f"{COVARIATE_WIND_RUN_KEY_PREFIX}:{self.newest_origin.isoformat()}"
            f":{self.origins_per_batch}x{self.effective_stride_days}:{self.batch_count}"
            f":{self.as_of_time.astimezone(UTC).date().isoformat()}"
        )

    def work_items(self) -> tuple[JobWorkItemSpec, ...]:
        """One shard per (target cell, origin batch) -- the grain this lane claims, retries and reports on."""
        items: list[JobWorkItemSpec] = []
        for position, batch_origin in enumerate(self.batch_origins()):
            items.extend(
                JobWorkItemSpec(
                    shard_key=f"{target.cell_id}:{batch_origin.isoformat()}",
                    kind=COVARIATE_WIND_WORK_ITEM_KIND,
                    payload={
                        "cell_id": target.cell_id,
                        "series_id": target.series_id,
                        "history_start": self.history_start.isoformat(),
                        "history_end": self.history_end.isoformat(),
                        "origin_date": batch_origin.isoformat(),
                        "as_of_time": self.as_of_time.astimezone(UTC).isoformat(),
                        "origin_count": self.origins_per_batch,
                        "origin_stride_days": self.effective_stride_days,
                        "horizon_count": self.horizon_count,
                        "calibration_days": self.calibration_days,
                        "alpha": self.alpha,
                        "schema_version": self.schema_version,
                        "quality_policy_key": self.quality_policy_key,
                    },
                    # Nearest the frontier claims first; `position` grows as batches walk back.
                    priority=self.batch_count - position,
                )
                for target in self.targets
            )
        return tuple(items)


def covariate_wind_definition_spec() -> JobDefinitionSpec:
    """The lane's declared shape. Its name/version/handler match what a receipt's job run records."""
    return JobDefinitionSpec(
        name=TRAINING_DEFINITION_NAME,
        version=TRAINING_DEFINITION_VERSION,
        handler=TRAINING_HANDLER_TOKEN,
        queue_name=TRAINING_QUEUE_NAME,
        max_attempts=TRAINING_MAX_ATTEMPTS,
        lease_seconds=TRAINING_LEASE_SECONDS,
        time_budget_seconds=TRAINING_TIME_BUDGET_SECONDS,
        parameters={
            "lane": "covariate_wind_train",
            "label": EVALUATION_LABEL,
            "publication_authorized": False,
            "schema_version": SCHEMA_VERSION,
        },
    )


async def plan_covariate_wind_lane(
    session: AsyncSession,
    plan: CovariateWindLanePlan,
    *,
    requested_by: str | None = None,
) -> OpenedJobRun:
    """Declare the lane and fan its (cell, origin batch) shards out, idempotently. The caller commits.

    Idempotent twice over, exactly as the archive lane is: `open_job_run` inserts the run
    `ON CONFLICT (logical_run_key) DO NOTHING` and each shard `ON CONFLICT (job_run_id,
    shard_key) DO NOTHING`, and the shard keys are derived from the plan's own pinned grid, so
    replanning the same declared shape adds nothing and re-keys nothing.
    """
    definition = await ensure_job_definition(session, covariate_wind_definition_spec())
    return await open_job_run(
        session,
        definition,
        logical_run_key=plan.logical_run_key,
        work_items=plan.work_items(),
        requested_by=requested_by,
        target_partitions={
            "lane": "covariate_wind_train",
            "newest_origin": plan.newest_origin.isoformat(),
            "batch_count": plan.batch_count,
            "origins_per_batch": plan.origins_per_batch,
            "target_count": len(plan.targets),
        },
    )


def training_request_from_payload(payload: Mapping[str, object]) -> WindTrainingRequest:
    """Validate one stored work-item payload into the pinned request its batch runs under."""
    return WindTrainingRequest(
        cell_id=_required_text(payload, "cell_id"),
        series_id=_required_text(payload, "series_id"),
        history_start=_required_day(payload, "history_start"),
        history_end=_required_day(payload, "history_end"),
        origin_date=_required_day(payload, "origin_date"),
        as_of_time=_required_instant(payload, "as_of_time"),
        horizon_count=_optional_int(payload, "horizon_count", DEFAULT_HORIZON_COUNT),
        calibration_days=_optional_int(payload, "calibration_days", DEFAULT_CALIBRATION_DAYS),
        origin_count=_optional_int(payload, "origin_count", 1),
        origin_stride_days=_optional_int(payload, "origin_stride_days", DEFAULT_HORIZON_COUNT),
        alpha=_optional_float(payload, "alpha", DEFAULT_RIDGE_ALPHA),
        schema_version=_required_text(payload, "schema_version"),
        quality_policy_key=_required_text(payload, "quality_policy_key"),
        requested_by=f"agri-service ops jobs-run {TRAINING_DEFINITION_NAME}",
    )


def batch_estimate_seconds(cursor: Mapping[str, object] | None) -> int:
    """How long the next batch is expected to take, preferring what a previous attempt measured."""
    if cursor is None:
        return DEFAULT_BATCH_ESTIMATE_SECONDS
    measured = cursor.get(CURSOR_MEASURED_SECONDS)
    if isinstance(measured, bool) or not isinstance(measured, int | float):
        return DEFAULT_BATCH_ESTIMATE_SECONDS
    return max(int(measured), 1)


@job_handler(TRAINING_HANDLER_TOKEN)
async def covariate_wind_training_handler(invocation: JobInvocation) -> JobHandlerOutcome:
    """Train and score ONE (cell, origin batch), writing its receipt chain, or say why it could not.

    A batch is indivisible: the fit, the rolling-origin scoring and the receipt are one
    transaction, so the only budget decision available is whether to START it. That is what
    `has_budget_for` is asked before anything is read, mirroring `archive_walk_handler`.

    A batch that cannot be evaluated FAILS rather than completing. Completing it would make a
    shard that produced no evidence indistinguishable from one that produced a receipt, which is
    the exact silence `jobs/AGENTS.md` says the ledger exists to prevent.
    """
    try:
        request = training_request_from_payload(invocation.payload)
    except CovariateWindPayloadError as error:
        # A deployment fault, not a transient one: retrying an unreadable payload cannot fix it,
        # so it burns the attempt budget and dead-letters into a report that says it is missing.
        return JobHandlerOutcome.failed(PAYLOAD_FAILURE_CLASS, str(error))

    estimated_seconds = batch_estimate_seconds(invocation.cursor)
    if not invocation.has_budget_for(estimated_seconds):
        # Declined BEFORE the heartbeat and before any read. The runtime tests its deadline
        # before calling a handler and a handler call cannot be interrupted, so the decision to
        # START is the only place a tick's budget can be honoured for an indivisible unit.
        return JobHandlerOutcome.yielded(
            reason=(
                f"{BUDGET_STOP_REASON}: {estimated_seconds}s estimated against "
                f"{invocation.seconds_remaining:.0f}s left of this tick"
            ),
            progress_fraction=invocation.progress_fraction,
            cursor={CURSOR_MEASURED_SECONDS: estimated_seconds},
            metrics={"cell_id": request.cell_id, "origin_date": request.origin_date.isoformat()},
        )
    if not await invocation.heartbeat():
        # The runtime abandons a shard whose fence moved whatever we return; returning now is
        # what stops a superseded worker writing a receipt under a lease it no longer holds.
        return JobHandlerOutcome.failed(FENCE_LOST_FAILURE_CLASS, "another worker owns this batch")

    started_at = time.monotonic()
    session = current_covariate_wind_context().session
    try:
        report = await run_covariate_wind_training(session, request, persist=True)
    except OriginNotEvaluableError as error:
        return JobHandlerOutcome.failed(NOT_EVALUABLE_FAILURE_CLASS, str(error))
    except ForecastTrainingPersistError as error:
        return JobHandlerOutcome.failed(GOVERNANCE_FAILURE_CLASS, str(error))
    measured_seconds = max(int(time.monotonic() - started_at), 1)

    receipt = report.receipt
    logger.info(
        "covariate_wind_batch_trained",
        cell_id=request.cell_id,
        origin_date=request.origin_date.isoformat(),
        origin_count=len(report.backtest.origins),
        training_run_id=None if receipt is None else str(receipt.training_run_id),
    )
    return JobHandlerOutcome.completed(
        cursor={CURSOR_MEASURED_SECONDS: measured_seconds},
        metrics={
            "cell_id": request.cell_id,
            "origin_date": request.origin_date.isoformat(),
            "scored_origin_count": len(report.backtest.origins),
            "skipped_origin_count": len(report.backtest.skipped),
            "usable_day_count": report.coverage.usable_day_count,
            "excluded_day_count": report.coverage.excluded_day_count,
            "mean_absolute_error": report.backtest.aggregate.mean_absolute_error,
            "root_mean_squared_error": report.backtest.aggregate.root_mean_squared_error,
            "training_run_id": None if receipt is None else str(receipt.training_run_id),
            "backtest_metric_count": 0 if receipt is None else receipt.backtest_metric_count,
            "measured_seconds": measured_seconds,
        },
    )


def covariate_wind_targets(pairs: Sequence[tuple[str, str]]) -> tuple[WindTrainingTarget, ...]:
    """Build the plan's targets from `(cell_id, series_id)` pairs, refusing a duplicate cell."""
    seen: set[str] = set()
    targets: list[WindTrainingTarget] = []
    for cell_id, series_id in pairs:
        if cell_id in seen:
            raise ValueError(f"cell {cell_id} appears twice; one cell has one series in this lane")
        seen.add(cell_id)
        targets.append(WindTrainingTarget(cell_id=cell_id, series_id=series_id))
    return tuple(targets)
