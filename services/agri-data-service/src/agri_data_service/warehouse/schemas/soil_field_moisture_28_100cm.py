"""Autoload the ERA5-Land 28-100 cm soil-moisture snapshot product."""

from typing import Final

from agri_data_service.warehouse.parquet.snapshot_signal_product import register_snapshot_lineage_product

STREAM: Final = "soil-field-moisture-28-100cm"
SOIL_FIELD_MOISTURE_28_100CM_SCHEMA, SOIL_FIELD_MOISTURE_28_100CM_TIER_DERIVATION = register_snapshot_lineage_product(
    STREAM
)

__all__ = ["SOIL_FIELD_MOISTURE_28_100CM_SCHEMA", "SOIL_FIELD_MOISTURE_28_100CM_TIER_DERIVATION", "STREAM"]
