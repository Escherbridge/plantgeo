"""Sanic application factory."""

import structlog
from redis.asyncio import Redis
from sanic import Sanic

from agri_data_service.config import settings
from agri_data_service.db.engine import engine
from agri_data_service.routes import health_bp, locations_bp, species_bp, strategies_bp

logger = structlog.get_logger()


def create_app(args=None) -> Sanic:
    """Create and configure the Sanic application."""
    app = Sanic("agri-data-service")

    # --- sanic-ext configuration ---
    app.config.CORS_ORIGINS = settings.cors_origins
    app.config.OAS_UI_DEFAULT = "swagger"
    app.config.API_TITLE = "Agri Data Service"
    app.config.API_VERSION = "1.0.0"
    app.config.API_DESCRIPTION = "Regenerative agriculture data warehouse API for PlantGeo"

    # --- Structured logging ---
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if settings.sanic_debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # --- Lifecycle listeners ---
    @app.before_server_start
    async def setup_resources(app: Sanic, _loop: object) -> None:
        """Initialize database and Redis connections."""
        app.ctx.redis = Redis.from_url(settings.redis_url, decode_responses=True)
        logger.info(
            "server_starting",
            host=settings.sanic_host,
            port=settings.sanic_port,
            db=settings.database_url.split("@")[-1],  # log host only, not credentials
        )

    @app.after_server_stop
    async def teardown_resources(app: Sanic, _loop: object) -> None:
        """Dispose database engine and close Redis."""
        await engine.dispose()
        await app.ctx.redis.aclose()
        logger.info("server_stopped")

    # --- Request ID middleware ---
    @app.middleware("request")
    async def inject_request_id(request: object) -> None:
        import uuid as _uuid

        request.ctx.request_id = str(_uuid.uuid4())  # type: ignore[attr-defined]
        structlog.contextvars.bind_contextvars(request_id=request.ctx.request_id)  # type: ignore[attr-defined]

    @app.middleware("response")
    async def add_request_id_header(request: object, response: object) -> None:
        response.headers["X-Request-ID"] = request.ctx.request_id  # type: ignore[attr-defined]

    # --- Register blueprints ---
    from sanic import Blueprint

    api_v1 = Blueprint.group(locations_bp, strategies_bp, species_bp, url_prefix="/api/v1")
    app.blueprint(api_v1)
    app.blueprint(health_bp)

    return app
