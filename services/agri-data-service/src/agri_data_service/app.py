"""Sanic application factory."""

import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit

import structlog
from sanic import Blueprint, Request, Sanic
from sanic.config import Config
from sanic.response import BaseHTTPResponse

from agri_data_service.config import settings
from agri_data_service.db.engine import dispose_combined_local_engine, dispose_service_engines
from agri_data_service.foundation.region import (
    assert_region_bindings_are_servable,
    load_region,
    unverified_binding_slugs,
)
from agri_data_service.interface.http import botanical_occurrences_bp, botanical_species_information_bp, parquet_bp
from agri_data_service.pipeline.source_bindings import (
    declared_layer_source_contracts,
    declared_source_coverage_claims,
)
from agri_data_service.routes import (
    agent_bp,
    agent_tools_bp,
    health_bp,
    jobs_bp,
    strategies_bp,
)

logger = structlog.get_logger()

type AgriApp = Sanic[Config, SimpleNamespace]


def _assert_region_is_servable() -> None:
    """Fail at boot when this deployment's manifest binds a layer to a source that cannot serve it.

    `federation.md` §2: "the manifest may only bind a source whose coverage contains the region's
    envelope; a binding that does not is a startup error, not a runtime surprise". Runs before the
    Sanic app exists so the process dies on a mis-binding rather than serving the wrong region's
    data under this one's name. Sources with no declared claim yet are logged, not raised on --
    see `foundation/region/bindings.py::unverified_binding_slugs`.

    The contracts argument carries the SAME rule one step further: a source bound to the wrong
    LAYER (coverage agrees, ISO codes cover, and the lane then calls a method it does not have) is
    refused here rather than inside a scheduled turn (STYLE-REVIEW-W5 B2).
    """
    region = load_region()
    source_claims = declared_source_coverage_claims()
    assert_region_bindings_are_servable(region, source_claims, declared_layer_source_contracts())
    unverified = unverified_binding_slugs(region, source_claims)
    if unverified:
        logger.info("region_bindings_unverified", region=region.slug, source_slugs=list(unverified))


def create_app(_args: object | None = None) -> AgriApp:
    """Create and configure the Sanic application."""
    _assert_region_is_servable()
    app: AgriApp = Sanic("agri-data-service")

    # --- sanic-ext configuration ---
    app.config.CORS_ORIGINS = settings.cors_origins
    app.config.OAS_UI_DEFAULT = "swagger"
    app.config.API_TITLE = "Agri Data Service"
    app.config.API_VERSION = "1.0.0"
    app.config.API_DESCRIPTION = "Regenerative agriculture data warehouse API for PlantGeo"
    app.config.REQUEST_MAX_SIZE = settings.request_max_size

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
    async def setup_resources(_app: AgriApp, _loop: object) -> None:
        """Report startup without opening an unused cache connection."""
        if settings.service_profile == "receiver_writer":
            database_url = settings.require_receiver_writer_database_url()
        elif settings.service_profile == "published_reader":
            database_url = settings.require_published_reader_database_url()
        else:
            database_url = settings.require_combined_local_database_url()
        database_target = urlsplit(database_url)
        logger.info(
            "server_starting",
            host=settings.sanic_host,
            port=settings.sanic_port,
            service_profile=settings.service_profile,
            database_host=database_target.hostname,
            database_port=database_target.port,
            database_name=database_target.path.removeprefix("/"),
        )

    @app.after_server_stop
    async def teardown_resources(_app: AgriApp, _loop: object) -> None:
        """Dispose the database pool."""
        await dispose_service_engines()
        await dispose_combined_local_engine()
        logger.info("server_stopped")

    # --- Request ID middleware ---
    @app.middleware("request")
    async def inject_request_id(request: Request) -> None:
        request.ctx.request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request.ctx.request_id)

    @app.middleware("response")
    async def add_request_id_header(
        request: Request,
        response: BaseHTTPResponse,
    ) -> None:
        response.headers["X-Request-ID"] = request.ctx.request_id

    # --- Register blueprints ---
    # `parquet_bp` mounts on the two READ profiles and not on `receiver_writer`: it opens no database
    # pool at all (it reads object storage), so no profile's DSN is involved, but the write ingress
    # has no reason to carry a public read surface. See interface/http/AGENTS.md.
    profile_blueprints = {
        "combined_local": (
            strategies_bp,
            jobs_bp,
            parquet_bp,
            agent_tools_bp,
            botanical_species_information_bp,
            botanical_occurrences_bp,
        ),
        "receiver_writer": (jobs_bp,),
        # Forecasts are withheld until a source-direct Parquet forecast lane is published.
        # Keeping the PostgreSQL-backed blueprint mounted would violate the environmental
        # cutover even when the Next.js bridge no longer calls it.
        "published_reader": (parquet_bp, agent_tools_bp, botanical_species_information_bp, botanical_occurrences_bp),
    }[settings.service_profile]
    api_v1 = Blueprint.group(*profile_blueprints, url_prefix="/api/v1")
    app.blueprint(api_v1)
    app.blueprint(health_bp)
    # Registered in every profile; it answers 503 until ANTHROPIC_API_KEY is set.
    app.blueprint(agent_bp)

    return app
