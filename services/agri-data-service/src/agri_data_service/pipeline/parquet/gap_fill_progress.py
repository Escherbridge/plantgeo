"""One walk's running tally: what each lane attempted, what it wrote, and what it still owes.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionTally,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.gap_fill_contract import (
    GAP_FILL_PARTITION_KIND,
    LadderRepairOutcome,
    LaneDayOutcome,
    LaneFillOutcome,
    LaneFillVerdict,
    LaneGapCensus,
    _append_note,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


@dataclass(slots=True)
class _LaneProgress:
    """Mutable running tally for one lane's turn; frozen into a `LaneFillVerdict` at the end."""

    census: LaneGapCensus
    pending: list[date]
    #: Days owing a re-derivation, drained only once `pending` is empty. A day that owes an EXPORT is
    #: strictly more valuable than a day that owes a generalization of rows already published.
    repairs: list[date] = field(default_factory=list)
    #: Repairs this turn ATTEMPTED and could not finish because the base rung's columns no longer
    #: match the lane's registered tier derivation -- a data problem (a stale base-rung export) that
    #: no later tick can resolve by re-deriving. See `_ladder_schema_mismatch`.
    ladder_unrepairable: int = 0
    #: Repairs this turn attempted and could not finish for any OTHER reason. Counted apart from
    #: `ladder_unrepairable` because nothing here proves the failure is permanent -- the next tick's
    #: census reselects the same day and may simply succeed -- so it must not read as admin-needed.
    ladder_retry_failed: int = 0
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
            # NOT LEFT AT `len(self.repairs)` ALONE: a day this turn popped, attempted and could not
            # resolve is gone from `repairs` (it was already popped before the attempt) but is just as
            # unfinished as one never reached at all, and a summary that only counted the latter is
            # exactly the green tick this field exists to prevent.
            ladder_remaining=len(self.repairs) + self.ladder_unrepairable + self.ladder_retry_failed,
            ladder_unrepairable=self.ladder_unrepairable,
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

    A REPAIR THAT RAISES MUST STILL BE COUNTED, though, which for a year it was not: the day was
    already popped off `entry.repairs` before this call, so a caller that only inspected `written` and
    `contended` here made the day vanish from every tally at once -- `repaired` stayed 0 (correctly,
    nothing was derived) but so did `ladder_remaining`, and the lane's `outcome` never moved off
    `complete`. `blocked` is `repair_one_lane_day`'s answer for the UNREPAIRABLE half of that: a
    schema mismatch between the published base rung and the lane's current tier derivation, which
    reads identically on every future tick and needs an admin retract-and-re-export, not a re-attempt.
    It is folded into `entry.blocked` -- the same "this driver may not resolve it" tally an admin-needed
    EXPORT day already uses -- so the lane's outcome is elevated exactly as that case already is, and
    into `ladder_unrepairable` so the ladder-specific count says so too. Anything else that raised is
    `ladder_retry_failed`: nothing proves THAT failure is permanent, so it must not read as admin-needed,
    but it must not read as zero either.
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
    elif result.outcome == "blocked":
        entry.blocked += 1
        entry.ladder_unrepairable += 1
    elif result.outcome == "raised":
        entry.ladder_retry_failed += 1
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
