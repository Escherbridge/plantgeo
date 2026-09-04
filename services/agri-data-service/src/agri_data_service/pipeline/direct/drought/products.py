"""The drought lane's identity and weekly cadence, read from the registry rather than restated.

Only one product publishes here -- `DROUGHT_STREAM` -- so this module exists for the same reason
`climate/products.py` and `soil/products.py` do: to hold the constants `adapter.py`, `forward.py`
and `backfill.py` all need, in one place a test can import without pulling the whole driver.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.usdm_history import usdm_release_weeks
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

#: USDM releases on Tuesdays only; `date.weekday()` where Monday is 0 (`ingest/usdm.py::TUESDAY`).
USDM_RELEASE_WEEKDAY: Final = 1
DAYS_PER_WEEK: Final = 7


def drought_lane_registration() -> LaneRegistration:
    """Read the registered drought lane fresh on every call -- never cached at import.

    `LANE_REGISTRY[DROUGHT_STREAM]` is the single place `history_floor` (2022-08-09),
    `publication_lag_days` (4) and `cadence_days` (7) are declared and MEASURED --
    `pipeline/parquet/lane_registry.py`, the `DROUGHT_STREAM` registration's `floor_basis`. This
    module may not edit that file, and re-declaring its numbers here would be a second copy free to
    drift from the one the existing gap-fill/drain driver still reads.
    """
    return LANE_REGISTRY[DROUGHT_STREAM]


def newest_settled_tuesday(*, today: date, publication_lag_days: int) -> date:
    """Return the newest USDM release Tuesday this lane may be held to, given today and the lag.

    USDM releases Thursday for the preceding Tuesday (`ingest/usdm.py` module docstring);
    `publication_lag_days` (4, read from the registration) already covers that plus slack, so this
    only has to step back from `today - publication_lag_days` to the most recent Tuesday.
    """
    candidate = today - timedelta(days=publication_lag_days)
    return candidate - timedelta(days=(candidate.weekday() - USDM_RELEASE_WEEKDAY + DAYS_PER_WEEK) % DAYS_PER_WEEK)


def release_weeks(first_day: date, last_day: date) -> tuple[date, ...]:
    """Every USDM release Tuesday in `[first_day, last_day]`, oldest first.

    Delegates to `ingest.usdm_history.usdm_release_weeks` -- the SAME canonical Tuesday walk
    `pipeline/validation/drought.py` trusts for reconciliation and `ingest/usdm_history.py` trusts
    for the (now-dead) Postgres backfill -- rather than re-deriving "what counts as a release week"
    a fourth time.
    """
    return tuple(week.release_date for week in usdm_release_weeks(first_day, last_day))


__all__ = [
    "DAYS_PER_WEEK",
    "USDM_RELEASE_WEEKDAY",
    "drought_lane_registration",
    "newest_settled_tuesday",
    "release_weeks",
]
