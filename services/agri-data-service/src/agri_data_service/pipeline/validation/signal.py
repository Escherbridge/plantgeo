"""Reconcile the signal-plane Parquet export against the SOURCE SYSTEM, never local state.

Layer L2 (pipeline/validation): needs the database; may import `foundation`, `warehouse`, and
`agri_data_service.execution` (a pre-existing, non-lattice orchestration package this rebuild does
not touch; verified acyclic -- `execution` imports neither `pipeline` nor `planes`). May NOT import
`method`, `planes`, or `interface`.

Two independent checks, deliberately not one:

  1. `find_missing_export_partitions` -- discoverable by LISTING objects, never by opening one
     (`conductor/code_styleguides/layer-lanes.md` section 4). Detects a day the export PIPELINE
     itself failed to produce any object for -- neither a part file nor a governed-absence marker
     -- which is a defect in this lane's own run, not a source-side gap.
  2. `classify_signal_day` / `validate_signal_export_day` -- reconciles what the lane WROTE
     (`exported_signal_row_counts`, computed from the actual exported `pa.Table`) against what the
     SOURCE holds, reusing `execution.coverage_contract`'s already-proven per-signal reconciliation
     over `agri.signal_observation` and `agri.signal_coverage_audit`. This is independent evidence,
     not the exporter's own SQL re-run: `execution.historical_writer.*` populates those two tables
     at ingest time, on a completely different code path than `pipeline/lanes/signal.py`'s export.

`docs/lanes/weather-observations.md` section 5 item 1 names the ready-made test case this module
must catch: `surface_shortwave_radiation` carrying zero rows for NASA POWER in July 2026 while every
sibling NASA signal carries the full lattice, with no governed-absence row explaining it.
`execution.coverage_contract.DayState.MISSING` already captures exactly that per signal, with no
cross-signal comparison needed -- see `classify_signal_day`.

THE TIER IS PINNED, NOT A PARAMETER. Check 1 asks whether the export pipeline ran, and the export
pipeline writes exactly `WRITTEN_ZOOM_TIER`. Accepting a `zoom` argument here would let a caller aim
"did the export run?" at a rung no export ever targets, where the honest answer is always "no" and
the report would read as this lane's own failure rather than as derivation lag -- precisely the
attribution error the two-checks split above exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from agri_data_service.execution.coverage_census import census_contracts
from agri_data_service.execution.coverage_contract import (
    LANE_COVERAGE_CONTRACTS,
    DayCoverage,
    DayState,
    LaneCoverageContract,
)
from agri_data_service.foundation.parquet.paths import missing_partition_days, partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The rung the lane's own export lands on: the most detailed one, the one nothing generalised.
# Derived from the ladder so adding a rung above cannot leave this validator checking a stale tier.
WRITTEN_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

# Every contract currently declared belongs to this plane -- `agri.signal_observation` and
# `agri.signal_coverage_audit` back no other Parquet stream (docs/lanes/weather-observations.md
# section 1). A future lane adding its own contract to `LANE_COVERAGE_CONTRACTS` would need to
# narrow this default explicitly; it is named here rather than re-declared to avoid a second,
# driftable copy of the governed 19-signal list.
SIGNAL_PLANE_COVERAGE_CONTRACTS: Final[tuple[LaneCoverageContract, ...]] = LANE_COVERAGE_CONTRACTS


class SignalValidationError(RuntimeError):
    """Raised when a signal-plane validation cannot be carried out as asked."""


class SignalLaneOutcome(StrEnum):
    """What one signal's one contracted day looks like once export and source are compared."""

    MATCHED = "matched"
    """The source holds this day complete, and the export wrote exactly that many rows."""

    EXPORT_SHORTFALL = "export_shortfall"
    """The source holds this day complete; the export wrote FEWER rows. A defect in this lane."""

    EXPORT_EXCESS = "export_excess"
    """The export wrote MORE rows than the source's own accounting shows. Also a defect."""

    UNEXPLAINED_SOURCE_GAP = "unexplained_source_gap"
    """The source holds nothing for this day and no governed absence explains it.

    Not an export defect -- the export faithfully transcribes an empty source. This is the
    radiation-hole class of finding: real, live, and reportable, never silently accepted.
    """

    SOURCE_PARTIAL = "source_partial"
    """The source covers only part of the lattice for this day (THIN). A partial fill, not a fabrication."""

    GOVERNED_ABSENT = "governed_absent"
    """The source explicitly recorded a `no_data` audit row covering this day. Expected, not a failure."""


