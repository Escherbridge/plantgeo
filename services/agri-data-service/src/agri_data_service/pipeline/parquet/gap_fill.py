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

A STATIC LOOKUP HAS NO BACKLOG AT ALL, AND THAT IS THE SECOND MECHANISM HERE. `daily_series` and
`release_series` lanes get the window walk above. A `static_lookup` lane instead reads its SOURCE
WATERMARK, and owes exactly one snapshot dated at that watermark -- or nothing, when a partition
already covers it AND was exported at or after the source's own change instant. Nothing can be
"missed", because no calendar day ever carried an obligation for a reference fact.

THE ONE SNAPSHOT A STATIC LANE OWES MAY BE A DAY IT ALREADY HOLDS. When the source changed again
later on the same UTC day, the version owed IS that day, re-exported: `write_partition` overwrites
by key, so the fill path below needs no separate correction mode. The census reads the export
instant out of the SAME listing it takes the days from, so this costs no extra object-store call.

THIS DRIVER FILLS ONE ZOOM TIER -- THE BASE ONE -- AND CENSUSES THAT SAME TIER. A lane adapter
exports the ungeneralized population, which is the most detailed rung of the ladder; the coarser
rungs are DERIVED from those objects in Polars/DuckDB (RUNBOOK §0.32.2 decision 2), never from a
day-scoped Postgres query. A driver that "filled" a derived tier would be inventing a generalization
nobody computed, exactly as filling `kind=forecast` would invent a projection nobody issued -- so the
tier is a module constant here rather than a caller's argument, for the same reason the kind is.

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import func, select, text

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
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.parquet.derivation import DerivationResult, derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.objectstore import (
    EmptyPartitionError,
    GovernedAbsenceConflictError,
    SurplusPruneResult,
    oldest_export_instant,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

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

# Matches `jobs-pulse`'s own tick budget: generous enough that a healthy incremental tick never trips
# it, short enough that one stuck lane cannot consume an entire hourly cadence.
DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS: Final = 600.0

# How many of a lane's missing days the census REPORTS. The days themselves are all walked; a report
# that inlined ~9,400 dates for one lane would be unreadable and would bury the counts that matter.
GAP_CENSUS_REPORT_DAY_SAMPLE: Final = 10

# Transaction-local, matching the 120 s convention every other direct SQL caller in this service uses
# (jobs/lease.py::LEASE_STATEMENT_TIMEOUT_SECONDS, and cli.py's loader verbs).
_STATEMENT_TIMEOUT: Final = text("SET LOCAL statement_timeout = '120s'")

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
    forecastable: bool = False
    cadence_days: int = 1
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
    detail: str | None = None

    def to_row(self) -> dict[str, object]:
        """Render one summary-table row."""
        return {
            "lane": self.slug,
            "outcome": self.outcome,
            "considered": self.considered,
            "written": self.written,
            "absent": self.absent,
            "blocked": self.blocked,
            "contended": self.contended,
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
            "budget_exhausted_lanes": [lane.slug for lane in self.lanes if lane.outcome == "budget_exhausted"],
            "blocked": sum(lane.blocked for lane in self.lanes),
            "blocked_lanes": [lane.slug for lane in self.lanes if lane.blocked],
            "contended": sum(lane.contended for lane in self.lanes),
            "failed": self.failed,
            "failing_lanes": [lane.slug for lane in self.failing_lanes],
        }


def lane_window(lane: LaneRegistration, *, today: date) -> tuple[date, date] | None:
    """Return the settled `[first, last]` day range a SERIES lane may fill, or `None` when it has none.

    `last` is `today - publication_lag_days`: a day the upstream has not published yet is not a gap,
    and asking for it would write a partition thinner than the day really is. `first` is the
    declared history floor.

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
    return _census_shell(
        lane,
        zoom,
        first_day=version_day,
        last_day=version_day,
        data_days=len(complete_days),
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
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            keys=store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND, zoom),
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
    return _census_shell(
        lane,
        zoom,
        first_day=first_day,
        last_day=last_day,
        data_days=sum(1 for status in statuses.values() if status == "data"),
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
    """Classify one lane's coverage OF THE BASE TIER from the object LISTING alone -- never by opening a file.

    A governed-absence marker counts as covered, not as a gap: `missing_partition_days` already
    treats it that way, which is what stops the driver re-attempting a day the source truly has
    nothing for on every tick forever. A day holding parts WITHOUT a completion marker is the
    opposite case and counts as work -- but only for a SERIES lane, where it is reported as
    `incomplete_days` and appears in `missing_days` too, because repairing it is the same operation
    as filling a day never attempted. A STATIC lane's `missing_days` still holds only the version its
    watermark owes: its day is a version stamp, not a calendar position, so an unfinished old version
    is reported through `incomplete_days` and `static_detail` and left for an admin. See
    `_static_lane_census`.

    The tier is `GAP_FILL_ZOOM_TIER` rather than an argument, because this census exists to feed THIS
    driver, and this driver can only write the tier its lane adapters export. Auditing a derived tier
    is a different question with a different mechanism, and it must not borrow this answer: the row
    carries its `zoom` so no reader can mistake one for the other.
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
        # Surfaced by name, not just summed: a lane accumulating unfinished days every tick is
        # crashing mid-export, and that reads as ordinary backlog in a `missing_days` total.
        "lanes_with_unfinished_days": [entry.slug for entry in census if entry.incomplete_days],
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
        # Reported separately from `lanes_with_gaps` so an operator can tell a reference set that
        # MATCHES its source from one nobody asked about. Both show zero missing days.
        "static_lanes_current": [entry.slug for entry in census if entry.static_state == "current"],
        "static_lanes_unread": [entry.slug for entry in census if entry.static_state == "watermark_unread"],
    }


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
    The tier is named in the evidence as well as in the key, because a marker lifted out of its path
    would otherwise read as a claim about the whole ladder when it settles exactly one rung.
    """
    return GovernedAbsence(
        reason=f"the {slug} z{zoom} day export returned zero rows for {day.isoformat()}",
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
    written: int = 0
    absent: int = 0
    blocked: int = 0
    contended: int = 0
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
        # `blocked` outranks `complete`, `filled` and `budget_exhausted`: those all say the tick went
        # as well as it could, and a day needing an admin says the opposite. Only `raised` outranks
        # it, because a raised lane stopped taking turns and that is the more severe fact.
        if self.blocked and outcome != "raised":
            outcome = "blocked"
        return LaneFillVerdict(
            slug=self.census.slug,
            outcome=outcome,
            considered=len(self.census.missing_days),
            written=self.written,
            absent=self.absent,
            blocked=self.blocked,
            contended=self.contended,
            remaining=len(self.pending),
            parts=self.parts,
            rows=self.rows,
            written_bytes=self.written_bytes,
            seconds=self.seconds,
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


def _seeded_progress(census: LaneGapCensus, *, today: date) -> _LaneProgress:
    """Open one lane's tally, already stopped when its census settled the question before any export."""
    progress = _LaneProgress(census=census, pending=list(census.missing_days))
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
    return progress


