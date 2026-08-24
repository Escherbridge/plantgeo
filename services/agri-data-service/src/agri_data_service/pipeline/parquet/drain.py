"""The bulk Postgres -> Parquet drain: every lane's whole history, in one focused job.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or interface.

RUNBOOK section 0.32.1 decision 2 is the reason this exists. The 13,037 remaining lane-days are
data ALREADY in Postgres; pushing them through the hourly cron's 600-second budget, one day per
lane per round, behind an 86-minute `ingest-all`, is absurd when it can be one job that runs until
it is done. Postgres becomes a one-time cut-off here, not a source: once a lane's history is in
Parquet, its forward path writes Parquet directly and its Postgres exporter is backfill-only.

THIS IS NOT A SECOND DEFINITION OF A LANE-DAY. Every day goes through
`gap_fill.fill_one_lane_day`, the same function the hourly cron calls -- so the advisory lock, the
prune, the coarse rungs and the completion marker behave identically in both. What this module
adds is the WALK: which days, in what order, and what to do when one of them fails.

THE BUCKET IS THE CHECKPOINT, and there is deliberately no state file. A drain that dies after
9,000 of 13,037 days is resumed by running it again: `build_gap_census` re-reads the object listing
and a day that carries its completion marker is no longer missing. That is the same rule the cron
uses, so the two cannot disagree about what is done -- and it means an interrupted drain can never
resume against a stale ledger, which a checkpoint file would eventually do the first time someone
purged an object without updating it.

WHY A FAILING DAY DOES NOT STOP ITS LANE, unlike the cron
---------------------------------------------------------
`run_gap_fill` stops a lane on `raised`, and is right to: it fills newest-first, so the next day
would almost certainly fail identically and burning the tick rediscovering that costs every other
lane its turn. A drain walks the whole history, where the opposite is true -- one unparseable day
in 2003 must not cost `fire-detections` the other ~9,000. So a failure here is RECORDED and the
walk continues.

That trade needs a floor, or a lane whose source is simply gone would burn hours failing 9,000
times in a row. `max_consecutive_failures` is it: consecutive failures stop the lane, and any
success resets the count. A lane that fails intermittently drains; a lane that is broken stops
early and says so.

CONCURRENCY IS DELIBERATE AND IS THE CRON'S PROBLEM, NOT A HAZARD TO AVOID. RUNBOOK 0.33.3 B has
this running WHILE the hourly cron runs -- "build drain -> run drain -> THEN stop the cron",
because stopping the cron first freezes the warehouse with nothing replacing it. The interleaving
that would otherwise corrupt a day (the slower run's prune deleting parts the faster one just
wrote, then stamping a marker whose `part_count` matches the truncated remainder exactly) is closed
by the lane-day advisory lock inside `fill_one_lane_day`. `contended` is the expected, healthy
outcome when the two meet on one day, not an error: the other run is writing it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.pipeline.parquet.gap_fill import (
    FAILING_LANE_OUTCOMES,
    build_gap_census,
    fill_one_lane_day,
    postgres_lane_day_lock,
    resolve_lane_watermarks,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.gap_fill import LaneDayLock, LaneGapCensus
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# Consecutive failures that stop one lane. Three, not one: a transient statement timeout against a
# production database under load is normal and self-heals on the next day, while three in a row is
# a lane whose source or query is wrong rather than a lane having a bad minute.
DEFAULT_MAX_CONSECUTIVE_FAILURES: Final = 3

# Days a lane drains before the walk moves to the next lane. Round-robin rather than lane-at-a-time
# so a drain interrupted at any point has made progress on EVERY lane -- twelve lanes each half
# drained is a far more useful intermediate state than six drained and six untouched, because every
# map layer improves together rather than the last six staying empty until the very end.
DEFAULT_DAYS_PER_LANE_TURN: Final = 25

# How many times ONE day may come back `contended` before the drain gives up on it this run.
#
# WITHOUT THIS THE DRAIN CAN SPIN FOREVER, and the collision is not hypothetical -- it is the
# expected endgame of every lane. The drain fills OLDEST-first and the hourly cron fills
# NEWEST-first, so the last day a lane has left is exactly the day the cron is most likely to be
# holding. A requeue with no cap plus the default `time_budget_seconds=None` is then an infinite
# loop that reports progress: the day is popped, contended, pushed back, popped again, forever.
#
# Five, because a cron tick is bounded at 600 seconds and a lane-day is far shorter: five turns
# through the round-robin is long enough that a genuine collision has cleared, and short enough
# that a stuck lock (see `postgres_lane_day_lock`'s pool precondition) surfaces in minutes rather
# than never. A day that exhausts this is left PENDING and reported, so re-running the drain takes
# it -- the bucket is the checkpoint, and nothing about it has been written.
MAX_CONTENDED_RETRIES_PER_DAY: Final = 5


def _utc_now() -> datetime:
    """The completion marker's `completed_at`; injectable so a test pins a deterministic payload."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DrainDayFailure:
    """One lane-day the drain could not write, kept so the summary names days rather than a count."""

    slug: str
    day: date
    outcome: str
    detail: str | None


