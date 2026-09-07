"""Write one lane-day's COARSE rungs from the base rung that was just written.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or interface.

This is the fusion RUNBOOK section 0.34.2 asks for, and the one place the pure transform in
`warehouse/parquet/tiers.py` meets an object store. The transform decides what a coarser rung
CONTAINS; this module decides when it is written, pruned and declared finished.

WHY THE BASE ROWS ARE READ BACK RATHER THAN HELD IN MEMORY -- a deliberate, stated deviation from
0.34.2's "derives the coarse rungs from what is already in memory". The thirteen lane adapters
return counts, not tables (`LaneRegistration.adapter` -> `LaneRunResult`), and `soil-survey`
deliberately never holds its day at all: it streams ~3,016 bounded batches to `part-0..part-N`
precisely so memory stays flat across 1.5M delineations (0.32.2 decision 4). Threading a table back
out of every adapter would either undo that streaming or force a second, table-less code path for
the one lane that needs it most.

What 0.34.2 was actually buying is preserved in full: the corpus is still walked ONCE. The drain
does not make a second pass over 13,037 days re-reading what it wrote; it re-reads ONE day,
immediately, while that day is the only thing in flight. The cost is one extra GET per part per day
against bytes that were written seconds earlier.

ORDERING, AND WHY THE BASE MARKER MUST BE WRITTEN LAST
------------------------------------------------------
Each tier is its own partition space with its own completion marker, and the BASE marker is what
admits a day to the ladder census at all: `gap_fill` selects ladder repairs from days whose base rung
holds parts AND asserts it finished. If the base marker were written before the coarse rungs, a run
that died in between would leave a day that is base-complete and rung-empty -- which the ladder
census does now catch, but only because it exists; for the year it did not, such a day was empty at
every zoom under 13 forever, on a green tick. The ordering costs nothing and does not depend on a
census being right, so it stays.

So the caller must write the coarse rungs FIRST and mark the base LAST. `_finalize_written_day`
does exactly that, and this module raises rather than half-succeeding so that ordering has
something to refuse on. The reverse failure is harmless and self-healing: a run that dies after the
coarse rungs but before the base marker leaves the day `incomplete`, and the next tick redoes all
of it.

A coarse rung IS marked complete as it lands, because a reader at z9 consults the z9 marker. A day
whose coarse rungs are marked while its base is not is readable and correct -- the coarse rows were
derived from a base that is fully written, merely not yet declared.

AN ABSENT DAY OWES ITS LADDER TOO, AND THAT IS WHY `write_absence_ladder` LIVES HERE
------------------------------------------------------------------------------------
`pipeline/parquet/objectstore.py`'s module docstring assigns this obligation to this step by name:
"CROSS-TIER AGREEMENT OF ONE DAY IS NOT THIS MODULE'S INVARIANT ... 'Every tier of a published day
is present' is the DERIVATION step's obligation". `ObjectStore.write_absence` is a correct per-tier
primitive -- it marks the rung it is given and refuses that rung alone -- so a caller that marks only
the base rung leaves three rungs saying nothing, and the day is then unrepresentable in an
availability generation: `availability_index.py::_validate_generation_day` demands the exact
four-rung ladder, and `_verify_absence_object` demands each rung's row cite a marker at ITS OWN key,
so the three missing rows can be neither omitted nor synthesised. Measured 2026-09-06, that gap is
3,205 refused days across five lanes, every one of them the same one-rung shape.

A DERIVED-EMPTY COMPLETION MARKER IS NOT THE COARSE RUNG'S ANSWER HERE. `_retract_tier` writes
`_complete.empty.json` for a rung whose NON-EMPTY base generalised away, and it says the rows
existed; a governed absence says the SOURCE had nothing. Using the first to close the second would
be a false statement about upstream, which is why the coarse rungs of an absent day carry the base
rung's OWN marker bytes and nothing else.

Nothing in the ladder writer is a derivation in the transform sense -- there are no rows to
generalise -- so it shares this module's ORDERING and its all-or-nothing failure rule rather than its
transform. Coarse rungs first and the base rung last, because only the base rung is censused: a run
that dies mid-ladder must leave the day `missing` and re-selectable, never covered-but-empty above a
base rung that says nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.pipeline.parquet.objectstore import GovernedAbsenceConflictError
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS, derive_tier

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]
    from duckdb import DuckDBPyConnection

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import AbsenceWriteReceipt, ObjectStore

#: The whole ladder one governed absence settles, COARSE RUNGS FIRST AND THE BASE RUNG LAST. Spelled
#: exactly as `gap_fill.py::_ABSENCE_LADDER_TIERS` and `direct/evacuation_zones/adapter.py` already
#: spell it, and ordered for the reason this module's docstring gives: only the base rung is censused,
#: so a run that dies mid-ladder must leave the day re-selectable rather than covered above a base
#: rung that says nothing.
ABSENCE_LADDER_TIERS: Final[tuple[ZoomTier, ...]] = (*DERIVED_ZOOM_TIERS, BASE_ZOOM_TIER)

# How many rows one derived part file holds. Matched to `pipeline/lanes/calendar.py:36`'s
# `ROWS_PER_PART` rather than to `burn_severity.py`'s 100: a coarse rung is by construction smaller
# than the base it came from, so the lane with the WIDEST parts is the right reference -- sizing to
# the narrowest would mint thousands of tiny objects for rungs that hold a few hundred rows.
DERIVED_ROWS_PER_PART: Final = 10_000


class TierWriteError(RuntimeError):
    """Raised when a lane-day's coarse rungs cannot be written as a complete set."""


