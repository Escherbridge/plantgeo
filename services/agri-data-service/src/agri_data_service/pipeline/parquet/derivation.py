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
from agri_data_service.foundation.parquet.paths import MAX_PART_INDEX
from agri_data_service.pipeline.parquet.objectstore import (
    BANDED_BASE_ROWS_PER_PART,
    GovernedAbsenceConflictError,
    ParquetWriteError,
    required_part_count,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    BASE_ZOOM_TIER,
    DERIVED_ZOOM_TIERS,
    MAX_DERIVATION_ROWS,
    TIER_RESOLUTION_DEGREES,
    GridAggregation,
    derive_tier,
    floor_to_resolution,
    grid_key_columns,
    tier_derivation,
    tier_resolution_degrees,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]
    from duckdb import DuckDBPyConnection

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import (
        AbsenceWriteReceipt,
        ObjectStore,
        ParquetWriteReceipt,
        ReadPartReceipt,
    )

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

#: Aggregates the fold admits. `sum`/`min`/`max`/`all`/`any` are associative -- the aggregate of the
#: per-band aggregates IS the day's -- and `null` is constant. `first` is admitted ONLY under the
#: constancy contract (one distinct value per group at every derived rung), which the fold ENFORCES
#: (`_refuse_varying_first`): the whole-day `first` is the first base row in base order, the banded one
#: is not, and they agree only when there is nothing to choose. `mean` and `sha256-lines` are not
#: associative and are refused at declaration.
BAND_SAFE_AGGREGATES: Final[frozenset[str]] = frozenset({"sum", "min", "max", "first", "all", "any", "null"})
#: Rungs a banded lane derives PER BAND from in-band base rows (z9, z5)...
PER_BAND_TIERS: Final[tuple[ZoomTier, ...]] = DERIVED_ZOOM_TIERS[:-1]
#: ...and the one rung it derives from the WRITTEN rung above it (z0 from z5), never from the base.
CHAINED_TIER: Final[ZoomTier] = DERIVED_ZOOM_TIERS[-1]
CHAINED_TIER_SOURCE: Final[ZoomTier] = DERIVED_ZOOM_TIERS[-2]
#: Bands are counted in z5 cells, so every per-band rung's pitch must divide the z5 pitch (0.01 | 0.2).
BAND_EDGE_PITCHES: Final[tuple[float, ...]] = tuple(TIER_RESOLUTION_DEGREES[tier] for tier in PER_BAND_TIERS)
#: The z5 pitch: the unit bands are counted in, and the pitch the declared base lattice must divide.
Z5_CELL_PITCH_DEGREES: Final[float] = TIER_RESOLUTION_DEGREES[CHAINED_TIER_SOURCE]
_RATIO_TOLERANCE: Final = 1e-9
#: How far `lat / base` may sit from an integer before a row is refused as not a lattice origin. Float
#: noise on an origin is ~1e-12 at 50 deg / 0.0025 deg; a centroid is 0.5 away.
_LATTICE_ORIGIN_TOLERANCE: Final = 1e-6


class TierWriteError(RuntimeError):
    """Raised when a lane-day's coarse rungs cannot be written as a complete set."""


class LatitudeBandingError(ValueError):
    """Raised when a latitude-band declaration is unlawful for the lane it names."""


class NonConstantFirstError(TierWriteError):
    """Raised when a `first` column varies within a group the banded fold would aggregate; names column and group."""


class UnknownPartBoundsError(TierWriteError):
    """Raised when a banded day's parts have no recorded cell range and reading it whole could exceed the cap."""


class AbsenceLadderError(RuntimeError):
    """Raised when a lane-day's governed absence cannot be marked at every rung it was asked for.

    A SEPARATE NAME FROM `TierWriteError`, because the two failures ask for different repairs. A tier
    write failing means rows could not be generalised and the day owes a re-derivation; an absence
    ladder failing means a day upstream had nothing for is marked at no rung, and the next tick
    re-selects it as an ordinary gap. `GovernedAbsenceConflictError` stays the answer when the day
    holds DATA, so a caller that already handles a conflict keeps handling it.
    """


def _integer_ratio(value: float, unit: float) -> int | None:
    """`value / unit` as a positive integer when it is one within float tolerance, else `None`."""
    quotient = value / unit
    nearest = round(quotient)
    if nearest < 1 or abs(quotient - nearest) >= _RATIO_TOLERANCE:
        return None
    return nearest


