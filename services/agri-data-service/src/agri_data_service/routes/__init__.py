"""Route blueprints for the Agri Data Service API."""

from agri_data_service.routes.health import health_bp
from agri_data_service.routes.locations import locations_bp
from agri_data_service.routes.species import species_bp
from agri_data_service.routes.strategies import strategies_bp

__all__ = ["health_bp", "locations_bp", "strategies_bp", "species_bp"]
