"""A lane held behind a recorded-supersession requirement is visible as a typed action, not a buried string."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog

from agri_data_service.execution.job_executor_service import (
    CLOCK_RELEASE_STREAK_LIMIT,
    LANE_SPECS,
    OPERATOR_SUPERSESSION_BLOCKER_PREFIX,
    ExecutorTickSummary,
    LaneTickResult,
    LatestRun,
    OperatorAction,
    _held_checkpoint_result,
    announce_operator_actions,
    judge_failed_checkpoint,
    scheduled_bucket,
    supersession_command,
)
from agri_data_service.execution.lane_ids import DROUGHT_DIRECT_LANE_ID, FIRE_PERIMETERS_DIRECT_LANE_ID

NOW = datetime(2026, 9, 15, 6, tzinfo=UTC)
RUN_ID = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


def _held(lane_id: str, *, streak: int, scheduled_for: datetime | None = None) -> LaneTickResult:
    spec = LANE_SPECS[lane_id]
    latest = LatestRun(
        run_id=RUN_ID,
        scheduled_for=NOW - timedelta(hours=3) if scheduled_for is None else scheduled_for,
        status="failed",
        work_claimable=False,
        consecutive_failures=streak,
    )
    verdict = judge_failed_checkpoint(spec, latest, NOW)
    assert not verdict.released
    return _held_checkpoint_result(spec, latest, verdict)


def _breaker_streak(lane_id: str) -> int:
    return CLOCK_RELEASE_STREAK_LIMIT[LANE_SPECS[lane_id].catch_up_policy]


def test_a_lane_at_its_breaker_names_its_release_command_as_a_typed_action() -> None:
    result = _held(DROUGHT_DIRECT_LANE_ID, streak=_breaker_streak(DROUGHT_DIRECT_LANE_ID))
    command = supersession_command(LANE_SPECS[DROUGHT_DIRECT_LANE_ID], RUN_ID)
    assert result.operator_action == command
    assert result.blockers == (f"{OPERATOR_SUPERSESSION_BLOCKER_PREFIX}{command}",), "the string form is unchanged"
    assert result.to_dict()["operator_action"] == command


def test_a_lane_the_clock_will_release_carries_no_action() -> None:
    """Held only until the next bucket opens: the current bucket failed and nothing newer exists yet."""
    spec = LANE_SPECS[FIRE_PERIMETERS_DIRECT_LANE_ID]
    result = _held(FIRE_PERIMETERS_DIRECT_LANE_ID, streak=1, scheduled_for=scheduled_bucket(spec, NOW))
    assert result.operator_action is None
    assert result.blockers == ()


def test_the_tick_summary_lifts_every_action_to_the_top_level() -> None:
    held = _held(DROUGHT_DIRECT_LANE_ID, streak=_breaker_streak(DROUGHT_DIRECT_LANE_ID))
    quiet = LaneTickResult(lane_id="x", state="not_due")
    summary = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(quiet, held))

    assert summary.operator_actions == (
        OperatorAction(lane_id=DROUGHT_DIRECT_LANE_ID, run_id=RUN_ID, command=held.operator_action or ""),
    )
    rendered = summary.to_dict()["operator_actions"]
    assert rendered == [{"lane_id": DROUGHT_DIRECT_LANE_ID, "run_id": str(RUN_ID), "command": held.operator_action}]


def test_an_action_is_announced_once_per_process_and_cleared_once() -> None:
    held = _held(DROUGHT_DIRECT_LANE_ID, streak=_breaker_streak(DROUGHT_DIRECT_LANE_ID))
    holding = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(held,))
    released = ExecutorTickSummary(observed_at=NOW, leader=True, lanes=(LaneTickResult(lane_id="x", state="ran"),))
    follower = ExecutorTickSummary(observed_at=NOW, leader=False, lanes=())
    announced: set[tuple[str, str | None]] = set()

    with structlog.testing.capture_logs() as logs:
        announce_operator_actions(holding, announced)
        announce_operator_actions(holding, announced)
        announce_operator_actions(follower, announced)
        announce_operator_actions(released, announced)

    events = [(entry["event"], entry["log_level"]) for entry in logs]
    assert events == [
        ("plantgeo_job_executor_operator_action_required", "error"),
        ("plantgeo_job_executor_operator_action_cleared", "info"),
    ], "one announcement while held, silence while unchanged and on a follower tick, one clearance"
    assert logs[0]["command"] == held.operator_action
    assert logs[0]["run_id"] == str(RUN_ID)
    assert announced == set()