@dataclass(frozen=True, slots=True)
class LatitudeBanding:
    """Fold a `GridAggregation` lane-day over bands of `z5_cells_per_band` z5 (0.2 deg) cells each.

    MEMBERSHIP IS EXACT INTEGER ARITHMETIC ON THE DECLARED LATTICE, never IEEE division of a latitude
    by 0.2. A latitude is first a base-cell index -- `round(lat / base_resolution_degrees)`, where
    `round` absorbs the ulp noise of any division path because a lattice origin is `k * base` -- then
    a z5 cell (`// cells_per_z5`, an exact integer because `__post_init__` requires the base pitch to
    divide 0.2), then a band (`// z5_cells_per_band`). Both integer steps agree between Python and
    Polars, and the first step is the one place a float is touched. Polars 1.43 evaluates `col / c`
    through different paths for 1-row and N-row frames (`32.8 / 0.2` is 163.999... on one and 164.0 on
    the other, measured 2026-09-18), which is why `floor(lat / 0.2)` was NOT one rule. z0 (5.0) is not
    required to fit a band: it is derived from the written z5 rung. See `AGENTS.md`, "Latitude-band
    folding".
    """

    z5_cells_per_band: int
    base_resolution_degrees: float

    def __post_init__(self) -> None:
        if not isinstance(self.z5_cells_per_band, int) or self.z5_cells_per_band < 1:
            raise LatitudeBandingError(
                f"a band is a whole number of z5 cells, at least one, got {self.z5_cells_per_band!r}; to declare a "
                f"band by its height in degrees use `LatitudeBanding.of_height(degrees, base_resolution_degrees)`"
            )
        base = self.base_resolution_degrees
        if base <= 0 or _integer_ratio(Z5_CELL_PITCH_DEGREES, base) is None:
            raise LatitudeBandingError(
                f"base rung pitch {self.base_resolution_degrees} deg does not divide the z5 pitch "
                f"{Z5_CELL_PITCH_DEGREES} deg: band edges must be multiples of every per-band rung pitch "
                f"{BAND_EDGE_PITCHES} and of the base rung pitch so flooring composes exactly -- a base cell "
                f"straddling an edge would be aggregated in two bands and the fold would no longer equal the "
                f"whole-day derivation"
            )

    @classmethod
    def of_height(cls, band_height_degrees: float, base_resolution_degrees: float) -> LatitudeBanding:
        """Declare a band by its height in degrees; refused unless the height is a whole number of z5 cells."""
        cells = _integer_ratio(band_height_degrees, Z5_CELL_PITCH_DEGREES)
        if cells is None:
            raise LatitudeBandingError(
                f"band height {band_height_degrees} deg is not a multiple of {Z5_CELL_PITCH_DEGREES} deg: band edges "
                f"must be multiples of every per-band rung pitch {BAND_EDGE_PITCHES} and of the base rung pitch "
                f"{base_resolution_degrees} so flooring composes exactly -- a cell straddling an edge would be "
                f"aggregated in two bands and the fold would no longer equal the whole-day derivation"
            )
        return cls(z5_cells_per_band=cells, base_resolution_degrees=base_resolution_degrees)

    @property
    def band_height_degrees(self) -> float:
        """The band's height in degrees, for messages only; membership never uses it."""
        return self.z5_cells_per_band * Z5_CELL_PITCH_DEGREES

    @property
    def cells_per_z5(self) -> int:
        """How many base cells one z5 cell spans -- an exact integer, `__post_init__` made sure."""
        cells = _integer_ratio(Z5_CELL_PITCH_DEGREES, self.base_resolution_degrees)
        if cells is None:  # pragma: no cover - established by __post_init__ on a frozen instance
            raise LatitudeBandingError(f"base pitch {self.base_resolution_degrees} no longer divides the z5 pitch")
        return cells

    def base_cell_index_expression(self, latitude_column: str) -> pl.Expr:
        """`round(lat / base)`: the lattice row index; NaN and null are unlocated (null) and belong to no band.

        UNAMBIGUOUS ONLY FOR LATTICE ORIGINS (`k * base`, which spec FR-6 requires the lane to emit). A
        centroid (`k * base + base / 2`) sits exactly between two integers and `round` may pick either,
        so `write_banded_base_day` refuses such rows (`refuse_non_lattice_origins`).
        """
        return (pl.col(latitude_column).fill_nan(None) / self.base_resolution_degrees).round().cast(pl.Int64)

    def refuse_non_lattice_origins(self, frame: pl.DataFrame, latitude_column: str, *, layer: str) -> None:
        """Refuse a frame whose located latitudes are not `k * base` within tolerance: origins, not centroids."""
        quotient = pl.col(latitude_column).fill_nan(None) / self.base_resolution_degrees
        offenders = frame.filter((quotient - quotient.round()).abs() > _LATTICE_ORIGIN_TOLERANCE)
        if offenders.height:
            sample = offenders[latitude_column].head(3).to_list()
            raise LatitudeBandingError(
                f"{layer}: {offenders.height:,} row(s) have a latitude that is not a multiple of the declared base "
                f"pitch {self.base_resolution_degrees} deg (e.g. {sample}); base rows must be lattice origins, not "
                f"centroids -- band membership rounds `lat / base` to the nearest integer, which is unambiguous only "
                f"at an origin"
            )

    def z5_cell_index_expression(self, latitude_column: str) -> pl.Expr:
        """The z5 cell a row is in, as exact integer floor division of its base-cell index."""
        return self.base_cell_index_expression(latitude_column) // self.cells_per_z5

    def band_index_expression(self, latitude_column: str) -> pl.Expr:
        """The band each row is in: `z5_cell // z5_cells_per_band`, integer floor division."""
        return self.z5_cell_index_expression(latitude_column) // self.z5_cells_per_band

    def band_of_cell(self, z5_cell: int) -> int:
        """The band a z5 cell index is in -- the same integer floor division, on integers Python and Polars agree on."""
        return z5_cell // self.z5_cells_per_band

    def cell_interval(self, band: int) -> tuple[int, int]:
        """The INCLUSIVE z5 cell index range `(first, last)` of `band`."""
        first = band * self.z5_cells_per_band
        return first, first + self.z5_cells_per_band - 1

    def part_selector(self, band: int, part_cell_ranges: Mapping[str, PartCellRange]) -> Callable[[str], bool]:
        """Select the parts whose recorded z5 cell range meets `band`; a part with no recorded range is selected."""
        first, last = self.cell_interval(band)

        def selected(relative_path: str) -> bool:
            recorded = part_cell_ranges.get(relative_path)
            if recorded is None or recorded.z5_cell_min is None or recorded.z5_cell_max is None:
                return True
            return recorded.z5_cell_max >= first and recorded.z5_cell_min <= last

        return selected