@dataclass(slots=True)
class DrainLaneProgress:
    """One lane's running tally across the whole walk."""

    slug: str
    considered: int
    pending: list[date]
    written: int = 0
    absent: int = 0
    blocked: int = 0
    contended: int = 0
    failed: int = 0
    consecutive_failures: int = 0
    contended_retries: dict[date, int] = field(default_factory=dict)
    abandoned: list[DrainDayFailure] = field(default_factory=list)
    parts: int = 0
    rows: int = 0
    written_bytes: int = 0
    seconds: float = 0.0
    stopped_reason: str | None = None
    failures: list[DrainDayFailure] = field(default_factory=list)

    @property
    def done(self) -> bool:
        """True when this lane has no days left, or has been stopped."""
        return self.stopped_reason is not None or not self.pending


@dataclass(frozen=True, slots=True)
class DrainSummary:
    """What the whole drain did, per lane, plus every day it could not write."""

    run_id: str
    lanes: tuple[DrainLaneProgress, ...]
    seconds: float

    @property
    def days_written(self) -> int:
        """Lane-days that landed with data."""
        return sum(lane.written for lane in self.lanes)

    @property
    def days_remaining(self) -> int:
        """Lane-days still missing when the walk ended."""
        return sum(len(lane.pending) for lane in self.lanes)

    @property
    def failures(self) -> tuple[DrainDayFailure, ...]:
        """Every day that failed, across every lane."""
        return tuple(failure for lane in self.lanes for failure in lane.failures)

    def to_report(self) -> dict[str, object]:
        """Render the summary a CLI prints, with per-lane rows and a bounded failure sample."""
        return {
            "run_id": self.run_id,
            "seconds": round(self.seconds, 1),
            "days_written": self.days_written,
            "days_remaining": self.days_remaining,
            "failures": len(self.failures),
            "abandoned_contended": sum(len(lane.abandoned) for lane in self.lanes),
            "lanes": [
                {
                    "lane": lane.slug,
                    "considered": lane.considered,
                    "written": lane.written,
                    "absent": lane.absent,
                    "blocked": lane.blocked,
                    "contended": lane.contended,
                    "failed": lane.failed,
                    "remaining": len(lane.pending),
                    "abandoned_contended": len(lane.abandoned),
                    "rows": lane.rows,
                    "parts": lane.parts,
                    "megabytes": round(lane.written_bytes / 1_048_576, 1),
                    "seconds": round(lane.seconds, 1),
                    "stopped": lane.stopped_reason,
                }
                for lane in self.lanes
            ],
            "failure_sample": [
                {"lane": f.slug, "day": f.day.isoformat(), "outcome": f.outcome, "detail": f.detail}
                for f in self.failures[:20]
            ],
        }


def plan_drain(census: Sequence[LaneGapCensus]) -> tuple[DrainLaneProgress, ...]:
    """Turn a census into the walk, OLDEST DAY FIRST.

    The cron fills newest-first, because a map's most recent day is the one a user is looking at.
    A drain reverses that on purpose: it is building a history, and a history that fills from the
    far end backwards leaves the time slider with a moving hole in the middle for the whole run.
    Oldest-first means the covered span grows forward as one contiguous block, so every intermediate
    state of the drain is a warehouse with a shorter but HONEST history rather than a longer one
    full of gaps.
    """
    return tuple(
        DrainLaneProgress(
            slug=entry.slug,
            considered=len(entry.missing_days),
            pending=sorted(entry.missing_days),
            stopped_reason=entry.error,
        )
        for entry in census
    )


