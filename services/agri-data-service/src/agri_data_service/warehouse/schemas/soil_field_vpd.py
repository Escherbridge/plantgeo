"""Autoload the dedicated VPD snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_signal_plane_product

SOIL_FIELD_VPD_STREAM: Final = "soil-field-vpd"
SOIL_FIELD_VPD_SCHEMA, SOIL_FIELD_VPD_TIER_DERIVATION = register_signal_plane_product(SOIL_FIELD_VPD_STREAM)

__all__ = ["SOIL_FIELD_VPD_SCHEMA", "SOIL_FIELD_VPD_STREAM", "SOIL_FIELD_VPD_TIER_DERIVATION"]
