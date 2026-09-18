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
from typing import TYPE_CHECKING

from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_census import (
    _static_lane_census,
    build_gap_census,
    build_lane_census,
    derived_rung_completions,
    gap_census_report,
)
from agri_data_service.pipeline.parquet.gap_fill_contract import (
    DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    FAILING_LANE_OUTCOMES,
    GAP_CENSUS_REPORT_DAY_SAMPLE,
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    MAX_STATIC_EXPORT_ATTEMPTS,
    GapFillContractError,
    GapFillSummary,
    LadderRepairOutcome,
    LaneDayOutcome,
    LaneFillOutcome,
    LaneFillVerdict,
    LaneGapCensus,
    LaneWatermarkReading,
    _ladder_schema_mismatch,
    _lane_day_lock_key,
    _utc_now,
    lane_window,
    no_derived_tiers,
    postgres_lane_day_lock,
    statement_timeout,
    unlocked_lane_day,
    zero_row_absence,
    zero_row_absence_reason,
)
from agri_data_service.pipeline.parquet.gap_fill_day import (
    _absence_reason_of_record,
    _export_one_day,
    _fill_static_day,
    _finalize_written_day,
    _govern_absent_day,
    fill_one_lane_day,
    resolve_lane_watermarks,
)
from agri_data_service.pipeline.parquet.gap_fill_progress import (
    _drain_owed_availability,
    _record_day_outcome,
    _record_repair_outcome,
    _seeded_progress,
)
from agri_data_service.pipeline.parquet.gap_fill_repair import repair_one_lane_day

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

    # Both are Callable type aliases, never bound at runtime in gap_fill_contract (see its own
    # `if TYPE_CHECKING:` block); `__all__` below re-exports the NAME for callers that only ever
    # reference it as a type (`tests/parquet/test_gap_fill.py` imports it the same way, inside its
    # own TYPE_CHECKING guard), so ruff's "used at runtime" heuristic on `__all__` membership is a
    # false positive here, not a use.
    from agri_data_service.pipeline.parquet.gap_fill_contract import (  # noqa: TC004
        LaneDayLock,
        TierDeriver,
    )
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


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


#: The one import path for gap fill. Every name the sibling modules define is re-exported here, so
#: the split into `gap_fill_{contract,day,progress,repair}` and `gap_census` changed no caller; the
#: underscored entries are reached by the lane adapters, `drain.py` and the contract tests.
__all__ = [
    "DEFAULT_GAP_FILL_TIME_BUDGET_SECONDS",
    "DEFAULT_STATEMENT_TIMEOUT_SECONDS",
    "FAILING_LANE_OUTCOMES",
    "GAP_CENSUS_REPORT_DAY_SAMPLE",
    "GAP_FILL_PARTITION_KIND",
    "GAP_FILL_ZOOM_TIER",
    "MAX_STATIC_EXPORT_ATTEMPTS",
    "GapFillContractError",
    "GapFillSummary",
    "LadderRepairOutcome",
    "LaneDayLock",
    "LaneDayOutcome",
    "LaneFillOutcome",
    "LaneFillVerdict",
    "LaneGapCensus",
    "LaneWatermarkReading",
    "TierDeriver",
    "_absence_reason_of_record",
    "_drain_owed_availability",
    "_export_one_day",
    "_fill_static_day",
    "_finalize_written_day",
    "_govern_absent_day",
    "_ladder_schema_mismatch",
    "_lane_day_lock_key",
    "_record_day_outcome",
    "_record_repair_outcome",
    "_seeded_progress",
    "_static_lane_census",
    "_utc_now",
    "build_gap_census",
    "build_lane_census",
    "derived_rung_completions",
    "fill_one_lane_day",
    "gap_census_report",
    "lane_window",
    "no_derived_tiers",
    "postgres_lane_day_lock",
    "repair_one_lane_day",
    "resolve_lane_watermarks",
    "run_gap_fill",
    "statement_timeout",
    "unlocked_lane_day",
    "zero_row_absence",
    "zero_row_absence_reason",
]
