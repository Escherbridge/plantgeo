"""Health check endpoint."""

import structlog
from sanic import Blueprint, json
from sqlalchemy import text

from agri_data_service.db.engine import async_session

logger = structlog.get_logger()

health_bp = Blueprint("health", url_prefix="/")


@health_bp.get("/health")
async def health_check(request):
    """Check database and Redis connectivity."""
    db_ok = False
    redis_ok = False

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        logger.warning("health_check_db_failed")

    try:
        await request.app.ctx.redis.ping()
        redis_ok = True
    except Exception:
        logger.warning("health_check_redis_failed")

    status = "ok" if (db_ok and redis_ok) else "degraded"
    return json({"status": status, "db": db_ok, "redis": redis_ok}, status=200 if db_ok else 503)
