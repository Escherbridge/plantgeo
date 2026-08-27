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

TWO SELECTIONS, ONE WALK
------------------------
`selection="missing"` is the drain above: days with no base rung at all, exported from Postgres.

`selection="ladder"` repairs the days the FUSION ARRIVED TOO LATE FOR. Every day written before the
coarse rungs shipped is base-complete and therefore invisible to `build_gap_census`, which walks
`GAP_FILL_ZOOM_TIER` and nothing else -- so nothing would ever bring it back, and it stays empty at
every zoom under 13 forever on a green tick. `build_ladder_census` is the second question that
census deliberately refuses to answer: which base-complete days are NOT complete at every rung.
Measured against production on 2026-08-25, that is ~1,040 lane-days across eleven lanes.

IT DERIVES FROM THE PUBLISHED BASE AND NEVER RE-QUERIES POSTGRES, because the base rung of those
days is already correct -- the rungs above it are simply absent. Re-exporting them would cost hours
of database time to rewrite bytes that are already right, and `signal` alone measured 151 s for one
cold day. If a base rung is genuinely stale (columns the current schema declares that the file does
not carry), `derive_tier` refuses and the day is reported `raised` rather than papered over: that
lane needs its base retracted and drained, which is `selection="missing"` after an admin retraction.

Both selections run through the SAME walk -- round-robin, oldest-first, the consecutive-failure
floor, the contended cap and the time budget -- because those are properties of walking a backlog,
not of what a day owes.

EVERY ENTRY POINT HERE IS REACHABLE FROM ONE VERB, and that is a correctness property rather than a
convenience. `cli.py`'s `parquet-drain --selection ladder` runs the ladder walk and
`--selection ladder --dry-run` prints `ladder_census_report`; `parquet-retire-legacy-layout` runs
the sweep below. A repair that only a Python REPL can start is a repair an operator reads about in
a commit message, does not run, and believes has happened: `--dry-run` would echo the BASE census,
show no ladder work, and the ~1,037 days would stay empty below z13 on exit code 0.

