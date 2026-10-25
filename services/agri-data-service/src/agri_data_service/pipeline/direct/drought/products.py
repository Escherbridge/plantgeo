"""Shim: moved to `pipeline/lanes/drought/products.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.drought.products import (
    DAYS_PER_WEEK,
    USDM_RELEASE_WEEKDAY,
    drought_lane_registration,
    newest_settled_tuesday,
    release_weeks,
)

__all__ = [
    "DAYS_PER_WEEK",
    "USDM_RELEASE_WEEKDAY",
    "drought_lane_registration",
    "newest_settled_tuesday",
    "release_weeks",
]
