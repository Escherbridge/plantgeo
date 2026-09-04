"""The Parquet gap-fill driver: ONE mechanism that serves both the incremental tick and the backfill.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or interface.

MISSING DAYS ARE ORDERED NEWEST-FIRST, AND THAT IS THE WHOLE DESIGN. A newly published day is simply
the newest missing day of its lane, so a driver that always takes the newest missing day first keeps
every lane's leading edge current *while* years of history remain unfilled -- there is no separate
"incremental" job to write, schedule, or keep in agreement with the backfill. Reverse the order and
the two collapse into one lane spending months walking 2000 before it ever writes yesterday.

LANES ARE VISITED ROUND-ROBIN, one day each per round. Straight sequential order would let
`fire-detections` -- roughly 9,400 missing days from its 2000-11-01 floor -- consume an entire cron
tick before `signal` wrote a single partition. Round-robin bounds that unfairness to one day per lane
per tick: every lane's newest missing day is attempted in round 1, and only then does any lane touch
its history. Within a round lanes are visited in a fixed registry order, so a budget exhausted
mid-round does still favour the earlier slugs -- by at most one day each, not by a whole lane.

A PARTIALLY DRAINED BACKLOG IS THE EXPECTED STEADY STATE, NOT A FAILURE. The wall-clock budget stops
the walk cleanly at a day boundary and the summary reports what remains; only a lane that RAISED
fails the tick. See `AGENTS.md` in this directory for the operational notes.

A STATIC LOOKUP HAS NO BACKLOG AT ALL, AND THAT IS THE SECOND MECHANISM HERE. `daily_series` and
`release_series` lanes get the window walk above. A `static_lookup` lane instead reads its SOURCE
WATERMARK, and owes exactly one snapshot dated at that watermark -- or nothing, when a partition
already covers it AND was exported at or after the source's own change instant. Nothing can be
"missed", because no calendar day ever carried an obligation for a reference fact.

THE ONE SNAPSHOT A STATIC LANE OWES MAY BE A DAY IT ALREADY HOLDS. When the source changed again
later on the same UTC day, the version owed IS that day, re-exported: `write_partition` overwrites
by key, so the fill path below needs no separate correction mode. The census reads the export
instant out of the SAME listing it takes the days from, so this costs no extra object-store call.

THIS DRIVER EXPORTS ONE ZOOM TIER -- THE BASE ONE -- AND CENSUSES THE WHOLE LADDER. A lane adapter
exports the ungeneralized population, which is the most detailed rung of the ladder; the coarser
rungs are DERIVED from those objects in Polars/DuckDB (RUNBOOK §0.32.2 decision 2), never from a
day-scoped Postgres query. A driver that "filled" a derived tier from Postgres would be inventing a
generalization nobody computed, exactly as filling `kind=forecast` would invent a projection nobody
issued -- so the export tier is a module constant here rather than a caller's argument.

BUT THE CENSUS MAY NOT STOP AT THE BASE RUNG, and for a year it did. Walking `GAP_FILL_ZOOM_TIER`
alone made a day whose coarse rungs were never written read as complete, so nothing ever selected it
again: 1,040 lane-days were invisible above z13 on a green tick, and the only thing that found them
was a separate `drain --selection ladder` census nobody ran hourly. `missing_days` and
`ladder_repair_days` are therefore two queues -- one owes an EXPORT, the other owes only a
RE-DERIVATION from base parts that are already correct -- and `run_gap_fill` drains the second after
the first, lane by lane. A repair touches no lane adapter and no source table at all, which is also
why the ladder queue is scoped to the WHOLE BUCKET while the export queue is scoped to the settled
window: `writer_ceiling` keeps this driver out of a direct writer's days, and that ceiling is about
exporting, not about generalising bytes already published.

EVERY LANE-DAY IS WRITTEN EXPORT -> PRUNE -> MARK, THE FIRST PART WRITE RETRACTS ANY EARLIER
MARK, AND THE MARK IS WHAT MAKES THE DAY COUNT (owner, RUNBOOK 0.34.1/0.35.1). Retraction lives in
`objectstore.write_partition` at `part_index == 0`, NOT here: clearing it before the export was
attempted stripped the claim off an intact release every time an unrelated attempt failed.

The completion marker is not a soil-survey patch: the half-written
release it closes was reachable on EVERY multi-part lane, and streaming soil-survey from ~10 parts
to ~3,016 only changed the odds. So the fix lives here, in the one function every lane-day passes
through, rather than thirteen times in `pipeline/lanes/`. A day whose census says `incomplete` is
filled exactly like one that says `missing`; the two are reported apart so an operator can tell a
backlog from a lane that crashes half-way through the same day every hour.

THE PRUNE IS NO LONGER A STATIC-LANE PRIVILEGE, and that follows directly from the marker. Before
it, a series day holding any part at all read as covered and was never re-exported, so a shrinking
re-export could not happen there. Now an unfinished series day IS re-exported, so it can -- and an
unpruned day would publish the tail of the older, larger export beside the new one.

COVERAGE IS THEREFORE PER TIER AND EVERY CENSUS ROW SAYS WHICH ONE. `partition_day_statuses` ignores
keys of another tier, and nothing above it may put them back: a day present at `zoom=00` says
nothing about whether the base tier was ever written for it, and a census that added the two together
would report a covered day over a real gap and then decline to fill it.
"""

from __future__ import annotations

import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import func, select, text