THE PRE-ZOOM LAYOUT IS RETIRED HERE TOO. `retire_legacy_layout_objects` removes the objects written
before the zoom axis existed, which sit one path segment shallower and are therefore invisible to
every reader, every census and every prune in this service: `try_parse_partition_path` returns
`None` for them, so `prune_surplus_parts` skips them and `list_partition_objects` filters them out.
Measured 2026-08-25: 2,274 keys, 645.7 MB, across twelve layers. Nothing reads them and nothing
would ever collect them.
"""

from __future__ import annotations

import re
import time
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.db.vegetation_publication import (
    try_postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
)
from agri_data_service.foundation.parquet.paths import (
    COVERED_PARTITION_STATUSES,
    PARTITION_KINDS,
    completed_partition_days,
    layer_prefix,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
    validate_layer_slug,
    validate_partition_kind,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_fill import (
    FAILING_LANE_OUTCOMES,
    GAP_FILL_PARTITION_KIND,
    # Private to `gap_fill`, and imported rather than respelt on purpose: it is the ONE definition
    # of a lane-day's advisory-lock identity, and `_derive_one_day` must take exactly the lock
    # `fill_one_lane_day` takes or the two writers do not exclude each other at all.
    _lane_day_lock_key,
    build_gap_census,
    fill_one_lane_day,
    postgres_lane_day_lock,
    resolve_lane_watermarks,
)
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS, derivation_session
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from contextlib import AbstractContextManager

    from duckdb import DuckDBPyConnection
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.gap_fill import (
        LaneDayLock,
        LaneDayOutcome,
        LaneGapCensus,
        TierDeriver,
    )
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ObjectStoreBackend

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

# The per-statement budget a DRAIN uses, five times the hourly tick's.
#
# The cron's 120 s is right FOR THE CRON: its whole tick is 600 s, so a statement running longer
# than a fifth of it starves every other lane of a turn. A drain has no tick to protect and a much
# worse worst case -- `signal` reads an 11 GB heap one cell batch at a time (RUNBOOK 0.22.5), and a
# cold day of it measured 151 s against production on 2026-08-24, which the cron's ceiling CANCELS
# outright. Sharing one number means either the cron overruns or the drain can never finish that
# lane; these are two jobs with two budgets.
DRAIN_STATEMENT_TIMEOUT_SECONDS: Final = 600

# What a lane-day OWES this run. See the module docstring's "Two selections, one walk".
DrainSelection = Literal["missing", "ladder"]
DEFAULT_DRAIN_SELECTION: Final[DrainSelection] = "missing"

# The pre-zoom key layout, transcribed from `foundation/parquet/paths.py` as it stood at commit
# 68da7af^ -- the last commit before the zoom axis was inserted. It differs from today's layout by
# exactly one path segment: `/zoom=NN` between `kind=` and `year=`, and nothing else.
#
# TRANSCRIBED HERE RATHER THAN LEFT IN `paths.py` because it is a RETIRED layout. Keeping a pattern
# for it beside the live ones would oblige every future reader of that module to work out which of
# the two it must not use, and the only code that ever needs it again is the sweep that deletes it.
LEGACY_LAYOUT_PATTERN: Final = re.compile(
    r"^layer=(?P<layer>[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"/kind=(?P<kind>observed|forecast)"
    r"/year=(?P<year>\d{4})"
    r"/month=(?P<month>\d{2})"
    r"/day=(?P<day>\d{2})"
    r"/(?P<file>part-\d+\.parquet|absent\.json|_complete\.json)$"
)

# How many keys one layer's legacy sweep will look at before it reports `truncated` and stops.
# Generous against the 2,274 measured across the whole bucket on 2026-08-25, and finite so that a
# mis-scoped prefix cannot page through a bucket forever.
MAX_LEGACY_KEYS_PER_LAYER: Final = 100_000


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


@dataclass(frozen=True, slots=True)
class _DayResult:
    """What one lane-day came to, whichever selection ran it.

    A NAMED SHAPE RATHER THAN `fill_one_lane_day`'s FIVE-TUPLE because the ladder selection has a
    sixth fact the export selection cannot have: WHICH rungs derived to nothing. A day is not the
    unit of emptiness -- see `DerivationResult.emptied` -- so the walk cannot infer it from the
    day's part count, and a tuple that means different things at different lengths is worse than
    one dataclass with a documented default.
    """

    outcome: LaneDayOutcome
    parts: int
    rows: int
    written_bytes: int
    detail: str | None
    # Ladder selection only. The export selection leaves this empty even when a rung of the day it
    # exported emptied: that path's ladder is `gap_fill`'s to report, and its census never re-selects
    # a base-complete day, so nothing here would loop on it.
    emptied_tiers: tuple[ZoomTier, ...] = ()


@dataclass(frozen=True, slots=True)
class LaneLadderCensus:
    """One lane's ZOOM-LADDER coverage: which published days are not complete at every rung.

    A DIFFERENT QUESTION FROM `LaneGapCensus`, AND IT MUST STAY ONE. That census answers "which days
    have no base rung", over `GAP_FILL_ZOOM_TIER` alone, and says so in its own docstring: "Auditing
    a derived tier is a different question with a different mechanism, and it must not borrow this
    answer." This is that mechanism. Blending the two would let a lane read as fully drained while
    every zoom under 13 was empty -- which is exactly the state eleven lanes were in on 2026-08-25.
    """

    slug: str
    # Days whose BASE rung holds parts and asserts it finished. The population the ladder is over.
    base_day_count: int
    ladder_complete_day_count: int
    # Base-complete days missing at least one coarse rung: the work a `ladder` drain walks.
    incomplete_days: tuple[date, ...]
    # Of those, the ones already carrying SOME coarse rung -- a fusion interrupted part-way, rather
    # than a day written before the fusion existed. Reported apart because they are different
    # incidents: one is expected backlog, the other is a run dying mid-ladder and worth chasing.
    partial_ladder_days: tuple[date, ...]
    # Days the base rung governs as ABSENT. They can never carry a coarse rung, and this driver
    # cannot give them one: `write_absence` is a governed statement per tier and minting three more
    # of them per day from a repair sweep is an admin decision, not a drain's. Counted so the gap is
    # visible rather than silently folded into "not complete".
    base_absent_days: int
    # Days holding both parts and a governed absence: an admin-only anomaly, never touched here.
    base_conflict_days: int
    truncated: bool = False
    error: str | None = None

    def to_report(self) -> dict[str, object]:
        """Render the row `--dry-run` echoes for a ladder repair: what WOULD be derived, per lane."""
        return {
            "lane": self.slug,
            "base_days": self.base_day_count,
            "ladder_complete_days": self.ladder_complete_day_count,
            "incomplete_ladder_days": len(self.incomplete_days),
            "partial_ladder_days": len(self.partial_ladder_days),
            "base_absent_days": self.base_absent_days,
            "base_conflict_days": self.base_conflict_days,
            "oldest_incomplete_day": None if not self.incomplete_days else self.incomplete_days[0].isoformat(),
            "newest_incomplete_day": None if not self.incomplete_days else self.incomplete_days[-1].isoformat(),
            "truncated": self.truncated,
            "error": self.error,
        }


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
    # Ladder selection only: days whose every coarse rung derived to NO ROWS, so the day now carries
    # no derived completion marker and the next ladder census will select it again.
    #
    # THIS IS THE ONE PLACE THE BUCKET-AS-CHECKPOINT RULE DOES NOT SELF-TERMINATE, so it is reported
    # rather than left to be rediscovered. Only a lane with nullable coordinates can reach it --
    # `water-gauges` and `sensors`, whose rows may have no location at all -- and for such a day the
    # rungs are honestly empty, which is indistinguishable from never-derived through a listing.
    emptied: list[date] = field(default_factory=list)

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
            # Days a `ladder` selection will keep re-selecting because every rung of them is
            # honestly empty. Surfaced at the top level so it cannot be mistaken for backlog.
            "emptied_ladders": sum(len(lane.emptied) for lane in self.lanes),
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
                    "emptied_ladders": len(lane.emptied),
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


def _days_named_by(keys: Iterable[str], *, layer: str) -> set[date]:
    """Return every day `keys` mentions for `layer` at any tier, through the three canonical parsers.

    Parsed rather than string-sliced so a key from another layer, another kind or the retired
    pre-zoom layout cannot contribute a day -- the last of which would otherwise silently widen the
    window a ladder census walks by years.
    """
    days: set[date] = set()
    for key in keys:
        for parse in (try_parse_partition_path, try_parse_absence_marker_path, try_parse_completion_marker_path):
            parsed = parse(key)
            if parsed is not None and parsed.layer == layer:
                days.add(parsed.day)
                break
    return days


def _servable_days(keys: Sequence[str], *, layer: str, kind: PartitionKind) -> set[date]:
    """Days whose BASE rung a reader may actually answer from: `data` or `absent`, nothing else.

    NOT `_days_named_by`, AND THE DIFFERENCE DECIDES A DELETE. "Mentioned by some key" is a much
    weaker claim than "published": a day holding a completion marker whose parts were deleted out
    from under it is `missing`, and a day holding parts with no marker is `incomplete` -- both are
    named by a key, NEITHER is in `COVERED_PARTITION_STATUSES`, and no reader in this service serves
    either. Calling such a day "already published in the zoom layout" is how the legacy sweep would
    delete the only surviving copy of it while reporting the deletion as superseded.

    That is not a hypothetical under concurrency: `write_partition` clears the completion marker as
    it uploads `part-0`, so any day the hourly cron is mid-re-export reads `incomplete` for the
    length of that export -- inside the window of a sweep that listed once at its start.
    """
    named = _days_named_by(keys, layer=layer)
    if not named:
        return set()
    statuses = partition_day_statuses(
        layer=layer,
        kind=kind,
        zoom=BASE_ZOOM_TIER,
        first_day=min(named),
        last_day=max(named),
        keys=keys,
    )
    return {day for day, status in statuses.items() if status in COVERED_PARTITION_STATUSES}


def build_lane_ladder_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    tiers: Sequence[ZoomTier] = DERIVED_ZOOM_TIERS,
    max_days_per_lane: int | None = None,
) -> LaneLadderCensus:
    """Classify one lane's published days by whether EVERY coarse rung asserts it finished.

    THE WINDOW COMES FROM THE LISTING, NOT FROM `lane_window`, and that is what lets one function
    serve all three lane natures. A `static_lookup` lane has no calendar window at all -- its
    partition day is a version stamp -- so asking `lane_window` for one raises. The days a lane has
    actually published are a fact about the bucket, and the bucket is what a repair sweep is over.

    A rung counts as finished only when it carries its own completion marker, for the same reason
    `completed_partition_days` is the shared primitive one contract down: parts without a marker are
    a derivation that stopped part-way, and re-deriving one is exactly the work never deriving it
    was. Only the BASE rung's day statuses are consulted for the population, because a day with no
    base rows has nothing a coarse rung could be derived FROM.

    THE BASE POPULATION IS STRICTER THAN THE RUNG TEST -- parts AND a marker for the base, a marker
    alone for a rung -- and that asymmetry is deliberate rather than an oversight. A rung marked
    while holding no parts is unreachable through the two operations that write one: `_write_tier`
    puts its parts before its marker, and `_retract_tier` clears the marker before deleting the
    parts. Spelling a stricter rule here would be a SECOND definition of "finished", and the one
    that eventually drifted would decide a day was done when the shared primitive said it was not.

    AN EMPTY `tiers` IS REFUSED RATHER THAN VACUOUSLY COMPLETE. The rung loop below intersects, so
    with no rungs to intersect over every published day reads as ladder-complete and the census
    reports a green ladder for a warehouse that has none. Today `DERIVED_ZOOM_TIERS` is never empty;
    it becomes empty the day `ZOOM_TIERS` is reduced to one entry, and that change must fail loudly
    rather than silently report every lane finished.
    """
    if not tiers:
        raise ValueError(
            f"a ladder census over no rungs would report every published day of {lane.slug!r} complete; ask for "
            f"{tuple(DERIVED_ZOOM_TIERS)} or a subset of it"
        )
    kind = GAP_FILL_PARTITION_KIND
    try:
        base_keys = store.list_partition_keys(lane.slug, kind, BASE_ZOOM_TIER)
        published = _days_named_by(base_keys, layer=lane.slug)
        if not published:
            return LaneLadderCensus(
                slug=lane.slug,
                base_day_count=0,
                ladder_complete_day_count=0,
                incomplete_days=(),
                partial_ladder_days=(),
                base_absent_days=0,
                base_conflict_days=0,
            )
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=kind,
            zoom=BASE_ZOOM_TIER,
            first_day=min(published),
            last_day=max(published),
            keys=base_keys,
        )
        # `completed_partition_days` rather than a local rule: it is the one primitive every reader
        # of this warehouse shares for "did this day's export assert that it finished", and a
        # second spelling of it here is how one of the two eventually gets it wrong.
        marked_by_tier = {
            tier: completed_partition_days(
                store.list_partition_keys(lane.slug, kind, tier), layer=lane.slug, kind=kind, zoom=tier
            )
            for tier in tiers
        }
    except Exception as error:  # per-lane isolation: an unreadable listing must never read as "no gaps"
        return LaneLadderCensus(
            slug=lane.slug,
            base_day_count=0,
            ladder_complete_day_count=0,
            incomplete_days=(),
            partial_ladder_days=(),
            base_absent_days=0,
            base_conflict_days=0,
            error=f"listing {lane.slug!r} for a ladder census failed: {type(error).__name__}: {error}",
        )
    base_data = {day for day, status in statuses.items() if status == "data"}
    complete = set(base_data)
    partial: set[date] = set()
    for marked in marked_by_tier.values():
        complete &= marked
        partial |= base_data & marked
    incomplete = sorted(base_data - complete)
    # BOTH SETS ARE TRUNCATED TOGETHER, or the report contradicts itself: `partial_ladder_days` is a
    # SUBSET of `incomplete_days`, and truncating only the superset published `incomplete=1,
    # partial=3` -- a subset larger than the set containing it. `partial` is reported against the
    # days this census actually selected, so the two always describe the same walk.
    selected = incomplete if max_days_per_lane is None else incomplete[:max_days_per_lane]
    return LaneLadderCensus(
        slug=lane.slug,
        base_day_count=len(base_data),
        ladder_complete_day_count=len(complete),
        incomplete_days=tuple(selected),
        partial_ladder_days=tuple(day for day in selected if day in partial),
        base_absent_days=sum(1 for status in statuses.values() if status == "absent"),
        base_conflict_days=sum(1 for status in statuses.values() if status == "conflict"),
        truncated=max_days_per_lane is not None and len(incomplete) > max_days_per_lane,
    )


def build_ladder_census(
    lanes: Sequence[LaneRegistration],
    store: ObjectStore,
    *,
    tiers: Sequence[ZoomTier] = DERIVED_ZOOM_TIERS,
    max_days_per_lane: int | None = None,
) -> tuple[LaneLadderCensus, ...]:
    """Take a ladder census of every requested lane, isolating one lane's listing failure from the rest."""
    return tuple(
        build_lane_ladder_census(lane, store, tiers=tiers, max_days_per_lane=max_days_per_lane) for lane in lanes
    )


