"""Autoload the relative-humidity snapshot-breakdown product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_snapshot_lineage_product

CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM: Final = "climate-field-relative-humidity"
CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA, CLIMATE_FIELD_RELATIVE_HUMIDITY_TIER_DERIVATION = (
    register_snapshot_lineage_product(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM)
)

__all__ = [
    "CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA",
    "CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM",
    "CLIMATE_FIELD_RELATIVE_HUMIDITY_TIER_DERIVATION",
]
