"""Route blueprints for the Agri Data Service API."""

from agri_data_service.jobs.scheduler import jobs_bp
from agri_data_service.routes.agent_analysis import agent_bp
from agri_data_service.routes.health import health_bp
from agri_data_service.routes.strategies import strategies_bp

__all__ = [
    "agent_bp",
    "health_bp",
    "jobs_bp",
    "strategies_bp",
]
