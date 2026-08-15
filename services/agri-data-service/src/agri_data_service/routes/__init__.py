"""Route blueprints for the Agri Data Service API."""

from agri_data_service.jobs.scheduler import jobs_bp
from agri_data_service.routes.agent_analysis import agent_bp
from agri_data_service.routes.forecasts import forecasts_bp
from agri_data_service.routes.health import health_bp
from agri_data_service.routes.historical_promotion import historical_promotion_bp
from agri_data_service.routes.local_publication import local_publication_bp
from agri_data_service.routes.ops import ops_bp
from agri_data_service.routes.recommendations import recommendations_bp
from agri_data_service.routes.strategies import strategies_bp

__all__ = [
    "agent_bp",
    "forecasts_bp",
    "health_bp",
    "historical_promotion_bp",
    "jobs_bp",
    "local_publication_bp",
    "ops_bp",
    "recommendations_bp",
    "strategies_bp",
]
