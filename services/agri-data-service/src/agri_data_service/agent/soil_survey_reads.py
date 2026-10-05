"""Soil survey at a selection, through the SAME admitted-release reader the map's /soil-survey route uses.

A static lane: read once at the admitted release, no date, no history. See agent/AGENTS.md,
"Closest-datapoint reads (2026-10-04)", for why this does not go through the observed-partition listing.
"""

from __future__ import annotations

import asyncio
import json
import math
from itertools import pairwise
from typing import TYPE_CHECKING, Any, Final, cast

import duckdb
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import ValidationError

from agri_data_service.agent import warehouse
from agri_data_service.agent.day_tolerance import day_tolerance
from agri_data_service.agent.selection_geodesy import EARTH_RADIUS_KM
from agri_data_service.config import settings
from agri_data_service.foundation.soil_survey.receipts import SoilSurveyError
from agri_data_service.foundation.soil_survey.release import NATIVE_RUNG
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.pipeline.parquet.availability_primitives import AvailabilityError
from agri_data_service.pipeline.parquet.availability_storage import BotoAvailabilityStorage
from agri_data_service.planes.soil_survey import (
    SoilSurveyViewport,
    gather_admitted_soil_survey_viewport,
    load_admitted_release,
    render_served_soil_survey,
    run_admitted_soil_survey_query,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.agent.selection_scope import Selection
    from agri_data_service.foundation.soil_survey.release import Release
    from agri_data_service.parquet_ops.duckdb_session import ServingSession
    from agri_data_service.pipeline.parquet.availability_storage import AvailabilityStorage

SOIL_SURVEY_LANE: Final = "soil-survey"

#: Nearest-delineation search boxes (half-widths in degrees, ~0.5 / 2 / 9 km). Each one is a full
#: viewport read under the map's own part/row/byte caps; a box over those caps ends the search.
NEAREST_DELINEATION_HALF_WIDTHS: Final = (0.005, 0.02, 0.08)

_KM_PER_DEGREE_LATITUDE: Final = math.pi * EARTH_RADIUS_KM / 180

#: The faults `interface/http/soil_survey.py` maps to `soil_survey_read_refused`, mirrored here.
_READ_FAULTS: Final = (
    SoilSurveyError,
    ValueError,
    ValidationError,
    ServingRefusalError,
    AvailabilityError,
    BotoCoreError,
    ClientError,
    duckdb.Error,
)


def release_storage() -> AvailabilityStorage:
    """The release store the map's route reads; a seam tests replace with an in-memory store."""
    return BotoAvailabilityStorage.from_credentials(
        settings.require_object_store(), prefix=settings.object_store_prefix
    )


def _segment_distance_km(point: tuple[float, float], start: Sequence[float], end: Sequence[float]) -> float:
    """Point-to-segment distance on a local equirectangular plane centred on the point (sub-percent at <= 10 km)."""
    scale = math.cos(math.radians(point[1])) * _KM_PER_DEGREE_LATITUDE
    ax, ay = (start[0] - point[0]) * scale, (start[1] - point[1]) * _KM_PER_DEGREE_LATITUDE
    bx, by = (end[0] - point[0]) * scale, (end[1] - point[1]) * _KM_PER_DEGREE_LATITUDE
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    fraction = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + fraction * dx, ay + fraction * dy)


def distance_to_geojson_km(point: tuple[float, float], geometry: dict[str, Any]) -> float:
    """Distance from a point to the nearest edge of a GeoJSON Polygon/MultiPolygon."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    return min(
        _segment_distance_km(point, start, end)
        for polygon in polygons
        for ring in polygon
        for start, end in pairwise(ring)
    )


async def _query(storage: AvailabilityStorage, release: Release, request: SoilSurveyViewport) -> list[dict[str, Any]]:
    """Gather off the event loop, then run the one bounded SQL inside the agent's serving slot."""
    gathered = await asyncio.to_thread(gather_admitted_soil_survey_viewport, request, storage=storage, release=release)
    table = gathered.table
    if table is None:
        return []

    def work(session: ServingSession) -> list[dict[str, Any]]:
        return run_admitted_soil_survey_query(session.connection, table, request)

    return await warehouse.source().run(work, operation="agent_soil_survey_release")