def ladder_census_report(census: Sequence[LaneLadderCensus]) -> dict[str, object]:
    """Render a ladder `--dry-run`: which lanes owe coarse rungs, and how many days each owes."""
    return {
        "lanes": [entry.to_report() for entry in census],
        "lane_count": len(census),
        "incomplete_ladder_days": sum(len(entry.incomplete_days) for entry in census),
        "lanes_with_incomplete_ladders": [entry.slug for entry in census if entry.incomplete_days],
        # Named, not just summed: a day carrying SOME rungs is a run that died mid-ladder, which
        # reads as ordinary backlog in a total and is a different incident.
        "lanes_with_partial_ladders": [entry.slug for entry in census if entry.partial_ladder_days],
        # Days no coarse rung can ever cover through this driver. A reader at z9 on such a day finds
        # nothing and cannot tell "deliberately empty" from "never written" -- see `LaneLadderCensus`.
        "base_absent_days": sum(entry.base_absent_days for entry in census),
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
    }


def plan_ladder_drain(census: Sequence[LaneLadderCensus]) -> tuple[DrainLaneProgress, ...]:
    """Turn a ladder census into the walk, oldest day first, exactly as `plan_drain` does."""
    return tuple(
        DrainLaneProgress(
            slug=entry.slug,
            considered=len(entry.incomplete_days),
            pending=sorted(entry.incomplete_days),
            stopped_reason=entry.error,
        )
        for entry in census
    )


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
    statement_timeout_seconds: int = DRAIN_STATEMENT_TIMEOUT_SECONDS,
    selection: DrainSelection = DEFAULT_DRAIN_SELECTION,
    on_day: Callable[[str, date, str, str | None], None] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = _utc_now,
    lane_day_lock: LaneDayLock = postgres_lane_day_lock,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
) -> DrainSummary:
    """Walk every lane's outstanding history and write it, round-robin, until nothing is left.

    `selection` decides WHAT a day owes, never how the walk behaves -- see "Two selections, one
    walk" in the module docstring. `missing` exports days with no base rung from Postgres; `ladder`
    derives the coarse rungs of days whose base rung is already published, and touches no lane
    adapter and no source table at all.

    `time_budget_seconds` is OPTIONAL here and unset by default, which is the whole point of the
    drain: the cron's 600-second ceiling is what made this job necessary. When it is set it bounds
    when a new DAY is STARTED, never a day already in hand -- the same rule `run_gap_fill` applies,
    so a bounded drain still never abandons a half-written day.

    `on_day` is called after every finished day so a CLI can stream progress across a run measured
    in hours. It is deliberately a callback rather than a logger: this module has no opinion about
    where a human is watching from, and a drain that printed would be untestable.

    ONE DUCKDB SESSION SERVES THE WHOLE LADDER WALK. A geometry lane opens a session and pays
    `LOAD spatial` PER RUNG otherwise -- three per day, ~3,000 across the measured 1,037-day repair
    -- and `derivation_session` exists to be reused exactly this way. The export selection opens
    none here: its rungs are derived inside `gap_fill`, which owns that path's session.
    """
    deadline = None if time_budget_seconds is None else monotonic() + time_budget_seconds
    started = monotonic()
    if selection == "ladder":
        # NO WATERMARK READ. A watermark answers "which version does this static lane owe", which is
        # a question about exporting a base rung; a ladder repair exports nothing and would be
        # opening database connections for an answer it never consults.
        progress = plan_ladder_drain(build_ladder_census(lanes, store, max_days_per_lane=max_days_per_lane))
        derivation_scope: AbstractContextManager[DuckDBPyConnection | None] = derivation_session()
    else:
        watermarks = await resolve_lane_watermarks(session, store, lanes=lanes, today=today)
        census = build_gap_census(lanes, store, today=today, max_days_per_lane=max_days_per_lane, watermarks=watermarks)
        progress = plan_drain(census)
        derivation_scope = nullcontext()
    by_slug = {lane.slug: lane for lane in lanes}

    with derivation_scope as connection:
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
                        statement_timeout_seconds=statement_timeout_seconds,
                        selection=selection,
                        derive_tiers=derive_tiers,
                        connection=connection,
                        on_day=on_day,
                    )
            if not advanced:
                break
    return DrainSummary(run_id=run_id, lanes=progress, seconds=monotonic() - started)