@dataclass(frozen=True, slots=True)
class PartCellRange:
    """What one written base part holds, as recorded by the writer that produced it: its digest and z5 cell range.

    `sha256` is what makes the record trustworthy across time: the fold re-checks it against the bytes
    it reads back, so a part another process rewrote in place since is a refusal, never a silently
    mis-banded read. `None` bounds mean the part holds only unlocated rows and is read for every band.
    """

    sha256: str
    z5_cell_min: int | None
    z5_cell_max: int | None


@dataclass(frozen=True, slots=True)
class BandedBaseWrite:
    """Everything `write_banded_base_day` produced: the receipts, and the per-part cell ranges the fold reads by."""

    receipts: tuple[ParquetWriteReceipt, ...]
    part_cell_ranges: Mapping[str, PartCellRange]


_BANDINGS: Final[dict[str, LatitudeBanding]] = {}


def register_latitude_banding(stream: str, banding: LatitudeBanding) -> LatitudeBanding:
    """Declare that `stream` derives its coarse rungs band by band; refuses a lane the fold is not exact for.

    Refused: a strategy other than `GridAggregation` (nothing to band on), and any aggregate outside
    `BAND_SAFE_AGGREGATES`, named by column -- a `mean` of per-band means is not the day's mean and a
    digest of per-band digests is not the day's digest. Re-declaring identically is a no-op. Must run
    AFTER `register_tier_derivation` and BEFORE the first derive, from a pipeline module the executor
    imports (the lane package's adapter/products module); see `AGENTS.md`, "Latitude-band folding".
    """
    strategy = tier_derivation(stream).strategy
    if not isinstance(strategy, GridAggregation):
        raise LatitudeBandingError(
            f"{stream}: only a GridAggregation lane can be folded over latitude bands; its strategy is "
            f"{type(strategy).__name__}, which has no latitude column to band on"
        )
    unsafe = [(spec.column, spec.how) for spec in strategy.aggregations if spec.how not in BAND_SAFE_AGGREGATES]
    if unsafe:
        named = ", ".join(f"{column!r} aggregates {how!r}" for column, how in unsafe)
        raise LatitudeBandingError(
            f"{stream}: {named}; that aggregate is not associative across bands, so a banded derivation would "
            f"publish a value the whole day never had. Banded mode admits only {sorted(BAND_SAFE_AGGREGATES)}"
        )
    existing = _BANDINGS.get(stream)
    if existing is not None and existing != banding:
        raise LatitudeBandingError(f"stream {stream!r} already declares a different latitude banding: {existing}")
    _BANDINGS[stream] = banding
    return banding