async def run_drain(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single drain
    session: AsyncSession,
    store: ObjectStore,
    *,
    lanes: Sequence[LaneRegistration],
    today: date,
    run_id: str,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
    days_per_lane_turn: int = DEFAULT_DAYS_PER_LANE_TURN,
    max_days_per_lane: int | None = None,
    time_budget_seconds: float | None = None,
    on_day: Callable[[str, date, str, str | None], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = _utc_now,
    lane_day_lock: LaneDayLock = postgres_lane_day_lock,
) -> DrainSummary:
    """Walk every lane's missing history and write it, round-robin, until nothing is left.

    `time_budget_seconds` is OPTIONAL here and unset by default, which is the whole point of the
    drain: the cron's 600-second ceiling is what made this job necessary. When it is set it bounds
    when a new DAY is STARTED, never a day already in hand -- the same rule `run_gap_fill` applies,
    so a bounded drain still never abandons a half-written day.

    `on_day` is called after every finished day so a CLI can stream progress across a run measured
    in hours. It is deliberately a callback rather than a logger: this module has no opinion about
    where a human is watching from, and a drain that printed would be untestable.
    """
    deadline = None if time_budget_seconds is None else monotonic() + time_budget_seconds
    started = monotonic()
    watermarks = await resolve_lane_watermarks(session, store, lanes=lanes, today=today)
    census = build_gap_census(lanes, store, today=today, max_days_per_lane=max_days_per_lane, watermarks=watermarks)
    progress = plan_drain(census)
    by_slug = {lane.slug: lane for lane in lanes}

    while any(not lane.done for lane in progress):
        if deadline is not None and monotonic() >= deadline:
            break
        advanced = False
        for lane in progress:
            if lane.done:
                continue
            for _ in range(days_per_lane_turn):
                if lane.done:
                    break
                if deadline is not None and monotonic() >= deadline:
                    break
                advanced = True
                await _drain_one_day(
                    session,
                    store,
                    by_slug[lane.slug],
                    lane,
                    today=today,
                    run_id=run_id,
                    now=now,
                    lane_day_lock=lane_day_lock,
                    monotonic=monotonic,
                    max_consecutive_failures=max_consecutive_failures,
                    on_day=on_day,
                )
        if not advanced:
            break
    return DrainSummary(run_id=run_id, lanes=progress, seconds=monotonic() - started)


async def _drain_one_day(  # noqa: PLR0913 - one coordinate of the day being drained per arg
    session: AsyncSession,
    store: ObjectStore,
    registration: LaneRegistration,
    lane: DrainLaneProgress,
    *,
    today: date,
    run_id: str,
    now: Callable[[], datetime],
    lane_day_lock: LaneDayLock,
    monotonic: Callable[[], float],
    max_consecutive_failures: int,
    on_day: Callable[[str, date, str, str | None], None] | None,
) -> None:
    """Fill one day through the cron's own per-day path, then fold the outcome into the lane tally."""
    day = lane.pending.pop(0)
    began = monotonic()
    outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
        session,
        store,
        registration,
        day=day,
        run_id=run_id,
        now=now,
        today=today,
        lane_day_lock=lane_day_lock,
    )
    lane.seconds += monotonic() - began
    lane.parts += parts
    lane.rows += rows
    lane.written_bytes += written_bytes
    if outcome == "written":
        lane.written += 1
    elif outcome == "absent":
        lane.absent += 1
    elif outcome == "blocked":
        lane.blocked += 1
    elif outcome == "contended":
        # The cron holds this lane-day right now. Not a failure and not progress: put it BACK so a
        # later turn retakes it, or the drain would leave a hole exactly where the two runs met --
        # the one gap nothing else would ever come back for, since the cron fills newest-first and
        # this day is old.
        lane.contended += 1
        seen = lane.contended_retries.get(day, 0) + 1
        lane.contended_retries[day] = seen
        if seen < MAX_CONTENDED_RETRIES_PER_DAY:
            lane.pending.append(day)
        else:
            # Left pending-no-more and RECORDED rather than retried into an infinite loop. Nothing
            # was written for this day, so re-running the drain simply takes it again.
            lane.abandoned.append(
                DrainDayFailure(
                    slug=lane.slug,
                    day=day,
                    outcome="contended",
                    detail=(
                        f"another run held this lane-day on {seen} separate turns, so the drain stopped retrying it "
                        f"this run rather than spinning on it; nothing was written and a later run will take it"
                    ),
                )
            )
    if outcome in FAILING_LANE_OUTCOMES and outcome != "blocked":
        lane.failed += 1
        lane.consecutive_failures += 1
        lane.failures.append(DrainDayFailure(slug=lane.slug, day=day, outcome=outcome, detail=detail))
        if lane.consecutive_failures >= max_consecutive_failures:
            lane.stopped_reason = (
                f"{lane.consecutive_failures} consecutive failures ending at {day.isoformat()}; a lane failing this "
                f"steadily is broken rather than unlucky, and the remaining {len(lane.pending)} days would fail the "
                f"same way: {detail}"
            )
    elif outcome != "contended":
        lane.consecutive_failures = 0
    if on_day is not None:
        on_day(lane.slug, day, outcome, detail)


__all__ = [
    "DEFAULT_DAYS_PER_LANE_TURN",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DrainDayFailure",
    "DrainLaneProgress",
    "DrainSummary",
    "plan_drain",
    "run_drain",
]
