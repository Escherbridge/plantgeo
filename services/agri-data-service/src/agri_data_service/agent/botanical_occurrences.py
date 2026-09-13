"""Three bounded agent tools over the governed occurrence plane, with no silent substitution.

NOT ADDED TO `WAREHOUSE_TOOLS` by this change: the hunk that registers them lives in the track's
`evidence/shared-registration.patch`, because `agent/tools.py` is shared with another in-flight slice.

THE SHAPE EVERY TOOL HERE RETURNS: an `exact` block saying whether the requested thing was found and
how many, and -- separately -- a `substitutes` list whose every entry carries `substitute: true`. A
neighbour is returned NEXT TO the exact result, never in place of it. A model that reads a neighbour
as the answer has been misled by the tool, so the tool does not offer the opportunity.
"""

from __future__ import annotations

import json
import math
from contextvars import ContextVar
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any, Final

from anthropic import beta_async_tool
from pydantic import Field

from agri_data_service.foundation.botanical_occurrences.coordinates import haversine_meters
from agri_data_service.planes.botanical_occurrences import (
    DETAIL_ZOOM_FLOOR,
    MAX_LIMIT,
    BotanicalOccurrenceRequest,
    BotanicalOccurrenceRequestError,
    BotanicalOccurrenceServingError,
    read_botanical_occurrences,
    refused,
    unavailable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Where the agent reads generations from: a local root in tests, the bucket in production. A
#: ContextVar rather than an argument so a model can never point a tool at another root.
_generation_root: ContextVar[str | None] = ContextVar("botanical_generation_root", default=None)

#: The widest neighbour search a tool will run, in metres. Past this a "neighbour" is not a
#: neighbour: it is a different place, and offering it invites the model to reason across it.
MAX_NEIGHBOUR_RADIUS_METERS: Final = 50_000.0
DEFAULT_NEIGHBOUR_RADIUS_METERS: Final = 10_000.0
DEFAULT_NEIGHBOUR_LIMIT: Final = 10

#: The claims this evidence CANNOT support, restated in every tool's payload. A specimen proves a
#: documented collection event; it does not measure cover, abundance, current occupancy, suitability
#: or absence, and it cannot say what was known at any past publication date.
REFUSED_CLAIMS: Final[tuple[str, ...]] = (
    "abundance",
    "cover",
    "current_occupancy",
    "habitat_suitability",
    "surveyed_absence",
    "historical_publication_state",
)

_DEGREES_PER_METER_LATITUDE: Final = 1 / 111_320.0

ReleaseSetId = Annotated[
    str,
    Field(min_length=8, description="Exact published release_set_id; `current` is refused, not resolved."),
]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
NeighbourLimit = Annotated[int, Field(ge=1, le=MAX_LIMIT, description="Maximum neighbour rows to return.")]


def use_generation_root(root: str | None) -> None:
    """Pin which publication root the agent tools read; production leaves it None for the bucket."""
    _generation_root.set(root)


def _payload(body: Mapping[str, Any]) -> str:
    """Render a tool result as compact JSON text, the way `agent/tools.py` does."""
    return json.dumps({**body, "refused_claims": list(REFUSED_CLAIMS)}, sort_keys=True, default=str)


def _bbox_around(longitude: float, latitude: float, radius_meters: float) -> tuple[float, float, float, float]:
    """A degree box that CONTAINS the metric radius; the exact distance filter runs afterwards.

    Longitude is widened by 1/cos(latitude), so the box does not shrink below the circle it is meant
    to enclose at high latitude. The floor on the cosine is a pole guard, not a projection.
    """
    latitude_span = radius_meters * _DEGREES_PER_METER_LATITUDE
    longitude_span = latitude_span / max(math.cos(math.radians(latitude)), 0.01)
    return (longitude - longitude_span, latitude - latitude_span, longitude + longitude_span, latitude + latitude_span)


def _read(request: BotanicalOccurrenceRequest) -> dict[str, Any]:
    try:
        return read_botanical_occurrences(request, root=_generation_root.get())
    except BotanicalOccurrenceRequestError as error:
        return refused(error.reason, str(error))
    except BotanicalOccurrenceServingError as error:
        return unavailable(str(error))


def _exact_block(features: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The requested result's own state. `empty` is a real answer; it is never filled by a neighbour."""
    return {"state": "found" if features else "empty", "count": len(features)}


@beta_async_tool
async def botanical_occurrences_in_region(
    release_set_id: ReleaseSetId,
    minimum_longitude: Longitude,
    minimum_latitude: Latitude,
    maximum_longitude: Longitude,
    maximum_latitude: Latitude,
    taxon_concept_id: str | None = None,
    limit: NeighbourLimit = DEFAULT_NEIGHBOUR_LIMIT,
) -> str:
    """Read documented specimen records inside one bbox from ONE pinned release set.

    Every record is evidence that a collection event was documented at a place and time. It is NOT
    abundance, NOT cover, NOT current occupancy, NOT habitat suitability and NOT surveyed absence:
    an empty result means no admitted record was found, never that the plant is not there. This tool
    cannot answer what was known at a past publication date, and `current` is not a release set.
    """
    request = BotanicalOccurrenceRequest(
        release_set_id=release_set_id,
        bbox=(minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude),
        zoom=DETAIL_ZOOM_FLOOR,
        taxon_concept_id=taxon_concept_id,
        limit=limit,
    )
    result = _read(request)
    if result["state"] != "detail":
        return _payload({"tool": "botanical_occurrences_in_region", **result})
    features = result["features"]
    return _payload(
        {
            "tool": "botanical_occurrences_in_region",
            "state": "detail",
            "release_set_id": release_set_id,
            "exact": _exact_block(features),
            "features": features,
            "counts": result["counts"],
            "truncated": result["truncated"],
            "next_cursor": result["next_cursor"],
        }
    )


@beta_async_tool
async def botanical_occurrence_spatial_neighbours(
    release_set_id: ReleaseSetId,
    longitude: Longitude,
    latitude: Latitude,
    taxon_concept_id: str | None = None,
    radius_meters: float = DEFAULT_NEIGHBOUR_RADIUS_METERS,
    limit: NeighbourLimit = DEFAULT_NEIGHBOUR_LIMIT,
) -> str:
    """Return records near a point, each with its real distance and the support it represents.

    The `exact` block answers whether anything was documented AT the queried point's own cell. The
    `substitutes` list is separate and every entry is flagged `substitute: true` with its own
    `distance_m` and coordinate uncertainty -- a nearby specimen is not evidence about the queried
    point. This tool reports no abundance, no cover, no occupancy, no suitability, no surveyed
    absence and no historical-publication claim.
    """
    radius = min(max(radius_meters, 1.0), MAX_NEIGHBOUR_RADIUS_METERS)
    request = BotanicalOccurrenceRequest(
        release_set_id=release_set_id,
        bbox=_bbox_around(longitude, latitude, radius),
        zoom=DETAIL_ZOOM_FLOOR,
        taxon_concept_id=taxon_concept_id,
        spatial_quality="all",
        limit=MAX_LIMIT,
    )
    result = _read(request)
    if result["state"] != "detail":
        return _payload({"tool": "botanical_occurrence_spatial_neighbours", **result})
    measured = [
        {
            **feature,
            "substitute": True,
            "distance_m": haversine_meters(longitude, latitude, feature["longitude"], feature["latitude"]),
            "distance_semantics": "to_record_point" if feature["spatial_class"] == "exact" else "to_reported_point",
        }
        for feature in result["features"]
        if feature["longitude"] is not None and feature["latitude"] is not None
    ]
    measured.sort(key=lambda entry: entry["distance_m"])
    within_radius = [entry for entry in measured if entry["distance_m"] <= radius]
    # "At the point" is deliberately a small tolerance rather than an exact coordinate match: a
    # coordinate equal to fifteen decimal places is not a thing field data contains.
    at_point = [entry for entry in within_radius if entry["distance_m"] <= 1.0]
    return _payload(
        {
            "tool": "botanical_occurrence_spatial_neighbours",
            "state": "detail",
            "release_set_id": release_set_id,
            "queried_point": {"longitude": longitude, "latitude": latitude},
            "radius_m": radius,
            "exact": _exact_block(at_point),
            "substitutes": within_radius[:limit],
            "counts": result["counts"],
        }
    )


def _signed_days(reference: date, other: date) -> int:
    return (other - reference).days


@beta_async_tool
async def botanical_occurrence_temporal_neighbours(  # noqa: PLR0913 - one bbox ordinate and one window bound per argument
    release_set_id: ReleaseSetId,
    minimum_longitude: Longitude,
    minimum_latitude: Latitude,
    maximum_longitude: Longitude,
    maximum_latitude: Latitude,
    window_start: str,
    window_end: str,
    taxon_concept_id: str | None = None,
    limit: NeighbourLimit = DEFAULT_NEIGHBOUR_LIMIT,
) -> str:
    """Return records near a date window, each with its OWN event interval and the days between.

    `exact` counts records whose interval OVERLAPS the requested window -- reported as overlap, never
    as an invented exact-day match. Everything else is a flagged substitute carrying `signed_days`
    (negative before the window, positive after), `abs_days` and `overlap: false`. A record whose
    date is unknown is neither: it cannot answer a temporal question at all. No abundance, cover,
    occupancy, suitability, surveyed absence or historical-publication claim follows from any of it.
    """
    try:
        requested_start, requested_end = date.fromisoformat(window_start), date.fromisoformat(window_end)
    except ValueError:
        return _payload(
            {
                "tool": "botanical_occurrence_temporal_neighbours",
                **refused("invalid_request", "window_start and window_end must be ISO calendar days"),
            }
        )
    request = BotanicalOccurrenceRequest(
        release_set_id=release_set_id,
        bbox=(minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude),
        zoom=DETAIL_ZOOM_FLOOR,
        taxon_concept_id=taxon_concept_id,
        spatial_quality="all",
        limit=MAX_LIMIT,
    )
    result = _read(request)
    if result["state"] != "detail":
        return _payload({"tool": "botanical_occurrence_temporal_neighbours", **result})

    overlapping: list[dict[str, Any]] = []
    neighbours: list[dict[str, Any]] = []
    for feature in result["features"]:
        interval = feature["event_interval"]
        if not interval["start"] or not interval["end"]:
            continue
        start, end = date.fromisoformat(interval["start"]), date.fromisoformat(interval["end"])
        if end >= requested_start and start <= requested_end:
            overlapping.append({**feature, "overlap": True, "signed_days": 0, "abs_days": 0})
            continue
        signed = _signed_days(requested_end, start) if start > requested_end else _signed_days(requested_start, end)
        neighbours.append(
            {**feature, "substitute": True, "overlap": False, "signed_days": signed, "abs_days": abs(signed)}
        )
    neighbours.sort(key=lambda entry: entry["abs_days"])
    return _payload(
        {
            "tool": "botanical_occurrence_temporal_neighbours",
            "state": "detail",
            "release_set_id": release_set_id,
            "requested_interval": {"start": window_start, "end": window_end},
            "exact": _exact_block(overlapping),
            "overlapping": overlapping[:limit],
            "substitutes": neighbours[:limit],
            "counts": result["counts"],
        }
    )


BOTANICAL_OCCURRENCE_TOOLS: Final = (
    botanical_occurrences_in_region,
    botanical_occurrence_spatial_neighbours,
    botanical_occurrence_temporal_neighbours,
)

__all__ = [
    "BOTANICAL_OCCURRENCE_TOOLS",
    "DEFAULT_NEIGHBOUR_RADIUS_METERS",
    "MAX_NEIGHBOUR_RADIUS_METERS",
    "REFUSED_CLAIMS",
    "botanical_occurrence_spatial_neighbours",
    "botanical_occurrence_temporal_neighbours",
    "botanical_occurrences_in_region",
    "use_generation_root",
]
