"""Shim: moved to `pipeline/lanes/drought/source_protocol.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.drought.source_protocol import (
    DroughtAreaPayload,
    DroughtReleaseDay,
    DroughtReleasePayload,
    DroughtSource,
)

__all__ = [
    "DroughtAreaPayload",
    "DroughtReleaseDay",
    "DroughtReleasePayload",
    "DroughtSource",
]
