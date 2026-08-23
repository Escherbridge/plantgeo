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
dated at or after it already exists. Nothing can be "missed", because no calendar day ever carried
an obligation for a reference fact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import text

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.lane_contract import (
    LaneContractError,
    nature_has_time_axis,
    newest_covered_day,
    resolve_static_lane,
)
from agri_data_service.foundation.parquet.paths import (
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.objectstore import EmptyPartitionError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.lane_contract import (
        LaneNature,
        SourceWatermark,
        StaticLaneState,
    )
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

# `current` is a static lane at or ahead of its source watermark. It is deliberately NOT folded
# into `complete`: "this reference set matches its source" and "this window has no gaps left" are
# different claims about different clocks, and an operator scanning a summary needs to see which.
LaneFillOutcome = Literal["complete", "filled", "budget_exhausted", "raised", "no_window", "current"]
LaneDayOutcome = Literal["written", "absent", "raised"]

# The outcomes that mean this tick found something WRONG, as opposed to found work still to do.
# Named once so the summary, the exit rule and any log line cannot disagree about what failure is.
FAILING_LANE_OUTCOMES: Final[frozenset[str]] = frozenset({"raised"})


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
            "budget_exhausted_lanes": [lane.slug for lane in self.lanes if lane.outcome == "budget_exhausted"],
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


def _census_shell(lane: LaneRegistration, **overrides: object) -> LaneGapCensus:
    """Build one census row with the lane's own declared fields already filled in."""
    fields: dict[str, object] = {
        "slug": lane.slug,
        "nature": lane.nature,
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
    today: date,
    reading: LaneWatermarkReading | None,
) -> LaneGapCensus:
    """Classify one `static_lookup` lane against its source watermark and the objects already written.

    THE COUNTS ARE OVER THE WHOLE STREAM, not over a window, because a static lane has none: a
    reference set holds N versions, and how many of them exist is the useful number. `missing_days`
    holds at most one entry -- the version the source says is owed.
    """
    if reading is not None and reading.error is not None:
        return _census_shell(lane, static_state="watermark_unread", error=reading.error)
    try:
        keys = store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND)
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, static_state="watermark_unread", error=_listing_failure(lane, error))
    data_days = {
        parsed.day
        for key in keys
        if (parsed := try_parse_partition_path(key)) is not None
        and parsed.layer == lane.slug
        and parsed.kind == GAP_FILL_PARTITION_KIND
    }
    marker_days = {
        marker.day
        for key in keys
        if (marker := try_parse_absence_marker_path(key)) is not None
        and marker.layer == lane.slug
        and marker.kind == GAP_FILL_PARTITION_KIND
    }
    watermark = None if reading is None else reading.watermark
    try:
        verdict = resolve_static_lane(
            watermark=watermark,
            newest_covered_day=newest_covered_day(layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, keys=keys),
            today=today,
        )
    except LaneContractError as error:
        return _census_shell(lane, static_state="watermark_unread", error=f"{lane.slug}: {error}")
    version_day = watermark.day if watermark is not None else None
    return _census_shell(
        lane,
        first_day=version_day,
        last_day=version_day,
        data_days=len(data_days),
        absent_days=len(marker_days - data_days),
        conflict_days=len(marker_days & data_days),
        missing_days=() if verdict.version_day is None else (verdict.version_day,),
        static_state=verdict.state,
        source_watermark=version_day,
        watermark_basis=None if watermark is None else watermark.basis,
        static_detail=verdict.detail,
    )


def _series_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None,
) -> LaneGapCensus:
    """Classify one `daily_series` or `release_series` lane's settled window from the LISTING alone."""
    window = lane_window(lane, today=today)
    if window is None:
        return _census_shell(lane)
    first_day, last_day = window
    try:
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            first_day=first_day,
            last_day=last_day,
            keys=store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND),
        )
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, first_day=first_day, last_day=last_day, error=_listing_failure(lane, error))
    # NEWEST-FIRST. `partition_day_statuses` answers chronologically; this reversal is the whole
    # reason one driver serves both the leading edge and the backlog. See the module docstring.
    #
    # Cadence filters the candidates BEFORE they become work: a release series only publishes on its
    # own step from the floor, so the six intervening days are not gaps the driver should chase.
    # It never suppresses a day that already holds data or a marker -- those are read from the
    # listing above and reported as-is, so a real partition off the expected step stays visible.
    missing = tuple(
        day
        for day, status in sorted(statuses.items(), reverse=True)
        if status == "missing" and (day - lane.history_floor).days % lane.cadence_days == 0
    )
    return _census_shell(
        lane,
        first_day=first_day,
        last_day=last_day,
        data_days=sum(1 for status in statuses.values() if status == "data"),
        absent_days=sum(1 for status in statuses.values() if status == "absent"),
        conflict_days=sum(1 for status in statuses.values() if status == "conflict"),
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
    nothing for on every tick forever.

    `reading` is a static lane's watermark, resolved by the caller because reading it needs a
    database session and this function deliberately stays synchronous and listing-only. Omitting it
    for a static lane reports `watermark_unread` -- honestly "we did not look", never "no gaps".
    """
    if nature_has_time_axis(lane.nature):
        return _series_lane_census(lane, store, today=today, max_days_per_lane=max_days_per_lane)
    return _static_lane_census(lane, store, today=today, reading=reading)


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
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
        # Reported separately from `lanes_with_gaps` so an operator can tell a reference set that
        # MATCHES its source from one nobody asked about. Both show zero missing days.
        "static_lanes_current": [entry.slug for entry in census if entry.static_state == "current"],
        "static_lanes_unread": [entry.slug for entry in census if entry.static_state == "watermark_unread"],
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
