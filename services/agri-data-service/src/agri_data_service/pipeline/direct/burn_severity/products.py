"""The burn-severity lane's identity and its release-day <-> ignition-year mapping.

Only one product publishes here -- `BURN_SEVERITY_STREAM` -- so this module exists for the same
reason `drought/products.py` does: to hold the constants `adapter.py`, `forward.py` and
`backfill.py` all need, in one place a test can import without pulling the whole driver.

Unlike `drought`'s weekly Tuesday walk, this lane has no calendar cadence to derive: a release day
is not "the next day on a fixed step from a floor", it is whatever `ingest/mtbs.py`'s own governed
`MTBS_ANNUAL_RELEASE_DATES` table says it is (`docs/lanes/burn-severity.md` section 3). This module
reads that table THROUGH `ingest/mtbs.py`'s own resolver functions rather than re-deriving it, so a
future governance action (dating a new fire year's completion) only has to land in one place.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from agri_data_service.ingest.mtbs import resolve_data_available_at, resolve_release_years
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration


def burn_severity_lane_registration() -> LaneRegistration:
    """Read the registered burn-severity lane fresh on every call -- never cached at import.

    `LANE_REGISTRY[BURN_SEVERITY_STREAM]` is the single place `history_floor` (2020-11-24) and
    `publication_lag_days` (7) are declared and reasoned about --
    `pipeline/parquet/lane_registry.py`, the `BURN_SEVERITY_STREAM` registration's `floor_basis`.
    This module may not edit that file (it is the join agent's to land the `writer_ceiling`/adapter
    swap in), and re-declaring its numbers here would be a second copy free to drift from the one
    the existing Postgres-reading adapter still reads.
    """
    return LANE_REGISTRY[BURN_SEVERITY_STREAM]


def release_days_by_ignition_year() -> dict[date, tuple[int, ...]]:
    """Every governed MTBS release day, mapped to the ignition-year cohort(s) it publishes.

    `resolve_release_years(None)` is `ingest/mtbs.py`'s own default -- every fire year carrying an
    established completion date in `MTBS_ANNUAL_RELEASE_DATES` -- so this reads the SAME governed
    set the Postgres-era `ingest-mtbs` job captures on every run, never a second copy of it.

    Grouped by day because the lane's own export
    (`sql/pipeline/burn_severity_day_export.sql:20-21`) reads by `data_available_at::date`, and
    nothing in `MTBS_ANNUAL_RELEASE_DATES` structurally forbids two ignition-year cohorts from
    resolving to one release day -- a single physical MTBS release could in principle close two
    fire years at once, even though today's five entries happen to land on five distinct dates.
    A direct writer that assumed a 1:1 day-to-year mapping would silently drop the second cohort
    the day that stops being true.
    """
    by_day: dict[date, list[int]] = defaultdict(list)
    for ignition_year in resolve_release_years(None):
        by_day[resolve_data_available_at(ignition_year).date()].append(ignition_year)
    return {day: tuple(sorted(years)) for day, years in by_day.items()}


def governed_release_days() -> tuple[date, ...]:
    """Every governed release day, oldest first -- the ENTIRE candidate set this lane ever walks.

    Unlike `drought`'s weekly Tuesdays, this is not a calendar walk bounded by a settled edge: MTBS
    publishes quarterly and a fire year is only a candidate at all once a human dates its completion
    in `MTBS_ANNUAL_RELEASE_DATES` (`docs/lanes/burn-severity.md` section 3). There is no
    "newest settled day given today and a publication lag" computation here, unlike
    `drought/products.py::newest_settled_tuesday` -- every entry in this tuple is already a real,
    past release, and the set only grows through a governance action (a code change), never through
    the passage of time alone. DO NOT bound this to a recent window the way an agent-tool LIST cap
    elsewhere in this project does (see this worker's brief, "MTBS's record runs 1984-present"):
    walking the full governed set, however small, is the whole point of a `release_series` lane.
    """
    return tuple(sorted(release_days_by_ignition_year()))


__all__ = [
    "burn_severity_lane_registration",
    "governed_release_days",
    "release_days_by_ignition_year",
]