from agri_data_service.db.advisory_keys import parquet_lane_publication_barrier_from_day_lock_key
from agri_data_service.db.vegetation_publication import (
    try_postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
)
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.lane_contract import (
    LaneContractError,
    nature_has_time_axis,
    resolve_static_lane,
)
from agri_data_service.foundation.parquet.paths import (
    UNFILLED_PARTITION_STATUSES,
    completed_partition_days,
    completed_rung_days,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.availability_extension import (
    POSTGRES_DAY_EXPORT_ORIGIN,
    AvailabilityExtensionOutcome,
    AvailabilityExtensionTally,
    FinalizedLaneDay,
    LaneDaySource,
    RepairedBaseRung,
    claim_repaired_lane_day,
    extend_availability_for_lane_day,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.availability_index import EvidenceReceipt
from agri_data_service.pipeline.parquet.derivation import DerivationResult, derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.objectstore import (
    EmptyPartitionError,
    GovernedAbsenceConflictError,
    SurplusPruneResult,
    availability_lane_root,
    oldest_export_instant,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
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
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, PartitionRead, WrittenObjectLedger

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
    # Repairs the census found and this turn did not reach. Apart from `remaining`, which counts days
    # owing an export, because the two are answered by different work.
    ladder_remaining: int
    # Days this driver may not resolve on its own. Reported apart from `written`/`absent` because
    # they are neither, and apart from a raised lane because the lane kept working.
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


def lane_window(lane: LaneRegistration, *, today: date) -> tuple[date, date] | None:
    """Return the settled `[first, last]` day range a SERIES lane may fill, or `None` when it has none.

    `last` is `today - publication_lag_days`, clamped to `writer_ceiling` when a dedicated writer
    owns newer days. A day the upstream has not published yet is not a gap, and a day beyond the
    generic writer's ownership is not its work. `first` is the declared history floor.

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
    if last_day < lane.history_floor:
        return None
    return lane.history_floor, last_day


def _census_shell(lane: LaneRegistration, zoom: ZoomTier, **overrides: object) -> LaneGapCensus:
    """Build one census row with the lane's own declared fields and the tier it was taken at filled in."""
    fields: dict[str, object] = {
        "slug": lane.slug,
        "nature": lane.nature,
        "zoom": zoom,
        "forecastable": lane.forecastable,
        "cadence_days": lane.cadence_days,
        "writer_ceiling": lane.writer_ceiling,
        "history_floor": lane.history_floor,
        "publication_lag_days": lane.publication_lag_days,
        "floor_basis": lane.floor_basis,
        "first_day": None,
        "last_day": None,
        "data_days": 0,
        "absent_days": 0,
        "conflict_days": 0,
        "incomplete_days": 0,
        "missing_days": (),
        "truncated": False,
    }
    fields.update(overrides)
    return LaneGapCensus(**fields)  # type: ignore[arg-type]


def _listing_failure(lane: LaneRegistration, error: Exception) -> str:
    """The message a per-lane listing failure carries; it must never read as 'no gaps found'."""
    return f"listing {lane.slug!r} failed: {type(error).__name__}: {error}"


def derived_rung_completions(
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind = GAP_FILL_PARTITION_KIND,
    tiers: Sequence[ZoomTier] = _DERIVED_GAP_FILL_TIERS,
) -> dict[ZoomTier, set[date]]:
    """Return, per DERIVED rung, the days that rung holds as `data`. One listing per rung, no reads.

    THE COST, STATED: exactly `len(tiers)` extra `list_partition_keys` calls per lane per census --
    three today -- and not one object GET. See `AGENTS.md` in this directory, "What one ladder census
    costs per tick".

    A RUNG COUNTS AS FINISHED WHEN ITS RECEIPT MATCHES WHAT IT HOLDS -- parts under an ordinary
    marker, or nothing under a derived-empty one. `completed_rung_days` rather than
    `completed_partition_days`, because a marker whose parts were deleted out from under it is a LOST
    rung: counting it finished is what made such a rung unrepairable by any tick.
    """
    if not tiers:
        raise GapFillContractError(
            f"a ladder census of {layer!r} over no rungs would report every published day complete; ask for "
            f"{tuple(_DERIVED_GAP_FILL_TIERS)} or a subset of it"
        )
    return {
        tier: completed_rung_days(store.list_partition_keys(layer, kind, tier), layer=layer, kind=kind, zoom=tier)
        for tier in tiers
    }


def _base_published_days(keys: Sequence[str], *, layer: str, zoom: ZoomTier) -> set[date]:
    """Every day of the base rung a coarse rung could be derived FROM: parts AND a completion marker.

    THE SPAN COMES FROM THE KEYS, not from `lane_window`, so this answers over the whole bucket and
    serves a `static_lookup` lane's version stamps as readily as a series lane's calendar. It is the
    same shape `drain.build_lane_ladder_census` takes, and it asks the same shared primitive
    (`partition_day_statuses`) rather than re-deciding what `data` means for a third time.
    """
    days = {
        parsed.day
        for key in keys
        if (parsed := try_parse_partition_path(key)) is not None
        and parsed.layer == layer
        and parsed.kind == GAP_FILL_PARTITION_KIND
        and parsed.zoom == zoom
    }
    if not days:
        return set()
    statuses = partition_day_statuses(
        layer=layer,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=zoom,
        first_day=min(days),
        last_day=max(days),
        keys=keys,
    )
    return {day for day, status in statuses.items() if status == "data"}


@dataclass(frozen=True, slots=True)
class _LadderRepairCensus:
    """One lane's ladder half: the days this TICK may repair, and the ones only the bulk drain reaches."""

    days: tuple[date, ...]
    truncated: bool = False
    error: str | None = None
    #: Ladder-incomplete days OUTSIDE this tick's scope. Nothing in an hourly run will select them, so
    #: they are reported as `reindex_owed` rather than left as a silence a green tick reads over.
    out_of_scope: int = 0


def _ladder_repair_census(  # noqa: PLR0913 - one census coordinate per arg, none foldable
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    base_keys: Sequence[str],
    zoom: ZoomTier,
    max_days_per_lane: int | None,
    scope: tuple[date, date] | None = None,
) -> _LadderRepairCensus:
    """Census one lane's derived rungs, newest first, over `scope` when the caller bounds one.

    THE HOURLY TICK IS SCOPED AND THE BULK DRAIN IS NOT. `scope` is the lane's own settled window
    unioned with the days a direct writer owns past it, which is the whole range an hourly run is
    responsible for; the whole-bucket walk stays in `drain --selection ladder`. Days outside it are
    counted, never dropped silently. See `AGENTS.md`, "What one ladder census costs per tick".

    A FAILURE IS REPORTED, NEVER SWALLOWED INTO AN EMPTY SET: an empty repair set means "the ladder
    is whole", which is the one thing an unreadable bucket cannot say. It is scoped to the LADDER
    half -- the base census that produced `base_keys` still stands.
    """
    try:
        base_data_days = _base_published_days(base_keys, layer=lane.slug, zoom=zoom)
        if not base_data_days:
            return _LadderRepairCensus(days=())
        completions = derived_rung_completions(store, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND)
    except Exception as error:  # per-lane isolation: an unreadable rung listing must not end the census
        return _LadderRepairCensus(
            days=(), error=f"censusing {lane.slug!r} derived rungs failed: {type(error).__name__}: {error}"
        )
    # INTERSECTED, NEVER UNIONED: a day is ladder-complete only when EVERY rung holds it, so the
    # complete set is the intersection and everything else owes a re-derivation. A union would call a
    # day whole because one of its three rungs landed.
    complete = set(base_data_days)
    for marked in completions.values():
        complete &= marked
    incomplete = base_data_days - complete
    in_scope = incomplete if scope is None else {day for day in incomplete if scope[0] <= day <= scope[1]}
    ordered = tuple(sorted(in_scope, reverse=True))
    out_of_scope = len(incomplete) - len(in_scope)
    if max_days_per_lane is None:
        return _LadderRepairCensus(days=ordered, out_of_scope=out_of_scope)
    return _LadderRepairCensus(
        days=ordered[:max_days_per_lane],
        truncated=len(ordered) > max_days_per_lane,
        out_of_scope=out_of_scope,
    )


def _static_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    zoom: ZoomTier,
    today: date,
    reading: LaneWatermarkReading | None,
) -> LaneGapCensus:
    """Classify one `static_lookup` lane's TIER against its source watermark and the objects already written.

    THE COUNTS ARE OVER THE WHOLE STREAM AT ONE TIER, not over a window, because a static lane has
    none: a reference set holds N versions, and how many of them exist is the useful number.
    `missing_days` holds at most one entry -- the version the source says is owed.

    AN UNFINISHED OLD VERSION IS REPORTED, NEVER RE-EXPORTED, and that asymmetry is the point. A
    static lane's partition day is a VERSION STAMP, so re-exporting a stranded 2026-08-20 today
    would write today's population under that day's key and manufacture a version that never
    existed. The half-release therefore stays on disk as garbage only an admin may retract -- but
    the lane must not report `current` while it sits there, so `static_detail` names it and
    `incomplete_days` counts it.
    """
    if reading is not None and reading.error is not None:
        return _census_shell(lane, zoom, static_state="watermark_unread", error=reading.error)
    try:
        listed = store.list_partition_objects(lane.slug, GAP_FILL_PARTITION_KIND, zoom)
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, zoom, static_state="watermark_unread", error=_listing_failure(lane, error))
    # The listing is already tier-scoped by its prefix; the tier is re-checked from the PARSED key
    # anyway, so a store prefix or a hand-placed object cannot smuggle another rung into these sets.
    part_days = {
        parsed.day
        for entry in listed
        if (parsed := try_parse_partition_path(entry.relative_path)) is not None
        and parsed.layer == lane.slug
        and parsed.kind == GAP_FILL_PARTITION_KIND
        and parsed.zoom == zoom
    }
    # THE SET THAT DECIDES CURRENCY. A static lane's whole verdict hangs off its newest data day, so
    # a release killed part-way through uploading -- every part of it newer than the watermark --
    # would otherwise resolve the lane `current` on top of half a snapshot. That is RUNBOOK 0.33.2
    # hazard 2 verbatim, and restricting the set to days that ASSERTED completion is what closes it.
    # Asked of the shared primitive rather than re-derived here: two spellings of "which days
    # completed" is how the census and the readers drift apart.
    complete_days = part_days & completed_partition_days(
        (entry.relative_path for entry in listed),
        layer=lane.slug,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=zoom,
    )
    marker_days = {
        marker.day
        for entry in listed
        if (marker := try_parse_absence_marker_path(entry.relative_path)) is not None
        and marker.layer == lane.slug
        and marker.kind == GAP_FILL_PARTITION_KIND
        and marker.zoom == zoom
    }
    newest_day = max(complete_days, default=None)
    watermark = None if reading is None else reading.watermark
    try:
        # Handed to the resolver APART, from the sets already built above: for a version stamp a part
        # file and a governed absence make opposite claims. See `resolve_static_lane`.
        verdict = resolve_static_lane(
            watermark=watermark,
            newest_data_day=newest_day,
            newest_data_instant=(
                None
                if newest_day is None
                else oldest_export_instant(
                    listed, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, zoom=zoom, day=newest_day
                )
            ),
            newest_marker_day=max(marker_days, default=None),
            today=today,
        )
    except LaneContractError as error:
        return _census_shell(lane, zoom, static_state="watermark_unread", error=f"{lane.slug}: {error}")
    version_day = watermark.day if watermark is not None else None
    stranded = sorted(part_days - complete_days - marker_days, reverse=True)
    detail = verdict.detail
    if stranded:
        named = ", ".join(day.isoformat() for day in stranded[:GAP_CENSUS_REPORT_DAY_SAMPLE])
        stranded_note = (
            f"{len(stranded)} version(s) hold part files with no completion marker and cannot be "
            f"repaired by this driver -- a static lane's day is a version stamp, so re-exporting one "
            f"today would date the CURRENT population as that version. Retracting them is an admin "
            f"action: {named}"
        )
        detail = f"{detail}; {stranded_note}" if detail else stranded_note
    # A version stamp is still a lane-day with a four-rung ladder, so a static lane owes its coarse
    # rungs exactly as a series lane does -- and a repair re-derives them from the version's own base
    # parts, which is the one correction a static lane may take without inventing a version. UNCAPPED
    # because a reference set holds versions, not a calendar: capping would defer a rung for a lane
    # that has three days in the bucket.
    ladder = _ladder_repair_census(
        lane,
        store,
        base_keys=tuple(entry.relative_path for entry in listed),
        zoom=zoom,
        max_days_per_lane=None,
    )
    return _census_shell(
        lane,
        zoom,
        first_day=version_day,
        last_day=version_day,
        data_days=len(complete_days),
        ladder_repair_days=ladder.days,
        ladder_truncated=ladder.truncated,
        ladder_error=ladder.error,
        ladder_out_of_scope_days=ladder.out_of_scope,
        # Both arithmetics run over `part_days`, not `complete_days`: a governed absence sitting
        # beside ANY data is the contradiction worth escalating, whether or not that data finished,
        # and scoring it against the completed set alone would quietly downgrade it to `absent`.
        absent_days=len(marker_days - part_days),
        conflict_days=len(marker_days & part_days),
        incomplete_days=len(part_days - complete_days - marker_days),
        missing_days=() if verdict.version_day is None else (verdict.version_day,),
        static_state=verdict.state,
        source_watermark=version_day,
        watermark_basis=None if watermark is None else watermark.basis,
        static_detail=detail,
    )


def _series_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    zoom: ZoomTier,
    today: date,
    max_days_per_lane: int | None,
) -> LaneGapCensus:
    """Classify one `daily_series` or `release_series` lane's settled window AT ONE TIER, from the LISTING alone."""
    window = lane_window(lane, today=today)
    if window is None:
        return _census_shell(lane, zoom)
    first_day, last_day = window
    try:
        base_keys = store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND, zoom)
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            keys=base_keys,
        )
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, zoom, first_day=first_day, last_day=last_day, error=_listing_failure(lane, error))
    # NEWEST-FIRST. `partition_day_statuses` answers chronologically; this reversal is the whole
    # reason one driver serves both the leading edge and the backlog. See the module docstring.
    #
    # Cadence filters the candidates BEFORE they become work: a release series only publishes on its
    # own step from the floor, so the six intervening days are not gaps the driver should chase.
    # It never suppresses a day that already holds data or a marker -- those are read from the
    # listing above and reported as-is, so a real partition off the expected step stays visible.
    # The cadence filter guards `missing` ONLY. An `incomplete` day is one this lane demonstrably
    # exported before, so whether it sits on the declared step is already settled by the fact that
    # something wrote it -- and suppressing it here would strand a half-written off-step day forever.
    missing = tuple(
        day
        for day, status in sorted(statuses.items(), reverse=True)
        if status in UNFILLED_PARTITION_STATUSES
        and (status != "missing" or (day - lane.history_floor).days % lane.cadence_days == 0)
    )
    # THE LADDER SCOPE IS THE SETTLED WINDOW UNIONED WITH THE DIRECT-WRITER TAIL, not the settled
    # window alone and not the whole bucket. `lane_window` clamps `last_day` to `writer_ceiling`, so
    # every day a DIRECT writer owns sits outside it -- correct for exports, which this driver must
    # not attempt there, and wrong for rungs, which are derived from published base parts and invoke
    # no writer at all. Bounding it at `today` keeps an hourly tick responsible for the range it can
    # actually serve while `drain --selection ladder` still walks the whole bucket; whatever falls
    # outside is counted as `reindex_owed` rather than dropped. It costs no extra request: the base
    # keys are the ones already listed above.
    ladder = _ladder_repair_census(
        lane,
        store,
        base_keys=base_keys,
        zoom=zoom,
        max_days_per_lane=max_days_per_lane,
        scope=(lane.history_floor, max(last_day, today)),
    )
    return _census_shell(
        lane,
        zoom,
        first_day=first_day,
        last_day=last_day,
        data_days=sum(1 for status in statuses.values() if status == "data"),
        ladder_repair_days=ladder.days,
        ladder_truncated=ladder.truncated,
        ladder_error=ladder.error,
        ladder_out_of_scope_days=ladder.out_of_scope,
        absent_days=sum(1 for status in statuses.values() if status == "absent"),
        conflict_days=sum(1 for status in statuses.values() if status == "conflict"),
        incomplete_days=sum(1 for status in statuses.values() if status == "incomplete"),
        missing_days=missing if max_days_per_lane is None else missing[:max_days_per_lane],
        truncated=max_days_per_lane is not None and len(missing) > max_days_per_lane,
    )


