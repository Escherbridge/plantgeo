"""Strategy CRUD endpoints."""

from sanic import Blueprint

strategies_bp = Blueprint("strategies", url_prefix="/strategies")


@strategies_bp.get("/")
async def list_strategies(request):
    """List all strategies with optional category filter."""
    from sanic import json
    return json({"data": [], "total": 0, "limit": 20, "offset": 0})


@strategies_bp.get("/<strategy_id:uuid>")
async def get_strategy(request, strategy_id):
    """Get a strategy by ID with suitability rules."""
    from sanic import json
    return json({"error": "Not implemented"}, status=501)
