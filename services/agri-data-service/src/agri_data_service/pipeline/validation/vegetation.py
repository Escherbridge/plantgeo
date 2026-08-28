"""Reconcile the vegetation (Sentinel-2 NDVI) lane's written Parquet against its SOURCE SYSTEM.

Layer L3 pipeline: may import `foundation` and `warehouse`; may NOT import `method`, `planes`, or
`interface`. The source system for this export is `agri.forecast_observation`, reached through
`agri.forecast_series` -- never `agri.signal_observation` (`docs/lanes/vegetation.md` section 4).
See `conductor/code_styleguides/layer-lanes.md` section 4 and `docs/lanes/vegetation.md` sections
5.1-5.3 and 6 for the sparse/cloud-gated grain and the known release-duplication defect this module
must surface rather than be fooled by.

THE TIER IS PINNED, NOT A PARAMETER. `written_partition_keys` is whatever the caller listed, and
this module decides which of those keys count: only the rung the exporter actually writes. That
matters even though the caller supplies the keys -- a caller that listed generously would otherwise
have a derived coarse rung silently answer "the base export ran for this day", and on a lane whose
genuine absences are cloud-gated and frequent, a false "covered" is indistinguishable from the
sparsity the module exists to respect.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.vegetation_source import SourceCellDay

# The rung the lane's own export lands on: the most detailed one, the one nothing generalised.
# Derived from the ladder so adding a rung above cannot leave this validator checking a stale tier.
WRITTEN_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

MISSING_FROM_PARQUET: Final = "missing_from_parquet"
INCOMPLETE_PARTITION: Final = "incomplete_partition"
DUPLICATE_SOURCE_RELEASES: Final = "duplicate_source_releases"


@dataclass(frozen=True, slots=True)
class VegetationReconciliationFinding:
    """One honest, named disagreement between the source system and the written Parquet plane."""

    observed_day: date
    kind: str
    detail: str


@dataclass(frozen=True, slots=True)
class VegetationReconciliationReport:
    """The full result of reconciling one cell/window against `agri.forecast_observation`."""

    first_day: date
    last_day: date
    source_day_count: int
    findings: tuple[VegetationReconciliationFinding, ...]

    @property
    def is_clean(self) -> bool:
        """Return whether the source and the written Parquet plane agree with no findings."""
        return not self.findings


def reconcile_against_source(
    *,
    source_cell_days: Sequence[SourceCellDay],
    written_partition_keys: Sequence[str],
    first_day: date,
    last_day: date,
) -> VegetationReconciliationReport:
    """Compare what the SOURCE holds against what is written -- never against local intermediate state.

    A calendar day the source itself has no row for is a governed absence and is never reported as
    a gap: Sentinel-2 NDVI is cloud-gated and genuinely sparse (`docs/lanes/vegetation.md` section
    5.3), so an honest missing day must not be turned into a false alarm. A day the source DOES hold
    but the Parquet plane has no partition or absence marker for is a real export gap.

    Days holding part files without a completion marker are reported distinctly from missing days:
    an operator reading "0 missing" over a lane that crashes mid-export every night is being misled.
    """
    source_days = {row.observed_day for row in source_cell_days}
    statuses = partition_day_statuses(
        layer=VEGETATION_PLANE_STREAM,
        kind="observed",
        zoom=WRITTEN_ZOOM_TIER,
        first_day=first_day,
        last_day=last_day,
        keys=written_partition_keys,
    )
    days_without_a_written_partition = tuple(day for day, status in statuses.items() if status == "missing")
    incomplete_days = tuple(day for day, status in statuses.items() if status == "incomplete")

    findings = [
        VegetationReconciliationFinding(
            observed_day=day,
            kind=MISSING_FROM_PARQUET,
            detail=(
                f"{VEGETATION_PLANE_STREAM!r} kind=observed has no partition or absence marker for "
                f"{day.isoformat()}, but agri.forecast_observation holds governed rows for it"
            ),
        )
        for day in sorted(day for day in days_without_a_written_partition if day in source_days)
    ]
    findings.extend(
        VegetationReconciliationFinding(
            observed_day=day,
            kind=INCOMPLETE_PARTITION,
            detail=(
                f"{VEGETATION_PLANE_STREAM!r} kind=observed holds part files for {day.isoformat()} "
                f"with no completion marker; the export was killed mid-upload and serves a PREFIX of "
                f"the release rather than the whole day"
            ),
        )
        for day in sorted(day for day in incomplete_days if day in source_days)
    )
    duplicated_cell_counts_by_day: dict[date, int] = {}
    for row in source_cell_days:
        if row.source_release_count > 1:
            duplicated_cell_counts_by_day[row.observed_day] = duplicated_cell_counts_by_day.get(row.observed_day, 0) + 1
    findings.extend(
        VegetationReconciliationFinding(
            observed_day=day,
            kind=DUPLICATE_SOURCE_RELEASES,
            detail=(
                f"{cell_count} cell(s) on {day.isoformat()} were produced by more than one "
                "agri.source_release for this lane; overlapping agri-service forecast vegetation-register runs "
                "wrote duplicate cell-day rows that the exporter's newest-release-wins dedup hides "
                "from the written Parquet -- visible only against the source"
            ),
        )
        for day, cell_count in sorted(duplicated_cell_counts_by_day.items())
    )
    return VegetationReconciliationReport(
        first_day=first_day,
        last_day=last_day,
        source_day_count=len(source_days),
        findings=tuple(findings),
    )
