"""Value types every historical backfill domain shares: the window, the cell, the row, the audit.

Part of the shared execute path that stays at `execution/` root while producers move under
`execution/<domain>/` (RUNBOOK §0.25.1 decision 2). These four types were extracted from
`historical_backfill.py` because five sibling domains -- CAMS, GloFAS, CEMS, AgERA5, ERA5 --
imported them from there, which made every one of them depend on the NASA POWER producer. A
domain package importing another domain package is exactly what the wave-2 boundary forbids
(`conductor/code_styleguides/layer-lanes.md` §1), so the shared half moved down rather than the
dependents moving sideways.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 - pydantic resolves these at runtime

from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import ContractModel


def four_calendar_years_before(value: date) -> date:
    """Return the same calendar day four years earlier, folding Feb 29 onto Feb 28."""
    try:
        return value.replace(year=value.year - 4)
    except ValueError:
        # A February 29 end date maps to February 28 when the matching past
        # calendar year is not a leap year.
        return value.replace(year=value.year - 4, day=28)


class HistoricalBackfillWindow(ContractModel):
    """One inclusive four-calendar-year historical window."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def require_exact_four_calendar_years(self) -> HistoricalBackfillWindow:
        expected_start = four_calendar_years_before(self.end_date)
        if self.start_date != expected_start:
            raise ValueError(
                "historical backfill windows must start exactly four calendar years before end_date "
                f"({expected_start.isoformat()})"
            )
        return self

    @property
    def day_count(self) -> int:
        """Return the inclusive number of daily observations per signal."""
        return (self.end_date - self.start_date).days + 1


class AnalysisGridCell(ContractModel):
    """One stable analysis-grid centroid authorized for an upstream point request."""

    cell_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    @field_validator("latitude", "longitude", mode="before")
    @classmethod
    def require_finite_coordinate(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError("analysis-cell coordinates must be finite numeric values")
        return value


@dataclass(frozen=True)
class HistoricalSignalObservation:
    """One normalized source value or explicitly preserved source missingness."""

    cell_key: str
    source_parameter: str
    signal_name: str
    observed_at: datetime
    original_value: float | None
    original_unit: str
    normalized_value: float | None
    normalized_unit: str
    quality_flag: str
    is_observed: bool
    payload_checksum: str
    # What fraction of the sub-daily observations a daily row was actually reduced from. It maps
    # onto `agri.signal_observation.coverage_fraction` (CHECK 0..1). Lanes whose provider publishes
    # one value per day leave it at 1; a lane that reduces hours to a day -- CAMS -- must set it, or
    # an 18-of-24-hour mean is written as `accepted` with no per-row trace that the day was partial.
    coverage_fraction: float = 1.0


@dataclass(frozen=True)
class HistoricalCoverageAudit:
    """Per-cell, per-signal evidence that a requested date window is complete or not."""

    cell_key: str
    source_parameter: str
    signal_name: str
    window_start: datetime
    window_end: datetime
    expected_observation_count: int
    received_observation_count: int
    status: str