def latitude_banding(stream: str) -> LatitudeBanding | None:
    """Return `stream`'s declared banding, or `None` -- the whole-day path every existing lane takes."""
    return _BANDINGS.get(stream)


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
    part_cell_ranges: Mapping[str, PartCellRange] | None = None,
) -> DerivationResult:
    """Derive, write, prune and mark every coarse rung of one lane-day. Raises if any rung fails.

    All or nothing by raising; `base_table` skips the read-back for a caller that already holds the
    day; `connection` is the reused DuckDB session; an empty `tiers` is refused. A lane that declared
    `register_latitude_banding` is folded band by band (`_derive_banded`), reading each band's parts
    by the `part_cell_ranges` its writer returned (`BandedBaseWrite.part_cell_ranges`); with neither
    `base_table` nor ranges the day's extent is unknown and it is read whole or refused. Every other
    lane takes the whole-day path unchanged. See `AGENTS.md` in this directory,
    "derive_and_write_day_tiers: all four rungs or none" and "Latitude-band folding".
    """
    if not tiers:
        raise TierWriteError(
            f"{layer} {day.isoformat()}: a derivation was asked for NO rungs, which would report a complete ladder "
            f"over one that was never built. Ask for {tuple(DERIVED_ZOOM_TIERS)} or a subset of it"
        )
    reports: list[DerivedTierReport] = []
    notes: list[str] = []
    emptied: list[ZoomTier] = []
    banding = latitude_banding(layer)
    landed: dict[ZoomTier, pl.DataFrame] = {}
    if banding is None:
        source = _as_frame(base_table if base_table is not None else store.read_partition(layer, kind, 13, day))
        for tier in tiers:
            try:
                derived = derive_tier(source, stream=layer, tier=tier, connection=connection)
            except Exception as error:
                raise TierWriteError(
                    f"{layer} z{tier} {day.isoformat()}: the derivation itself failed, so this day has no honest "
                    f"coarse rung and must not be marked complete: {type(error).__name__}: {error}"
                ) from error
            landed[tier] = derived
    else:
        landed = _derive_banded(
            store,
            banding,
            layer=layer,
            kind=kind,
            day=day,
            base_table=base_table,
            tiers=tiers,
            connection=connection,
            part_cell_ranges=part_cell_ranges,
        )
    for tier in tiers:
        report = _land_tier(store, landed[tier], layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now)
        if report is None:
            notes.append(
                f"{layer} z{tier} {day.isoformat()}: every base row was dropped at this rung, so it holds no parts"
            )
            emptied.append(tier)
        else:
            reports.append(report)
    return DerivationResult(tiers=tuple(reports), notes=tuple(notes), emptied=tuple(emptied))


def _land_tier(  # noqa: PLR0913 - one coordinate of the rung being landed per arg
    store: ObjectStore,
    derived: pl.DataFrame,
    *,
    layer: str,
    kind: PartitionKind,
    tier: ZoomTier,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
) -> DerivedTierReport | None:
    """Write a non-empty rung and report it, or retract an empty one and return `None`."""
    if derived.height == 0:
        # Not an error and not an absence: the base day held rows, but every one of them was
        # dropped at this rung -- an unlocated gauge, or a feature below the tier's area floor.
        # A governed absence would claim upstream had nothing, which is false.
        #
        # IT MUST STILL RETRACT WHATEVER THIS RUNG HELD BEFORE. Skipping `_write_tier`, the only
        # place a rung is pruned or re-marked, would let an earlier, larger derivation's parts AND
        # its completion marker survive, and every reader at this zoom would go on being served rows
        # the base day no longer contains, from a rung that still claims to be finished. That is the
        # stable lie this whole contract exists to prevent, arrived at from the other direction.
        _retract_tier(store, layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now)
        return None
    return _write_tier(store, derived, layer=layer, kind=kind, tier=tier, day=day, run_id=run_id, now=now)


def _derive_banded(  # noqa: PLR0913 - one coordinate of the day being folded per arg
    store: ObjectStore,
    banding: LatitudeBanding,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    base_table: pl.DataFrame | pa.Table | None,
    tiers: Sequence[ZoomTier],
    connection: DuckDBPyConnection | None,
    part_cell_ranges: Mapping[str, PartCellRange] | None,
) -> dict[ZoomTier, pl.DataFrame]:
    """Derive every requested rung of one lane-day band by band, and the chained rung from the rung above it.

    Per band: the in-band base rows (`base_table` filtered, or the parts whose recorded z5 cell range
    meets the band) are asserted under `MAX_DERIVATION_ROWS`, every `first` column is asserted constant
    per group, and `derive_tier` runs once per per-band rung. Each rung's pieces are then concatenated,
    a cell two bands both produced is merged with the same associative aggregates, and the frame is
    sorted to its grain -- the frame the whole-day path produces. z0 is `derive_tier(z5_frame)`, never
    the base. A rung is empty only when EVERY band left it empty. Nothing is written here.
    """
    strategy = _grid_strategy(layer)
    per_band = {tier for tier in tiers if tier != CHAINED_TIER}
    if CHAINED_TIER in tiers:
        per_band.add(CHAINED_TIER_SOURCE)
    whole, bands = _band_source(
        store,
        banding,
        strategy,
        layer=layer,
        kind=kind,
        day=day,
        base_table=base_table,
        part_cell_ranges=part_cell_ranges,
    )
    pieces = _derive_per_band(
        store,
        banding,
        strategy,
        whole=whole,
        bands=bands,
        per_band=per_band,
        layer=layer,
        kind=kind,
        day=day,
        connection=connection,
        part_cell_ranges=part_cell_ranges or {},
    )
    empty = _as_frame(get_stream_schema(layer, kind).arrow_schema.empty_table())
    frames = _assemble_rungs(pieces, strategy, layer=layer, day=day, empty=empty)
    if CHAINED_TIER in tiers:
        frames[CHAINED_TIER] = _derive_chained(
            frames[CHAINED_TIER_SOURCE], strategy, layer=layer, day=day, connection=connection, empty=empty
        )
    return frames


