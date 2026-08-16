"""Route blueprints for the Agri Data Service API."""

# `jobs.scheduler` already imports `jobs.strategy_mv_refresh` (for its own periodic driver), which is
# what registers that lane in THIS process at import time -- see jobs/dispatch.py's own docstring on
# why a lane must register itself rather than being discovered generically. `jobs.matview_refresh`
# has no periodic driver of its own to import it as a side effect, so it is imported here directly:
# without this line, POST /api/v1/jobs/trigger with lane_id="matview-refresh" 404s as an unknown
# lane even though the module exists, because nothing ever ran it.
from agri_data_service.jobs import matview_refresh as _matview_refresh_registers_on_import  # noqa: F401
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