async def _nearest_delineation(
    storage: AvailabilityStorage, release: Release, point: tuple[float, float]
) -> tuple[dict[str, Any] | None, str]:
    """Search growing viewports for the delineation edge nearest the point; say why the search stopped."""
    longitude, latitude = point
    for half in NEAREST_DELINEATION_HALF_WIDTHS:
        request = SoilSurveyViewport(
            (longitude - half, latitude - half, longitude + half, latitude + half), NATIVE_RUNG
        )
        try:
            rows = await _query(storage, release, request)
        except SoilSurveyError:
            return None, "viewport_budget_reached"
        measured = [(distance_to_geojson_km(point, json.loads(str(row["geometry_json"]))), row) for row in rows]
        if measured:
            distance, row = min(measured, key=lambda pair: (pair[0], str(pair[1]["mupolygonkey"])))
            # Inside the box's inscribed circle no delineation outside the box can be nearer.
            if distance <= half * _KM_PER_DEGREE_LATITUDE * math.cos(math.radians(abs(latitude) + half)):
                return {**row, "distance_km": round(distance, 3)}, "found"
    return None, "search_bound_reached"


def _map_unit(feature: dict[str, Any]) -> dict[str, Any]:
    """The map's rendered feature without its geometry: the agent needs map-unit facts, not rings."""
    return {key: value for key, value in feature.items() if key != "geometry"}


async def soil_survey_selection(selected: Selection) -> dict[str, Any]:
    """Map units at the selected point from the admitted SSURGO release, marked static and dateless."""
    base: dict[str, Any] = {
        "parquet_lane": SOIL_SURVEY_LANE,
        "lane_nature": "static_lookup",
        "static": True,
        "reader": "planes.soil_survey",
        **day_tolerance(SOIL_SURVEY_LANE).to_wire(),
        "history": [],
    }
    admitted = settings.ssurgo_admitted_release_sha256
    if not admitted:
        return {
            **base,
            "selected": {
                "state": "refused",
                "static": True,
                "refusal_code": "soil_survey_release_not_admitted",
                "message": "No SSURGO release is admitted for serving; the map shows no soil survey either.",
                "features": [],
            },
        }
    point = (selected.longitude, selected.latitude)
    request = SoilSurveyViewport((*point, *point), NATIVE_RUNG, point=point)
    try:
        storage = await asyncio.to_thread(release_storage)
        release = await asyncio.to_thread(load_admitted_release, storage, admitted)
        rows = await _query(storage, release, request)
        search = "covers"
        nearest_distance: float | None = 0.0
        if not rows:
            nearest, search = await _nearest_delineation(storage, release, point)
            rows = [] if nearest is None else [nearest]
            nearest_distance = None if nearest is None else nearest["distance_km"]
    except _READ_FAULTS as error:
        return {
            **base,
            "selected": {
                "state": "refused",
                "static": True,
                "refusal_code": "soil_survey_read_refused",
                "message": f"The admitted SSURGO read failed ({type(error).__name__}).",
                "features": [],
            },
        }
    rendered = render_served_soil_survey(
        rows, release=release, request=request, touched_areas=(), admitted_sha256=admitted
    )
    features = [_map_unit(feature) for feature in cast("list[dict[str, Any]]", rendered["features"])]
    result: dict[str, Any] = {
        "state": "published",
        "static": True,
        "release_day": rendered["releaseDay"],
        "revision": rendered["revision"],
        "features": features,
        "features_truncated": rendered["truncated"],
        "nearest_search": search,
    }
    if features:
        covers = search == "covers"
        result.update(
            {
                "spatial_relation": "covers" if covers else "nearest_cell",
                "distance_km": 0.0 if covers else nearest_distance,
                "distance_km_basis": "covers" if covers else "delineation_edge",
            }
        )
    return {**base, "selected": result}
