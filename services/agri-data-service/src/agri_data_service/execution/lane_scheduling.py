"""Pure cadence-bucket math and failed-checkpoint policy: no DB I/O, no subprocess, no side effects.

Split out of `job_executor_service.py` (soft size ceiling, `federation.md` §3). The DB-coupled callers
(leader lock, definition registration, `read_lane_checkpoint`, tick planning) stay in
`job_executor_service.py`, which constructs the dataclasses declared here. See execution/AGENTS.md,
"Failed checkpoints are superseded by the clock or by an operator".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.execution.lane_specs import (
    CLOCK_RELEASE_STREAK_LIMIT,
    EXECUTOR_DEFINITION_VERSION,
    ExecutorConfigurationError,
    LaneExecutionSpec,
    LaneWorkClass,
    ReleaseMechanism,
)

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from agri_data_service.jobs import JobDefinitionRecord

SUPERSEDE_RUN_COMMAND: Final = "agri-service ops jobs-supersede-run"


@dataclass(frozen=True, slots=True)
class LatestRun:
    run_id: uuid.UUID
    scheduled_for: datetime
    status: str
    work_claimable: bool
    has_work_items: bool = True
    terminal_items_need_rollup: bool = False
    definition_id: uuid.UUID | None = None
    definition_version: str = EXECUTOR_DEFINITION_VERSION
    definition_enabled: bool = True
    superseded_by_operator: bool = False
    consecutive_failures: int = 0

    @property
    def open(self) -> bool:
        return self.status in {"queued", "running"}


@dataclass(frozen=True, slots=True)
class DueLane:
    spec: LaneExecutionSpec
    definition: JobDefinitionRecord
    scheduled_for: datetime
    existing_run_id: uuid.UUID | None
    last_scheduled_for: datetime | None
    #: The failed or partial checkpoint this bucket supersedes, and what released it; None for an ordinary bucket.
    superseded_run_id: uuid.UUID | None = None
    supersession: ReleaseMechanism | None = None


def fair_due_order(candidates: Sequence[DueLane]) -> tuple[DueLane, ...]:
    """Interleave work classes while ordering eligible lanes by their oldest cadence checkpoint."""
    oldest = datetime.min.replace(tzinfo=UTC)

    def lane_key(candidate: DueLane) -> tuple[datetime, str]:
        return (candidate.last_scheduled_for or oldest, candidate.spec.lane_id)

    incremental = sorted(
        (candidate for candidate in candidates if candidate.spec.work_class == "incremental"),
        key=lane_key,
    )
    backlog = sorted(
        (candidate for candidate in candidates if candidate.spec.work_class == "backlog"),
        key=lane_key,
    )
    ordered: list[DueLane] = []
    while incremental or backlog:
        if incremental:
            ordered.append(incremental.pop(0))
        if backlog:
            ordered.append(backlog.pop(0))
    return tuple(ordered)


def scheduled_bucket(spec: LaneExecutionSpec, now: datetime) -> datetime:
    """Return this lane's current cadence bucket using its declared phase offset."""
    if now.utcoffset() is None:
        raise ExecutorConfigurationError("the scheduler clock must include a timezone")
    if spec.cadence_seconds is None:
        raise ExecutorConfigurationError(f"lane {spec.lane_id!r} has no recurring cadence")
    epoch_seconds = int(now.timestamp())
    bucket = (
        (epoch_seconds - spec.phase_offset_seconds) // spec.cadence_seconds
    ) * spec.cadence_seconds + spec.phase_offset_seconds
    return datetime.fromtimestamp(bucket, tz=UTC)


def next_scheduled_bucket(
    spec: LaneExecutionSpec,
    now: datetime,
    latest_scheduled_for: datetime | None,
) -> datetime:
    """Choose the next logical bucket under the lane's restart catch-up contract."""
    current = scheduled_bucket(spec, now)
    if latest_scheduled_for is None or latest_scheduled_for >= current:
        return current
    if spec.catch_up_policy == "coalesce_latest":
        return current
    return min(bucket_after(spec, latest_scheduled_for), current)


