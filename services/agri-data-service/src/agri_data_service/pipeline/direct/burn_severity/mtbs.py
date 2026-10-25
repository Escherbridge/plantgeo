"""Shim: moved to `pipeline/lanes/burn_severity/mtbs.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.burn_severity.mtbs import (
    MTBS_BURN_SEVERITY_SOURCE,
    MTBS_COVERAGE,
    MTBS_SOURCE_SLUG,
    BurnSeverityDaySource,
    BurnSeverityFetchError,
    MtbsBurnSeveritySource,
    fetch_burn_severity_release_day,
)

__all__ = [
    "MTBS_BURN_SEVERITY_SOURCE",
    "MTBS_COVERAGE",
    "MTBS_SOURCE_SLUG",
    "BurnSeverityDaySource",
    "BurnSeverityFetchError",
    "MtbsBurnSeveritySource",
    "fetch_burn_severity_release_day",
]
