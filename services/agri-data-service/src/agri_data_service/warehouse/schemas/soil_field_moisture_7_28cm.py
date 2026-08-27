"""Autoload the ERA5-Land 7–28 cm soil-moisture snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_snapshot_lineage_product

STREAM: Final = "soil-field-moisture-7-28cm"
SOIL_FIELD_MOISTURE_7_28CM_SCHEMA, SOIL_FIELD_MOISTURE_7_28CM_TIER_DERIVATION = register_snapshot_lineage_product(
    STREAM
)

__all__ = ["SOIL_FIELD_MOISTURE_7_28CM_SCHEMA", "SOIL_FIELD_MOISTURE_7_28CM_TIER_DERIVATION", "STREAM"]