async def _derive_one_day(  # noqa: PLR0913 - one coordinate of the day being derived per arg
    session: AsyncSession,
    store: ObjectStore,
    registration: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    lane_day_lock: LaneDayLock,
    derive_tiers: TierDeriver,
    connection: DuckDBPyConnection | None = None,
) -> _DayResult:
    """Write one already-published day's coarse rungs from its base rung, under the lane-day lock.

    THE SAME LOCK KEY AS THE EXPORT PATH, IMPORTED RATHER THAN RESPELT. `fill_one_lane_day` holds
    `_lane_day_lock_key(lane, day)` across export, prune, rungs and marker precisely so no second
    writer can interleave with it -- and this IS a second writer of three of those four objects. A
    repair sweep that took a different key, or no key, would race the hourly cron on exactly the
    days it is repairing: the cron's derivation and this one would both prune and both mark, and
    the loser's `part_count` would describe a rung the winner had already replaced.

    NO STATEMENT TIMEOUT AND NO EXPORT. The only statement this path issues is the advisory lock
    itself; the base rows come from the object store. That is the whole reason this selection is
    cheap enough to run over a thousand days: `signal` measured 151 s for ONE cold day of its
    Postgres export, and none of those seconds buy anything when the base rung is already correct.

    A FAILED DERIVATION CHANGES NOTHING, which is what makes the repair safe to re-run. The base
    rung and its marker are never touched here, so a day that raises is simply still ladder-
    incomplete and the next census selects it again.

    THE LOCK ITSELF IS INSIDE THE GUARD, not only the derivation, and that is the difference between
    a recorded failure and a lost run. `pg_try_advisory_lock` is a real statement against a real
    session: a connection reset, a statement timeout or a session already in a failed transaction
    raises THERE, before the derivation is reached, and an unguarded `async with` would carry that
    out of the whole walk -- every lane's tally, not just this day's, and the module docstring
    promises the opposite.

    THE SESSION IS ROLLED BACK ON EVERY PATH, exactly as `_export_one_day` does one contract over.
    SQLAlchemy 2.0's `autobegin` means the lock statement OPENS a transaction that this path would
    otherwise never end: over a multi-hour repair that is one backend idle-in-transaction from the
    first day to the last, which `idle_in_transaction_session_timeout` eventually terminates
    mid-run. The advisory lock is session-scoped, so the rollback does not release it.
    """
    barrier = (
        try_postgres_vegetation_publication_barrier
        if registration.slug == VEGETATION_PLANE_STREAM
        else unlocked_vegetation_publication_barrier
    )
    try:
        async with barrier(session) as publication_granted:
            if publication_granted is False:
                return _DayResult(
                    "contended",
                    0,
                    0,
                    0,
                    f"{day.isoformat()}: exact vegetation audit holds the publication barrier; derivation deferred",
                )
            async with lane_day_lock(session, _lane_day_lock_key(registration, day)) as granted:
                if not granted:
                    return _DayResult(
                        "contended",
                        0,
                        0,
                        0,
                        f"{day.isoformat()}: another run holds this lane-day, so its coarse rungs were left alone "
                        "rather than derived beside a base rung being rewritten; a later turn will take it",
                    )
                derived = derive_tiers(
                    store,
                    layer=registration.slug,
                    kind=GAP_FILL_PARTITION_KIND,
                    day=day,
                    run_id=run_id,
                    now=now,
                    connection=connection,
                )
    except Exception as error:
        return _DayResult(
            "raised",
            0,
            0,
            0,
            f"{day.isoformat()}: the coarse rungs could not be derived from the published base rung, so this "
            f"day stays visible only at z{BASE_ZOOM_TIER}. A base rung that no longer matches its lane's schema "
            f"reads exactly like this and needs retracting and re-exporting, not re-deriving: "
            f"{type(error).__name__}: {error}",
        )
    finally:
        await _end_lane_day_transaction(session)
    notes = list(derived.notes)
    if derived.tiers:
        rungs = ", ".join(
            f"z{report.tier} {report.row_count} rows in {report.part_count} part(s)" for report in derived.tiers
        )
        notes.append(f"{day.isoformat()}: derived {rungs}")
    return _DayResult(
        "written",
        derived.part_count,
        derived.row_count,
        derived.byte_count,
        "; ".join(notes) or None,
        emptied_tiers=tuple(derived.emptied),
    )


