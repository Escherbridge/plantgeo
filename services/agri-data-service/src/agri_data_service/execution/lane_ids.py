"""The executor lane identifiers, spelled once so a leaf module can name a lane without importing the scheduler."""

from __future__ import annotations

from typing import Final

FIRE_DETECTIONS_DIRECT_LANE_ID: Final = "fire-detections-direct-forward"
WATER_GAUGES_DIRECT_LANE_ID: Final = "water-gauges-direct-forward"
MTBS_FORWARD_LANE_ID: Final = "mtbs-forward"
CLIMATE_DIRECT_LANE_ID: Final = "climate-nasa-power-direct-forward"
SOIL_DIRECT_LANE_ID: Final = "soil-era5-land-direct-forward"
VEGETATION_DIRECT_LANE_ID: Final = "vegetation-sentinel2-ndvi-direct-forward"
#: Registered but SHADOW: absent from the deployed `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allow-list
#: on purpose. See `execution/vegetation_partition_promotion.py`.
VEGETATION_NDVI_PROMOTION_LANE_ID: Final = "vegetation-ndvi-governed-plane-promotion"
WEATHER_OBSERVATIONS_DIRECT_LANE_ID: Final = "weather-observations-direct-forward"
DROUGHT_DIRECT_LANE_ID: Final = "drought-direct-forward"
FIRE_PERIMETERS_DIRECT_LANE_ID: Final = "fire-perimeters-direct-forward"
SENSORS_DIRECT_LANE_ID: Final = "sensors-direct-forward"
WATERSHEDS_DIRECT_LANE_ID: Final = "watersheds-direct-forward"
EVACUATION_ZONES_DIRECT_LANE_ID: Final = "evacuation-zones-direct-forward"
BURN_SEVERITY_DIRECT_LANE_ID: Final = "burn-severity-direct-forward"

__all__ = [
    "BURN_SEVERITY_DIRECT_LANE_ID",
    "CLIMATE_DIRECT_LANE_ID",
    "DROUGHT_DIRECT_LANE_ID",
    "EVACUATION_ZONES_DIRECT_LANE_ID",
    "FIRE_DETECTIONS_DIRECT_LANE_ID",
    "FIRE_PERIMETERS_DIRECT_LANE_ID",
    "MTBS_FORWARD_LANE_ID",
    "SENSORS_DIRECT_LANE_ID",
    "SOIL_DIRECT_LANE_ID",
    "VEGETATION_DIRECT_LANE_ID",
    "VEGETATION_NDVI_PROMOTION_LANE_ID",
    "WATERSHEDS_DIRECT_LANE_ID",
    "WATER_GAUGES_DIRECT_LANE_ID",
    "WEATHER_OBSERVATIONS_DIRECT_LANE_ID",
]
