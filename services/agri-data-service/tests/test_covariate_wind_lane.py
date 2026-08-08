"""Database-free proofs for the covariate wind durable lane: work-item grain and handler contract.

No database. The runtime seam is `JobInvocation`, built by hand, and the training call is stubbed
so these tests pin the handler's DECISIONS -- budget, fence, failure classification -- rather than
re-testing the estimator underneath it.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.execution import covariate_wind_lane
from agri_data_service.execution.covariate_wind_lane import (
    COVARIATE_WIND_WORK_ITEM_KIND,
    DEFAULT_BATCH_ESTIMATE_SECONDS,
    FENCE_LOST_FAILURE_CLASS,
    GOVERNANCE_FAILURE_CLASS,
    NOT_EVALUABLE_FAILURE_CLASS,
    PAYLOAD_FAILURE_CLASS,
    CovariateWindContextError,
    CovariateWindLaneContext,
    CovariateWindLanePlan,
    CovariateWindPayloadError,
    batch_estimate_seconds,
    covariate_wind_definition_spec,
    covariate_wind_lane_context,
    covariate_wind_targets,
    covariate_wind_training_handler,
    training_request_from_payload,
)
from agri_data_service.execution.covariate_wind_model import OriginNotEvaluableError
from agri_data_service.execution.covariate_wind_persist import (
    TRAINING_DEFINITION_NAME,
    TRAINING_HANDLER_TOKEN,
    ForecastTrainingPersistError,
    TrainingReceipt,
)
from agri_data_service.jobs import JobInvocation
from agri_data_service.jobs.registry import JOB_HANDLERS

# `build_report` is imported rather than re-built: these tests assert on the metrics a REAL report
# produces, and a second hand-shaped stand-in would drift away from the first the day either moves.
from tests.test_covariate_wind_persist import build_report

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

CELL_A = "11111111-1111-4111-8111-111111111111"
CELL_B = "1111111a-1111-4111-8111-111111111111"
SERIES_A = "22222222-2222-4222-8222-222222222222"
SERIES_B = "2222222a-2222-4222-8222-222222222222"
QUALITY_POLICY_KEY = "reviewed-eval-policy"
AS_OF_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
NEWEST_ORIGIN = date(2026, 7, 1)
HISTORY_START = date(2024, 1, 1)
HISTORY_END = date(2026, 7, 1)

PLAN_BATCH_COUNT = 3
PLAN_ORIGINS_PER_BATCH = 4
PLAN_STRIDE_DAYS = 5
PLAN_TARGET_COUNT = 2
EXPECTED_WORK_ITEM_COUNT = PLAN_BATCH_COUNT * PLAN_TARGET_COUNT
MEASURED_SECONDS = 42
GENEROUS_BUDGET_SECONDS = 900.0
TIGHT_BUDGET_SECONDS = 5.0


def build_plan(**overrides: object) -> CovariateWindLanePlan:
    """A two-cell, three-batch plan; overrides let one test change exactly one thing."""
    fields: dict[str, object] = {
        "targets": covariate_wind_targets([(CELL_A, SERIES_A), (CELL_B, SERIES_B)]),
        "history_start": HISTORY_START,
        "history_end": HISTORY_END,
        "newest_origin": NEWEST_ORIGIN,
        "as_of_time": AS_OF_TIME,
        "quality_policy_key": QUALITY_POLICY_KEY,
        "batch_count": PLAN_BATCH_COUNT,
        "origins_per_batch": PLAN_ORIGINS_PER_BATCH,
        "origin_stride_days": PLAN_STRIDE_DAYS,
    }
    fields.update(overrides)
    return CovariateWindLanePlan(**fields)  # type: ignore[arg-type] - a keyword bag by design


class UnusedSession:
    """A session the handler is handed but never reaches: the training call is stubbed in these tests."""

    async def execute(self, statement: object, _parameters: object = None) -> object:
        """Refuse loudly, because a test that reached here would be testing something else."""
        raise AssertionError(f"the stubbed training call should have run instead of {statement!r}")


def bound_context() -> CovariateWindLaneContext:
    """Bind the never-touched session, so the handler passes its context check and stops there."""
    return CovariateWindLaneContext(session=cast("AsyncSession", UnusedSession()))


class StubHeartbeat:
    """A heartbeat that answers a scripted verdict and records how often it was asked."""

    def __init__(self, *, holds_fence: bool = True) -> None:
        self.holds_fence = holds_fence
        self.calls = 0

    async def __call__(self) -> bool:
        """Answer whether this worker still owns the shard."""
        self.calls += 1
        return self.holds_fence


def build_invocation(
    *,
    payload: Mapping[str, object] | None = None,
    cursor: Mapping[str, object] | None = None,
    seconds_remaining: float = GENEROUS_BUDGET_SECONDS,
    heartbeat: StubHeartbeat | None = None,
) -> JobInvocation:
    """One claimed shard, exactly as the runtime would hand it to the handler."""
    return JobInvocation(
        shard_key=f"{CELL_A}:{NEWEST_ORIGIN.isoformat()}",
        kind=COVARIATE_WIND_WORK_ITEM_KIND,
        payload=payload if payload is not None else build_plan().work_items()[0].payload,
        cursor=cursor,
        parameters={},
        attempt_number=1,
        max_attempts=3,
        progress_fraction=0.0,
        seconds_remaining=seconds_remaining,
        heartbeat=heartbeat or StubHeartbeat(),
    )


def test_the_handler_is_registered_under_the_token_a_stored_definition_names() -> None:
    """A definition whose handler token resolves to nothing claims work and then cannot run it."""
    assert TRAINING_HANDLER_TOKEN in JOB_HANDLERS
    assert JOB_HANDLERS.handler_for(TRAINING_HANDLER_TOKEN) is covariate_wind_training_handler
    # The lane's declared definition and the receipt writer's must name the same handler.
    assert covariate_wind_definition_spec().handler == TRAINING_HANDLER_TOKEN
    assert covariate_wind_definition_spec().name == TRAINING_DEFINITION_NAME


def test_the_definition_leaves_the_lease_longer_than_one_slice() -> None:
    """A lease shorter than a slice is a self-inflicted fence loss on every tick."""
    spec = covariate_wind_definition_spec()

    assert spec.lease_seconds > spec.time_budget_seconds


def test_the_plan_fans_one_work_item_per_cell_and_origin_batch() -> None:
    plan = build_plan()

    items = plan.work_items()

    assert len(items) == EXPECTED_WORK_ITEM_COUNT
    assert {item.kind for item in items} == {COVARIATE_WIND_WORK_ITEM_KIND}
    # The shard key IS the (entity, origin batch) grain, so a completeness report groups by it.
    assert len({item.shard_key for item in items}) == EXPECTED_WORK_ITEM_COUNT
    for item in items:
        cell, _, origin = item.shard_key.partition(":")
        assert cell in {CELL_A, CELL_B}
        assert date.fromisoformat(origin) in plan.batch_origins()


def test_the_batches_walk_backwards_from_the_frontier_without_overlapping() -> None:
    plan = build_plan()

    origins = plan.batch_origins()

    assert origins[0] == NEWEST_ORIGIN
    span = PLAN_ORIGINS_PER_BATCH * PLAN_STRIDE_DAYS
    assert plan.batch_span_days == span
    # Consecutive batches are one whole batch-span apart, so no rolling origin is planned twice.
    assert all((origins[index] - origins[index + 1]).days == span for index in range(len(origins) - 1))


def test_the_batch_nearest_the_frontier_claims_first() -> None:
    items = build_plan().work_items()

    by_origin = {item.shard_key.split(":", 1)[1]: item.priority for item in items}

    assert by_origin[NEWEST_ORIGIN.isoformat()] == max(by_origin.values())


def test_replanning_the_same_declared_shape_produces_the_same_run_key_and_shards() -> None:
    """Idempotence is what makes a replan on every tick free rather than duplicating work."""
    first, second = build_plan(), build_plan()

    assert first.logical_run_key == second.logical_run_key
    assert [item.shard_key for item in first.work_items()] == [item.shard_key for item in second.work_items()]
    # Moving the frontier mints its own run rather than editing the one already in flight.
    assert build_plan(newest_origin=NEWEST_ORIGIN + timedelta(days=1)).logical_run_key != first.logical_run_key


def test_the_plan_refuses_a_shape_that_could_not_fan_out() -> None:
    with pytest.raises(ValueError, match="at least one target cell"):
        build_plan(targets=())
    with pytest.raises(ValueError, match="as_of_time must include a timezone"):
        build_plan(as_of_time=datetime(2026, 8, 1, 12, 0))  # noqa: DTZ001 - the naive value is the point
    with pytest.raises(ValueError, match="history_start must precede"):
        build_plan(history_start=HISTORY_END, history_end=HISTORY_START)


def test_two_targets_may_not_name_the_same_cell() -> None:
    with pytest.raises(ValueError, match="appears twice"):
        covariate_wind_targets([(CELL_A, SERIES_A), (CELL_A, SERIES_B)])


def test_a_work_item_payload_round_trips_into_the_pinned_request_it_was_planned_from() -> None:
    plan = build_plan()
    item = plan.work_items()[0]

    request = training_request_from_payload(item.payload)

    assert request.cell_id == CELL_A
    assert request.series_id == SERIES_A
    assert request.origin_date == NEWEST_ORIGIN
    assert request.origin_count == PLAN_ORIGINS_PER_BATCH
    assert request.origin_stride_days == PLAN_STRIDE_DAYS
    assert request.quality_policy_key == QUALITY_POLICY_KEY
    # The as-of gate the plan pinned survives the ledger round trip, which is what keeps a
    # re-claimed shard resolving its own receipt instead of writing a second one.
    assert request.as_of_time == AS_OF_TIME


def test_an_unreadable_payload_is_refused_in_typed_terms() -> None:
    payload = dict(build_plan().work_items()[0].payload)
    payload["as_of_time"] = "2026-08-01T12:00:00"

    with pytest.raises(CovariateWindPayloadError, match="must carry a timezone"):
        training_request_from_payload(payload)


def test_a_payload_number_that_is_a_bool_is_refused_rather_than_coerced() -> None:
    payload = dict(build_plan().work_items()[0].payload)
    payload["horizon_count"] = True

    with pytest.raises(CovariateWindPayloadError, match="must be an integer"):
        training_request_from_payload(payload)


def test_the_estimate_prefers_what_a_previous_attempt_measured() -> None:
    assert batch_estimate_seconds(None) == DEFAULT_BATCH_ESTIMATE_SECONDS
    assert batch_estimate_seconds({"measured_seconds": MEASURED_SECONDS}) == MEASURED_SECONDS
    # A cursor that says something impossible falls back rather than trusting it.
    assert batch_estimate_seconds({"measured_seconds": "soon"}) == DEFAULT_BATCH_ESTIMATE_SECONDS
    assert batch_estimate_seconds({"measured_seconds": True}) == DEFAULT_BATCH_ESTIMATE_SECONDS


async def test_the_handler_yields_without_reading_when_the_batch_does_not_fit_the_tick() -> None:
    """A yield is not a failure and must not spend the retry budget, so it must come before any work."""
    heartbeat = StubHeartbeat()
    invocation = build_invocation(seconds_remaining=TIGHT_BUDGET_SECONDS, heartbeat=heartbeat)

    outcome = await covariate_wind_training_handler(invocation)

    assert outcome.kind == "yielded"
    assert outcome.resume_at is None
    assert outcome.cursor is not None
    # Declined before the heartbeat and before any session was touched.
    assert heartbeat.calls == 0


async def test_the_handler_stops_when_the_fence_has_already_moved() -> None:
    heartbeat = StubHeartbeat(holds_fence=False)
    invocation = build_invocation(heartbeat=heartbeat)

    outcome = await covariate_wind_training_handler(invocation)

    assert outcome.kind == "failed"
    assert outcome.failure_class == FENCE_LOST_FAILURE_CLASS
    assert heartbeat.calls == 1


async def test_the_handler_refuses_an_unreadable_payload_loudly() -> None:
    outcome = await covariate_wind_training_handler(build_invocation(payload={"cell_id": ""}))

    assert outcome.kind == "failed"
    assert outcome.failure_class == PAYLOAD_FAILURE_CLASS


async def test_a_batch_that_cannot_be_evaluated_fails_rather_than_completing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completing a batch that produced no receipt would make it read as landed. It is missing."""

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise OriginNotEvaluableError("no rolling origin could be scored: window too short")

    monkeypatch.setattr(covariate_wind_lane, "run_covariate_wind_training", refuse)
    async with covariate_wind_lane_context(bound_context()):
        outcome = await covariate_wind_training_handler(build_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == NOT_EVALUABLE_FAILURE_CLASS
    assert "window too short" in (outcome.reason or "")


async def test_a_missing_governance_prerequisite_fails_rather_than_parking_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise ForecastTrainingPersistError("no validated release set exists")

    monkeypatch.setattr(covariate_wind_lane, "run_covariate_wind_training", refuse)
    async with covariate_wind_lane_context(bound_context()):
        outcome = await covariate_wind_training_handler(build_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == GOVERNANCE_FAILURE_CLASS


async def test_a_completed_batch_reports_its_receipt_and_its_exclusion_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = build_report()
    training_run_id = "88888888-8888-4888-8888-888888888888"
    persisted = replace(
        report,
        persisted=True,
        receipt=_stub_receipt(training_run_id=training_run_id),
    )

    async def succeed(*_args: object, **_kwargs: object) -> object:
        return persisted

    monkeypatch.setattr(covariate_wind_lane, "run_covariate_wind_training", succeed)
    async with covariate_wind_lane_context(bound_context()):
        outcome = await covariate_wind_training_handler(build_invocation())

    assert outcome.kind == "completed"
    assert outcome.progress_fraction == 1.0
    assert outcome.metrics["training_run_id"] == training_run_id
    assert outcome.metrics["scored_origin_count"] == len(report.backtest.origins)
    # The accounting rides on the ledger too, so a thin batch is visible without opening the receipt.
    assert outcome.metrics["excluded_day_count"] == report.coverage.excluded_day_count
    assert outcome.metrics["usable_day_count"] == report.coverage.usable_day_count
    assert outcome.cursor is not None


async def test_the_handler_refuses_to_run_with_no_bound_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unbound context must raise, not quietly train against nothing."""

    async def unreachable(*_args: object, **_kwargs: object) -> object:  # pragma: no cover - never called
        raise AssertionError("the handler must refuse before it reaches the training call")

    monkeypatch.setattr(covariate_wind_lane, "run_covariate_wind_training", unreachable)
    with pytest.raises(CovariateWindContextError, match="no covariate wind lane context is bound"):
        await covariate_wind_training_handler(build_invocation())


def _stub_receipt(*, training_run_id: str) -> TrainingReceipt:
    """A receipt stand-in; only the fields the handler folds into its metrics are load-bearing."""
    identifier = uuid.UUID(training_run_id)
    return TrainingReceipt(
        training_key="covariate-wind-ridge:test",
        training_run_id=identifier,
        training_run_status="validated",
        forecast_run_id=identifier,
        feature_snapshot_id=identifier,
        feature_snapshot_status="validated",
        model_id=identifier,
        model_artifact_id=identifier,
        job_run_id=identifier,
        release_set_id=identifier,
        release_set_key="governed-release",
        quality_policy_id=identifier,
        model_checksum="0" * 64,
        validation_checksum="1" * 64,
        feature_checksum="2" * 64,
        input_release_checksum="3" * 64,
        parameter_checksum="4" * 64,
        training_code_checksum="5" * 64,
        backtest_metric_count=1,
    )
