"""Location CRUD endpoints."""

from sanic import Blueprint

locations_bp = Blueprint("locations", url_prefix="/locations")


@locations_bp.get("/")
async def list_locations(request):
    """List all locations with pagination."""
    from sanic import json
    return json({"data": [], "total": 0, "limit": 20, "offset": 0})


@locations_bp.get("/<location_id:uuid>")
async def get_location(request, location_id):
    """Get a location by ID with all associated profiles."""
    from sanic import json
    return json({"error": "Not implemented"}, status=501)


@locations_bp.post("/")
async def create_location(request):
    """Create a new location from lat/lng."""
    from sanic import json
    return json({"error": "Not implemented"}, status=501)


@locations_bp.get("/<location_id:uuid>/context")
async def get_location_context(request, location_id):
    """Get aggregated environmental context for a location."""
    from sanic import json
    return json({"error": "Not implemented"}, status=501)