@dataclass(frozen=True, slots=True)
class SignalLaneFinding:
    """One signal's reconciliation for one contracted day: the source's own state plus what landed."""

    day: date
    source_key: str
    signal_name: str
    source_state: DayState
    source_observed_cell_count: int
    source_expected_cell_count: int
    exported_row_count: int
    outcome: SignalLaneOutcome

    @property
    def is_reportable_gap(self) -> bool:
        """True for every outcome an operator must act on or at least see; false for the two clean ones."""
        return self.outcome not in (SignalLaneOutcome.MATCHED, SignalLaneOutcome.GOVERNED_ABSENT)

    def describe(self) -> str:
        """Render one finding as an actionable line: the day, the lane, the signal, and the source response."""
        return (
            f"{self.day.isoformat()}: lane {self.source_key!r} signal {self.signal_name!r} -- "
            f"source holds {self.source_observed_cell_count}/{self.source_expected_cell_count} cells "
            f"(state={self.source_state.value}), export wrote {self.exported_row_count} row(s) "
            f"-> {self.outcome.value}"
        )


def classify_signal_day(
    *,
    day: date,
    source_key: str,
    signal_name: str,
    coverage: DayCoverage,
    exported_row_count: int,
) -> SignalLaneFinding:
    """Classify one signal's one day from the source's own reconciled state and what was exported.

    Pure: takes the already-reconciled `DayCoverage` rather than a session, so the whole
    classification ladder is unit-testable without a database -- the same split
    `execution/coverage_contract.py`'s own module docstring describes between "decide what a day's
    state is" and "ask the database what it holds".
    """
    if exported_row_count > coverage.observed_cell_count:
        # Checked first regardless of source state: the export holding MORE than the source's own
        # count is always an anomaly, never explained by a partial or absent day.
        outcome = SignalLaneOutcome.EXPORT_EXCESS
    elif coverage.state is DayState.ABSENT:
        outcome = SignalLaneOutcome.GOVERNED_ABSENT
    elif coverage.state is DayState.MISSING:
        outcome = SignalLaneOutcome.UNEXPLAINED_SOURCE_GAP
    elif coverage.state is DayState.THIN:
        outcome = SignalLaneOutcome.SOURCE_PARTIAL
    elif exported_row_count < coverage.observed_cell_count:
        outcome = SignalLaneOutcome.EXPORT_SHORTFALL
    else:
        outcome = SignalLaneOutcome.MATCHED
    return SignalLaneFinding(
        day=day,
        source_key=source_key,
        signal_name=signal_name,
        source_state=coverage.state,
        source_observed_cell_count=coverage.observed_cell_count,
        source_expected_cell_count=coverage.expected_cell_count,
        exported_row_count=exported_row_count,
        outcome=outcome,
    )


def exported_signal_row_counts(table: pa.Table, *, day: date) -> Mapping[str, int]:
    """Count what the lane actually WROTE for `day`, per `signal_name`, from the exported table itself.

    Refuses a table carrying any other day: mixing days here would silently compare one day's
    export against a different day's source reconciliation.
    """
    if table.num_rows == 0:
        return {}
    observed_days = set(table.column("observed_day").to_pylist())
    stray = observed_days - {day}
    if stray:
        raise SignalValidationError(
            f"exported table carries day(s) {sorted(stray)} other than the {day.isoformat()} being validated"
        )
    counts: dict[str, int] = {}
    for name in table.column("signal_name").to_pylist():
        counts[name] = counts.get(name, 0) + 1
    return counts


