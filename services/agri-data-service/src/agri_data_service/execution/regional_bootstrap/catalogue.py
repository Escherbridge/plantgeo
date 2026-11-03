"""Explicit current compilation capabilities; these are not production admission grants."""

from dataclasses import dataclass

from agri_data_service.foundation.region.layer_availability import PLATFORM_LAYER_SLUGS


@dataclass(frozen=True, slots=True)
class SourcePlan:
    source: str
    binding: str
    provider: str
    temporal_mode: str
    layers: tuple[str, ...]
    blockers: tuple[str, ...]


SOURCE_PLANS = {
    "water": SourcePlan(
        "usgs-water-data", "usgs_water_data", "usgs-water-data", "daily_archive", ("water-gauges",), ()
    ),
    "weather": SourcePlan(
        "open-meteo",
        "open_meteo",
        "open-meteo",
        "current",
        ("weather-observations",),
        ("current_day_executor_not_admitted",),
    ),
    "fire": SourcePlan(
        "nasa-firms", "firms", "nasa-firms", "current", ("fire-detections",), ("source_requests_ignore_manifest",)
    ),
    "soil": SourcePlan(
        "open-meteo-era5-land",
        "era5_land",
        "open-meteo",
        "daily_archive",
        tuple(sorted(layer for layer in PLATFORM_LAYER_SLUGS if layer.startswith("soil-field-"))),
        ("source_support_not_region_generic",),
    ),
    "vegetation": SourcePlan(
        "sentinel2",
        "sentinel2_ndvi",
        "earth-search",
        "daily_archive",
        ("vegetation",),
        ("source_support_not_region_generic",),
    ),
    "climate": SourcePlan(
        "nasa-power",
        "nasa_power",
        "nasa-power",
        "daily_archive",
        tuple(sorted(layer for layer in PLATFORM_LAYER_SLUGS if layer.startswith("climate-field-"))),
        ("source_support_not_region_generic",),
    ),
}