def _grid_strategy(layer: str) -> GridAggregation:
    """The banded lane's strategy; `register_latitude_banding` already refused anything else."""
    strategy = tier_derivation(layer).strategy
    if not isinstance(strategy, GridAggregation):  # pragma: no cover - refused at declaration
        raise TierWriteError(f"{layer}: banded derivation on a {type(strategy).__name__} strategy")
    return strategy


def _band_source(  # noqa: PLR0913 - one coordinate of the day being read per arg
    store: ObjectStore,
    banding: LatitudeBanding,
    strategy: GridAggregation,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    base_table: pl.DataFrame | pa.Table | None,
    part_cell_ranges: Mapping[str, PartCellRange] | None,
) -> tuple[pl.DataFrame | None, range]:
    """Decide where band rows come from and which bands the day spans.

    Returns the whole frame when the caller handed one in, or when the day's extent is unknown (then
    banding is by filtering it); else `None` with the bands enumerated from the caller's recorded part
    ranges, so each band is read from the bucket on its own. The extent is KNOWN only when every part
    the bucket lists today has a recorded range WITH bounds -- a part the ranges do not name (a
    re-export that grew the day) makes it unknown, and so do ranges that are all `None` (an export of
    unlocated rows): those parts are read whole, digest-checked against the ranges, and banded by
    filter, because enumerating zero bands would fetch nothing, skip the digest check, and retract every
    rung of a day another process may since have rewritten in place. An unknown extent on a day too
    large to read whole is refused.
    """
    if base_table is not None:
        whole = _as_frame(base_table)
    else:
        parts = store.list_day_parts(layer, kind, BASE_ZOOM_TIER, day)
        recorded = [] if part_cell_ranges is None else [part_cell_ranges.get(path) for path in parts]
        cells = [
            (part.z5_cell_min, part.z5_cell_max)
            for part in recorded
            if part is not None and part.z5_cell_min is not None and part.z5_cell_max is not None
        ]
        if parts and recorded and all(recorded) and cells:
            low, high = min(first for first, _ in cells), max(last for _, last in cells)
            return None, range(banding.band_of_cell(low), banding.band_of_cell(high) + 1)
        _refuse_unaffordable_whole_read(len(parts), layer=layer, day=day)
        read = store.read_partition_with_receipts(layer, kind, BASE_ZOOM_TIER, day)
        if part_cell_ranges:
            _refuse_rewritten_parts(read.parts, part_cell_ranges, layer=layer, day=day)
        whole = _as_frame(read.table)
    banding.refuse_non_lattice_origins(whole, strategy.latitude_column, layer=layer)
    band_of = banding.band_index_expression(strategy.latitude_column)
    low, high = whole.select(band_of.min().alias("low"), band_of.max().alias("high")).row(0)
    if low is None or high is None:
        return whole, range(0)
    return whole, range(int(low), int(high) + 1)


def _refuse_unaffordable_whole_read(part_count: int, *, layer: str, day: date) -> None:
    """Refuse to read a banded day whole when its part count, at the banded writer's part size, may exceed the cap.

    THE ESTIMATE ASSUMES `BANDED_BASE_ROWS_PER_PART` ROWS PER PART, which is what `write_banded_base_day`
    writes. A day written by another path in fewer, larger parts is UNDER-estimated here (20 parts of
    1M rows read as 5M); the listing carries no object sizes, so a byte-based bound is the recorded
    follow-up (`ObjectStoreBackend.size_of` would cost one HEAD per part).
    """
    estimate = part_count * BANDED_BASE_ROWS_PER_PART
    if part_count == 0 or estimate <= MAX_DERIVATION_ROWS:
        return
    raise UnknownPartBoundsError(
        f"{layer} {day.isoformat()}: the day's {part_count} base parts have no recorded z5 cell ranges in hand (none "
        f"were passed as `part_cell_ranges`, or the bucket lists parts they do not name), so it cannot be banded "
        f"without reading it whole -- and {part_count} parts at an ASSUMED {BANDED_BASE_ROWS_PER_PART:,} rows/part "
        f"(the banded writer's part size; a day written in larger parts is under-estimated) may hold {estimate:,} "
        f"rows, over MAX_DERIVATION_ROWS ({MAX_DERIVATION_ROWS:,}). Hand the day in as `base_table`, or pass the "
        f"`BandedBaseWrite.part_cell_ranges` its writer returned; durable per-part bounds are a recorded follow-up"
    )


