"""Per-tick result containers: one lane's tick verdict and one tick's whole-fleet summary.

Split out of `job_executor_service.py` (soft size ceiling, `federation.md` §3). These are plain result
dataclasses with no process-held mutable state, so they move freely; `TurnReport` itself, and the
mutable `_LANE_TURN_REPORTS` cache that folds a writer's stdout into it, stay in `job_executor_service.py`
because `tests/execution/test_command_stderr_capture.py` monkeypatches that cache and its sibling
`run_scheduled_command` globals as one unit -- splitting them apart would silently defeat the fixture's
per-test isolation. See execution/AGENTS.md, "Turn reports".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import uuid
    from collections.abc import Mapping
    from datetime import datetime

    from agri_data_service.execution.job_executor_service import TurnReport

LaneTickState = Literal[
    "shadow",
    "source_specific",
    "paused",
    "not_due",
    "deferred_fairness",
    "deferred_shutdown",
    "ran",
    "failed",
]


@dataclass(frozen=True, slots=True)
class OperatorAction:
    """One held lane and the single command that releases it."""

    lane_id: str
    run_id: uuid.UUID | None
    command: str

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "run_id": None if self.run_id is None else str(self.run_id),
            "command": self.command,
        }


@dataclass(frozen=True, slots=True)
class LaneTickResult:
    lane_id: str
    state: LaneTickState
    scheduled_for: datetime | None = None
    run_id: uuid.UUID | None = None
    run_status: str | None = None
    detail: str | None = None
    slice_summary: Mapping[str, object] | None = None
    command: tuple[str, ...] | None = None
    blockers: tuple[str, ...] = ()
    due_prediction: str | None = None
    #: The exact `jobs-supersede-run` invocation that releases this lane, when only an operator can. Typed so
    #: a consumer never parses it back out of `blockers`; see execution/AGENTS.md, "Operator action surface".
    operator_action: str | None = None
    #: What the lane's own terminal report said this bucket, when the command ran in this process.
    turn_report: TurnReport | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "state": self.state,
            "scheduled_for": None if self.scheduled_for is None else self.scheduled_for.isoformat(),
            "run_id": None if self.run_id is None else str(self.run_id),
            "run_status": self.run_status,
            "detail": self.detail,
            "slice": None if self.slice_summary is None else dict(self.slice_summary),
            "command": None if self.command is None else list(self.command),
            "blockers": list(self.blockers),
            "due_prediction": self.due_prediction,
            "operator_action": self.operator_action,
            "turn_report": None if self.turn_report is None else self.turn_report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExecutorTickSummary:
    observed_at: datetime
    leader: bool
    lanes: tuple[LaneTickResult, ...]

    @property
    def failed(self) -> bool:
        return any(lane.state == "failed" for lane in self.lanes)

    @property
    def operator_actions(self) -> tuple[OperatorAction, ...]:
        """Every lane this tick found held behind a recorded-supersession requirement, with its release command."""
        return tuple(
            OperatorAction(lane_id=lane.lane_id, run_id=lane.run_id, command=lane.operator_action)
            for lane in self.lanes
            if lane.operator_action is not None
        )

    @property
    def incomplete_lanes(self) -> tuple[LaneTickResult, ...]:
        """Every lane that ran this tick, exited 0, and still reported days it could not write."""
        return tuple(lane for lane in self.lanes if lane.turn_report is not None and lane.turn_report.incomplete)

    def to_dict(self) -> dict[str, object]:
        return {
            "event": "plantgeo_job_executor_tick",
            "observed_at": self.observed_at.isoformat(),
            "leader": self.leader,
            "failed": self.failed,
            "operator_actions": [action.to_dict() for action in self.operator_actions],
            "incomplete_lanes": [
                {
                    "lane_id": lane.lane_id,
                    "days_unwritten": lane.turn_report.days_unwritten,
                    "consecutive_incomplete_buckets": lane.turn_report.consecutive_incomplete_buckets,
                }
                for lane in self.incomplete_lanes
                if lane.turn_report is not None
            ],
            "lanes": [lane.to_dict() for lane in self.lanes],
        }
