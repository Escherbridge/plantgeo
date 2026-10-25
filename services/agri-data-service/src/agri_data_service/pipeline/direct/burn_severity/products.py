"""Shim: moved to `pipeline/lanes/burn_severity/products.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.burn_severity.products import (
    burn_severity_lane_registration,
    governed_release_days,
    release_days_by_ignition_year,
)

__all__ = [
    "burn_severity_lane_registration",
    "governed_release_days",
    "release_days_by_ignition_year",
]
