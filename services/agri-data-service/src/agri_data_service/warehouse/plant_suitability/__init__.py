"""Plant-suitability rule engine (regional-guide pools x USDA PLANTS envelope); see AGENTS.md in this directory."""

from agri_data_service.warehouse.plant_suitability.config import PRODUCTION, V0_FROZEN, RuleConfig
from agri_data_service.warehouse.plant_suitability.engine import (
    candidates_for_cell,
    evaluate_cells,
    layer_metadata,
    licence_excluded_sources,
    with_layer_metadata,
)
from agri_data_service.warehouse.plant_suitability.labels import assert_no_fire_claims, find_fire_claims
from agri_data_service.warehouse.plant_suitability.wetland import resolve_wetland_ratings

__all__ = [
    "PRODUCTION",
    "V0_FROZEN",
    "RuleConfig",
    "assert_no_fire_claims",
    "candidates_for_cell",
    "evaluate_cells",
    "find_fire_claims",
    "layer_metadata",
    "licence_excluded_sources",
    "resolve_wetland_ratings",
    "with_layer_metadata",
]
