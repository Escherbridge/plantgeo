"""Shim: moved to `pipeline/lanes/evacuation_zones/watermark.py` by p4-extract; see `DEPRECATED_ALIASES.md`."""

from agri_data_service.pipeline.lanes.evacuation_zones.watermark import (
    WATERMARK_RETRY_ATTEMPTS,
    WATERMARK_RETRY_BASE_SECONDS,
    WATERMARK_RETRY_MAX_SECONDS,
    EvacuationZonesWatermarkError,
    PublishedSnapshot,
    read_evacuation_zones_source_watermark,
    read_published_snapshot,
    watermark_for_capture,
)

__all__ = [
    "WATERMARK_RETRY_ATTEMPTS",
    "WATERMARK_RETRY_BASE_SECONDS",
    "WATERMARK_RETRY_MAX_SECONDS",
    "EvacuationZonesWatermarkError",
    "PublishedSnapshot",
    "read_evacuation_zones_source_watermark",
    "read_published_snapshot",
    "watermark_for_capture",
]
