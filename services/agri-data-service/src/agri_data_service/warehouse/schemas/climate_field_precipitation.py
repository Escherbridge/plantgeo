"""Autoload the precipitation snapshot-breakdown product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_snapshot_lineage_product

CLIMATE_FIELD_PRECIPITATION_STREAM: Final = "climate-field-precipitation"
CLIMATE_FIELD_PRECIPITATION_SCHEMA, CLIMATE_FIELD_PRECIPITATION_TIER_DERIVATION = register_snapshot_lineage_product(
    CLIMATE_FIELD_PRECIPITATION_STREAM
)

__all__ = [
    "CLIMATE_FIELD_PRECIPITATION_SCHEMA",
    "CLIMATE_FIELD_PRECIPITATION_STREAM",
    "CLIMATE_FIELD_PRECIPITATION_TIER_DERIVATION",
]