class AbsenceLadderError(RuntimeError):
    """Raised when a lane-day's governed absence cannot be marked at every rung it was asked for.

    A SEPARATE NAME FROM `TierWriteError`, because the two failures ask for different repairs. A tier
    write failing means rows could not be generalised and the day owes a re-derivation; an absence
    ladder failing means a day upstream had nothing for is marked at no rung, and the next tick
    re-selects it as an ordinary gap. `GovernedAbsenceConflictError` stays the answer when the day
    holds DATA, so a caller that already handles a conflict keeps handling it.
    """


@dataclass(frozen=True, slots=True)
class DerivedTierReport:
    """What one lane-day's coarse rungs cost and came to, per rung."""

    tier: ZoomTier
    part_count: int
    row_count: int
    byte_count: int


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """Every coarse rung of one lane-day, plus the notes a driver should surface."""

    tiers: tuple[DerivedTierReport, ...]
    notes: tuple[str, ...]
    # Rungs that derived to NO ROWS: their parts were retracted and a DERIVED-EMPTY completion marker
    # was written in their place.
    #
    # NAMED PER RUNG BECAUSE A DAY IS NOT THE UNIT. A day whose z9 holds rows and whose z0 empties
    # wrote parts, so a driver measuring emptiness by the day's total part count sees `written` and
    # moves on. It is no longer a day the ladder census re-selects forever -- the zero-part marker
    # closes the rung -- but it is still the one rung a reader will find honestly empty, and
    # `pipeline/parquet/drain.py` reports it from here so that emptiness is stated rather than
    # inferred from a silence.
    emptied: tuple[ZoomTier, ...] = ()

    @property
    def part_count(self) -> int:
        """Total part files written across every derived rung."""
        return sum(report.part_count for report in self.tiers)

    @property
    def row_count(self) -> int:
        """Total rows written across every derived rung."""
        return sum(report.row_count for report in self.tiers)

    @property
    def byte_count(self) -> int:
        """Total bytes written across every derived rung."""
        return sum(report.byte_count for report in self.tiers)


