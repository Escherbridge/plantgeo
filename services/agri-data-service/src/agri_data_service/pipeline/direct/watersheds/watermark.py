"""Shim: moved to `pipeline/lanes/watersheds/watermark.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.watersheds.watermark import (
    WatershedsWatermarkError,
    read_watersheds_source_watermark,
)

__all__ = [
    "WatershedsWatermarkError",
    "read_watersheds_source_watermark",
]
