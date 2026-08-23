"""The Parquet gap-fill driver: ONE mechanism that serves both the incremental tick and the backfill.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or interface.

MISSING DAYS ARE ORDERED NEWEST-FIRST, AND THAT IS THE WHOLE DESIGN. A newly published day is simply
the newest missing day of its lane, so a driver that always takes the newest missing day first keeps
every lane's leading edge current *while* years of history remain unfilled -- there is no separate
"incremental" job to write, schedule, or keep in agreement with the backfill. Reverse the order and
the two collapse into one lane spending months walking 2000 before it ever writes yesterday.

LANES ARE VISITED ROUND-ROBIN, one day each per round. Straight sequential order would let
`fire-detections` -- roughly 9,400 missing days from its 2000-11-02 floor -- consume an entire cron
tick before `signal` wrote a single partition. Round-robin bounds that unfairness to one day per lane
per tick: every lane's newest missing day is attempted in round 1, and only then does any lane touch
its history. Within a round lanes are visited in a fixed registry order, so a budget exhausted
mid-round does still favour the earlier slugs -- by at most one day each, not by a whole lane.

A PARTIALLY DRAINED BACKLOG IS THE EXPECTED STEADY STATE, NOT A FAILURE. The wall-clock budget stops
the walk cleanly at a day boundary and the summary reports what remains; only a lane that RAISED
fails the tick. See `AGENTS.md` in this directory for the operational notes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# This driver fills settled OBSERVED days only. `kind=forecast` partitions are produced by each
# lane's own `method/monte_carlo/<slug>.py`, from an issue date rather than from a gap census, and
# a driver that "filled a missing forecast day" would be inventing a projection nobody issued.
GAP_FILL_PARTITION_KIND: Final[PartitionKind] = "observed"

# Matches `jobs-pulse`'s own tick budget: generous enough that a healthy incremental tick never trips
# it, short enough that one stuck lane cannot consume an entire hourly cadence.
DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS: Final = 600.0

# How many of a lane's missing days the census REPORTS. The days themselves are all walked; a report
# that inlined ~9,400 dates for one lane would be unreadable and would bury the counts that matter.
GAP_CENSUS_REPORT_DAY_SAMPLE: Final = 10

# Transaction-local, matching the 120 s convention every other direct SQL caller in this service uses
# (jobs/lease.py::LEASE_STATEMENT_TIMEOUT_SECONDS, and cli.py's loader verbs).
_STATEMENT_TIMEOUT: Final = text("SET LOCAL statement_timeout = '120s'")

LaneFillOutcome = Literal["complete", "filled", "budget_exhausted", "raised", "no_window"]
LaneDayOutcome = Literal["written", "absent", "raised"]

# The outcomes that mean this tick found something WRONG, as opposed to found work still to do.
# Named once so the summary, the exit rule and any log line cannot disagree about what failure is.
FAILING_LANE_OUTCOMES: Final[frozenset[str]] = frozenset({"raised"})


@dataclass(frozen=True, slots=True)
class LaneGapCensus:
    """One lane's coverage as the object listing reports it, before anything is written."""

    slug: str
    window_kind: str
    history_floor: date
    publication_lag_days: int
    floor_basis: str
    first_day: date | None
    last_day: date | None
    data_days: int
    absent_days: int
    conflict_days: int
    missing_days: tuple[date, ...]
    truncated: bool
    error: str | None = None

    @property
    def window_days(self) -> int:
        """Total calendar days between the lane's floor and its settled cutoff, inclusive."""
        if self.first_day is None or self.last_day is None:
            return 0
        return (self.last_day - self.first_day).days + 1

    def to_report(self) -> dict[str, object]:
        """Render the census row `--dry-run` echoes: counts, the newest few gaps, and the floor's citation."""
        return {
            "lane": self.slug,
            "window_kind": self.window_kind,
            "history_floor": self.history_floor.isoformat(),
            "publication_lag_days": self.publication_lag_days,
            "window_first_day": None if self.first_day is None else self.first_day.isoformat(),
            "window_last_day": None if self.last_day is None else self.last_day.isoformat(),
            "window_days": self.window_days,
            "data_days": self.data_days,
            "absent_days": self.absent_days,
            "conflict_days": self.conflict_days,
            "missing_days": len(self.missing_days),
            "missing_truncated": self.truncated,
            "newest_missing_days": [day.isoformat() for day in self.missing_days[:GAP_CENSUS_REPORT_DAY_SAMPLE]],
            "oldest_missing_day": None if not self.missing_days else self.missing_days[-1].isoformat(),
            "floor_basis": self.floor_basis,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class LaneFillVerdict:
    """What one lane's turn actually did this tick, and what it left behind."""

    slug: str
    outcome: LaneFillOutcome
    considered: int
    written: int
    absent: int
    remaining: int
    parts: int
    rows: int
    written_bytes: int
    seconds: float
    detail: str | None = None

    def to_row(self) -> dict[str, object]:
        """Render one summary-table row."""
        return {
            "lane": self.slug,
            "outcome": self.outcome,
            "considered": self.considered,
            "written": self.written,
            "absent": self.absent,
            "remaining": self.remaining,
            "parts": self.parts,
            "rows": self.rows,
            "bytes": self.written_bytes,
            "seconds": round(self.seconds, 3),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GapFillSummary:
    """This tick's whole verdict: one row per lane, in the order the driver visited them."""

    lanes: tuple[LaneFillVerdict, ...]
    run_id: str

    @property
    def failing_lanes(self) -> tuple[LaneFillVerdict, ...]:
        """Every lane whose own export raised. A drained-but-incomplete lane is NOT one of these."""
        return tuple(lane for lane in self.lanes if lane.outcome in FAILING_LANE_OUTCOMES)

    @property
    def failed(self) -> bool:
        """True only when a lane genuinely failed; a remaining backlog is a healthy steady state."""
        return bool(self.failing_lanes)

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object the CLI verb echoes as one line."""
        return {
            "run_id": self.run_id,
            "lanes": [lane.to_row() for lane in self.lanes],
            "lane_count": len(self.lanes),
            "written": sum(lane.written for lane in self.lanes),
            "absent": sum(lane.absent for lane in self.lanes),
            "remaining": sum(lane.remaining for lane in self.lanes),
            "parts": sum(lane.parts for lane in self.lanes),
            "rows": sum(lane.rows for lane in self.lanes),
            "bytes": sum(lane.written_bytes for lane in self.lanes),
            "budget_exhausted_lanes": [
                lane.slug for lane in self.lanes if lane.outcome == "budget_exhausted"
            ],
            "failed": self.failed,
            "failing_lanes": [lane.slug for lane in self.failing_lanes],
        }


def lane_window(lane: LaneRegistration, *, today: date) -> tuple[date, date] | None:
    """Return the settled `[first, last]` day range this lane may fill, or `None` when it has none.

    `last` is `today - publication_lag_days`: a day the upstream has not published yet is not a gap,
    and asking for it would write a partition thinner than the day really is.

    `first` is the declared history floor for a daily series, but for a `current_snapshot` lane it is
    `last` -- those three exports broadcast the caller's day onto every row and apply no date
    predicate, so filling a historical day would stamp today's state onto a past date. A snapshot day
    the cron missed is lost rather than fabricated, and that is the correct trade.
    """
    last_day = today - timedelta(days=lane.publication_lag_days)
    if last_day < lane.history_floor:
        return None
    if lane.window_kind == "current_snapshot":
        return last_day, last_day
    return lane.history_floor, last_day


def build_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
) -> LaneGapCensus:
    """Classify one lane's window from the object LISTING alone -- never by opening a file.

    A governed-absence marker counts as covered, not as a gap: `missing_partition_days` already
    treats it that way, which is what stops the driver re-attempting a day the source truly has
    nothing for on every tick forever.
    """
    window = lane_window(lane, today=today)
    if window is None:
        return LaneGapCensus(
            slug=lane.slug,
            window_kind=lane.window_kind,
            history_floor=lane.history_floor,
            publication_lag_days=lane.publication_lag_days,
            floor_basis=lane.floor_basis,
            first_day=None,
            last_day=None,
            data_days=0,
            absent_days=0,
            conflict_days=0,
            missing_days=(),
            truncated=False,
        )
    first_day, last_day = window
    try:
        keys = store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND)
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            first_day=first_day,
            last_day=last_day,
            keys=keys,
        )
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return LaneGapCensus(
            slug=lane.slug,
            window_kind=lane.window_kind,
            history_floor=lane.history_floor,
            publication_lag_days=lane.publication_lag_days,
            floor_basis=lane.floor_basis,
            first_day=first_day,
            last_day=last_day,
            data_days=0,
            absent_days=0,
            conflict_days=0,
            missing_days=(),
            truncated=False,
            error=f"listing {lane.slug!r} failed: {type(error).__name__}: {error}",
        )
    # NEWEST-FIRST. `partition_day_statuses` answers chronologically; this reversal is the whole
    # reason one driver serves both the leading edge and the backlog. See the module docstring.
    #
    # Cadence filters the candidates BEFORE they become work: a weekly source only publishes on its
    # own step from the floor, so the six intervening days are not gaps the driver should chase.
    # It never suppresses a day that already holds data or a marker -- those are read from the
    # listing above and reported as-is, so a real partition off the expected step stays visible.
    missing = tuple(
        day
        for day, status in sorted(statuses.items(), reverse=True)
        if status == "missing" and (day - lane.history_floor).days % lane.cadence_days == 0
    )
    truncated = max_days_per_lane is not None and len(missing) > max_days_per_lane
    return LaneGapCensus(
        slug=lane.slug,
        window_kind=lane.window_kind,
        history_floor=lane.history_floor,
        publication_lag_days=lane.publication_lag_days,
        floor_basis=lane.floor_basis,
        first_day=first_day,
        last_day=last_day,
        data_days=sum(1 for status in statuses.values() if status == "data"),
        absent_days=sum(1 for status in statuses.values() if status == "absent"),
        conflict_days=sum(1 for status in statuses.values() if status == "conflict"),
        missing_days=missing if max_days_per_lane is None else missing[:max_days_per_lane],
        truncated=truncated,
    )


def build_gap_census(
    lanes: Sequence[LaneRegistration],
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
) -> tuple[LaneGapCensus, ...]:
    """Census every requested lane, isolating one lane's listing failure from the rest."""
    return tuple(
        build_lane_census(lane, store, today=today, max_days_per_lane=max_days_per_lane) for lane in lanes
    )


def gap_census_report(census: Sequence[LaneGapCensus]) -> dict[str, object]:
    """Render `--dry-run`'s whole answer: what WOULD be filled, without writing one object."""
    return {
        "lanes": [entry.to_report() for entry in census],
        "lane_count": len(census),
        "missing_days": sum(len(entry.missing_days) for entry in census),
        "lanes_with_gaps": [entry.slug for entry in census if entry.missing_days],
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
    }


def zero_row_absence(
    slug: str,
    *,
    day: date,
    run_id: str,
    observed: str,
    recorded_at: datetime,
) -> GovernedAbsence:
    """Build the evidence for a day whose export query genuinely returned nothing.

    THE PAYLOAD CLAIMS ONLY WHAT THIS RUN OBSERVED. It says the day-scoped export query over this
    warehouse's own tables returned zero rows; it never says the upstream source system was asked,
    because this driver does not contact one. Reconciling the two is `pipeline/validation/<slug>.py`.
    """
    return GovernedAbsence(
        reason=f"the {slug} day export returned zero rows for {day.isoformat()}",
        upstream_response=(
            f"pipeline/lanes/{slug.replace('-', '_')}.py's day-scoped export query over this warehouse's own "
            f"tables returned 0 rows for {day.isoformat()}, and the writer refused it: {observed}. "
            "THIS RUN DID NOT CONTACT THE UPSTREAM SOURCE SYSTEM -- this records what Postgres held at "
            f"export time, never a claim about what {slug}'s source published. Reconciling the two against "
            "the live source is pipeline/validation's job, not this driver's."
        ),
        recorded_at=recorded_at,
        run_id=run_id,
    )


@dataclass(slots=True)
class _LaneProgress:
    """Mutable running tally for one lane's turn; frozen into a `LaneFillVerdict` at the end."""

    census: LaneGapCensus
    pending: list[date]
    written: int = 0
    absent: int = 0
    parts: int = 0
    rows: int = 0
    written_bytes: int = 0
    seconds: float = 0.0
    stopped: bool = False
    outcome: LaneFillOutcome = "complete"
    detail: str | None = None

    def verdict(self) -> LaneFillVerdict:
        """Freeze this lane's tally, deriving the outcome from what actually happened."""
        outcome = self.outcome
        if outcome == "complete" and (self.written or self.absent):
            outcome = "filled"
        return LaneFillVerdict(
            slug=self.census.slug,
            outcome=outcome,
            considered=len(self.census.missing_days),
            written=self.written,
            absent=self.absent,
            remaining=len(self.pending),
            parts=self.parts,
            rows=self.rows,
            written_bytes=self.written_bytes,
            seconds=self.seconds,
            detail=self.detail,
        )


def _seeded_progress(census: LaneGapCensus, *, today: date) -> _LaneProgress:
    """Open one lane's tally, already stopped when its census settled the question before any export."""
    progress = _LaneProgress(census=census, pending=list(census.missing_days))
    if census.error is not None:
        progress.stopped, progress.outcome, progress.detail = True, "raised", census.error
    elif census.first_day is None:
        progress.stopped, progress.outcome = True, "no_window"
        progress.detail = (
            f"nothing has settled yet: the floor {census.history_floor.isoformat()} is later than "
            f"{today.isoformat()} minus this lane's {census.publication_lag_days}-day publication lag"
        )
    return progress


def _utc_now() -> datetime:
    """The absence marker's `recorded_at`; injectable so a test pins a deterministic payload."""
    return datetime.now(UTC)


async def _pin_statement_timeout(session: AsyncSession) -> None:
    """Pin the transaction-local statement timeout; `SET LOCAL` dies with each rollback, so re-pin per day."""
    await session.execute(_STATEMENT_TIMEOUT)


async def _fill_one_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one lane-day, returning `(outcome, parts, rows, bytes, detail)` and never raising.

    The session is rolled back on EVERY path, success included. These exports are read-only, so
    holding one snapshot open across a 600-second tick would pin the xmin horizon of a production
    database for no benefit -- and after a failed statement the rollback is what lets the NEXT lane
    run at all, which is what makes per-lane isolation real rather than asserted.
    """
    await _pin_statement_timeout(session)
    try:
        result = await lane.adapter(session, store, day=day, run_id=run_id)
    except EmptyPartitionError as empty:
        await session.rollback()
        try:
            receipt = store.write_absence(
                zero_row_absence(lane.slug, day=day, run_id=run_id, observed=str(empty), recorded_at=now()),
                layer=lane.slug,
                kind=GAP_FILL_PARTITION_KIND,
                day=day,
            )
        except Exception as conflict:  # a marker that cannot be written is a real failure, not an absence
            return "raised", 0, 0, 0, f"{day.isoformat()}: absence marker refused: {conflict}"
        return "absent", 0, 0, receipt.byte_count, None
    except Exception as error:  # per-lane isolation: one lane's fault must not end the tick
        await session.rollback()
        return "raised", 0, 0, 0, f"{day.isoformat()}: {type(error).__name__}: {error}"
    await session.rollback()
    outcome: LaneDayOutcome = "absent" if result.absence_recorded else "written"
    return outcome, result.part_count, result.row_count, result.byte_count, None


async def run_gap_fill(  # noqa: PLR0913 - one parameter per operator-tunable knob of a single tick
    session: AsyncSession,
    store: ObjectStore,
    *,
    lanes: Sequence[LaneRegistration],
    today: date,
    run_id: str,
    time_budget_seconds: float = DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS,
    max_days_per_lane: int | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = _utc_now,
) -> GapFillSummary:
    """Fill every lane's newest missing day, then its next-newest, until the wall-clock budget is spent.

    `time_budget_seconds` bounds when a new DAY is STARTED, never a day already in hand: a lane's own
    export finishes whatever it began, exactly as `jobs-pulse` bounds starting a new lane rather than
    killing one mid-slice. A lane that raises stops taking further turns -- its next day would almost
    certainly fail identically, and burning the rest of the tick rediscovering that costs every other
    lane its turn -- but every OTHER lane keeps going, and the raised lane's detail names the day.
    """
    deadline = monotonic() + time_budget_seconds
    census = build_gap_census(lanes, store, today=today, max_days_per_lane=max_days_per_lane)
    progress = [_seeded_progress(entry, today=today) for entry in census]
    by_slug = {lane.slug: lane for lane in lanes}

    budget_spent = False
    while not budget_spent:
        progressed = False
        for entry in progress:
            if entry.stopped or not entry.pending:
                continue
            if monotonic() >= deadline:
                budget_spent = True
                break
            progressed = True
            day = entry.pending.pop(0)
            started = monotonic()
            outcome, parts, rows, written_bytes, detail = await _fill_one_day(
                session, store, by_slug[entry.census.slug], day=day, run_id=run_id, now=now
            )
            entry.seconds += monotonic() - started
            entry.parts += parts
            entry.rows += rows
            entry.written_bytes += written_bytes
            if outcome == "raised":
                entry.stopped, entry.outcome, entry.detail = True, "raised", detail
            elif outcome == "absent":
                entry.absent += 1
            else:
                entry.written += 1
        if not progressed:
            break

    for entry in progress:
        if entry.pending and not entry.stopped:
            entry.outcome = "budget_exhausted"
    return GapFillSummary(lanes=tuple(entry.verdict() for entry in progress), run_id=run_id)