async def _end_lane_day_transaction(session: AsyncSession) -> None:
    """Close whatever transaction this lane-day opened, and never fail a walk over the attempt.

    Swallowed for the same reason `postgres_lane_day_lock` swallows a failed release: a rollback
    that cannot be issued means the connection is already gone, and the days after this one will
    say so through the consecutive-failure floor. Turning it into an exception here would take the
    whole walk down, which is the failure this guard exists to prevent.
    """
    with suppress(Exception):
        await session.rollback()


async def _run_one_day(  # noqa: PLR0913 - one coordinate of the day being run per arg
    session: AsyncSession,
    store: ObjectStore,
    registration: LaneRegistration,
    *,
    day: date,
    today: date,
    run_id: str,
    now: Callable[[], datetime],
    lane_day_lock: LaneDayLock,
    statement_timeout_seconds: int,
    selection: DrainSelection,
    derive_tiers: TierDeriver,
    connection: DuckDBPyConnection | None,
) -> _DayResult:
    """Run one day through the path its selection names: a Postgres export, or a derivation alone.

    THE ONLY PLACE THE TWO SELECTIONS DIVERGE. Everything around it -- the round-robin, the
    oldest-first order, the consecutive-failure floor, the contended cap and the budget -- is a
    property of walking a backlog and must not learn which kind of backlog it is walking.
    """
    if selection == "ladder":
        return await _derive_one_day(
            session,
            store,
            registration,
            day=day,
            run_id=run_id,
            now=now,
            lane_day_lock=lane_day_lock,
            derive_tiers=derive_tiers,
            connection=connection,
        )
    outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
        session,
        store,
        registration,
        day=day,
        run_id=run_id,
        now=now,
        today=today,
        lane_day_lock=lane_day_lock,
        statement_timeout_seconds=statement_timeout_seconds,
    )
    return _DayResult(outcome, parts, rows, written_bytes, detail)


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
    statement_timeout_seconds: int,
    selection: DrainSelection,
    derive_tiers: TierDeriver,
    connection: DuckDBPyConnection | None,
    on_day: Callable[[str, date, str, str | None], None] | None,
) -> None:
    """Fill one day through the cron's own per-day path, then fold the outcome into the lane tally.

    NOTHING RAISES OUT OF HERE. The module docstring's promise -- "a failure here is RECORDED and
    the walk continues" -- is only true if the walk holds a guard, and one day's escape costs every
    lane its whole tally, not merely its own. Each selection's own path already converts what it can
    (`_export_one_day` catches its lane adapter, `_derive_one_day` catches its lock and its
    derivation); this is the floor under both, for the statements each issues before its own try.
    """
    day = lane.pending.pop(0)
    began = monotonic()
    try:
        result = await _run_one_day(
            session,
            store,
            registration,
            day=day,
            today=today,
            run_id=run_id,
            now=now,
            lane_day_lock=lane_day_lock,
            statement_timeout_seconds=statement_timeout_seconds,
            selection=selection,
            derive_tiers=derive_tiers,
            connection=connection,
        )
    except Exception as error:
        await _end_lane_day_transaction(session)
        result = _DayResult(
            "raised",
            0,
            0,
            0,
            f"{day.isoformat()}: the {selection} path raised out of its own handling, so nothing about this day is "
            f"known to have been written; it stays selectable and the next run takes it: "
            f"{type(error).__name__}: {error}",
        )
    outcome, parts, detail = result.outcome, result.parts, result.detail
    # A LADDER DAY IS RE-SELECTED FOREVER IF *ANY* RUNG EMPTIED, not only if the whole day did: an
    # emptied rung is retracted and carries no completion marker, and the census intersects markers
    # across every rung. `parts == 0` is kept beside it for a deriver that reports no rungs at all.
    if selection == "ladder" and outcome == "written" and (result.emptied_tiers or parts == 0):
        lane.emptied.append(day)
    lane.seconds += monotonic() - began
    lane.parts += parts
    lane.rows += result.rows
    lane.written_bytes += result.written_bytes
    # `days_written` COUNTS DAYS THAT WROTE, hence the part test beside the outcome. A ladder day
    # whose every rung was honestly empty comes back `written` with nothing behind it, and counting
    # it would report progress the bucket does not hold; it lands in `emptied` above instead, and in
    # no other tally. The export selection cannot reach that state -- `_finalize_written_day` returns
    # `raised` for a day that wrote no parts.
    if outcome == "written" and parts > 0:
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


