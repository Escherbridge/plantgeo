"""HTTP transport for the static botanical profile lookup; see this directory's `AGENTS.md`."""

from __future__ import annotations

from typing import Final

from sanic import Blueprint, Request
from sanic.response import HTTPResponse

from agri_data_service.planes.botanical_species_profiles import (
    ProfileRequestError,
    encode_profile_response,
    invalid_request,
    parse_profile_request,
    read_species_profile,
)

botanical_species_profiles_bp = Blueprint("botanical_species_profiles", url_prefix="/botanical-species-profiles")

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503

_REFUSAL_HTTP_STATUS: Final = {
    "invalid_profile_request": HTTP_BAD_REQUEST,
    "profile_release_unpublished": HTTP_CONFLICT,
    "profile_release_integrity": HTTP_SERVICE_UNAVAILABLE,
    "profile_store_unconfigured": HTTP_SERVICE_UNAVAILABLE,
    "profile_store_unavailable": HTTP_SERVICE_UNAVAILABLE,
    "profile_read_at_capacity": HTTP_SERVICE_UNAVAILABLE,
    "profile_read_timed_out": HTTP_SERVICE_UNAVAILABLE,
    "profile_response_over_budget": HTTP_CONFLICT,
}


@botanical_species_profiles_bp.get("/lookup")
async def lookup_species_profile(request: Request) -> HTTPResponse:
    """Return the exact requested taxon's immutable profile and bounded assertion evidence."""
    try:
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise ProfileRequestError("each profile lookup parameter must occur exactly once")
        parameters = {key: request.args.getlist(key)[0] for key in request.args}
        parsed = parse_profile_request(parameters)
        storage = getattr(request.app.ctx, "botanical_profile_storage", None)
        result = await read_species_profile(parsed, storage=storage)
    except ProfileRequestError as error:
        result = invalid_request(str(error))
    status = _REFUSAL_HTTP_STATUS[result["reason"]["code"]] if result["state"] == "refused" else HTTP_OK
    return HTTPResponse(
        body=encode_profile_response(result),
        status=status,
        headers={"Cache-Control": "no-store"},
        content_type="application/json",
    )
