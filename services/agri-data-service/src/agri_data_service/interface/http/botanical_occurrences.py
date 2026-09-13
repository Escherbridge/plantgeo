"""HTTP adapter for the governed botanical occurrence plane.

NOT REGISTERED IN `app.py` by this change. The registration hunk is held in
`conductor/tracks/botanical_occurrence_parquet_lane_20260911/evidence/shared-registration.patch`,
because `app.py` is shared with another in-flight slice and a route that mounts before its
independent data-contract review would be serving under a verdict nobody has given.
"""

from __future__ import annotations

from typing import Final

from sanic import Blueprint, Request
from sanic.response import HTTPResponse

from agri_data_service.planes.botanical_occurrences import (
    BotanicalOccurrenceRequestError,
    BotanicalOccurrenceServingError,
    encode_botanical_occurrences,
    parse_botanical_occurrence_request,
    read_botanical_occurrences,
    refused,
    unavailable,
)

botanical_occurrences_bp = Blueprint("botanical_occurrences", url_prefix="/botanical-occurrences")

HTTP_OK: Final = 200
HTTP_BAD_REQUEST: Final = 400
HTTP_CONFLICT: Final = 409
HTTP_SERVICE_UNAVAILABLE: Final = 503

#: Refusals that are the CALLER's to fix (an unpinned release, a bbox too wide for the zoom) answer
#: 400; refusals about the state of a published generation answer 409. The distinction matters
#: operationally: one is a client bug and the other is a publication that needs attention.
_CALLER_REFUSALS: Final[frozenset[str]] = frozenset(
    {"release_not_pinned", "bbox_too_large_for_zoom", "name_only_taxon_filter", "limit_exceeded", "invalid_request"}
)


def _status_for(result: dict[str, object]) -> int:
    state = result.get("state")
    if state == "unavailable":
        return HTTP_SERVICE_UNAVAILABLE
    if state == "refused":
        return HTTP_BAD_REQUEST if result.get("reason") in _CALLER_REFUSALS else HTTP_CONFLICT
    return HTTP_OK


@botanical_occurrences_bp.get("/query")
async def query_botanical_occurrences(request: Request) -> HTTPResponse:
    """Answer one bounded, release-pinned occurrence query in one of the four published states."""
    try:
        if any(len(request.args.getlist(key)) != 1 for key in request.args):
            raise BotanicalOccurrenceRequestError("each query parameter must occur exactly once")
        parsed = parse_botanical_occurrence_request({key: request.args.getlist(key)[0] for key in request.args})
        root = getattr(request.app.ctx, "botanical_occurrences_root", None)
        target = getattr(request.app.ctx, "botanical_occurrences_target", None)
        result = read_botanical_occurrences(parsed, root=root, target=target)
    except BotanicalOccurrenceRequestError as error:
        result = refused(error.reason, str(error))
    except BotanicalOccurrenceServingError as error:
        # A generation marked complete whose artifacts will not open is a PUBLICATION fault, and it
        # is reported as unavailable rather than as an empty answer: there is no honest empty here.
        result = unavailable(str(error))
    return HTTPResponse(
        body=encode_botanical_occurrences(result),
        status=_status_for(result),
        headers={"Cache-Control": "no-store"},
        content_type="application/json",
    )


__all__ = ["botanical_occurrences_bp", "query_botanical_occurrences"]
