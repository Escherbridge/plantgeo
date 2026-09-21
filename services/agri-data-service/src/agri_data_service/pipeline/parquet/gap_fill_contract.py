"""What the gap-fill driver may say: its tiers, budgets, outcome vocabulary, verdicts and locks.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import func, select, text

from agri_data_service.db.advisory_keys import parquet_lane_publication_barrier_from_day_lock_key
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionOutcome,
    AvailabilityExtensionTally,
)
from agri_data_service.pipeline.parquet.derivation import DerivationResult
from agri_data_service.warehouse.parquet.tiers import TierDerivationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager
    from datetime import date

    from duckdb import DuckDBPyConnection
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.foundation.parquet.lane_contract import (
        LaneNature,
        SourceWatermark,
        StaticLaneState,
    )
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

    # One lane-day's mutual exclusion, injectable so a test need not fake Postgres advisory
    # functions. Yields whether the lock was granted, and releases on exit; the real one is
    # `postgres_lane_day_lock` and the always-granted test seam is `unlocked_lane_day`.
    LaneDayLock = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]
    VegetationPublicationBarrier = Callable[[AsyncSession], AbstractAsyncContextManager[bool | None]]
    # The coarse-rung writer, injectable exactly as the lane-day lock above is. `no_derived_tiers`
    # is the no-op a driver test substitutes when the zoom ladder is not what it is exercising.
    TierDeriver = Callable[..., DerivationResult]


# This driver fills settled OBSERVED days only. `kind=forecast` partitions are produced by each
# lane's own `method/monte_carlo/<slug>.py`, from an issue date rather than from a gap census, and
# a driver that "filled a missing forecast day" would be inventing a projection nobody issued.
GAP_FILL_PARTITION_KIND: Final[PartitionKind] = "observed"

# The BASE tier: the most detailed rung of the ladder, which is the only one a lane's day export
# writes. Taken from the ladder's own top rather than written as a literal, so a rung added above z13
# moves the base with it -- the base is "the tier nothing generalized", not the number 13.
GAP_FILL_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

# The rungs DERIVED from the base one, taken from the ladder rather than listed, so a rung added to
# `ZOOM_TIERS` is covered by the governed-absence ladder without a second edit anywhere.
_DERIVED_GAP_FILL_TIERS: Final[tuple[ZoomTier, ...]] = tuple(tier for tier in ZOOM_TIERS if tier != GAP_FILL_ZOOM_TIER)

# Coarse rungs FIRST, the censused base rung LAST: a governed-absence ladder written in this order
# leaves an interrupted run's day `missing` rather than covered-but-empty above z13.
_ABSENCE_LADDER_TIERS: Final[tuple[ZoomTier, ...]] = (*_DERIVED_GAP_FILL_TIERS, GAP_FILL_ZOOM_TIER)

# Matches `jobs-pulse`'s own tick budget: generous enough that a healthy incremental tick never trips
# it, short enough that one stuck lane cannot consume an entire hourly cadence.
DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS: Final = 600.0

# How many of a lane's missing days the census REPORTS. The days themselves are all walked; a report
# that inlined ~9,400 dates for one lane would be unreadable and would bury the counts that matter.
GAP_CENSUS_REPORT_DAY_SAMPLE: Final = 10

# Transaction-local, matching the 120 s convention every other direct SQL caller in this service uses
# (jobs/lease.py::LEASE_STATEMENT_TIMEOUT_SECONDS, and interface/cli/commands.py's loader verbs).
#
# IT IS A DEFAULT, NOT A CONSTANT, AND THE DRAIN RAISES IT. 120 s is right for the hourly tick,
# whose whole budget is 600 s: a statement allowed to run longer than a fifth of the tick starves
# every other lane of its turn. A bulk drain has no tick to protect and a very different worst case
# -- `signal` reads an 11 GB heap one cell batch at a time (RUNBOOK 0.22.5) and a cold day of it
# measured 151 s against production on 2026-08-24, which the cron's ceiling CANCELS. Two jobs with
# different budgets need two timeouts; sharing one means either the cron overruns or the drain
# cannot finish.
DEFAULT_STATEMENT_TIMEOUT_SECONDS: Final = 120


def statement_timeout(seconds: int) -> TextClause:
    """Return the `SET LOCAL` that bounds one transaction's statements.

    Interpolated rather than bound: `SET` does not accept a bind parameter in PostgreSQL, so the
    value is coerced through `int()` at the boundary instead -- a caller cannot smuggle anything
    else through, and the failure for a non-integer is a TypeError here rather than SQL anywhere.
    """
    return text(f"SET LOCAL statement_timeout = '{int(seconds)}s'")


# How many times one static lane-day export may be attempted in a tick before the export-window race
# is reported rather than retried. Two: one export, and one re-export if the source moved under it.
MAX_STATIC_EXPORT_ATTEMPTS: Final = 2

# `current` is a static lane at or ahead of its source watermark. It is deliberately NOT folded
# into `complete`: "this reference set matches its source" and "this window has no gaps left" are
# different claims about different clocks, and an operator scanning a summary needs to see which.
LaneFillOutcome = Literal["complete", "filled", "budget_exhausted", "raised", "blocked", "no_window", "current"]
LaneDayOutcome = Literal["written", "absent", "raised", "blocked", "contended"]

# The outcomes that mean this tick found something WRONG, as opposed to found work still to do.
# Named once so the summary, the exit rule and any log line cannot disagree about what failure is.
#
# `contended` is deliberately NOT here: another run holding the lane-day is the lock doing its job,
# not a fault, and the day stays work for the next tick. Counting it as failure would turn every
# overlapping backfill into a red tick.
#
# `blocked` is failure that must NOT stop the lane, and it is its own outcome for exactly that
# reason. A day holding parts whose re-export now yields zero rows cannot be resolved by this
# driver -- `write_absence` refuses to govern a day that still holds data, and only an admin can
# decide whether those parts are still valid. Reported as `raised` it would stop the lane on its
# NEWEST day and starve every older missing day behind it, every tick, forever. So it fails the
# tick loudly and lets the lane keep draining its backlog.
FAILING_LANE_OUTCOMES: Final[frozenset[str]] = frozenset({"raised", "blocked"})


class GapFillContractError(RuntimeError):
    """Raised when a lane is asked a question its nature cannot answer."""


@dataclass(frozen=True, slots=True)
class LaneWatermarkReading:
    """One attempt to read a static lane's source watermark: the answer, or why there is none."""

    watermark: SourceWatermark | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LaneGapCensus:
    """One lane's coverage as the object listing reports it, before anything is written."""

    slug: str
    nature: LaneNature
    # WHICH TIER this census asked about. A coverage row that cannot name its tier is not coverage:
    # every count below is over one rung of the ladder, and the other three are unexamined.
    zoom: ZoomTier
    history_floor: date
    publication_lag_days: int
    floor_basis: str
    first_day: date | None
    last_day: date | None
    data_days: int
    absent_days: int
    conflict_days: int
    # Days holding part files with no completion marker: an export that started and never finished.
    # Counted apart from `missing_days` (which they are also inside, because they owe the same work)
    # so a summary can distinguish a backlog from a lane dying half-way through the same day.
    incomplete_days: int
    missing_days: tuple[date, ...]
    truncated: bool
    # BASE-COMPLETE DAYS THAT ARE NOT LADDER-COMPLETE: every one of them has parts and a marker at
    # `zoom`, and at least one DERIVED rung with no completion marker of its own. They owe a
    # re-derivation, never an export -- the base rows behind them are already correct -- so they are
    # a queue apart from `missing_days` and are counted apart in every report.
    ladder_repair_days: tuple[date, ...] = ()
    ladder_truncated: bool = False
    #: Ladder-incomplete days outside this tick's ladder scope. They owe a re-index that only
    #: `drain --selection ladder` will deliver, so they are reported as a number rather than as the
    #: silence an empty repair queue would otherwise be read as.
    ladder_out_of_scope_days: int = 0
    #: Set when the derived-rung listing itself failed. The base census still stands; only the ladder
    #: half is unknown, and reporting an empty repair set for it would read as "the ladder is whole".
    ladder_error: str | None = None
    forecastable: bool = False
    cadence_days: int = 1
    writer_ceiling: date | None = None
    # Static lanes only. `static_state` is `None` for a lane with a real time axis, which is what
    # keeps "this lane has no watermark" distinguishable from "its watermark was not read".
    static_state: StaticLaneState | None = None
    source_watermark: date | None = None
    watermark_basis: str | None = None
    static_detail: str | None = None
    error: str | None = None

    @property
    def window_days(self) -> int:
        """Total calendar days between the lane's floor and its settled cutoff, inclusive.

        For a `static_lookup` lane both ends are its watermark day, so this is 1 when a version is
        known and 0 when it is not -- a static lane has no window, and does not pretend to.
        """
        if self.first_day is None or self.last_day is None:
            return 0
        return (self.last_day - self.first_day).days + 1

    def to_report(self) -> dict[str, object]:
        """Render the census row `--dry-run` echoes: nature, counts, the newest gaps, and the citations."""
        return {
            "lane": self.slug,
            "nature": self.nature,
            "zoom": self.zoom,
            "forecastable": self.forecastable,
            "cadence_days": self.cadence_days,
            "history_floor": self.history_floor.isoformat(),
            "publication_lag_days": self.publication_lag_days,
            "writer_ceiling": None if self.writer_ceiling is None else self.writer_ceiling.isoformat(),
            "window_first_day": None if self.first_day is None else self.first_day.isoformat(),
            "window_last_day": None if self.last_day is None else self.last_day.isoformat(),
            "window_days": self.window_days,
            "data_days": self.data_days,
            "absent_days": self.absent_days,
            "conflict_days": self.conflict_days,
            "incomplete_days": self.incomplete_days,
            "missing_days": len(self.missing_days),
            "missing_truncated": self.truncated,
            "newest_missing_days": [day.isoformat() for day in self.missing_days[:GAP_CENSUS_REPORT_DAY_SAMPLE]],
            "oldest_missing_day": None if not self.missing_days else self.missing_days[-1].isoformat(),
            "ladder_repair_days": len(self.ladder_repair_days),
            "ladder_truncated": self.ladder_truncated,
            "ladder_out_of_scope_days": self.ladder_out_of_scope_days,
            "newest_ladder_repair_days": [
                day.isoformat() for day in self.ladder_repair_days[:GAP_CENSUS_REPORT_DAY_SAMPLE]
            ],
            "ladder_error": self.ladder_error,
            "static_state": self.static_state,
            "source_watermark": None if self.source_watermark is None else self.source_watermark.isoformat(),
            "watermark_basis": self.watermark_basis,
            "static_detail": self.static_detail,
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
    # Base-complete days whose coarse rungs this turn RE-DERIVED. Never counted as `written`: no row
    # was exported and no base object was touched, so folding the two would make a repair sweep look
    # like history being filled.
    repaired: int
    # Repairs the census found that this turn did not FINISH -- whether it never reached them (budget
    # exhausted) or it reached one and could not resolve it, retryably or not. Apart from `remaining`,
    # which counts days owing an export, because the two are answered by different work. See
    # `ladder_unrepairable` for the subset no later tick can clear on its own.
    ladder_remaining: int
    # Days this driver may not resolve on its own. Reported apart from `written`/`absent` because
    # they are neither, and apart from a raised lane because the lane kept working. Counts BOTH an
    # export day blocked by stray parts (`_govern_absent_day`) and a ladder day blocked by an
    # unrepairable schema mismatch (`ladder_unrepairable` is the ladder-only subset of this).
    blocked: int
    # Days another run was already writing. Not failure, not progress -- see `postgres_lane_day_lock`.
    contended: int
    remaining: int
    parts: int
    rows: int
    written_bytes: int
    seconds: float
    # Terminal days this tick could not index, and what stopped each one. `ladder_incomplete` and
    # `retry_claim_failed` are PERMANENT losses -- the base-tier census never revisits a completed
    # day -- so they are counted rather than left inside a detail string.
    availability: AvailabilityExtensionTally = field(default_factory=AvailabilityExtensionTally)
    detail: str | None = None
    #: The subset of `ladder_remaining` this driver can NEVER clear by re-ticking: a published base
    #: rung whose columns no longer match the lane's registered tier derivation
    #: (`warehouse.parquet.tiers.TierDerivationError`). Retracting and re-exporting that base rung is
    #: an admin action, so this is reported apart from an ordinary retryable backlog -- folding the two
    #: together would tell an operator "wait for the next tick" about a day no tick will ever fix.
    #: Defaulted rather than positional so a caller built before this field existed still constructs.
    ladder_unrepairable: int = 0

    def to_row(self) -> dict[str, object]:
        """Render one summary-table row."""
        return {
            "lane": self.slug,
            "outcome": self.outcome,
            "considered": self.considered,
            "written": self.written,
            "absent": self.absent,
            "repaired": self.repaired,
            "blocked": self.blocked,
            "contended": self.contended,
            "remaining": self.remaining,
            "ladder_remaining": self.ladder_remaining,
            "ladder_unrepairable": self.ladder_unrepairable,
            "parts": self.parts,
            "rows": self.rows,
            "bytes": self.written_bytes,
            "seconds": round(self.seconds, 3),
            **self.availability.to_summary(),
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

    @property
    def availability(self) -> AvailabilityExtensionTally:
        """Fold every lane's availability verdicts into one tick-wide tally."""
        total = AvailabilityExtensionTally()
        for lane in self.lanes:
            total.add(lane.availability)
        return total

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object the CLI verb echoes as one line."""
        return {
            "run_id": self.run_id,
            "lanes": [lane.to_row() for lane in self.lanes],
            "lane_count": len(self.lanes),
            "written": sum(lane.written for lane in self.lanes),
            "absent": sum(lane.absent for lane in self.lanes),
            # The ladder half of the tick, at the top level beside the export half: a repair that was
            # only visible inside a lane row is a repair nobody watches, which is how the rungs fell
            # a thousand days behind the base in the first place.
            "repaired": sum(lane.repaired for lane in self.lanes),
            "ladder_remaining": sum(lane.ladder_remaining for lane in self.lanes),
            "lanes_with_ladder_backlog": [lane.slug for lane in self.lanes if lane.ladder_remaining],
            # The subset of the ladder backlog above that NO later tick can clear alone -- a published
            # base rung whose columns no longer match the lane's registered tier derivation. Named
            # apart from `lanes_with_ladder_backlog` for the same reason `blocked_lanes` is named apart
            # from a healthy backlog: "wait for the next tick" is the wrong advice for these.
            "ladder_unrepairable": sum(lane.ladder_unrepairable for lane in self.lanes),
            "lanes_with_unrepairable_ladder_days": [lane.slug for lane in self.lanes if lane.ladder_unrepairable],
            "remaining": sum(lane.remaining for lane in self.lanes),
            "parts": sum(lane.parts for lane in self.lanes),
            "rows": sum(lane.rows for lane in self.lanes),
            "bytes": sum(lane.written_bytes for lane in self.lanes),
            "budget_exhausted_lanes": [lane.slug for lane in self.lanes if lane.outcome == "budget_exhausted"],
            "blocked": sum(lane.blocked for lane in self.lanes),
            "blocked_lanes": [lane.slug for lane in self.lanes if lane.blocked],
            "contended": sum(lane.contended for lane in self.lanes),
            **self.availability.to_summary(),
            # The two availability verdicts that lose a terminal day for good, named at the top level
            # so an operator does not have to read a per-lane detail string to find them.
            "availability_unindexed_lanes": [
                lane.slug
                for lane in self.lanes
                if lane.availability.ladder_incomplete or lane.availability.retry_claim_failed
            ],
            "failed": self.failed,
            "failing_lanes": [lane.slug for lane in self.failing_lanes],
        }


def lane_window(lane: LaneRegistration, *, today: date, first_day: date | None = None) -> tuple[date, date] | None:
    """Return the settled `[first, last]` day range a SERIES lane may fill, or `None` when it has none.

    `last` is `today - publication_lag_days`, clamped to `writer_ceiling` when a dedicated writer
    owns newer days. A day the upstream has not published yet is not a gap, and a day beyond the
    generic writer's ownership is not its work. `first` defaults to the writer's declared history
    floor. A census may explicitly pass the lane's complete-history floor to expose older provider
    gaps without changing what the writer itself owns.

    A `static_lookup` lane is REFUSED rather than answered. It has no window: its partition day is a
    version stamp keyed to a source watermark, not a position on the calendar, and handing back some
    plausible-looking day range is exactly how the old `current_snapshot` model came to re-snapshot
    the newest settled day forever.
    """
    if not nature_has_time_axis(lane.nature):
        raise GapFillContractError(
            f"lane {lane.slug!r} is a static_lookup and has no settled window; its coverage comes from "
            "`resolve_static_lane` against its source watermark, not from the calendar"
        )
    last_day = today - timedelta(days=lane.publication_lag_days)
    if lane.writer_ceiling is not None:
        last_day = min(last_day, lane.writer_ceiling)
    floor = lane.history_floor if first_day is None else first_day
    if lane.release_days is not None:
        candidates = tuple(day for day in lane.release_days if floor <= day <= last_day)
        return (candidates[0], candidates[-1]) if candidates else None
    if last_day < floor:
        return None
    return floor, last_day


def zero_row_absence_reason(slug: str, day: date) -> str:
    """Return the ONE reason every rung of one absent lane-day carries; the ladder requires they agree."""
    return f"the {slug} day export returned zero rows for {day.isoformat()}"


def zero_row_absence(  # noqa: PLR0913 - one coordinate of the marked day per arg, none foldable
    slug: str,
    *,
    zoom: ZoomTier,
    day: date,
    run_id: str,
    observed: str,
    recorded_at: datetime,
) -> GovernedAbsence:
    """Build the evidence for a day whose export query genuinely returned nothing, at the tier it was asked of.

    THE PAYLOAD CLAIMS ONLY WHAT THIS RUN OBSERVED. It says the day-scoped export query over this
    warehouse's own tables returned zero rows; it never says the upstream source system was asked,
    because this driver does not contact one. Reconciling the two is `pipeline/validation/<slug>.py`.
    The tier is named in the `upstream_response` as well as in the key, because a marker lifted out
    of its path would otherwise read as a claim about the whole ladder when it settles one rung --
    while the REASON stays rung-independent, because it is the one field the availability ladder
    requires every rung of one absent day to agree on.
    """
    return GovernedAbsence(
        reason=zero_row_absence_reason(slug, day),
        upstream_response=(
            f"pipeline/lanes/{slug.replace('-', '_')}.py's day-scoped export query over this warehouse's own "
            f"tables returned 0 rows for {day.isoformat()} at zoom tier {zoom}, and the writer refused it: "
            f"{observed}. "
            "THIS RUN DID NOT CONTACT THE UPSTREAM SOURCE SYSTEM -- this records what Postgres held at "
            f"export time, never a claim about what {slug}'s source published. Reconciling the two against "
            "the live source is pipeline/validation's job, not this driver's."
        ),
        recorded_at=recorded_at,
        run_id=run_id,
    )


def _utc_now() -> datetime:
    """The absence marker's `recorded_at`; injectable so a test pins a deterministic payload."""
    return datetime.now(UTC)


async def _pin_statement_timeout(session: AsyncSession, seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS) -> None:
    """Pin the transaction-local statement timeout; `SET LOCAL` dies with each rollback, so re-pin per day."""
    await session.execute(statement_timeout(seconds))


def _lane_day_lock_key(lane: LaneRegistration, day: date) -> str:
    """The advisory-lock identity of ONE LANE-DAY'S WHOLE LADDER: the unit two writers must never share.

    THE `z13` IN THE STRING IS A HISTORICAL SPELLING, NOT A SCOPE. This key was minted when a lane-day
    was one tier; it now excludes writers of every rung of that day, because `fill_one_lane_day` and
    `drain._derive_one_day` both hold it across the base rung AND z9/z5/z0. A repair that wanted to
    rebuild one rung alone must still take THIS key -- minting `...:z9:...` beside it would exclude
    nothing, and the two writers would prune and mark the same rung concurrently. The literal stays
    because it is a lock identity: changing the string is a flag day on which running processes stop
    excluding each other.
    """
    return f"parquet-gap-fill:{lane.slug}:{GAP_FILL_PARTITION_KIND}:z{GAP_FILL_ZOOM_TIER}:{day.isoformat()}"


@asynccontextmanager
async def postgres_lane_day_lock(session: AsyncSession, key: str) -> AsyncIterator[bool]:
    """Hold the shared lane barrier then one lane-day's exclusive lock, yielding whether both were taken.

    Why the lock is SESSION-scoped, why it tries rather than waits, and the `pool_size=1`
    precondition that lives in `db/engine.py`: see `AGENTS.md` in this directory, "The lane-day
    advisory lock is session-scoped, and that has a precondition elsewhere".
    """
    lane_barrier_key = parquet_lane_publication_barrier_from_day_lock_key(key)
    shared_granted = False
    day_granted = False
    try:
        shared_result = await session.execute(
            select(func.pg_try_advisory_lock_shared(func.hashtextextended(lane_barrier_key, 0)))
        )
        shared_granted = bool(shared_result.scalar())
        if shared_granted:
            held = await session.execute(select(func.pg_try_advisory_lock(func.hashtextextended(key, 0))))
            day_granted = bool(held.scalar())
        yield shared_granted and day_granted
    finally:
        if day_granted:
            # No rollback of its own on either side. Every export path already rolls back before
            # returning, and `pg_advisory_unlock` is not transactional, so wrapping this in one
            # would only add a transaction per lane-day for nothing -- and this driver's whole
            # session discipline is "never hold a snapshot you do not need".
            with suppress(Exception):  # the lock dies with the connection; never fail a tick over it
                await session.execute(select(func.pg_advisory_unlock(func.hashtextextended(key, 0))))
        if shared_granted:
            with suppress(Exception):
                await session.execute(
                    select(func.pg_advisory_unlock_shared(func.hashtextextended(lane_barrier_key, 0)))
                )


def no_derived_tiers(  # noqa: PLR0913 - the signature IS the seam; it must match what it replaces
    store: ObjectStore,  # noqa: ARG001
    *,
    layer: str,  # noqa: ARG001
    kind: PartitionKind,  # noqa: ARG001
    day: date,  # noqa: ARG001
    run_id: str,  # noqa: ARG001
    now: Callable[[], datetime],  # noqa: ARG001
    connection: DuckDBPyConnection | None = None,  # noqa: ARG001
    base_table: object | None = None,  # noqa: ARG001
) -> DerivationResult:
    """A tier derivation that writes nothing: the seam a test injects when the ladder is not the subject.

    It accepts every optional argument the real deriver does -- `connection` and `base_table` are
    both passed by `repair_one_lane_day` -- because a seam that refused one would raise `TypeError`
    inside the repair's own guard and report every ladder repair as a failed one. See `AGENTS.md`.
    """
    return DerivationResult(tiers=(), notes=())


@asynccontextmanager
async def unlocked_lane_day(session: AsyncSession, key: str) -> AsyncIterator[bool]:  # noqa: ARG001
    """A lane-day lock that never contends: the seam a test injects when serialisation is not the subject.

    It exists so that exercising this driver does not oblige every fake session to answer
    `pg_try_advisory_lock`, exactly as `monotonic` and `now` are injected rather than faked. A test
    that IS about serialisation injects one yielding `False` instead.
    """
    yield True


def _append_note(detail: str | None, note: str) -> str:
    """Fold one more note into a lane-day detail without losing the notes already there."""
    return note if detail is None else f"{detail}; {note}"


@dataclass(frozen=True, slots=True)
class LadderRepairOutcome:
    """What one re-derivation of an already-published day came to.

    A NAMED SHAPE RATHER THAN `fill_one_lane_day`'s FIVE-TUPLE because a repair has a sixth fact an
    export cannot have: WHICH rungs derived to nothing. A day is not the unit of emptiness -- see
    `DerivationResult.emptied` -- so a caller cannot infer it from the day's part count.
    """

    outcome: LaneDayOutcome
    parts: int
    rows: int
    written_bytes: int
    detail: str | None
    emptied_tiers: tuple[ZoomTier, ...] = ()
    #: What the availability step made of the repaired day. `None` when no availability storage was
    #: wired; otherwise the verdict a driver folds into its lane tally, exactly as an export's is.
    availability: AvailabilityExtensionOutcome | None = None


def _ladder_schema_mismatch(error: BaseException) -> bool:
    """True when `error`, or something it was raised from, is a `TierDerivationError`.

    `derive_and_write_day_tiers` (`pipeline/parquet/derivation.py`) wraps a `TierDerivationError` from
    `derive_tier` inside a `TierWriteError` -- `raise TierWriteError(...) from error` -- so the
    ORIGINAL cause naming the mismatched columns sits one step up `__cause__`, not on the exception
    this driver catches directly. Walked rather than pattern-matched on `str(error)`: a message this
    driver had to fuzzy-match against is a message a later reword of `tiers.py`'s prose would silently
    break, turning an admin-needed day back into a swallowed one.

    `TierDerivationError` NAMES exactly the case measured in production: a published base rung whose
    columns no longer match the lane's currently-registered tier derivation (RUNBOOK-worthy stale
    export, `warehouse.py:362-370`). It is the one failure this function recognizes as UNREPAIRABLE by
    a re-tick; every other exception -- a transient store error, a lock edge case, anything not this --
    is left for the caller to treat as retryable, because nothing here proves otherwise.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, TierDerivationError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


async def _end_lane_day_transaction(session: AsyncSession) -> None:
    """Close whatever transaction one repair opened, and never fail a walk over the attempt.

    Swallowed for the same reason `postgres_lane_day_lock` swallows a failed release: a rollback that
    cannot be issued means the connection is already gone, and the days after this one will say so
    through their own failures. Turning it into an exception here would take the whole walk down.
    """
    with suppress(Exception):
        await session.rollback()
