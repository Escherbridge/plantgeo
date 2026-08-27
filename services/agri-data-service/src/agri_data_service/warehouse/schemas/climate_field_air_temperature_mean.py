"""Autoload the dedicated mean air-temperature snapshot product."""

from typing import Final

from agri_data_service.warehouse.schemas.snapshot_signal_product import register_signal_plane_product

STREAM: Final = "climate-field-air-temperature-mean"
SCHEMA, TIER_DERIVATION = register_signal_plane_product(STREAM)

__all__ = ["SCHEMA", "STREAM", "TIER_DERIVATION"]
