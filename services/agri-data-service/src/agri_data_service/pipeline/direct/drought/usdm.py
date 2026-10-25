"""Shim: moved to `pipeline/lanes/drought/usdm.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.drought.usdm import (
    USDM_COVERAGE,
    USDM_DROUGHT_SOURCE,
    USDM_SOURCE_SLUG,
    DroughtDaySource,
    DroughtSourceError,
    UsdmDroughtSource,
    fetch_drought_day,
)

__all__ = [
    "USDM_COVERAGE",
    "USDM_DROUGHT_SOURCE",
    "USDM_SOURCE_SLUG",
    "DroughtDaySource",
    "DroughtSourceError",
    "UsdmDroughtSource",
    "fetch_drought_day",
]
