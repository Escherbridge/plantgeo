"""Shared boundaries for direct-source Parquet publication."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

LANE_BASE_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]
DIRECT_HOURLY_REFRESH_INTERVAL_SECONDS: Final = 3600
#: The legacy soil writer's cadence since G1 (O6/FR-21); lane_specs schedules it and the coverage wire reports it.
SOIL_DIRECT_REFRESH_INTERVAL_SECONDS: Final = 6 * 3600
FIRE_DETECTIONS_DIRECT_WRITER_START_DAY: Final = date(2026, 8, 25)
WATER_GAUGES_DIRECT_WRITER_START_DAY: Final = date(2026, 9, 2)
