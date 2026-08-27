"""Autoload the dedicated wind-speed snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_signal_plane_product

CLIMATE_FIELD_WIND_SPEED_STREAM: Final = "climate-field-wind-speed"
CLIMATE_FIELD_WIND_SPEED_SCHEMA, CLIMATE_FIELD_WIND_SPEED_TIER_DERIVATION = register_signal_plane_product(
    CLIMATE_FIELD_WIND_SPEED_STREAM
)

__all__ = [
    "CLIMATE_FIELD_WIND_SPEED_SCHEMA",
    "CLIMATE_FIELD_WIND_SPEED_STREAM",
    "CLIMATE_FIELD_WIND_SPEED_TIER_DERIVATION",
]