def derive_and_write_day_tiers(  # noqa: PLR0913 - one coordinate of the day being derived per arg
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    base_table: pl.DataFrame | pa.Table | None = None,
    tiers: Sequence[ZoomTier] = DERIVED_ZOOM_TIERS,
    connection: DuckDBPyConnection | None = None,
) -> DerivationResult:
    """Derive, write, prune and mark every coarse rung of one lane-day. Raises if any rung fails.

    All or nothing by raising; `base_table` skips the read-back for a caller that already holds the
    day; `connection` is the reused DuckDB session; an empty `tiers` is refused. See `AGENTS.md` in
    this directory, "derive_and_write_day_tiers: all four rungs or none", for each of those and for
    the named memory risk in the read-back.
    """
    if not tiers:
        raise TierWriteError(
            f"{layer} {day.isoformat()}: a derivation was asked for NO rungs, which would report a complete ladder "
            f"over one that was never built. Ask for {tuple(DERIVED_ZOOM_TIERS)} or a subset of it"
        )
    source = _as_frame(base_table if base_table is not None else store.read_partition(layer, kind, 13, day))
    reports: list[DerivedTierReport] = []
    notes: list[str] = []
    emptied: list[ZoomTier] = []
    for tier in tiers:
        try:
            derived = derive_tier(source, stream=layer, tier=tier, connection=connection)
        except Exception as error:
            raise TierWriteError(
                f"{layer} z{tier} {day.isoformat()}: the derivation itself failed, so this day has no honest coarse "
                f"rung and must not be marked complete: {type(error).__name__}: {error}"
            ) from error
        if derived.height == 0:
            # Not an error and not an absence: the base day held rows, but every one of them was
            # dropped at this rung -- an unlocated gauge, or a feature below the tier's area floor.
            # A governed absence would claim upstream had nothing, which is false.
            #
            # IT MUST STILL RETRACT WHATEVER THIS RUNG HELD BEFORE. A `continue` here skips
            # `_write_tier`, which is the only place a rung is pruned or re-marked -- so an earlier,
            # larger derivation's parts AND its completion marker would survive, and every reader at
            # this zoom would go on being served rows the base day no longer contains, from a rung
            # that still claims to be finished. That is the stable lie this whole contract exists to
            # prevent, arrived at from the other direction.
            notes.append(
                f"{layer} z{tier} {day.isoformat()}: every base row was dropped at this rung, so it holds no parts"
            )
            _retract_tier(store, layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now)
            emptied.append(tier)
            continue
        reports.append(_write_tier(store, derived, layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now))
    return DerivationResult(tiers=tuple(reports), notes=tuple(notes), emptied=tuple(emptied))


def _as_frame(table: pl.DataFrame | pa.Table) -> pl.DataFrame:
    """Accept either shape a caller may already hold, and hand the transform a Polars frame.

    The repair path reads the base rung back ITSELF -- it needs the parts' digests for the day's
    availability claim -- and hands the Arrow table straight through, so this function is what stops
    a second full download of the day being the price of citing what it read.
    """
    if isinstance(table, pl.DataFrame):
        return table
    frame = pl.from_arrow(table)
    if isinstance(frame, pl.DataFrame):
        return frame
    return frame.to_frame()  # pragma: no cover - a one-column read would be a store change


def _retract_tier(  # noqa: PLR0913 - one coordinate of the rung being emptied per arg
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    tier: ZoomTier,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
) -> None:
    """Empty one rung: clear its old claim, delete every part it held, then declare it EMPTY BY NAME.

    The receipt lands at `_complete.empty.json`, its own key, so no reader has to open a marker to
    tell an honestly-empty rung from one whose parts were deleted. Marker last, after the prune
    provably succeeded; a rung still claiming a governed absence is REFUSED rather than overwritten,
    because the caller heals that by retracting the claim. See `AGENTS.md` in this directory,
    "_retract_tier: emptiness is asserted, never inferred".
    """
    if store.absence_exists(layer, kind, tier, day):
        raise GovernedAbsenceConflictError(
            f"{layer!r} {kind} z{tier} {day} carries a governed-absence marker while its base rung holds rows; "
            "this rung's emptiness cannot be declared over a claim only an admin or the base writer may retract"
        )
    pruned = store.retract_partition_tier(layer, kind, tier, day)
    if pruned.failures:
        raise TierWriteError(
            f"{layer} z{tier} {day.isoformat()}: this rung derived to no rows, but the parts a previous derivation "
            f"left there could not be removed, so readers at this zoom would keep being served rows the base day no "
            f"longer holds: {'; '.join(pruned.failures)}"
        )
    store.write_completion_marker(
        PartitionCompletion(part_count=0, row_count=0, completed_at=now(), run_id=run_id, derived_empty=True),
        layer=layer,
        kind=kind,
        zoom=tier,
        day=day,
    )