def bucket_after(spec: LaneExecutionSpec, scheduled_for: datetime) -> datetime:
    """Return the cadence bucket immediately after `scheduled_for`: the one a replayed lane opens next."""
    if spec.cadence_seconds is None:
        raise ExecutorConfigurationError(f"lane {spec.lane_id!r} has no recurring cadence")
    return datetime.fromtimestamp(int(scheduled_for.timestamp()) + spec.cadence_seconds, tz=UTC)


@dataclass(frozen=True, slots=True)
class CheckpointVerdict:
    """The planner's ruling on a checkpoint that settled without success; the operator verb rules by it too."""

    #: The bucket that opens once the checkpoint is released. Never the failed bucket itself.
    next_bucket: datetime
    #: Whether `next_bucket` is already reachable on the clock, or is the bucket after a failed current one.
    newer_bucket_exists: bool
    #: What releases the lane: the clock (the next bucket supersedes the failure) or a recorded supersession.
    release: ReleaseMechanism
    #: Whether the planner opens `next_bucket` on this tick.
    released: bool
    #: The unbroken run of settled-without-success checkpoints ending in this one, at least 1.
    consecutive_failures: int


def judge_failed_checkpoint(spec: LaneExecutionSpec, latest: LatestRun, now: datetime) -> CheckpointVerdict:
    """Rule on a failed or partial checkpoint: the bucket that opens next, what releases it, and whether that is now.

    The clock releases a lane while its failure streak is below its policy's limit. A coalesce_latest lane
    declares a missed bucket not owed, so a transient failure is superseded by the next bucket -- but three
    in a row is a broken lane, and the breaker holds it until an operator records a supersession; the
    a former maintenance loop minted 200 dead letters for want of exactly that. A replay_oldest lane owes every
    bucket, so its limit is one and only a recorded supersession ever releases it.

    Whatever releases it, the lane resumes at the CURRENT bucket, never at the buckets the hold cost: a
    coalesce_latest lane never owed them, and an operator's supersession forgives them for a replay_oldest
    lane -- its command re-censuses its own backlog, and `fair_due_order` favours the oldest checkpoint, so
    replaying a day of buckets would hand one lane the backlog class's single turn for hours. Nothing
    reopens the failed bucket itself: its logical run key is spent and its dead letter stays as the record.
    See execution/AGENTS.md, "Failed checkpoints are superseded by the clock or by an operator".
    """
    current = scheduled_bucket(spec, now)
    newer_bucket_exists = current > latest.scheduled_for
    next_bucket = current if newer_bucket_exists else bucket_after(spec, latest.scheduled_for)
    streak = max(latest.consecutive_failures, 1)
    release: ReleaseMechanism = "clock" if streak < CLOCK_RELEASE_STREAK_LIMIT[spec.catch_up_policy] else "operator"
    released = newer_bucket_exists and (release == "clock" or latest.superseded_by_operator)
    return CheckpointVerdict(
        next_bucket=next_bucket,
        newer_bucket_exists=newer_bucket_exists,
        release=release,
        released=released,
        consecutive_failures=streak,
    )


def supersession_command(spec: LaneExecutionSpec, run_id: uuid.UUID) -> str:
    return f"{SUPERSEDE_RUN_COMMAND} --lane {spec.lane_id} --run-id {run_id}"


# `LaneWorkClass` is re-exported here for `fair_due_order`'s callers that only import lane_scheduling.
__all__ = [
    "SUPERSEDE_RUN_COMMAND",
    "CheckpointVerdict",
    "DueLane",
    "LaneWorkClass",
    "LatestRun",
    "bucket_after",
    "fair_due_order",
    "judge_failed_checkpoint",
    "next_scheduled_bucket",
    "scheduled_bucket",
    "supersession_command",
]