def build_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
    reading: LaneWatermarkReading | None = None,
) -> LaneGapCensus:
    """Classify one lane's coverage from the object LISTING alone -- never by opening a file.

    A governed-absence marker counts as covered, not as a gap: `missing_partition_days` already
    treats it that way, which is what stops the driver re-attempting a day the source truly has
    nothing for on every tick forever. A day holding parts WITHOUT a completion marker is the
    opposite case and counts as work -- but only for a SERIES lane, where it is reported as
    `incomplete_days` and appears in `missing_days` too, because repairing it is the same operation
    as filling a day never attempted. A STATIC lane's `missing_days` still holds only the version its
    watermark owes: its day is a version stamp, not a calendar position, so an unfinished old version
    is reported through `incomplete_days` and `static_detail` and left for an admin. See
    `_static_lane_census`.

    THE EXPORT TIER IS `GAP_FILL_ZOOM_TIER` AND THE LADDER IS CENSUSED BESIDE IT. `zoom`, and every
    count keyed to it, still describes the base rung alone -- this driver can only EXPORT the tier its
    lane adapters produce. What the row adds is `ladder_repair_days`: base-complete days whose derived
    rungs do not all hold them, which owe a re-derivation rather than an export. Keeping them in a
    separate field rather than folding them into `missing_days` is what stops a repair from ever being
    answered with a Postgres export, and what stops the two counts from meaning the same thing.

    THE TWO FIELDS ARE SCOPED DIFFERENTLY ON PURPOSE. `missing_days` is over the settled window,
    which `lane_window` clamps to `writer_ceiling`; `ladder_repair_days` is over that window UNIONED
    with the days a direct writer owns past the ceiling, and whatever falls outside is counted in
    `ladder_out_of_scope_days` rather than dropped. See `AGENTS.md`, "What one ladder census costs
    per tick".
    """
    if nature_has_time_axis(lane.nature):
        return _series_lane_census(
            lane, store, zoom=GAP_FILL_ZOOM_TIER, today=today, max_days_per_lane=max_days_per_lane
        )
    return _static_lane_census(lane, store, zoom=GAP_FILL_ZOOM_TIER, today=today, reading=reading)


def build_gap_census(
    lanes: Sequence[LaneRegistration],
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
    watermarks: Mapping[str, LaneWatermarkReading] | None = None,
) -> tuple[LaneGapCensus, ...]:
    """Census every requested lane, isolating one lane's listing failure from the rest."""
    readings = watermarks or {}
    return tuple(
        build_lane_census(
            lane,
            store,
            today=today,
            max_days_per_lane=max_days_per_lane,
            reading=readings.get(lane.slug),
        )
        for lane in lanes
    )


def gap_census_report(census: Sequence[LaneGapCensus]) -> dict[str, object]:
    """Render `--dry-run`'s whole answer: what WOULD be filled, without writing one object."""
    return {
        "lanes": [entry.to_report() for entry in census],
        "lane_count": len(census),
        "missing_days": sum(len(entry.missing_days) for entry in census),
        "lanes_with_gaps": [entry.slug for entry in census if entry.missing_days],
        # The days that are PUBLISHED and invisible below z13. Reported at the top level because a
        # census that only totalled `missing_days` is exactly how 1,040 of them stayed hidden.
        "ladder_repair_days": sum(len(entry.ladder_repair_days) for entry in census),
        # The ladder-incomplete days an HOURLY tick will never select, because they sit outside the
        # window it is responsible for. Only `drain --selection ladder` reaches them.
        "ladder_out_of_scope_days": sum(entry.ladder_out_of_scope_days for entry in census),
        "lanes_with_ladder_repairs": [entry.slug for entry in census if entry.ladder_repair_days],
        "lanes_with_ladder_errors": [entry.slug for entry in census if entry.ladder_error is not None],
        # Surfaced by name, not just summed: a lane accumulating unfinished days every tick is
        # crashing mid-export, and that reads as ordinary backlog in a `missing_days` total.
        "lanes_with_unfinished_days": [entry.slug for entry in census if entry.incomplete_days],
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
        # Reported separately from `lanes_with_gaps` so an operator can tell a reference set that
        # MATCHES its source from one nobody asked about. Both show zero missing days.
        "static_lanes_current": [entry.slug for entry in census if entry.static_state == "current"],
        "static_lanes_unread": [entry.slug for entry in census if entry.static_state == "watermark_unread"],
    }


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


