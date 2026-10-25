"""Shim: moved to `pipeline/lanes/watersheds/source.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.watersheds.source import (
    WATERMARK_BASIS,
    WatershedRecord,
    WatershedsSnapshotSource,
    WatershedsSourceError,
    fetch_watersheds_snapshot,
)

__all__ = [
    "WATERMARK_BASIS",
    "WatershedRecord",
    "WatershedsSnapshotSource",
    "WatershedsSourceError",
    "fetch_watersheds_snapshot",
]
