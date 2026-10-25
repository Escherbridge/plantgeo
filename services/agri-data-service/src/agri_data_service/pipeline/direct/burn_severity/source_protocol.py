"""Shim: moved to `pipeline/lanes/burn_severity/source_protocol.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.burn_severity.source_protocol import (
    BoundingBox,
    BurnSeverityRecordPayload,
    BurnSeverityReleaseDay,
    BurnSeveritySource,
    BurnSeverityThresholdsPayload,
)

__all__ = [
    "BoundingBox",
    "BurnSeverityRecordPayload",
    "BurnSeverityReleaseDay",
    "BurnSeveritySource",
    "BurnSeverityThresholdsPayload",
]