def _derive_per_band(  # noqa: PLR0913 - one coordinate of the day being folded per arg
    store: ObjectStore,
    banding: LatitudeBanding,
    strategy: GridAggregation,
    *,
    whole: pl.DataFrame | None,
    bands: range,
    per_band: set[ZoomTier],
    layer: str,
    kind: PartitionKind,
    day: date,
    connection: DuckDBPyConnection | None,
    part_cell_ranges: Mapping[str, PartCellRange],
) -> dict[ZoomTier, list[pl.DataFrame]]:
    """Derive every per-band rung for every band, collecting each rung's pieces band-major.

    A band read from the bucket re-checks every fetched part's digest against the range record it was
    selected by: a part rewritten in place since the ranges were recorded is refused by name, because
    trusting its stale range would filter its rows out of every band and retract the rungs as empty.
    """
    band_of = banding.band_index_expression(strategy.latitude_column)
    pieces: dict[ZoomTier, list[pl.DataFrame]] = {tier: [] for tier in per_band}
    for band in bands:
        if whole is not None:
            band_rows = whole.filter(band_of == band)
        else:
            read = store.read_partition_with_receipts(
                layer, kind, BASE_ZOOM_TIER, day, part_selector=banding.part_selector(band, part_cell_ranges)
            )
            _refuse_rewritten_parts(read.parts, part_cell_ranges, layer=layer, day=day)
            band_rows = _as_frame(read.table).filter(band_of == band)
        if band_rows.height == 0:
            continue
        scope = _band_scope(banding, band)
        if band_rows.height > MAX_DERIVATION_ROWS:
            raise TierWriteError(
                f"{layer} {day.isoformat()}: {scope} holds {band_rows.height:,} base rows, over MAX_DERIVATION_ROWS "
                f"({MAX_DERIVATION_ROWS:,}) for one derive_tier call; declare fewer z5 cells per band for this lane "
                f"rather than raising the cap"
            )
        for tier in (tier for tier in DERIVED_ZOOM_TIERS if tier in per_band):
            _refuse_varying_first(band_rows, strategy, tier=tier, layer=layer, day=day, scope=scope)
            pieces[tier].append(
                _derive_or_fail(band_rows, layer=layer, tier=tier, day=day, connection=connection, scope=scope)
            )
    return pieces


def _refuse_rewritten_parts(
    parts: Sequence[ReadPartReceipt], part_cell_ranges: Mapping[str, PartCellRange], *, layer: str, day: date
) -> None:
    """Refuse a part whose bytes no longer match the digest its cell range was recorded against."""
    for part in parts:
        recorded = part_cell_ranges.get(part.relative_path)
        if recorded is not None and recorded.sha256 != part.sha256:
            raise TierWriteError(
                f"{layer} {day.isoformat()}: {part.relative_path} was rewritten since its z5 cell range was recorded "
                f"(sha256 {recorded.sha256[:12]}... then, {part.sha256[:12]}... now); another process re-exported this "
                f"day in place, so the ranges in hand describe bytes that are gone and the day must be derived from "
                f"the ranges of the export that now stands"
            )


def _band_scope(banding: LatitudeBanding, band: int) -> str:
    """Name one band for a message: its index, its z5 cells and (informationally) its latitudes."""
    first, last = banding.cell_interval(band)
    low = first * Z5_CELL_PITCH_DEGREES
    high = (last + 1) * Z5_CELL_PITCH_DEGREES
    return f"band {band} (z5 cells {first}..{last}, latitudes [{low:.4g}, {high:.4g}))"


def _derive_or_fail(  # noqa: PLR0913 - one coordinate of the rung being derived per arg
    rows: pl.DataFrame,
    *,
    layer: str,
    tier: ZoomTier,
    day: date,
    connection: DuckDBPyConnection | None,
    scope: str,
) -> pl.DataFrame:
    """`derive_tier` with the all-or-nothing contract: any failure is a `TierWriteError` naming the scope."""
    try:
        return derive_tier(rows, stream=layer, tier=tier, connection=connection)
    except Exception as error:
        raise TierWriteError(
            f"{layer} z{tier} {day.isoformat()}: {scope} failed to derive, so this day has no honest coarse rung "
            f"and must not be marked complete: {type(error).__name__}: {error}"
        ) from error


