"""Shim: moved to `pipeline/lanes/evacuation_zones/products.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.evacuation_zones.products import (
    COVERED_STATE,
    DIRECT_PRODUCER,
    DIRECT_QUERY_URL,
    DIRECT_SOURCE_LITERAL,
    EVACUATION_ZONES_DIRECT_KIND,
    EVACUATION_ZONES_STREAM,
    MAX_ROWS_PER_PART,
    EvacuationZonesCoverageError,
    bbox_unconfigured_reason,
    refuse_uncovered_state,
    resolve_coverage_bbox,
)

__all__ = [
    "COVERED_STATE",
    "DIRECT_PRODUCER",
    "DIRECT_QUERY_URL",
    "DIRECT_SOURCE_LITERAL",
    "EVACUATION_ZONES_DIRECT_KIND",
    "EVACUATION_ZONES_STREAM",
    "MAX_ROWS_PER_PART",
    "EvacuationZonesCoverageError",
    "bbox_unconfigured_reason",
    "refuse_uncovered_state",
    "resolve_coverage_bbox",
]