# --- Retiring the pre-zoom layout ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyLayoutObject:
    """One object written before the zoom axis existed: where it is, and which day it belonged to."""

    key: str
    relative_path: str
    layer: str
    kind: PartitionKind
    day: date
    file_name: str
    byte_count: int | None = None


@dataclass(frozen=True, slots=True)
class LegacyLayoutRetirement:
    """One layer's pre-zoom residue: what was found, what was superseded, and what was removed."""

    layer: str
    # Legacy objects whose day the zoom layout holds at the base rung in a SERVABLE state -- `data`
    # or `absent`, per `COVERED_PARTITION_STATUSES`. Removing one of these can lose nothing: a newer
    # copy of that day is published where readers actually look. A day that is merely MENTIONED
    # there -- an `incomplete` half-export, or a completion marker whose parts are gone -- is not
    # superseded by it and is classified `orphaned` instead. See `_servable_days`.
    superseded: tuple[LegacyLayoutObject, ...]
    # Legacy objects for a day the zoom layout cannot serve. Removing one DOES drop the only usable
    # copy in this bucket -- recoverable, because the ordinary `missing` drain would re-export the
    # day from Postgres, but not free. Never removed unless a caller asks for it by name.
    orphaned: tuple[LegacyLayoutObject, ...]
    removed: tuple[str, ...]
    failures: tuple[str, ...]
    truncated: bool = False
    error: str | None = None

    @property
    def byte_count(self) -> int:
        """Bytes held by every legacy object found, superseded and orphaned together."""
        return sum(found.byte_count or 0 for found in (*self.superseded, *self.orphaned))

    def to_report(self) -> dict[str, object]:
        """Render the row a legacy sweep echoes, with a bounded sample of the orphans it refused."""
        return {
            "layer": self.layer,
            "superseded": len(self.superseded),
            "orphaned": len(self.orphaned),
            "removed": len(self.removed),
            "failures": len(self.failures),
            "megabytes": round(self.byte_count / 1_048_576, 1),
            "orphan_sample": [found.relative_path for found in self.orphaned[:10]],
            "truncated": self.truncated,
            "error": self.error,
        }