def _refuse_varying_first(  # noqa: PLR0913 - one coordinate of the check per arg
    rows: pl.DataFrame,
    strategy: GridAggregation,
    *,
    tier: ZoomTier,
    layer: str,
    day: date,
    scope: str,
) -> None:
    """Enforce the `first` constancy contract at `tier`: one distinct value per group, or a named refusal.

    Groups are formed exactly as `_derive_grid_tier` forms them -- coordinates floored with
    `floor_to_resolution` at the tier's pitch plus the tier's key columns -- so a column that passes
    here is one `first` could not have chosen among.
    """
    keys = grid_key_columns(strategy, tier)
    first_columns = [spec.column for spec in strategy.aggregations if spec.how == "first" and spec.column not in keys]
    if not first_columns:
        return
    resolution = tier_resolution_degrees(tier)
    coordinates = (strategy.longitude_column, strategy.latitude_column)
    grain = (*coordinates, *keys)
    coarsened = rows.drop_nulls(list(coordinates)).with_columns(
        floor_to_resolution(pl.col(column), resolution).alias(column) for column in coordinates
    )
    distinct = coarsened.group_by(grain).agg(pl.col(column).n_unique().alias(column) for column in first_columns)
    for column in first_columns:
        offenders = distinct.filter(pl.col(column) > 1)
        if offenders.height == 0:
            continue
        group = {name: value for name, value in offenders.row(0, named=True).items() if name in grain}
        raise NonConstantFirstError(
            f"{layer} z{tier} {day.isoformat()}: {scope}: column {column!r} aggregates `first` but holds "
            f"{offenders.row(0, named=True)[column]} distinct values within group {group} (and "
            f"{offenders.height - 1} more such groups); `first` is band-safe only when one value holds per group -- "
            f"the whole-day derivation would pick the first base row in base order and the banded one would not. "
            f"Key the rung on it, or make it constant"
        )


#: The band-safe aggregates as Polars expressions, mirroring `tiers._POLARS_AGGREGATES` entry for entry
#: (an all-null group sums to NULL not 0; `all`/`any` keep nulls; `null` is a TYPED null off the column).
#: Used only to merge a cell that two bands each derived a piece of; `mean`/`sha256-lines` are absent
#: because no such merge is exact for them, which is why banded mode refuses them.
_MERGE_AGGREGATES: Final[Mapping[str, Callable[[pl.Expr], pl.Expr]]] = {
    "sum": lambda column: pl.when(column.count() == 0).then(None).otherwise(column.sum()),
    "min": lambda column: column.min(),
    "max": lambda column: column.max(),
    "all": lambda column: column.all(ignore_nulls=False),
    "any": lambda column: column.any(ignore_nulls=False),
    "first": lambda column: column.first(),
    "null": lambda column: pl.when(pl.lit(value=False)).then(column.first()).otherwise(None),
}


def _assemble_rungs(
    pieces: dict[ZoomTier, list[pl.DataFrame]],
    strategy: GridAggregation,
    *,
    layer: str,
    day: date,
    empty: pl.DataFrame,
) -> dict[ZoomTier, pl.DataFrame]:
    """Concatenate each rung's band pieces, merge any cell two bands both derived a piece of, sort to the grain."""
    frames: dict[ZoomTier, pl.DataFrame] = {}
    for tier, parts in pieces.items():
        if not parts:
            frames[tier] = empty
            continue
        assembled = pl.concat(parts, how="vertical")
        frames[tier] = _merge_split_cells(assembled, strategy, tier=tier, layer=layer, day=day)
    return frames


def _merge_split_cells(
    assembled: pl.DataFrame, strategy: GridAggregation, *, tier: ZoomTier, layer: str, day: date
) -> pl.DataFrame:
    """Fold the pieces of a cell that straddled a band edge back into one row, exactly, or refuse by name.

    WHY A CELL CAN STRADDLE AT ALL: `_derive_grid_tier` floors each rung independently
    (`floor(lat / 0.01)` for z9, `floor(lat / 0.2)` for z5), and IEEE division does not compose --
    a row at 32.8 lands in the z9 cell whose origin prints as 32.79 while its z5 cell is 164, so the
    z9 cell holds rows from two bands when 164 opens one. Band membership follows the z5 cell, which
    keeps every z5 cell whole; a split z9 cell is re-aggregated here with the same associative
    aggregates, which is exactly what the whole-day derivation computed over those rows in one go.
    `first` is merged only under the constancy contract, checked across the split.
    """
    grain = (strategy.longitude_column, strategy.latitude_column, *grid_key_columns(strategy, tier))
    duplicated = assembled.select(grain).is_duplicated()
    if not duplicated.any():
        return assembled.sort(grain)
    split = assembled.filter(duplicated)
    keys = grid_key_columns(strategy, tier)
    aggregations = [spec for spec in strategy.aggregations if spec.column not in keys]
    first_columns = [spec.column for spec in aggregations if spec.how == "first"]
    if first_columns:
        distinct = split.group_by(grain).agg(pl.col(column).n_unique().alias(column) for column in first_columns)
        for column in first_columns:
            offenders = distinct.filter(pl.col(column) > 1)
            if offenders.height:
                group = {name: value for name, value in offenders.row(0, named=True).items() if name in grain}
                raise NonConstantFirstError(
                    f"{layer} z{tier} {day.isoformat()}: cell {group} was derived in two bands and column "
                    f"{column!r} aggregates `first` with a different value on each side; `first` is band-safe "
                    f"only when one value holds per group. Key the rung on it, or make it constant"
                )
    merged = split.group_by(grain).agg(
        *(_MERGE_AGGREGATES[spec.how](pl.col(spec.column)).alias(spec.column) for spec in aggregations)
    )
    return pl.concat([assembled.filter(~duplicated), merged.select(assembled.columns)], how="vertical").sort(grain)


