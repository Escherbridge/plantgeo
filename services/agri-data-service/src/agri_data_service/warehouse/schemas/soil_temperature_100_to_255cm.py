"""Autoload the ERA5-Land 100-255 cm soil-temperature snapshot product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_soil_temperature_product

STREAM: Final = "soil-temperature-100-to-255cm"
SCHEMA, TIER_DERIVATION = register_soil_temperature_product(STREAM)

__all__ = ["SCHEMA", "STREAM", "TIER_DERIVATION"]