def _utc_now() -> datetime:
    """The absence marker's `recorded_at`; injectable so a test pins a deterministic payload."""
    return datetime.now(UTC)


async def _pin_statement_timeout(session: AsyncSession) -> None:
    """Pin the transaction-local statement timeout; `SET LOCAL` dies with each rollback, so re-pin per day."""
    await session.execute(_STATEMENT_TIMEOUT)


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
    await _pin_statement_timeout(session)
    try:
        result = await lane.adapter(session, store, day=day, run_id=run_id)
    except EmptyPartitionError as empty:
        await session.rollback()
        try:
            receipt = store.write_absence(
                zero_row_absence(
                    lane.slug,
                    zoom=GAP_FILL_ZOOM_TIER,
                    day=day,
                    run_id=run_id,
                    observed=str(empty),
                    recorded_at=now(),
                ),
                layer=lane.slug,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=GAP_FILL_ZOOM_TIER,
                day=day,
            )
        except GovernedAbsenceConflictError as conflict:
            # The day still holds parts and its export now yields nothing. Only an admin can say
            # whether those parts remain valid, so this driver refuses to guess -- but it also
            # refuses to stop the lane over it, because this day is the NEWEST one and every older
            # gap sits behind it. See FAILING_LANE_OUTCOMES.
            return (
                "blocked",
                0,
                0,
                0,
                f"{day.isoformat()}: the export returned zero rows but the day still holds part "
                f"files, so it can be neither written nor governed as absent without an admin "
                f"deciding whether those parts are still valid: {conflict}",
            )
        except Exception as conflict:  # a marker that cannot be written is a real failure, not an absence
            return "raised", 0, 0, 0, f"{day.isoformat()}: absence marker refused: {conflict}"
        return "absent", 0, 0, receipt.byte_count, None
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
    # THE COARSE RUNGS, BEFORE THE BASE MARKER. Only the base tier is censused
    # (`build_gap_census` walks `GAP_FILL_ZOOM_TIER` alone), so the base marker is the one signal
    # that can bring this day back for another attempt. Marking it first and then failing to derive
    # would strand the day base-complete and permanently empty above z13 -- on a green tick, which
    # is the same shape of silent failure `contended` already has. Deriving first makes the failure
    # self-healing instead: the day stays unmarked and the next tick redoes all four rungs.
    try:
        derived = derive_tiers(
            store, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, day=day, run_id=run_id, now=now
        )
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
            session, store, lane, day=day, run_id=run_id, now=now, derive_tiers=derive_tiers
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
    """The advisory-lock identity of one lane-day-tier: the unit two writers must never share."""
    return f"parquet-gap-fill:{lane.slug}:{GAP_FILL_PARTITION_KIND}:z{GAP_FILL_ZOOM_TIER}:{day.isoformat()}"


