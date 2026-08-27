"""Autoload the ERA5-Land 0-7 cm soil-temperature snapshot product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_soil_temperature_product

STREAM: Final = "soil-temperature-0-to-7cm"
SCHEMA, TIER_DERIVATION = register_soil_temperature_product(STREAM)

__all__ = ["SCHEMA", "STREAM", "TIER_DERIVATION"]