def _write_tier(  # noqa: PLR0913 - one coordinate of the rung being written per arg
    store: ObjectStore,
    derived: pl.DataFrame,
    *,
    layer: str,
    kind: PartitionKind,
    tier: ZoomTier,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
) -> DerivedTierReport:
    """Write one rung's parts, prune what this write no longer covers, then declare it finished.

    PRUNE BEFORE MARK, exactly as `_finalize_written_day` does for the base rung and for the same
    reason: the marker's `part_count` is this write's claim about what the rung holds, and asserting
    it while a larger earlier derivation's tail is still published would make the marker disagree
    with the bucket at the moment it was written. A failed prune therefore fails the rung, which
    fails the day -- there is no "written but unmarked" middle state to fall back to here, because
    the base marker the caller is about to withhold is the thing that brings the day back.
    """
    table = derived.to_arrow()
    receipts = [
        store.write_partition(
            table.slice(start, DERIVED_ROWS_PER_PART),
            layer=layer,
            kind=kind,
            zoom=tier,
            day=day,
            part_index=part_index,
        )
        for part_index, start in enumerate(range(0, table.num_rows, DERIVED_ROWS_PER_PART))
    ]
    part_count = len(receipts)
    pruned = store.prune_surplus_parts(layer, kind, tier, day, written_part_count=part_count)
    if pruned.failures:
        raise TierWriteError(
            f"{layer} z{tier} {day.isoformat()}: a surplus part from a larger earlier derivation is still published "
            f"beside this one, so this rung cannot be marked complete: {'; '.join(pruned.failures)}"
        )
    row_count = derived.height
    store.write_completion_marker(
        PartitionCompletion(
            part_count=part_count,
            row_count=row_count,
            completed_at=now(),
            run_id=run_id,
            # SORTED BY relative_path, NOT BY part_index: `partition_path` mints unpadded part
            # numbers ("part-2.parquet", "part-10.parquet"), so lexical and numeric order diverge
            # past nine parts. `_validate_parts` refuses anything that is not already in that
            # sorted order, and this write is single-pass with no retry, so `receipts` is exactly
            # and only this rung's current parts -- nothing stale to filter out.
            parts=tuple(
                CompletedPart(
                    relative_path=receipt.relative_path,
                    row_count=receipt.row_count,
                    byte_count=receipt.byte_count,
                    sha256=receipt.sha256,
                )
                for receipt in sorted(receipts, key=lambda receipt: receipt.relative_path)
            ),
        ),
        layer=layer,
        kind=kind,
        zoom=tier,
        day=day,
    )
    return DerivedTierReport(
        tier=tier,
        part_count=part_count,
        row_count=row_count,
        byte_count=sum(receipt.byte_count for receipt in receipts),
    )


def write_absence_ladder(  # noqa: PLR0913 - one coordinate of the day being governed per arg
    store: ObjectStore,
    absence: GovernedAbsence,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    tiers: Sequence[ZoomTier] = ABSENCE_LADDER_TIERS,
) -> tuple[AbsenceWriteReceipt, ...]:
    """Mark one lane-day absent at EVERY named rung with ONE piece of evidence, or mark none of them.

    THE WHOLE LADDER IS CHECKED BEFORE THE FIRST MARKER IS WRITTEN, and a rung that fails after an
    earlier one succeeded is ROLLED BACK. Writing rung by rung and refusing on the first conflict
    leaves coarse rungs governing a day whose base rung still serves rows -- the exact stable lie the
    marker contract exists to prevent, and one no census brings back, because `build_gap_census`
    walks the base tier and finds its parts and its completion marker intact.

    ONE `GovernedAbsence` FOR THE WHOLE LADDER, NEVER ONE PER RUNG. Every rung is handed the same
    object and therefore the same canonical bytes, which is what
    `availability_index.py::_validate_generation_day` requires -- "availability day ... mixes absence
    reasons across its ladder" is raised on a day whose rungs disagree -- and what
    `_verify_absence_object` re-proves per rung against the row's own `absence_reason`. A caller
    minting a fresh reason per rung would publish four markers no generation can carry.

    THE ROLLBACK NEVER REMOVES A MARKER IT DID NOT CREATE. Which rungs already carried a marker is
    read BEFORE the first write, so a re-run over an already-governed day that fails part way leaves
    that day exactly as governed as it found it, rather than stripping rungs a previous run proved.

    EVERY NAMED RUNG IS WRITTEN, INCLUDING ONE THAT ALREADY HOLDS THESE BYTES, and that is deliberate
    rather than lazy: `availability_extension.py::_rung_objects` binds an absent day from THIS RUN'S
    written-object ledger, so a rung skipped as unchanged is a rung the availability step then reports
    as "carries no governed-absence marker from this run" and the day goes back to being a ladder gap.
    Re-putting identical bytes at the same key is the object store's own no-op -- one key, one object,
    the same digest before and after -- so nothing is duplicated by writing it. A caller that must not
    pay for the redundant writes passes `tiers` naming only the rungs it knows are missing;
    `scripts/backfill_absence_ladder.py` is exactly that caller.
    """
    ordered = tuple(tiers)
    if not ordered:
        raise AbsenceLadderError(
            f"{layer} {day.isoformat()}: a governed absence was asked for NO rungs, which would report a marked "
            f"day over one that was never marked. Ask for {ABSENCE_LADDER_TIERS} or a subset of it"
        )
    if len(set(ordered)) != len(ordered):
        raise AbsenceLadderError(
            f"{layer} {day.isoformat()}: the absence ladder {ordered} names a rung twice, so the second write "
            f"would silently overwrite the first and the receipt count would overstate what the day holds"
        )
    blocked = tuple(
        (tier, part) for tier in ordered if (part := store.part_blocking_absence(layer, kind, tier, day)) is not None
    )
    if blocked:
        rungs = ", ".join(f"z{tier} ({part})" for tier, part in blocked)
        raise GovernedAbsenceConflictError(
            f"{layer!r} {kind} {day.isoformat()} still holds part files at {rungs}, so it can be governed absent "
            f"at no rung: correcting a completed record is a manual admin action, and no marker was written"
        )
    # Read BEFORE the loop, so the rollback below can tell a rung this call created from one it merely
    # rewrote. Deleting the latter would make a failed re-run worse than the state it started from.
    preexisting = frozenset(tier for tier in ordered if store.absence_exists(layer, kind, tier, day))
    receipts: list[AbsenceWriteReceipt] = []
    for tier in ordered:
        try:
            receipts.append(store.write_absence(absence, layer=layer, kind=kind, zoom=tier, day=day))
        except Exception as refusal:
            undone = _retract_absence_ladder(
                store,
                layer=layer,
                kind=kind,
                day=day,
                rungs=tuple(receipt.zoom for receipt in receipts if receipt.zoom not in preexisting),
            )
            raise AbsenceLadderError(
                f"{layer} z{tier} {day.isoformat()}: the governed-absence marker was refused, so this day cannot "
                f"be marked absent as a complete ladder: {type(refusal).__name__}: {refusal}. {undone}"
            ) from refusal
    return tuple(receipts)


