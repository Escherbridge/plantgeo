"""Route blueprints for the Agri Data Service API."""

from agri_data_service.routes.forecasts import forecasts_bp
from agri_data_service.routes.health import health_bp
from agri_data_service.routes.historical_promotion import historical_promotion_bp
from agri_data_service.routes.local_publication import local_publication_bp
from agri_data_service.routes.strategies import strategies_bp

__all__ = [
    "forecasts_bp",
    "health_bp",
    "historical_promotion_bp",
    "local_publication_bp",
    "strategies_bp",
]