async def validate_signal_export_day(
    session: AsyncSession,
    *,
    day: date,
    exported_table: pa.Table,
    contracts: Sequence[LaneCoverageContract] = SIGNAL_PLANE_COVERAGE_CONTRACTS,
) -> tuple[SignalLaneFinding, ...]:
    """Reconcile one exported Parquet day against `agri.signal_observation`/`signal_coverage_audit`.

    `exported_table` must be what the lane actually wrote -- the table `read_signal_day` produced,
    or one read back from the persisted partition -- never re-derived from the governed SQL a
    second time; that would only prove the export code agrees with itself, which is exactly what
    `conductor/code_styleguides/layer-lanes.md` section 4 forbids.

    Each contract is narrowed to a one-day window (`earliest_required_day` replaced by `day`)
    before reconciling, so this costs one bounded query per contract rather than re-scanning each
    contract's entire multi-year required history to answer a single day's question.
    """
    exported_counts = exported_signal_row_counts(exported_table, day=day)
    narrowed = tuple(replace(contract, earliest_required_day=day) for contract in contracts)
    censuses = await census_contracts(session, narrowed, through_day=day, today=day)
    findings: list[SignalLaneFinding] = []
    for census in censuses:
        for reconciliation in census.signals:
            if not reconciliation.days:
                # A weekly-cadence contract whose anchor weekday is not `day`: nothing was
                # contracted for `day` at all, so there is nothing to reconcile -- not a gap.
                continue
            if len(reconciliation.days) != 1:
                raise SignalValidationError(
                    f"narrowing {census.contract.source_key!r}/{reconciliation.signal_name!r} to {day.isoformat()} "
                    f"produced {len(reconciliation.days)} contracted days, not exactly one"
                )
            findings.append(
                classify_signal_day(
                    day=day,
                    source_key=census.contract.source_key,
                    signal_name=reconciliation.signal_name,
                    coverage=reconciliation.days[0],
                    exported_row_count=exported_counts.get(reconciliation.signal_name, 0),
                )
            )
    return tuple(findings)


def find_missing_export_partitions(
    store: ObjectStore,
    *,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
) -> tuple[date, ...]:
    """Days in `[first_day, last_day]` with neither a part file nor a governed-absence marker at the written tier.

    Discovered by LISTING object keys, never by opening one -- `layer-lanes.md` section 4. This
    catches a defect distinct from `validate_signal_export_day`'s: the export PIPELINE itself never
    ran (or failed) for a contracted day, as opposed to the source holding a genuine gap the export
    faithfully transcribed.

    Scoped to `WRITTEN_ZOOM_TIER` on both halves -- the listing and the census -- so "the export
    never ran for this day" is a claim about the rung the export actually targets. A listing that
    spanned the ladder would count a derived coarse rung as evidence the base export ran, which is
    exactly backwards: the coarse rung is DOWNSTREAM of the base one and cannot exist without it.

    Returns strictly `missing` days. Days holding part files without a completion marker are
    `incomplete` -- a distinct defect reported by `find_incomplete_export_partitions` -- so an
    operator can distinguish "never attempted" from "attempted and killed mid-upload".
    """
    if last_day < first_day:
        raise SignalValidationError(f"partition window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    keys: list[str] = []
    for year in range(first_day.year, last_day.year + 1):
        keys.extend(store.list_partition_keys(SIGNAL_PLANE_STREAM, kind, WRITTEN_ZOOM_TIER, year=year))
    return missing_partition_days(
        layer=SIGNAL_PLANE_STREAM,
        kind=kind,
        zoom=WRITTEN_ZOOM_TIER,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )


def find_incomplete_export_partitions(
    store: ObjectStore,
    *,
    kind: PartitionKind,
    first_day: date,
    last_day: date,
) -> tuple[date, ...]:
    """Days in `[first_day, last_day]` holding part files without a completion marker at the written tier.

    Parallel to `find_missing_export_partitions`, scoped the same way, but reports the distinct
    failure mode where the export DID run but was killed part-way through uploading. An operator
    reading "0 missing" over a lane that crashes mid-export every night is being misled; this
    check surfaces the incomplete days so both counts are honest.
    """
    if last_day < first_day:
        raise SignalValidationError(f"partition window {first_day.isoformat()}..{last_day.isoformat()} runs backwards")
    keys: list[str] = []
    for year in range(first_day.year, last_day.year + 1):
        keys.extend(store.list_partition_keys(SIGNAL_PLANE_STREAM, kind, WRITTEN_ZOOM_TIER, year=year))
    statuses = partition_day_statuses(
        layer=SIGNAL_PLANE_STREAM,
        kind=kind,
        zoom=WRITTEN_ZOOM_TIER,
        first_day=first_day,
        last_day=last_day,
        keys=keys,
    )
    return tuple(day for day, status in statuses.items() if status == "incomplete")