def _derive_chained(  # noqa: PLR0913 - one coordinate of the rung being chained per arg
    source: pl.DataFrame,
    strategy: GridAggregation,
    *,
    layer: str,
    day: date,
    connection: DuckDBPyConnection | None,
    empty: pl.DataFrame,
) -> pl.DataFrame:
    """z0 from the assembled z5 frame, under the same `first` contract; an empty z5 gives an empty z0."""
    if source.height == 0:
        return empty
    scope = f"from the z{CHAINED_TIER_SOURCE} rung"
    _refuse_varying_first(source, strategy, tier=CHAINED_TIER, layer=layer, day=day, scope=scope)
    return _derive_or_fail(source, layer=layer, tier=CHAINED_TIER, day=day, connection=connection, scope=scope)


def write_banded_base_day(  # noqa: PLR0913 - one coordinate of the day being written per arg
    store: ObjectStore,
    table: pl.DataFrame | pa.Table,
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    rows_per_part: int = BANDED_BASE_ROWS_PER_PART,
) -> BandedBaseWrite:
    """Write a banded lane's BASE rung band-major: parts cut at band edges, `rows_per_part` rows at most, from 0.

    Sorted by band then latitude then longitude, so each part's z5 cell range lies within ONE band and
    the banded read-back fetches exactly the band's parts. Rows with a null or NaN latitude belong to
    no band and are written last; their parts record no range and are therefore read for every band,
    where the band filter drops them. The ranges are RETURNED, not cached: the caller hands them to
    `derive_and_write_day_tiers`, and each carries the part's digest so a later rewrite is caught. The
    whole part plan is checked against `MAX_PART_INDEX` BEFORE the first put, so a day that cannot be
    numbered writes nothing.
    """
    banding = latitude_banding(layer)
    if banding is None:
        raise LatitudeBandingError(
            f"{layer}: declares no latitude banding, so its base rung is written with `ObjectStore.write_partition` "
            f"like every other lane's; this writer exists for the band-major layout the fold reads back by band"
        )
    strategy = _grid_strategy(layer)
    frame = _as_frame(table)
    banding.refuse_non_lattice_origins(frame, strategy.latitude_column, layer=layer)
    band_column = "__latitude_band"
    ordered = frame.with_columns(banding.band_index_expression(strategy.latitude_column).alias(band_column)).sort(
        [band_column, strategy.latitude_column, strategy.longitude_column], nulls_last=True
    )
    bands = ordered.partition_by(band_column, maintain_order=True, include_key=False)
    planned = sum(required_part_count(band.height, rows_per_part) for band in bands)
    if planned > MAX_PART_INDEX + 1:
        raise ParquetWriteError(
            f"{layer} {day.isoformat()}: {ordered.height:,} rows over {len(bands)} bands at {rows_per_part:,} "
            f"rows/part need {planned:,} parts, but the layout numbers at most {MAX_PART_INDEX + 1:,} "
            f"(`MAX_PART_INDEX`); widen the parts rather than the layout"
        )
    receipts: list[ParquetWriteReceipt] = []
    ranges: dict[str, PartCellRange] = {}
    cell_of = banding.z5_cell_index_expression(strategy.latitude_column)
    for band in bands:
        for start in range(0, band.height, rows_per_part):
            part = band.slice(start, rows_per_part)
            receipt = store.write_partition(
                part.to_arrow(), layer=layer, kind=kind, zoom=BASE_ZOOM_TIER, day=day, part_index=len(receipts)
            )
            low, high = part.select(cell_of.min().alias("low"), cell_of.max().alias("high")).row(0)
            receipts.append(receipt)
            ranges[receipt.relative_path] = PartCellRange(
                sha256=receipt.sha256,
                z5_cell_min=None if low is None else int(low),
                z5_cell_max=None if high is None else int(high),
            )
    return BandedBaseWrite(receipts=tuple(receipts), part_cell_ranges=ranges)


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

    The all-or-nothing rule, the single shared `GovernedAbsence`, the rollback bound, and why an
    already-marked rung is re-written: see `AGENTS.md` in this directory, "Writing an absence ladder".
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
    "BAND_SAFE_AGGREGATES",
    "DERIVED_ROWS_PER_PART",
    "AbsenceLadderError",
    "BandedBaseWrite",
    "DerivationResult",
    "DerivedTierReport",
    "LatitudeBanding",
    "LatitudeBandingError",
    "NonConstantFirstError",
    "PartCellRange",
    "TierWriteError",
    "UnknownPartBoundsError",
    "derive_and_write_day_tiers",
    "govern_day_absent",
    "latitude_banding",
    "register_latitude_banding",
    "write_absence_ladder",
    "write_banded_base_day",
]
