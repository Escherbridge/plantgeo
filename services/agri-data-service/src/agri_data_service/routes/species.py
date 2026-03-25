"""Species CRUD endpoints."""

from sanic import Blueprint

species_bp = Blueprint("species", url_prefix="/species")


@species_bp.get("/")
async def list_species(request):
    """List species with filters and pagination."""
    from sanic import json
    return json({"data": [], "total": 0, "limit": 20, "offset": 0})


@species_bp.get("/<species_id:uuid>")
async def get_species(request, species_id):
    """Get species detail with companion relationships."""
    from sanic import json
    return json({"error": "Not implemented"}, status=501)