@asynccontextmanager
async def postgres_lane_day_lock(session: AsyncSession, key: str) -> AsyncIterator[bool]:
    """Hold one lane-day's SESSION-scoped advisory lock for the block, yielding whether it was taken.

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
    held = await session.execute(select(func.pg_try_advisory_lock(func.hashtextextended(key, 0))))
    granted = bool(held.scalar())
    try:
        yield granted
    finally:
        if granted:
            # No rollback of its own on either side. Every export path already rolls back before
            # returning, and `pg_advisory_unlock` is not transactional, so wrapping this in one
            # would only add a transaction per lane-day for nothing -- and this driver's whole
            # session discipline is "never hold a snapshot you do not need".
            with suppress(Exception):  # the lock dies with the connection; never fail a tick over it
                await session.execute(select(func.pg_advisory_unlock(func.hashtextextended(key, 0))))


def no_derived_tiers(  # noqa: PLR0913 - the signature IS the seam; it must match what it replaces
    store: ObjectStore,  # noqa: ARG001
    *,
    layer: str,  # noqa: ARG001
    kind: PartitionKind,  # noqa: ARG001
    day: date,  # noqa: ARG001
    run_id: str,  # noqa: ARG001
    now: Callable[[], datetime],  # noqa: ARG001
) -> DerivationResult:
    """A tier derivation that writes nothing: the seam a test injects when the ladder is not the subject.

    It exists for the same reason `unlocked_lane_day` does, one contract down. Deriving the coarse
    rungs READS THE BASE RUNG BACK from the store, so with the real deriver every stub lane in a
    driver test would have to write a schema-conforming part file before its day could close --
    turning every test about budgets, watermarks and per-lane isolation into a test about Parquet
    schemas. Tests that ARE about the ladder inject the real one, or call it directly.
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
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
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
    serialised: `cli.py`'s `parquet-gap-fill` verb takes no lease, and RUNBOOK 0.33.3 B has the bulk
    drain running CONCURRENTLY with this driver by design ("build drain -> run drain -> THEN stop
    the cron"), so the overlap is planned rather than hypothetical.
    """
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
        if lane.watermark is None:
            return await _export_one_day(
                session, store, lane, day=day, run_id=run_id, now=now, derive_tiers=derive_tiers
            )
        return await _fill_static_day(
            session, store, lane, day=day, run_id=run_id, now=now, today=today, derive_tiers=derive_tiers
        )


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
) -> GapFillSummary:
    """Fill every lane's newest missing day, then its next-newest, until the wall-clock budget is spent.

    `time_budget_seconds` bounds when a new DAY is STARTED, never a day already in hand: a lane's own
    export finishes whatever it began, exactly as `jobs-pulse` bounds starting a new lane rather than
    killing one mid-slice. A lane that raises stops taking further turns -- its next day would almost
    certainly fail identically, and burning the rest of the tick rediscovering that costs every other
    lane its turn -- but every OTHER lane keeps going, and the raised lane's detail names the day.
    """
    deadline = monotonic() + time_budget_seconds
    # Static lanes' source watermarks are read FIRST, before any listing: the census cannot classify
    # a reference set without knowing what version its source is on.
    watermarks = await resolve_lane_watermarks(session, store, lanes=lanes, today=today)
    census = build_gap_census(lanes, store, today=today, max_days_per_lane=max_days_per_lane, watermarks=watermarks)
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
            )
            entry.seconds += monotonic() - started
            entry.parts += parts
            entry.rows += rows
            entry.written_bytes += written_bytes
            _record_day_outcome(entry, outcome, detail)
        if not progressed:
            break

    for entry in progress:
        # A blocked lane keeps its own outcome even with days left over: "one of your days needs an
        # admin" is the fact worth surfacing, and `budget_exhausted` reads as a healthy backlog.
        if entry.pending and not entry.stopped and not entry.blocked:
            entry.outcome = "budget_exhausted"
    return GapFillSummary(lanes=tuple(entry.verdict() for entry in progress), run_id=run_id)
