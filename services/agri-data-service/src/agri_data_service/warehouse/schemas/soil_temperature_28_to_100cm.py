"""Autoload the ERA5-Land 28–100 cm soil-temperature snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_soil_temperature_product

STREAM: Final = "soil-temperature-28-to-100cm"
SCHEMA, TIER_DERIVATION = register_soil_temperature_product(STREAM)

__all__ = ["SCHEMA", "STREAM", "TIER_DERIVATION"]