def govern_day_absent(
    store: ObjectStore,
    absence: GovernedAbsence,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
) -> AbsenceWriteReceipt:
    """Govern one whole lane-day as absent and return the BASE rung's receipt, which is what callers key on.

    THE ONE CALL A LANE WRITER MAKES. `normalise_export_outcome`, `build_gap_census` and every
    downstream census read the base rung, so that receipt is the return value -- but it is now the
    LAST of four written rather than the only one written, which is the whole of this fix.
    """
    receipts = write_absence_ladder(store, absence, layer=layer, kind=kind, day=day)
    base = receipts[-1]
    if base.zoom != BASE_ZOOM_TIER:
        raise AbsenceLadderError(  # pragma: no cover - `ABSENCE_LADDER_TIERS` ends at the base rung by construction
            f"{layer} {day.isoformat()}: the absence ladder ended at z{base.zoom} rather than the base rung "
            f"z{BASE_ZOOM_TIER}, so the receipt returned to the lane writer settles the wrong rung"
        )
    return base


def _retract_absence_ladder(
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    rungs: tuple[ZoomTier, ...],
) -> str:
    """Undo a partly written absence ladder, so no rung governs a day the others do not.

    Failures are described rather than raised: this runs inside the handler for a write that already
    failed, and a second exception there would replace the reason the ladder stopped with the reason
    the cleanup stopped. The caller folds this sentence into that message instead.
    """
    if not rungs:
        return "no rung had been newly marked, so the day is exactly as this attempt found it"
    failures: list[str] = []
    retracted: list[ZoomTier] = []
    for tier in rungs:
        try:
            store.clear_absence_marker(layer, kind, tier, day)
        except Exception as error:
            failures.append(f"z{tier}: {type(error).__name__}: {error}")
        else:
            retracted.append(tier)
    undone = ", ".join(f"z{tier}" for tier in retracted) if retracted else "no rung"
    if failures:
        return (
            f"{undone} was retracted, but {'; '.join(failures)} could not be, so that rung still governs a day "
            f"the rest of the ladder does not and an admin must retract it"
        )
    return f"{undone} was retracted, so no rung governs this day"


__all__ = [
    "ABSENCE_LADDER_TIERS",
    "DERIVED_ROWS_PER_PART",
    "AbsenceLadderError",
    "DerivationResult",
    "DerivedTierReport",
    "TierWriteError",
    "derive_and_write_day_tiers",
    "govern_day_absent",
    "write_absence_ladder",
]
