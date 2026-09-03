"""Autoload the NASA POWER surface soil-wetness lane product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_soil_wetness_product

STREAM: Final = "soil-wetness-surface"
SCHEMA, TIER_DERIVATION = register_soil_wetness_product(STREAM)

__all__ = ["SCHEMA", "STREAM", "TIER_DERIVATION"]
