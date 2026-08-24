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
Each tier is its own partition space with its own completion marker, but ONLY the base tier is
censused: `build_gap_census` walks `GAP_FILL_ZOOM_TIER` and nothing else, so the base marker is the
only signal that can bring a day back for another attempt. If the base marker were written before
the coarse rungs, a run that died in between would leave a day that is base-complete -- therefore
never revisited -- and permanently missing above z13. The map would be empty at every zoom under
13 for that day, forever, on a green tick.

So the caller must write the coarse rungs FIRST and mark the base LAST. `_finalize_written_day`
does exactly that, and this module raises rather than half-succeeding so that ordering has
something to refuse on. The reverse failure is harmless and self-healing: a run that dies after the
coarse rungs but before the base marker leaves the day `incomplete`, and the next tick redoes all
of it.

A coarse rung IS marked complete as it lands, because a reader at z9 consults the z9 marker. A day
whose coarse rungs are marked while its base is not is readable and correct -- the coarse rows were
derived from a base that is fully written, merely not yet declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import polars as pl

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# How many rows one derived part file holds. Matched to `pipeline/lanes/calendar.py:36`'s
# `ROWS_PER_PART` rather than to `burn_severity.py`'s 100: a coarse rung is by construction smaller
# than the base it came from, so the lane with the WIDEST parts is the right reference -- sizing to
# the narrowest would mint thousands of tiny objects for rungs that hold a few hundred rows.
DERIVED_ROWS_PER_PART: Final = 10_000


class TierWriteError(RuntimeError):
    """Raised when a lane-day's coarse rungs cannot be written as a complete set."""


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
    base_table: pl.DataFrame | None = None,
    tiers: Sequence[ZoomTier] = DERIVED_ZOOM_TIERS,
) -> DerivationResult:
    """Derive, write, prune and mark every coarse rung of one lane-day. Raises if any rung fails.

    ALL OR NOTHING, BY RAISING. A partial ladder is the state the completion marker exists to make
    impossible, so a rung that cannot be written must not leave the caller free to mark the base
    day complete. The caller catches this and treats the whole day as unfinished; the next tick
    redoes it.

    `base_table` lets a caller that ALREADY holds the day's rows skip the read-back entirely. NO
    CALLER PASSES IT TODAY and that is worth stating plainly rather than implying otherwise: every
    path into this function runs through `gap_fill._finalize_written_day`, which receives counts
    from a lane adapter, never a table. The parameter exists for the forward API-direct writers of
    RUNBOOK 0.32.1 decision 1, which WILL hold the rows they just fetched.

    THE MEMORY RISK IS REAL AND NAMED: the read-back materialises the whole base day at once, which
    is precisely what `soil-survey`'s ~3,016-part streaming export avoids on the write side. At its
    full 1.5M-delineation universe that table is gigabytes. `MAX_DERIVATION_ROWS` refuses rather
    than swaps, so the failure is loud -- but a lane that trips it needs this function taught to
    fold rung-by-rung over batches, which is only correct for aggregates that are associative
    (`sum`/`min`/`max`/`all`/`any`) and NOT for `mean`.
    """
    source = base_table if base_table is not None else pl.from_arrow(store.read_partition(layer, kind, 13, day))
    if not isinstance(source, pl.DataFrame):  # pragma: no cover - a chunked read would be a store change
        source = pl.DataFrame(source)
    reports: list[DerivedTierReport] = []
    notes: list[str] = []
    for tier in tiers:
        try:
            derived = derive_tier(source, stream=layer, tier=tier)
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
            _retract_tier(store, layer=layer, kind=kind, tier=tier, day=day)
            continue
        reports.append(_write_tier(store, derived, layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now))
    return DerivationResult(tiers=tuple(reports), notes=tuple(notes))


def _retract_tier(store: ObjectStore, *, layer: str, kind: PartitionKind, tier: ZoomTier, day: date) -> None:
    """Empty one rung: clear its completion claim FIRST, then delete every part it held.

    `retract_partition_tier` rather than `prune_surplus_parts(written_part_count=0)`: that prune
    REFUSES zero on purpose, because a prune may only ever trail a completed write. Emptying a rung
    is a different intent and has its own named operation.
    """
    pruned = store.retract_partition_tier(layer, kind, tier, day)
    if pruned.failures:
        raise TierWriteError(
            f"{layer} z{tier} {day.isoformat()}: this rung derived to no rows, but the parts a previous derivation "
            f"left there could not be removed, so readers at this zoom would keep being served rows the base day no "
            f"longer holds: {'; '.join(pruned.failures)}"
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
        PartitionCompletion(part_count=part_count, row_count=row_count, completed_at=now(), run_id=run_id),
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


__all__ = [
    "DERIVED_ROWS_PER_PART",
    "DerivationResult",
    "DerivedTierReport",
    "TierWriteError",
    "derive_and_write_day_tiers",
]