def _legacy_layout_object(store: ObjectStore, key: str) -> LegacyLayoutObject | None:
    """Return the legacy object `key` names, or `None` when it is anything else at all.

    THE THREE LIVE PARSERS ARE CONSULTED FIRST AND THEIR VERDICT IS FINAL. This function's output
    feeds a delete, so the load-bearing property is not "does it look legacy" but "is it certainly
    not current": a key that any live parser accepts is a member of the zoom layout and is refused
    here even if the legacy pattern would also have matched it. Only then is the retired pattern
    applied, which is what keeps this from ever deleting a published rung.
    """
    relative_path = store.relative_key(key)
    if (
        try_parse_partition_path(relative_path) is not None
        or try_parse_absence_marker_path(relative_path) is not None
        or try_parse_completion_marker_path(relative_path) is not None
    ):
        return None
    matched = LEGACY_LAYOUT_PATTERN.match(relative_path.replace("\\", "/"))
    if matched is None:
        return None
    try:
        day = date(int(matched["year"]), int(matched["month"]), int(matched["day"]))
    except ValueError:  # 2025-02-30 in a key is not a day this sweep may claim to understand
        return None
    return LegacyLayoutObject(
        key=key,
        relative_path=relative_path,
        layer=matched["layer"],
        kind=validate_partition_kind(matched["kind"]),
        day=day,
        file_name=matched["file"],
    )


def retire_legacy_layout_objects(
    store: ObjectStore,
    backend: ObjectStoreBackend,
    *,
    layers: Sequence[str],
    dry_run: bool = True,
    include_orphaned: bool = False,
) -> tuple[LegacyLayoutRetirement, ...]:
    """Find, and optionally delete, the objects written before the zoom axis existed.

    `backend` IS A SEPARATE ARGUMENT AND CANNOT BE TAKEN FROM `store`, which is the whole reason
    this signature looks the way it does. `ObjectStore.list_partition_objects` FILTERS OUT every key
    its three parsers reject, so the legacy layout is invisible through it -- the objects cannot even
    be LISTED, let alone deleted, through the public store. `ObjectStoreBackend` is a public Protocol
    and the caller already holds the instance it built its store from; passing it here keeps the one
    unfiltered listing in this repo visible in a signature instead of hidden behind an attribute.

    DRY RUN BY DEFAULT. Deleting from the record of truth is not something a caller should be able
    to do by forgetting an argument.

    ORPHANS ARE REPORTED AND KEPT unless `include_orphaned` says otherwise: a legacy day the zoom
    layout cannot serve is the only usable copy in this bucket, and while it is recoverable from
    Postgres by the ordinary drain, a sweep whose stated job is removing superseded bytes should not
    quietly decide to re-export somebody's day as a side effect.

    SUPERSEDED MEANS SERVABLE, NOT MENTIONED -- `_servable_days`, not `_days_named_by`. The listing
    is taken ONCE at the start of a layer, and the hourly cron clears a day's completion marker as
    it uploads `part-0`, so a day being re-exported during this sweep reads `incomplete` in that
    snapshot. Under the weaker rule it would be classified superseded and its legacy copy deleted
    while the only zoom-layout copy was half-written.
    """
    return tuple(
        _retire_one_layer(store, backend, layer=layer, dry_run=dry_run, include_orphaned=include_orphaned)
        for layer in layers
    )


