"""Evaluate historical data targets without treating partial support or absence as data."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneCoverageTarget
    from agri_data_service.pipeline.runner.census import LaneCensus


@dataclass(frozen=True, slots=True)
class CoverageTargetStatus:
    """One fresh census assessment; no persisted success flag can mask a reopened hole."""

    required_days: int
    data_days: int
    absent_days: int
    incomplete_days: int
    missing_days: int
    minimum_days: int

    @property
    def reached(self) -> bool:
        return self.required_days > 0 and self.data_days >= self.minimum_days

    def report(self) -> dict[str, object]:
        """Keep permitted shortfall visible even when the configured target is reached."""
        return {
            "state": "target_reached" if self.reached else "target_unmet",
            "required_days": self.required_days,
            "data_days": self.data_days,
            "governed_absence_days": self.absent_days,
            "incomplete_days": self.incomplete_days,
            "missing_days": self.missing_days,
            "minimum_days": self.minimum_days,
            "remaining_days": self.required_days - self.data_days,
            "data_fraction": self.data_days / self.required_days if self.required_days else None,
        }


def assess_coverage(target: LaneCoverageTarget, census: LaneCensus) -> CoverageTargetStatus:
    """Count only full-ladder days whose source support is proven complete across every stream."""
    data = set(census.lane_data_days())
    absent = set(census.absent_days())
    days = census.days()
    incomplete = sum(
        day not in data
        and day not in absent
        and any(census.status(stream, day) != "missing" for stream in census.streams)
        for day in days
    )
    return CoverageTargetStatus(
        required_days=len(days),
        data_days=len(data),
        absent_days=len(absent),
        incomplete_days=incomplete,
        missing_days=len(days) - len(data) - len(absent) - incomplete,
        minimum_days=ceil(Decimal(len(days)) * Decimal(str(target.minimum_fraction))),
    )
