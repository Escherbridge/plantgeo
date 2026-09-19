"""Sanic application factory. This service never opens a database connection (decision D5)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final

import structlog
from sanic import Blueprint, Request, Sanic, json
from sanic.config import Config
from sanic.response import HTTPResponse  # noqa: TC002 - sanic-ext evaluates handler annotations at runtime.

from plantgeo_ml_service.config import get_settings
from plantgeo_ml_service.planes.routes import machine_learning_bp

if TYPE_CHECKING:
    from plantgeo_ml_service.config import ObjectStoreCredentials

logger = structlog.get_logger()

type MachineLearningApp = Sanic[Config, SimpleNamespace]

#: How long a readiness probe may spend proving the bucket answers, before it reports not-ready.
BUCKET_PROBE_TIMEOUT_SECONDS: Final = 5.0

health_bp = Blueprint("health", url_prefix="/")


@health_bp.get("/health")
async def liveness(_request: Request) -> HTTPResponse:
    """Report process liveness without coupling it to any dependency."""
    return json({"status": "ok"})


@health_bp.get("/ready")
async def readiness(_request: Request) -> HTTPResponse:
    """Report readiness: credentials resolve and a bounded HEAD on the bucket succeeds."""
    reason = await bucket_readiness_reason()
    if reason is not None:
        return json({"status": "not_ready", "reason": reason}, status=503)
    return json({"status": "ok"})


async def bucket_readiness_reason() -> str | None:
    """Return None when the bucket answered, else one typed reason string naming what refused."""
    try:
        credentials = get_settings().require_object_store()
    except ValueError as error:
        logger.info("readiness_object_store_unconfigured", detail=str(error))
        return "object_store_unconfigured"
    try:
        async with asyncio.timeout(BUCKET_PROBE_TIMEOUT_SECONDS):
            await asyncio.to_thread(head_bucket, credentials)
    except TimeoutError:
        return "object_store_timeout"
    except Exception as error:  # boto3 raises a client-specific type; the reason is what matters
        logger.info("readiness_object_store_unreachable", detail=str(error))
        return "object_store_unreachable"
    return None


def head_bucket(credentials: ObjectStoreCredentials) -> None:
    """Issue one bounded HEAD against the configured bucket; raises when it does not answer."""
    import boto3  # noqa: PLC0415 - imported lazily so an unconfigured process still starts and reports 503
    from botocore.config import Config as BotocoreConfig  # noqa: PLC0415 - see boto3 import above

    client = boto3.client(
        "s3",
        endpoint_url=credentials.endpoint_url,
        region_name=credentials.region,
        aws_access_key_id=credentials.access_key_id.get_secret_value(),
        aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
        config=BotocoreConfig(
            connect_timeout=BUCKET_PROBE_TIMEOUT_SECONDS,
            read_timeout=BUCKET_PROBE_TIMEOUT_SECONDS,
            retries={"max_attempts": 1},
        ),
    )
    client.head_bucket(Bucket=credentials.bucket)


def create_app(_args: object | None = None) -> MachineLearningApp:
    """Create and configure the Sanic application."""
    settings = get_settings()
    app: MachineLearningApp = Sanic("plantgeo-ml")

    app.config.CORS_ORIGINS = settings.cors_origins
    app.config.OAS_UI_DEFAULT = "swagger"
    app.config.API_TITLE = "PlantGeo ML Service"
    app.config.API_VERSION = "1.0.0"
    app.config.API_DESCRIPTION = "Parquet-fed machine-learning and Monte Carlo forecasting for PlantGeo"

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if settings.sanic_debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    app.blueprint(health_bp)
    app.blueprint(machine_learning_bp)
    return app