@dataclass(slots=True)
class _LaneProgress:
    """Mutable running tally for one lane's turn; frozen into a `LaneFillVerdict` at the end."""

    census: LaneGapCensus
    pending: list[date]
    #: Days owing a re-derivation, drained only once `pending` is empty. A day that owes an EXPORT is
    #: strictly more valuable than a day that owes a generalization of rows already published.
    repairs: list[date] = field(default_factory=list)
    written: int = 0
    absent: int = 0
    repaired: int = 0
    blocked: int = 0
    contended: int = 0
    parts: int = 0
    rows: int = 0
    written_bytes: int = 0
    seconds: float = 0.0
    #: This lane takes no more EXPORT turns. It says nothing about `repairs`, which read the bucket
    #: and need no source at all -- see the walk in `run_gap_fill` for which stops take those too.
    stopped: bool = False
    outcome: LaneFillOutcome = "complete"
    #: Every availability verdict this lane's days produced, including the owed-day drain's.
    availability: AvailabilityExtensionTally = field(default_factory=AvailabilityExtensionTally)
    detail: str | None = None

    def verdict(self) -> LaneFillVerdict:
        """Freeze this lane's tally, deriving the outcome from what actually happened."""
        outcome = self.outcome
        if outcome == "complete" and (self.written or self.absent or self.repaired):
            outcome = "filled"
        # `blocked` outranks `complete`, `filled` and `budget_exhausted`: those all say the tick went
        # as well as it could, and a day needing an admin says the opposite. Only `raised` outranks
        # it, because a raised lane stopped taking turns and that is the more severe fact.
        if self.blocked and outcome != "raised":
            outcome = "blocked"
        return LaneFillVerdict(
            slug=self.census.slug,
            outcome=outcome,
            # EXPORTS ONLY, deliberately: `considered` has always meant "days this lane was asked to
            # FILL", and `remaining` is read against it. The ladder queue reports itself through
            # `repaired` and `ladder_remaining` rather than inflating a count nothing else changed.
            considered=len(self.census.missing_days),
            written=self.written,
            absent=self.absent,
            repaired=self.repaired,
            ladder_remaining=len(self.repairs),
            blocked=self.blocked,
            contended=self.contended,
            remaining=len(self.pending),
            parts=self.parts,
            rows=self.rows,
            written_bytes=self.written_bytes,
            seconds=self.seconds,
            availability=self.availability,
            detail=self.detail,
        )


def _record_day_outcome(entry: _LaneProgress, outcome: LaneDayOutcome, detail: str | None) -> None:
    """Fold one finished lane-day into its lane's running tally, and decide whether the lane goes on.

    Only `raised` stops the lane. `blocked` is a failure the lane must survive: it lands on the
    NEWEST missing day, so stopping there would starve every older gap behind it on every tick
    forever -- see `FAILING_LANE_OUTCOMES`.
    """
    if outcome == "raised":
        entry.stopped, entry.outcome, entry.detail = True, "raised", detail
        # AND ITS LADDER REPAIRS GO WITH IT. Every other `stopped` reason is a lane with nothing to
        # export, whose rungs are still worth deriving; a raised lane is one whose source, schema or
        # store just failed, and re-deriving its published days on the same tick is the driver
        # guessing that the failure was narrow. The next tick's census re-selects every one of them.
        entry.repairs.clear()
        return
    if outcome == "blocked":
        entry.blocked += 1
        entry.detail = detail
        return
    if outcome == "contended":
        # Not a failure and not progress: another run owns this day. It is left out of every tally
        # so the tick's counts stay a record of what THIS run did.
        entry.contended += 1
        entry.detail = detail
        return
    if outcome == "absent":
        entry.absent += 1
    else:
        entry.written += 1
    # A pruned orphan, a withheld completion mark and an unproven export window are all reported on a
    # SUCCEEDING day, so the detail has to survive an outcome that is not `raised` or it is swallowed.
    if detail is not None:
        entry.detail = detail


def _record_repair_outcome(entry: _LaneProgress, result: LadderRepairOutcome) -> None:
    """Fold one re-derivation into its lane's tally, and decide whether the lane goes on.

    A REPAIR THAT RAISES DOES NOT STOP THE LANE, unlike an export that raises. An export failure is
    almost always the lane's source or schema, so the next day would fail identically and burning the
    tick to rediscover that costs every other lane its turn. A derivation failure is a property of
    ONE published day -- a base rung that predates a schema change, most often -- and the day after it
    is usually fine. Stopping here would let one poisoned day in the history hide every other lane's
    ladder gap behind it.
    """
    if result.emptied_tiers:
        entry.detail = _append_note(
            entry.detail,
            f"{', '.join(f'z{tier}' for tier in result.emptied_tiers)} derived to no rows and are published empty",
        )
    if result.outcome == "written":
        entry.repaired += 1
    elif result.outcome == "contended":
        entry.contended += 1
    if result.availability is not None:
        # A repaired day's index verdict counts exactly as an exported day's does. Without this the
        # `retry_claim_failed` a failed claim reports would live only inside a detail string, which
        # is the shape that let repaired days leave the generation silently in the first place.
        entry.availability.record(result.availability)
    if result.detail is not None:
        entry.detail = _append_note(entry.detail, result.detail)


def _seeded_progress(census: LaneGapCensus, *, today: date) -> _LaneProgress:
    """Open one lane's tally, already stopped when its census settled the question before any export."""
    progress = _LaneProgress(census=census, pending=list(census.missing_days), repairs=list(census.ladder_repair_days))
    # SEEDED, NOT ACCUMULATED. This is a standing gauge: the days whose ladder no tick of this driver
    # will reach, stated once per run from the census that measured them.
    progress.availability.reindex_owed = census.ladder_out_of_scope_days
    if census.error is not None:
        progress.stopped, progress.outcome, progress.detail = True, "raised", census.error
    elif census.static_state == "current":
        # NOT `complete`, and not a gap of zero. This reference set matches its source.
        progress.stopped, progress.outcome, progress.detail = True, "current", census.static_detail
    elif census.static_state in {"source_empty", "watermark_unread"}:
        progress.stopped, progress.outcome, progress.detail = True, "no_window", census.static_detail
    elif census.first_day is None:
        progress.stopped, progress.outcome = True, "no_window"
        progress.detail = (
            f"nothing has settled yet: the floor {census.history_floor.isoformat()} is later than "
            f"{today.isoformat()} minus this lane's {census.publication_lag_days}-day publication lag"
        )
    if census.ladder_error is not None:
        # APPENDED LAST, and never a stop. The base census still stands, so the lane's export work
        # goes ahead; what is unknown is the ladder half, and saying so keeps "we could not look" from
        # reading as the empty repair set that means "every rung is whole".
        progress.detail = _append_note(progress.detail, census.ladder_error)
    return progress


def _utc_now() -> datetime:
    """The absence marker's `recorded_at`; injectable so a test pins a deterministic payload."""
    return datetime.now(UTC)


async def _pin_statement_timeout(session: AsyncSession, seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS) -> None:
    """Pin the transaction-local statement timeout; `SET LOCAL` dies with each rollback, so re-pin per day."""
    await session.execute(statement_timeout(seconds))


async def resolve_lane_watermarks(
    session: AsyncSession,
    store: ObjectStore,
    *,
    lanes: Sequence[LaneRegistration],
    today: date,
) -> dict[str, LaneWatermarkReading]:
    """Read every static lane's source watermark, isolating one failed read from the rest of the tick.

    Only `static_lookup` lanes declare a resolver, so a run over series lanes alone touches nothing
    here. A lane whose read raises gets a reading carrying the reason, which its census reports as
    `watermark_unread` -- never as zero gaps, which would read as "current" and is a different claim.
    """
    readings: dict[str, LaneWatermarkReading] = {}
    for lane in lanes:
        resolver = lane.watermark
        if resolver is None:
            continue
        await _pin_statement_timeout(session)
        try:
            readings[lane.slug] = LaneWatermarkReading(watermark=await resolver(session, store, today=today))
        except Exception as error:  # one unreadable watermark must not end the tick
            readings[lane.slug] = LaneWatermarkReading(
                error=f"reading {lane.slug!r}'s source watermark failed: {type(error).__name__}: {error}"
            )
        finally:
            # Same discipline as a lane-day: these reads are read-only, and holding one snapshot
            # across a whole tick would pin a production xmin horizon for nothing.
            await session.rollback()
    return readings