def _retire_one_layer(
    store: ObjectStore,
    backend: ObjectStoreBackend,
    *,
    layer: str,
    dry_run: bool,
    include_orphaned: bool,
) -> LegacyLayoutRetirement:
    """Sweep one layer's prefix, isolating its failure from every other layer's."""
    validate_layer_slug(layer)
    try:
        # PER KIND, because the retired layout carried both and `kind=forecast` days are a different
        # population entirely: measuring a legacy forecast object against the observed days would
        # call it orphaned whenever the observed lane happened not to hold that calendar day.
        published = {
            partition_kind: _servable_days(
                store.list_partition_keys(layer, partition_kind, BASE_ZOOM_TIER), layer=layer, kind=partition_kind
            )
            for partition_kind in PARTITION_KINDS
        }
        found: list[LegacyLayoutObject] = []
        truncated = False
        for listed in backend.list_objects(store.key_for(layer_prefix(layer))):
            if len(found) >= MAX_LEGACY_KEYS_PER_LAYER:
                truncated = True
                break
            legacy = _legacy_layout_object(store, listed.key)
            if legacy is None or legacy.layer != layer:
                continue
            # Sized only once it is CERTAINLY legacy. A `size_of` per listed key would put a HEAD
            # against every object in the layer -- tens of thousands of them for `fire-detections`
            # -- to price a few hundred.
            found.append(replace(legacy, byte_count=backend.size_of(legacy.key)))
    except Exception as error:  # per-layer isolation: one unreadable prefix must not end the sweep
        return LegacyLayoutRetirement(
            layer=layer,
            superseded=(),
            orphaned=(),
            removed=(),
            failures=(),
            error=f"sweeping {layer!r} for pre-zoom objects failed: {type(error).__name__}: {error}",
        )
    superseded = tuple(legacy for legacy in found if legacy.day in published[legacy.kind])
    orphaned = tuple(legacy for legacy in found if legacy.day not in published[legacy.kind])
    if dry_run:
        return LegacyLayoutRetirement(
            layer=layer, superseded=superseded, orphaned=orphaned, removed=(), failures=(), truncated=truncated
        )
    removed: list[str] = []
    failures: list[str] = []
    for legacy in (*superseded, *(orphaned if include_orphaned else ())):
        try:
            backend.delete(legacy.key)
        except Exception as error:
            failures.append(f"{legacy.relative_path}: {type(error).__name__}: {error}")
        else:
            removed.append(legacy.relative_path)
    return LegacyLayoutRetirement(
        layer=layer,
        superseded=superseded,
        orphaned=orphaned,
        removed=tuple(removed),
        failures=tuple(failures),
        truncated=truncated,
    )


def legacy_layout_report(retirements: Sequence[LegacyLayoutRetirement]) -> dict[str, object]:
    """Render the whole sweep: totals, per-layer rows, and the layers whose orphans were kept."""
    return {
        "layers": [entry.to_report() for entry in retirements],
        "superseded": sum(len(entry.superseded) for entry in retirements),
        "orphaned": sum(len(entry.orphaned) for entry in retirements),
        "removed": sum(len(entry.removed) for entry in retirements),
        "failures": sum(len(entry.failures) for entry in retirements),
        "megabytes": round(sum(entry.byte_count for entry in retirements) / 1_048_576, 1),
        "layers_with_orphans": [entry.layer for entry in retirements if entry.orphaned],
        "layers_with_errors": [entry.layer for entry in retirements if entry.error is not None],
    }


__all__ = [
    "DEFAULT_DAYS_PER_LANE_TURN",
    "DEFAULT_DRAIN_SELECTION",
    "DEFAULT_MAX_CONSECUTIVE_FAILURES",
    "DRAIN_STATEMENT_TIMEOUT_SECONDS",
    "LEGACY_LAYOUT_PATTERN",
    "MAX_LEGACY_KEYS_PER_LAYER",
    "DrainDayFailure",
    "DrainLaneProgress",
    "DrainSelection",
    "DrainSummary",
    "LaneLadderCensus",
    "LegacyLayoutObject",
    "LegacyLayoutRetirement",
    "build_ladder_census",
    "build_lane_ladder_census",
    "ladder_census_report",
    "legacy_layout_report",
    "plan_drain",
    "plan_ladder_drain",
    "retire_legacy_layout_objects",
    "run_drain",
]
