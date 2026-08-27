"""Autoload the shortwave-radiation snapshot-breakdown product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_snapshot_lineage_product

CLIMATE_FIELD_SHORTWAVE_RADIATION_STREAM: Final = "climate-field-shortwave-radiation"
CLIMATE_FIELD_SHORTWAVE_RADIATION_SCHEMA, CLIMATE_FIELD_SHORTWAVE_RADIATION_TIER_DERIVATION = (
    register_snapshot_lineage_product(CLIMATE_FIELD_SHORTWAVE_RADIATION_STREAM)
)

__all__ = [
    "CLIMATE_FIELD_SHORTWAVE_RADIATION_SCHEMA",
    "CLIMATE_FIELD_SHORTWAVE_RADIATION_STREAM",
    "CLIMATE_FIELD_SHORTWAVE_RADIATION_TIER_DERIVATION",
]