async def _export_one_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one lane-day, returning `(outcome, parts, rows, bytes, detail)` and never raising.

    THE COMPLETION MARKER IS RETRACTED BY THE FIRST PART WRITE, NOT HERE. `write_partition`
    retracts it as it uploads `part-0`, so an attempt that fails before writing anything -- a
    statement timeout, a transient database error, a source that now returns nothing -- leaves a
    previously-complete day exactly as it found it. Retracting up front instead would have stripped
    the completion claim off an intact release every time an unrelated export attempt failed.

    The session is rolled back on EVERY path, success included. These exports are read-only, so
    holding one snapshot open across a 600-second tick would pin the xmin horizon of a production
    database for no benefit -- and after a failed statement the rollback is what lets the NEXT lane
    run at all, which is what makes per-lane isolation real rather than asserted.
    """
    await _pin_statement_timeout(session, statement_timeout_seconds)
    try:
        result = await lane.adapter(session, store, day=day, run_id=run_id)
    except EmptyPartitionError as empty:
        await session.rollback()
        return _govern_absent_day(store, lane, day=day, run_id=run_id, now=now, observed=str(empty))
    except Exception as error:  # per-lane isolation: one lane's fault must not end the tick
        await session.rollback()
        return "raised", 0, 0, 0, f"{day.isoformat()}: {type(error).__name__}: {error}"
    await session.rollback()
    if result.absence_recorded:
        # A governed absence is ONE object and cannot be half-written, so it asserts its own
        # completion and never gets a marker. Writing one here would put two markers on a day whose
        # only honest reading is `absent`.
        return "absent", result.part_count, result.row_count, result.byte_count, None
    return _finalize_written_day(
        store,
        lane,
        day=day,
        parts=result.part_count,
        rows=result.row_count,
        written_bytes=result.byte_count,
        run_id=run_id,
        now=now,
        derive_tiers=derive_tiers,
    )


def _govern_absent_day(  # noqa: PLR0913 - one coordinate of the day being governed per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    observed: str,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Govern one whole day as absent at EVERY rung, or write no marker at any rung at all.

    THE WHOLE LADDER IS CHECKED BEFORE THE FIRST MARKER IS WRITTEN. Writing coarse-first and
    refusing on the first conflict left the earlier rungs marked absent while z13 went on serving
    rows -- the exact stable lie the marker contract exists to prevent, and one no census brings
    back: `build_gap_census` walks the base tier, which still holds its parts and its completion.
    A rung that a later write still fails on is ROLLED BACK, marker by marker, for the same reason.

    THE COARSE RUNGS FIRST, THE BASE RUNG LAST, for the reason `_finalize_written_day` derives
    before it marks: only the base tier is censused, so a run that died after the base marker would
    leave a day covered and never revisited while every rung above it said nothing at all.
    """
    blocked = tuple(
        (zoom, part)
        for zoom in _ABSENCE_LADDER_TIERS
        if (part := store.part_blocking_absence(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)) is not None
    )
    if blocked:
        # Only an admin can say whether those parts remain valid, so this driver refuses to guess --
        # but it also refuses to stop the lane over it, because this day is the NEWEST one and every
        # older gap sits behind it. See FAILING_LANE_OUTCOMES.
        rungs = ", ".join(f"z{zoom} ({part})" for zoom, part in blocked)
        return (
            "blocked",
            0,
            0,
            0,
            f"{day.isoformat()}: the export returned zero rows but {rungs} still holds part files, so the day "
            f"can be neither written nor governed as absent without an admin deciding whether those parts are "
            f"still valid; no absence marker was written at any rung",
        )
    marked = 0
    written: list[ZoomTier] = []
    for zoom in _ABSENCE_LADDER_TIERS:
        try:
            receipt = store.write_absence(
                zero_row_absence(
                    lane.slug,
                    zoom=zoom,
                    day=day,
                    run_id=run_id,
                    observed=observed,
                    recorded_at=now(),
                ),
                layer=lane.slug,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=zoom,
                day=day,
            )
        except Exception as refusal:  # a marker that cannot be written is a real failure, not an absence
            rolled_back = _retract_absence_ladder(store, lane, day=day, written=tuple(written))
            return (
                "raised",
                0,
                0,
                marked,
                f"{day.isoformat()}: z{zoom} absence marker refused: {refusal}. {rolled_back}",
            )
        written.append(zoom)
        marked += receipt.byte_count
    return "absent", 0, 0, marked, None


def _retract_absence_ladder(
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    written: tuple[ZoomTier, ...],
) -> str:
    """Undo a partly-written absence ladder, so no rung governs a day the others do not."""
    if not written:
        return "no rung had been marked, so the day is exactly as this attempt found it"
    failures: list[str] = []
    for zoom in written:
        try:
            store.clear_absence_marker(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)
        except Exception as error:  # one rung's rollback must not skip the rest
            failures.append(f"z{zoom}: {type(error).__name__}: {error}")
    if failures:
        return (
            "the markers this attempt had already written could NOT all be retracted, so the day now "
            f"governs some rungs and not others and needs an admin: {'; '.join(failures)}"
        )
    return f"the {len(written)} marker(s) this attempt had written were retracted, leaving the day unmarked"


