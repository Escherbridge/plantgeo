"""Autoload the ERA5-Land 0–7 cm soil-moisture snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_snapshot_lineage_product

STREAM: Final = "soil-field-moisture-0-7cm"
SOIL_FIELD_MOISTURE_0_7CM_SCHEMA, SOIL_FIELD_MOISTURE_0_7CM_TIER_DERIVATION = register_snapshot_lineage_product(STREAM)

__all__ = ["SOIL_FIELD_MOISTURE_0_7CM_SCHEMA", "SOIL_FIELD_MOISTURE_0_7CM_TIER_DERIVATION", "STREAM"]
