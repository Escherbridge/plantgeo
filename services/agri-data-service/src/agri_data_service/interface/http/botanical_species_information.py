"""HTTP adapter for the transitional authoring species lookup."""

from __future__ import annotations

from typing import Final

from sanic import Blueprint, Request
from sanic.response import HTTPResponse

from agri_data_service.db.engine import published_reader_session
from agri_data_service.planes.botanical_species_information import (
    SpeciesInformationRequestError,
    encode_species_information,
    invalid_request,
    parse_species_information_request,
    read_species_information,
)

botanical_species_information_bp = Blueprint(
    "botanical_species_information", url_prefix="/botanical-species-information"
)
HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503


@botanical_species_information_bp.get("/lookup")
async def lookup_species_information(request: Request) -> HTTPResponse:
    """Return one exact Species UUID from the bounded authoring read plane."""
    try:
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise SpeciesInformationRequestError("each lookup parameter must occur exactly once")
        parsed = parse_species_information_request({key: request.args.getlist(key)[0] for key in request.args})
        provider = getattr(request.app.ctx, "species_information_session_provider", published_reader_session)
        result = await read_species_information(parsed, session_provider=provider)
    except SpeciesInformationRequestError as error:
        result = invalid_request(str(error))
    reason = result.get("reason") or {}
    status = HTTP_BAD_REQUEST if reason.get("code") == "invalid_species_information_request" else HTTP_OK
    if reason.get("code") == "species_information_response_over_budget":
        status = HTTP_CONFLICT
    elif reason.get("code") == "authoring_database_unavailable":
        status = HTTP_SERVICE_UNAVAILABLE
    return HTTPResponse(
        body=encode_species_information(result),
        status=status,
        headers={"Cache-Control": "no-store"},
        content_type="application/json",
    )