def _finalize_written_day(  # noqa: PLR0913 - one coordinate of the day being closed per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    parts: int,
    rows: int,
    written_bytes: int,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Close a written lane-day: prune what this export no longer wrote, then assert that it finished.

    PRUNE BEFORE MARK. The marker's `part_count` is the export's own claim about what the day holds,
    so asserting it while a larger earlier export's tail is still published would make the marker
    disagree with the bucket at the very moment it was written.

    A FAILED MARK IS `raised`, not a note on an otherwise successful day, and that is deliberate: an
    unmarked day is re-exported next tick, so a mark that keeps failing is a lane silently
    re-exporting the same day every hour forever while reporting success. The same reasoning already
    makes an absence marker that cannot be written a failure rather than an absence.

    A FAILED PRUNE STILL DOES NOT FAIL THE DAY -- the rows this export wrote are correct and no prune
    may undo that -- BUT IT WITHHOLDS THE MARK. Marking a day whose surplus parts survived would
    publish a completion claim over a two-generation mixture, which is the one statement this marker
    exists to make trustworthy. Leaving it unmarked keeps the day `incomplete`, so the next tick
    re-exports and re-prunes it: the outcome stays self-healing instead of becoming a stable lie.
    """
    if parts <= 0:
        return (
            "raised",
            parts,
            rows,
            written_bytes,
            f"{day.isoformat()}: the export reported {parts} part files while reporting data, so there "
            "is nothing a completion marker could honestly claim",
        )
    notes: list[str] = []
    # WRITE FIRST, PRUNE SECOND. A prune that ran first and then failed would leave the day EMPTY,
    # which reads as a present-but-thin version and is worse than the orphan it was removing.
    pruned = _prune_surplus(store, lane, day=day, written_part_count=parts)
    report = pruned.report
    if report is not None:
        notes.append(report)
    if pruned.failures:
        notes.append(
            f"{day.isoformat()}: the day is NOT being marked complete, because a surplus part from a "
            "larger earlier export is still published beside this one and a completion marker over "
            "that mixture would be false. The next tick re-exports and re-prunes this day."
        )
        return "written", parts, rows, written_bytes, "; ".join(notes)
    # THE COARSE RUNGS, BEFORE THE BASE MARKER. Withholding the base marker leaves the day
    # `incomplete`, which brings it back through the EXPORT queue and redoes all four rungs. Marking
    # it first and then failing to derive would instead leave it base-complete and rung-empty, which
    # now falls to the ladder queue -- a strictly weaker guarantee, since that queue depends on a
    # census being right where this ordering depends on nothing. Deriving first stays.
    try:
        derived = derive_tiers(store, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, day=day, run_id=run_id, now=now)
    except GovernedAbsenceConflictError as stranded:
        # A COARSE ABSENCE CLAIM SURVIVING A WRITTEN BASE RUNG, retracted here rather than left to
        # fail this day on every tick forever. It decides nothing: `write_partition` refuses a base
        # rung that still carries an absence claim, so the base export having SUCCEEDED proves that
        # claim was already retracted -- by an admin, or by a direct writer whose source began
        # publishing the day. Finishing that retraction across the ladder is the same obligation
        # `derive_tiers` has to finish the ladder, and a rung claiming a day is governed-empty while
        # the base rung serves its rows is the stable lie the whole marker contract exists to
        # prevent. The day still fails this tick and the next one redoes all four rungs.
        notes.append(_retract_derived_absences(store, lane, day=day, conflict=stranded))
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    except Exception as error:  # the base rows are published but the ladder above them is not
        notes.append(
            f"{day.isoformat()}: the base rung is written but its coarse rungs are not, so the day stays "
            f"unfinished and will be re-exported rather than be published as visible only above z13: "
            f"{type(error).__name__}: {error}"
        )
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    notes.extend(derived.notes)
    if derived.tiers:
        rungs = ", ".join(
            f"z{report.tier} {report.row_count} rows in {report.part_count} part(s)" for report in derived.tiers
        )
        notes.append(f"{day.isoformat()}: derived {rungs}")
    # The marker's counts describe the BASE rung alone. The derived rungs carry their own markers
    # with their own counts, written as each landed; folding them together here would make this
    # marker claim a population no single prefix holds.
    #
    # NO `parts=` HERE (unlike `derivation.py::_write_tier`): `parts`/`rows` above are
    # `LaneRunResult`'s folded totals, not receipts, and the ledger that DOES hold per-part digests
    # (`written`, opened by `fill_one_lane_day`) is not threaded this deep, nor safely reusable
    # across `_fill_static_day`'s retries without the mismatch guard `_rung_objects_from_ledger`
    # applies. See `pipeline/parquet/AGENTS.md`, "Per-part digests: `_write_tier` wired,
    # `_finalize_written_day` stays v1 (D3)".
    try:
        store.write_completion_marker(
            PartitionCompletion(part_count=parts, row_count=rows, completed_at=now(), run_id=run_id),
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=GAP_FILL_ZOOM_TIER,
            day=day,
        )
    except Exception as error:  # the rows are published but nothing may read them as finished
        notes.append(
            f"{day.isoformat()}: the {parts} part file(s) uploaded but the completion marker did not, so "
            f"this day stays unfinished and will be re-exported: {type(error).__name__}: {error}"
        )
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    return "written", parts, rows, written_bytes, "; ".join(notes) or None


async def _read_watermark(
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    today: date,
) -> tuple[SourceWatermark | None, str | None]:
    """Read one static lane's watermark for the race bracket, reporting the reason instead of raising."""
    resolver = lane.watermark
    if resolver is None:
        return None, None
    await _pin_statement_timeout(session)
    try:
        return await resolver(session, store, today=today), None
    except Exception as error:  # an unproven window is reportable; it is never a failed export
        return None, f"{type(error).__name__}: {error}"
    finally:
        # Same discipline as a lane-day: read-only, so never hold the snapshot past the answer.
        await session.rollback()


def _retract_derived_absences(
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    conflict: GovernedAbsenceConflictError,
) -> str:
    """Clear every coarse-rung absence claim over a day whose base rung holds data; report what happened."""
    failures: list[str] = []
    for zoom in _DERIVED_GAP_FILL_TIERS:
        try:
            store.clear_absence_marker(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)
        except Exception as error:
            failures.append(f"z{zoom}: {type(error).__name__}: {error}")
    if failures:
        return (
            f"{day.isoformat()}: a coarse rung still claims this day is governed as absent while its base rung "
            f"holds data, and the claim could not be retracted, so an admin must remove it: "
            f"{'; '.join(failures)} (from {conflict})"
        )
    return (
        f"{day.isoformat()}: a coarse rung claimed this day was governed as absent while its base rung holds "
        f"data; the base export already proved that claim retracted, so the surviving coarse markers were "
        f"removed and the next tick rebuilds the ladder: {conflict}"
    )


def _prune_surplus(
    store: ObjectStore, lane: LaneRegistration, *, day: date, written_part_count: int
) -> SurplusPruneResult:
    """Trail ANY completed export with the prune removing the parts it no longer wrote.

    Static lanes are no longer the only ones that need this. Before the completion marker, a series
    day holding any part at all read as covered and was never revisited, so a shrinking re-export
    could not arise there; now an unfinished series day IS re-exported, and it can.

    Scoped to the tier that was just written: the same day's coarser tiers hold a DIFFERENT
    resolution of it, not an older export of it, and a prune that reached them would delete a
    derivation this export never replaced.
    """
    try:
        return store.prune_surplus_parts(
            lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day, written_part_count=written_part_count
        )
    except Exception as error:  # the rows are written and correct; no prune may ever undo that
        # Returned as a FAILURE result rather than a prose note: the caller withholds the completion
        # mark on any failure, and a string it had to pattern-match would make that decision fragile.
        return SurplusPruneResult(
            removed=(),
            failures=(
                f"pruning surplus parts of {lane.slug} z{GAP_FILL_ZOOM_TIER} {day.isoformat()} failed, so parts "
                f"from a larger earlier export may still be published beside this one: "
                f"{type(error).__name__}: {error}",
            ),
        )


async def _fill_static_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one STATIC lane-day, prune what it no longer wrote, and prove the source held still.

    THE EXPORT INSTANT IS PUT TIME, NOT SELECT TIME, and that gap is a race this lane cannot survive
    silently. A source change committed after the export's SELECT but before its PUT lands inside the
    window, so the part file's `LastModified` is at or after the source's own change instant and
    `_resolve_watermark_day` reads it as captured -- permanently, because every later tick compares
    the same two unchanged instants. The snapshot then serves a superseded evacuation level while the
    lane reports `current`.

    So the window is BRACKETED: the watermark is read immediately before the export and again after
    it. A read taken before the SELECT cannot be later than the SELECT, so two equal instants prove
    nothing changed in between and the PUT instant honestly means captured. A moved instant means the
    snapshot's vintage is unknown, and the day is re-exported rather than published as current.
    Bounded by `MAX_STATIC_EXPORT_ATTEMPTS`: a source changing faster than one export takes is
    REPORTED, which is the correct failure mode, not latched, which is the one this closes.

    A lane whose source has no change instant at all (the computed calendar) is not exposed: its
    verdict already resolves at day resolution and never claims instant-resolution currency.
    """
    notes: list[str] = []
    outcome: LaneDayOutcome = "raised"
    parts = rows = written_bytes = 0
    detail: str | None = None
    for attempt in range(1, MAX_STATIC_EXPORT_ATTEMPTS + 1):
        before, before_error = await _read_watermark(session, store, lane, today=today)
        outcome, parts, rows, written_bytes, detail = await _export_one_day(
            session,
            store,
            lane,
            day=day,
            run_id=run_id,
            now=now,
            derive_tiers=derive_tiers,
            statement_timeout_seconds=statement_timeout_seconds,
        )
        # `parts <= 0` is not re-checked: `_finalize_written_day` already returns `raised` in that
        # case, so `written` implies at least one part landed.
        if outcome != "written":
            break
        # `_export_one_day` already pruned this attempt and marked it complete. Its note moves into
        # `notes` rather than staying in `detail`, which the next attempt would overwrite and lose.
        if detail is not None:
            notes.append(detail)
            detail = None
        after, after_error = await _read_watermark(session, store, lane, today=today)
        unread = before_error or after_error
        if unread is not None:
            notes.append(
                "the source watermark could not be re-read around this export, so it is UNPROVEN whether the "
                f"source changed between its select and its upload: {unread}"
            )
            break
        if before is None or after is None or before.instant is None or after.instant is None:
            break
        if after.instant == before.instant:
            break
        if attempt == MAX_STATIC_EXPORT_ATTEMPTS:
            notes.append(
                f"the source changed again during every one of {MAX_STATIC_EXPORT_ATTEMPTS} export attempts, so "
                f"this snapshot of {day.isoformat()} may predate the source change at "
                f"{after.instant.isoformat()} while its upload instant reads as current -- re-export it"
            )
            break
        notes.append(
            f"the source changed at {after.instant.isoformat()} DURING attempt {attempt}'s export window, so "
            "that snapshot's vintage is unknown and the day is being re-exported"
        )
    return outcome, parts, rows, written_bytes, "; ".join(note for note in (detail, *notes) if note) or None


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

    SESSION-scoped, not transaction-scoped, and that is the whole reason this is not
    `execution/provenance.py::advisory_lock`. That helper takes `pg_advisory_xact_lock`, which the
    very next `session.rollback()` releases -- and this driver rolls back immediately after every
    export, BEFORE the prune that deletes objects and the mark that publishes the day. A transaction
    lock would cover the read and leave the destructive half unguarded, which is exactly backwards.
    A session lock survives those rollbacks.

    TRY, never wait. This driver runs on a wall-clock budget; blocking on a lane-day another run is
    already writing would spend the tick queueing to redo work that is being done. The day stays
    missing and the next tick takes it.

    `pg_advisory_unlock` is not transactional either, so the release survives the caller's next
    rollback and needs none of its own. A failed release is swallowed: the lock dies with the
    connection, and losing a tick over it would be the larger fault.

    PRECONDITION, AND IT LIVES IN ANOTHER MODULE: a session lock belongs to one BACKEND, and
    SQLAlchemy returns the connection to the pool on every rollback -- which this driver does between
    acquire and release on every path. Acquire and release land on the same backend only because
    `db/engine.py:121` pins `pool_size=1, max_overflow=0` for this engine. RAISE THAT POOL AND THIS
    BREAKS SILENTLY: the unlock goes to a different connection, the original holds the lock for its
    lifetime, every later tick reports `contended` for that lane-day, and the day is never filled --
    on a green tick, because `contended` is deliberately not a failure. Anything changing that pool
    must move this to an explicitly checked-out connection first.
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


async def _extend_availability_for_result(  # noqa: PLR0913 - one coordinate of the finished day per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    result: tuple[LaneDayOutcome, int, int, int, str | None],
    *,
    day: date,
    today: date,
    run_id: str,
    now: Callable[[], datetime],
    written: WrittenObjectLedger,
    availability_storage: AvailabilityStorage | None,
    tally: AvailabilityExtensionTally | None,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Extend the lane's availability generation AFTER a terminal day, never turning that day back."""
    outcome, parts, rows, written_bytes, detail = result
    if outcome not in ("written", "absent"):
        return result
    terminal_state: Literal["published", "governed_absence"] = (
        "published" if outcome == "written" else "governed_absence"
    )
    published_at = now()
    # THE LANE'S OWN CEILING, NOT THE DAY. A day that declared itself its own ceiling ratcheted the
    # published pointer to the newest day written, and coverage then closed every lane exactly at
    # its last row -- so no lane could report a gap tail however far behind its source it had
    # fallen. `max` with the day keeps the statement honest for a day a forward writer published
    # past the generic ceiling: a lane cannot have a ceiling below a day it demonstrably holds.
    source_ceiling = max(allowed_source_ceiling(lane, today=today), day)
    try:
        extension = await extend_availability_for_lane_day(
            session,
            store,
            lane=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            outcome=FinalizedLaneDay(
                terminal_state=terminal_state,
                day=day,
                written=written,
                source=LaneDaySource(
                    origin=POSTGRES_DAY_EXPORT_ORIGIN,
                    run_id=run_id,
                    row_count=rows,
                    part_count=parts,
                    exported_at=published_at,
                    detail=f"{lane.slug} {lane.nature} day export",
                ),
                published_at=published_at,
                source_ceiling=source_ceiling,
                absence_reason=(None if terminal_state == "published" else zero_row_absence_reason(lane.slug, day)),
            ),
            availability=availability_storage,
            now=now,
        )
    except Exception as error:  # the day is terminal; the index owing an entry may never undo that
        return (
            outcome,
            parts,
            rows,
            written_bytes,
            _append_note(
                detail,
                f"{day.isoformat()}: the availability step raised and the day stays terminal: "
                f"{type(error).__name__}: {error}",
            ),
        )
    if tally is not None:
        tally.record(extension)
    return outcome, parts, rows, written_bytes, _append_note(detail, extension.note)


async def fill_one_lane_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date,
    lane_day_lock: LaneDayLock,
    vegetation_publication_barrier: VegetationPublicationBarrier = try_postgres_vegetation_publication_barrier,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    extend_availability: bool = True,
    availability_storage: AvailabilityStorage | None = None,
    availability_tally: AvailabilityExtensionTally | None = None,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one lane-day under its advisory lock. Static lanes also bracket their window.

    PUBLIC because the bulk drain (`pipeline/parquet/drain.py`) calls exactly this. The drain and
    the hourly cron must not drift on what one lane-day MEANS -- the lock, the prune, the coarse
    rungs and the marker are one indivisible unit, and a drain that reimplemented them would be
    the second definition of a contract that already took three review passes to settle.

    THE LOCK SPANS THE WHOLE DAY, export and prune and mark together, because the prune DELETES.
    Two unsynchronised runs on one lane-day can otherwise interleave so that the slower one's prune
    removes parts the faster one just wrote and then stamps a completion marker whose `part_count`
    matches the truncated remainder exactly -- the bucket and its receipt agreeing on a population
    that lost rows, which no later census or audit can detect. Nothing else in this path is
    serialised: `interface/cli/commands.py`'s `parquet-gap-fill` verb takes no lease, and RUNBOOK 0.33.3 B has the bulk
    drain running CONCURRENTLY with this driver by design ("build drain -> run drain -> THEN stop
    the cron"), so the overlap is planned rather than hypothetical.
    """
    barrier = (
        vegetation_publication_barrier
        if lane.slug == VEGETATION_PLANE_STREAM
        else unlocked_vegetation_publication_barrier
    )
    async with barrier(session) as publication_granted:
        if publication_granted is False:
            await session.rollback()
            return (
                "contended",
                0,
                0,
                0,
                f"{day.isoformat()}: exact vegetation audit holds the publication barrier; this day was deferred",
            )
        async with lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if not granted:
                return (
                    "contended",
                    0,
                    0,
                    0,
                    f"{day.isoformat()}: another run holds this lane-day, so it was skipped rather than "
                    "written twice; it stays missing and the next tick will take it",
                )
            # THE LEDGER SPANS THE WHOLE EXPORT so the availability step can name every object this
            # run wrote -- part keys and digests included -- without re-reading a single byte of them.
            # It records nothing unless this scope is open, so no other caller pays for it.
            with store.recording_written_objects() as written:
                if lane.watermark is None:
                    result = await _export_one_day(
                        session,
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        derive_tiers=derive_tiers,
                        statement_timeout_seconds=statement_timeout_seconds,
                    )
                else:
                    result = await _fill_static_day(
                        session,
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        today=today,
                        derive_tiers=derive_tiers,
                        statement_timeout_seconds=statement_timeout_seconds,
                    )
            # SILENT WHEN IT IS NOT WIRED. A run with no conditional storage has the same thing to
            # say about every day it writes, and saying it on each one would bury the notes that
            # describe what actually happened to that day.
            if not extend_availability or availability_storage is None:
                return result
            return await _extend_availability_for_result(
                session,
                store,
                lane,
                result,
                day=day,
                today=today,
                run_id=run_id,
                now=now,
                written=written,
                availability_storage=availability_storage,
                tally=availability_tally,
            )


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


async def repair_one_lane_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    lane_day_lock: LaneDayLock,
    today: date | None = None,
    vegetation_publication_barrier: VegetationPublicationBarrier = try_postgres_vegetation_publication_barrier,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    connection: DuckDBPyConnection | None = None,
    availability_storage: AvailabilityStorage | None = None,
) -> LadderRepairOutcome:
    """Re-derive one published day's coarse rungs from its base rung, then CLAIM the day for the index.

    THE CLAIM IS NOT OPTIONAL BOOKKEEPING. A repair rewrites three of the day's four rungs, so their
    receipts change; without a claim the day stays complete at every rung, is never re-selected, and
    sits outside the availability generation for good while the tick reports `repaired: 1`. See
    `AGENTS.md`, "A repaired day joins the index through a claim", for the lock, the read-back, the
    stranded-absence retraction and the session discipline.
    """
    barrier = (
        vegetation_publication_barrier
        if lane.slug == VEGETATION_PLANE_STREAM
        else unlocked_vegetation_publication_barrier
    )
    notes: list[str] = []
    try:
        async with barrier(session) as publication_granted:
            if publication_granted is False:
                return LadderRepairOutcome(
                    "contended",
                    0,
                    0,
                    0,
                    f"{day.isoformat()}: exact vegetation audit holds the publication barrier; derivation deferred",
                )
            async with lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
                if not granted:
                    return LadderRepairOutcome(
                        "contended",
                        0,
                        0,
                        0,
                        f"{day.isoformat()}: another run holds this lane-day, so its coarse rungs were left alone "
                        "rather than derived beside a base rung being rewritten; a later turn will take it",
                    )
                # ONE READ OF THE BASE RUNG SERVES BOTH HALVES: the derivation gets the rows and the
                # claim gets the key and digest of every part it cites. Letting the deriver read the
                # day again, or hashing the parts afterwards, would download it twice.
                #
                # ONLY WHEN A CLAIM IS OWED. With no availability storage wired there is nothing to
                # cite, so the read stays where it always was -- inside the deriver -- and this path
                # costs exactly what it did before.
                base = (
                    store.read_partition_with_receipts(lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day)
                    if availability_storage is not None
                    else None
                )
                with store.recording_written_objects() as written:
                    derived = _derive_repaired_rungs(
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        derive_tiers=derive_tiers,
                        connection=connection,
                        base_table=None if base is None else base.table,
                        notes=notes,
                    )
                # THE CLAIM IS WRITTEN UNDER THE SAME LOCK THE RUNGS WERE, exactly as the export path
                # extends availability inside its own lock. Written after the lock released, a
                # concurrent re-export could replace the base parts between the derivation and the
                # claim, and the claim would then name receipts that no longer exist -- a day that
                # spins on verification failure every tick instead of joining the index.
                extension = _claim_repaired_day(
                    store,
                    lane,
                    day=day,
                    run_id=run_id,
                    now=now,
                    today=today,
                    base=base,
                    written=written,
                    availability_storage=availability_storage,
                )
    except Exception as error:
        return LadderRepairOutcome(
            "raised",
            0,
            0,
            0,
            _append_note(
                "; ".join(notes) or None,
                f"{day.isoformat()}: the coarse rungs could not be derived from the published base rung, so this "
                f"day stays visible only at z{GAP_FILL_ZOOM_TIER}. A base rung that no longer matches its lane's "
                f"schema reads exactly like this and needs retracting and re-exporting, not re-deriving: "
                f"{type(error).__name__}: {error}",
            ),
        )
    finally:
        await _end_lane_day_transaction(session)
    notes.extend(derived.notes)
    if derived.tiers:
        rungs = ", ".join(
            f"z{report.tier} {report.row_count} rows in {report.part_count} part(s)" for report in derived.tiers
        )
        notes.append(f"{day.isoformat()}: derived {rungs}")
    if extension is not None:
        notes.append(extension.note)
    return LadderRepairOutcome(
        "written",
        derived.part_count,
        derived.row_count,
        derived.byte_count,
        "; ".join(notes) or None,
        emptied_tiers=tuple(derived.emptied),
        availability=extension,
    )


def _derive_repaired_rungs(  # noqa: PLR0913 - one coordinate of the day being re-derived per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver,
    connection: DuckDBPyConnection | None,
    base_table: pa.Table | None,
    notes: list[str],
) -> DerivationResult:
    """Derive the coarse rungs, healing a STRANDED coarse absence once before giving up on the day.

    The base rung demonstrably holds rows -- the ladder census selected this day because it does --
    so a coarse rung still claiming the day is governed as absent is the same stranded state
    `_finalize_written_day` retracts on the export path, and it is retracted the same way here. Left
    to the blanket guard it returned `raised` on every tick forever, because a direct-writer day
    never reaches the export path that knows how to heal it. Exactly ONE retry: a second conflict is
    a claim that could not be cleared, which is an admin's problem and not a loop's.
    """
    try:
        return derive_tiers(
            store,
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            run_id=run_id,
            now=now,
            connection=connection,
            base_table=base_table,
        )
    except GovernedAbsenceConflictError as stranded:
        notes.append(_retract_derived_absences(store, lane, day=day, conflict=stranded))
        return derive_tiers(
            store,
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            run_id=run_id,
            now=now,
            connection=connection,
            base_table=base_table,
        )


def _claim_repaired_day(  # noqa: PLR0913 - one coordinate of the repaired day per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date | None,
    base: PartitionRead | None,
    written: WrittenObjectLedger,
    availability_storage: AvailabilityStorage | None,
) -> AvailabilityExtensionOutcome | None:
    """Record that the repaired day owes a re-index, or say why it could not be claimed. Never raises."""
    if availability_storage is None or base is None:
        return None
    try:
        marker = store.read_completion_receipt(lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day)
        if marker is None:
            raise GapFillContractError(
                f"{lane.slug} {day.isoformat()}: the base rung holds parts but no completion marker, so the "
                "repaired day has no receipt an availability row could bind"
            )
        published_at = now()
        return claim_repaired_lane_day(
            store,
            lane=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            written=written,
            base_rung=RepairedBaseRung(
                rung=GAP_FILL_ZOOM_TIER,
                # SORTED BY OBJECT KEY, NOT BY PART INDEX. `read_partition_with_receipts` hands its
                # parts back in NUMERIC `part_index` order because that is the order the rows must be
                # concatenated in, while `availability_index._validate_data_receipt_collection`
                # demands LEXICOGRAPHIC key order. `paths.partition_path` mints unpadded names, so
                # the two agree only up to `part-9` and diverge the moment `part-10` exists.
                #
                # THE CLAIM ITSELF NEVER NOTICED. Nothing on this path validates receipt order -- not
                # `_RungObjects`, not `_rung_wire` -- so an unsorted claim was written, returned
                # `retry_owed`, and died one tick later in `_prepare_day`, where `TerminalEvidence`
                # refuses it. `_index_claimed_day` catches that `ValueError`, CLEARS the claim and
                # reports `evidence_unbuildable`, so the day was dropped ONCE and permanently: it is
                # complete at every rung, no census reselects it, and it never rejoins the index --
                # the "permanent and green" loss the claim exists to stop, arriving through the claim.
                # `objectstore.WrittenObjectLedger.parts_for` already sorts for the same reason.
                data_receipts=tuple(
                    EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
                    for part in sorted(base.parts, key=lambda read: read.relative_path)
                ),
                completion_receipt=EvidenceReceipt(key=marker.relative_path, sha256=marker.sha256),
                row_count=marker.completion.row_count,
                part_count=marker.completion.part_count,
            ),
            run_id=run_id,
            # THE LANE'S OWN CEILING, NOT THE DAY, for the reason `_extend_availability_for_result`
            # gives: a day that declared itself its own ceiling closes every lane at its last row.
            source_ceiling=max(allowed_source_ceiling(lane, today=today or day), day),
            published_at=published_at,
        )
    except Exception as error:  # the rungs are written; an unclaimable day may never undo that
        return AvailabilityExtensionOutcome(
            state="retry_claim_failed",
            lane_root=availability_lane_root(lane.slug, GAP_FILL_PARTITION_KIND),
            day=day,
            reason=(
                f"the coarse rungs were re-derived but no availability claim could be recorded, so the day's "
                f"new receipts stay outside the index: {type(error).__name__}: {error}"
            ),
            error_kind="repair_claim_unwritable",
        )


async def _end_lane_day_transaction(session: AsyncSession) -> None:
    """Close whatever transaction one repair opened, and never fail a walk over the attempt.

    Swallowed for the same reason `postgres_lane_day_lock` swallows a failed release: a rollback that
    cannot be issued means the connection is already gone, and the days after this one will say so
    through their own failures. Turning it into an exception here would take the whole walk down.
    """
    with suppress(Exception):
        await session.rollback()


async def _drain_owed_availability(  # noqa: PLR0913 - one coordinate of the tick being drained per arg
    session: AsyncSession,
    store: ObjectStore,
    progress: Sequence[_LaneProgress],
    *,
    lanes: Mapping[str, LaneRegistration],
    now: Callable[[], datetime],
    availability_storage: AvailabilityStorage,
) -> None:
    """Retry the availability step alone for every day a previous turn left owed. Never fails a tick."""
    for entry in progress:
        lane = lanes.get(entry.census.slug)
        if lane is None:
            continue
        try:
            outcomes = await retry_pending_availability(
                session,
                store,
                lane=lane.slug,
                kind=GAP_FILL_PARTITION_KIND,
                availability=availability_storage,
                now=now,
            )
        except Exception as error:  # an owed index entry may never stop a lane from exporting
            entry.detail = _append_note(entry.detail, f"owed availability retry raised: {error}")
            continue
        for outcome in outcomes:
            entry.availability.record(outcome)
            entry.detail = _append_note(entry.detail, outcome.note)


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
    lane_day_lock: LaneDayLock = postgres_lane_day_lock,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    extend_availability: bool = True,
    availability_storage: AvailabilityStorage | None = None,
) -> GapFillSummary:
    """Fill every lane's newest missing day, then its next-newest, until the wall-clock budget is spent.

    `time_budget_seconds` bounds when a new DAY is STARTED, never a day already in hand: a lane's own
    export finishes whatever it began, exactly as `jobs-pulse` bounds starting a new lane rather than
    killing one mid-slice. A lane that raises stops taking further turns -- its next day would almost
    certainly fail identically, and burning the rest of the tick rediscovering that costs every other
    lane its turn -- but every OTHER lane keeps going, and the raised lane's detail names the day.

    A LANE WITH NO MISSING DAYS MAY STILL OWE WORK. Once its export queue is empty it starts taking
    `ladder_repair_days` -- published days whose coarse rungs were never marked -- one per round, in
    the same round-robin and under the same budget. That work opens no database transaction beyond
    the advisory lock and reads no source table, so a tick whose exports are done spends its
    remaining budget making already-published days visible below z13 instead of returning early.
    """
    deadline = monotonic() + time_budget_seconds
    # Static lanes' source watermarks are read FIRST, before any listing: the census cannot classify
    # a reference set without knowing what version its source is on.
    watermarks = await resolve_lane_watermarks(session, store, lanes=lanes, today=today)
    census = build_gap_census(lanes, store, today=today, max_days_per_lane=max_days_per_lane, watermarks=watermarks)
    progress = [_seeded_progress(entry, today=today) for entry in census]
    by_slug = {lane.slug: lane for lane in lanes}
    if extend_availability and availability_storage is not None:
        # OWED AVAILABILITY IS DRAINED BEFORE ANY NEW DAY IS TAKEN. It is the cheap half of the
        # tick -- no export, no derivation, only the publication a previous turn could not finish --
        # and leaving it behind the walk would let a spent budget defer it again and again.
        await _drain_owed_availability(
            session,
            store,
            progress,
            lanes=by_slug,
            now=now,
            availability_storage=availability_storage,
        )

    budget_spent = False
    while not budget_spent:
        progressed = False
        for entry in progress:
            # A STOPPED LANE STILL REPAIRS ITS LADDER. `stopped` says this lane takes no more EXPORT
            # turns -- it is `current`, it has no settled window yet, or its census could not be
            # read -- and none of those say anything about rungs derived from base parts already in
            # the bucket. The three `static_lookup` lanes are exactly this case: they are `current`
            # almost every tick, so a repair queue gated on `stopped` would never once be drained for
            # them. A lane whose export RAISED is different and drops its repairs there, in
            # `_record_day_outcome`: something about that lane is wrong and the tick stops guessing.
            takes_export = bool(entry.pending) and not entry.stopped
            takes_repair = bool(entry.repairs)
            if not (takes_export or takes_repair):
                continue
            if monotonic() >= deadline:
                budget_spent = True
                break
            progressed = True
            started = monotonic()
            if takes_export:
                day = entry.pending.pop(0)
                outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
                    session,
                    store,
                    by_slug[entry.census.slug],
                    day=day,
                    run_id=run_id,
                    now=now,
                    today=today,
                    lane_day_lock=lane_day_lock,
                    derive_tiers=derive_tiers,
                    statement_timeout_seconds=statement_timeout_seconds,
                    extend_availability=extend_availability,
                    availability_storage=availability_storage,
                    availability_tally=entry.availability,
                )
                entry.parts += parts
                entry.rows += rows
                entry.written_bytes += written_bytes
                _record_day_outcome(entry, outcome, detail)
            else:
                # EXPORTS FIRST, REPAIRS AFTER, per lane and per round. A missing day is absent from
                # the map at every zoom; a ladder gap is a published day that is merely coarse-blind.
                # Both are drained by the same round-robin, so no lane's repairs can starve another
                # lane's exports.
                repair = await repair_one_lane_day(
                    session,
                    store,
                    by_slug[entry.census.slug],
                    day=entry.repairs.pop(0),
                    run_id=run_id,
                    now=now,
                    today=today,
                    lane_day_lock=lane_day_lock,
                    derive_tiers=derive_tiers,
                    availability_storage=availability_storage if extend_availability else None,
                )
                entry.parts += repair.parts
                entry.rows += repair.rows
                entry.written_bytes += repair.written_bytes
                _record_repair_outcome(entry, repair)
            entry.seconds += monotonic() - started
        if not progressed:
            break

    for entry in progress:
        # A blocked lane keeps its own outcome even with days left over: "one of your days needs an
        # admin" is the fact worth surfacing, and `budget_exhausted` reads as a healthy backlog.
        if (entry.pending or entry.repairs) and not entry.stopped and not entry.blocked:
            entry.outcome = "budget_exhausted"
    return GapFillSummary(lanes=tuple(entry.verdict() for entry in progress), run_id=run_id)
