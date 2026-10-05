"""One selection-aware evidence entry point for every published map surface."""

from __future__ import annotations

import json
from typing import Any, Final

import httpx

from agri_data_service.agent.day_tolerance import day_tolerance
from agri_data_service.agent.selection_reads import lane_selection, static_lane_selection
from agri_data_service.agent.selection_scope import (
    MAX_FEATURES,
    MAX_LANE_DAY_READS,
    MAX_RANGE_DAYS,
    MIN_MULTI_LANE_HISTORY_DAYS,
    PAGE_DAYS,
    Selection,
    history_page_days,
    utc_today,
)
from agri_data_service.agent.soil_survey_reads import SOIL_SURVEY_LANE, soil_survey_selection
from agri_data_service.agent.surfaces import (
    AGENT_SURFACE_NAMES,
    APP_SURFACE_NAMES,
    surface_lanes,
    surface_region_layer,
)
from agri_data_service.config import settings
from agri_data_service.foundation.region import is_layer_bound, load_region
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import RequestError

_APP_RESPONSE_BYTES: Final = 1_048_576


def catalogue() -> dict[str, Any]:
    """Advertise every map surface with its real reader and explicit evidence posture."""
    natures = {entry.layer: entry.nature for entry in registered_census_lanes()}
    region = load_region()
    rows = []
    for surface in AGENT_SURFACE_NAMES:
        reader = "map_app" if surface in APP_SURFACE_NAMES else "governed_parquet"
        binding = surface_region_layer(surface)
        rows.append(
            {
                "surface_name": surface,
                "reader": reader,
                "tool": "surface_evidence_for_selection",
                "parquet_lanes": list(surface_lanes(surface)),
                "lane_natures": {lane: natures.get(lane) for lane in surface_lanes(surface)},
                "lane_day_tolerance": {lane: day_tolerance(lane).to_wire() for lane in surface_lanes(surface)},
                "bound_in_region": None if binding is None else is_layer_bound(region, binding),
                "availability": "resolve_at_requested_selection",
            }
        )
    return {
        "layers": rows,
        "layer_count": len(rows),
        "region": region.slug,
        "bounds": {
            "max_history_days_per_page": PAGE_DAYS,
            "min_history_days_per_page_multi_lane": MIN_MULTI_LANE_HISTORY_DAYS,
            "max_lane_day_reads_per_call": MAX_LANE_DAY_READS,
            "max_calendar_range_days": MAX_RANGE_DAYS,
            "feature_rows_per_lane_day": MAX_FEATURES,
        },
        "aliases": {
            "VPD": "soil-field-vpd",
            "vapor pressure deficit": "soil-field-vpd",
            "vapour pressure deficit": "soil-field-vpd",
            "NDVI": "vegetation",
        },
        "note": (
            "All map surfaces are discoverable. Read the selected tile's numeric support and exact day. "
            "Catalogue entries do not prove measurements or publication; unavailable readers return typed refusals."
        ),
    }


def refusal(code: str, message: str, **context: object) -> dict[str, Any]:
    """State an inability to answer without inventing a spatial or temporal absence."""
    return {"state": "refused", "refusal_code": code, "message": message, **context}


async def retrieve(surface: str, selection: Selection) -> dict[str, Any]:
    """Resolve a complete map surface at the selected tile and exact active calendar bounds."""
    base = {"surface_name": surface, "requested_day": selection.day.isoformat(), "selection": selection.to_wire()}
    if surface not in AGENT_SURFACE_NAMES:
        return refusal("unknown_surface", "Use list_environmental_layers for accepted surface names.", **base)
    if surface in APP_SURFACE_NAMES:
        return await _app_evidence(surface, selection, base)
    binding = surface_region_layer(surface)
    if binding is not None and not is_layer_bound(load_region(), binding):
        return refusal("not_available_in_region", "This region binds no admitted source for this surface.", **base)
    return await _parquet_evidence(surface, selection, base)


async def _parquet_evidence(surface: str, selection: Selection, base: dict[str, Any]) -> dict[str, Any]:
    """Resolve the governed tile lanes after the surface's reader and region were admitted."""
    if selection.page_start > (selection.last - selection.first).days:
        return refusal("invalid_selection", "page_start lies past the requested calendar window.", **base)
    lanes = surface_lanes(surface)
    if not lanes:
        return refusal(
            "parquet_lane_not_published", "No governed map-serving lane is admitted for this surface.", **base
        )
    natures = {entry.layer: entry.nature for entry in registered_census_lanes()}
    static_lanes = {lane for lane in lanes if day_tolerance(lane).mode == "static"}
    dated_lanes = [lane for lane in lanes if lane not in static_lanes]
    page_days = history_page_days(len(dated_lanes)) if dated_lanes else 0
    today = utc_today()
    results = []
    for lane in lanes:
        try:
            if lane == SOIL_SURVEY_LANE:
                result = await soil_survey_selection(selection)
            elif lane in static_lanes:
                result = await static_lane_selection(surface, lane, natures.get(lane), selection)
            else:
                result = await lane_selection(
                    surface, lane, natures.get(lane), selection, page_days=page_days, today=today
                )
        except ServingRefusalError as error:
            result = {
                "parquet_lane": lane,
                "lane_nature": natures.get(lane),
                "history": [],
                "selected": refusal(error.code, error.message, requested_day=selection.day.isoformat(), features=[]),
            }
        results.append(result)
    total = (selection.last - selection.first).days + 1
    # Days after the server's UTC today are never scheduled, so pagination ends at today.
    schedulable = max(0, (min(selection.last, today) - selection.first).days + 1) if dated_lanes else 0
    page_count = max(0, min(page_days, schedulable - selection.page_start))
    sampled = sorted({entry["requested_day"] for lane in results for entry in lane["history"]})
    next_offset = selection.page_start + page_count
    return {
        **base,
        "lanes": results,
        "history": {
            "requested_day_count": total,
            "schedulable_day_count": schedulable,
            "days_per_page": page_days,
            "sampled_day_count": len(sampled),
            "page_start": selection.page_start,
            "next_page_start": next_offset if next_offset < schedulable else None,
            "complete": selection.page_start == 0 and next_offset == schedulable,
            "sampling": "selected_then_nearest_published_then_endpoints" if dated_lanes else "static_current_release",
            "sampled_days": sampled,
            # Every lane-day resolution this call made, substitution reads included (<= the catalogue's
            # max_lane_day_reads_per_call); a static lane is one read.
            "lane_day_reads": sum(int(lane.get("lane_day_reads", 1)) for lane in results),
        },
        "note": (
            "Evidence comes from the map's governed lane, serving rung and numeric source support intersecting "
            "the selected tile. covers_probe_point identifies support containing the coordinate. When no support "
            "covers it, spatial_relation is nearest_cell and distance_km is the great-circle distance to that "
            "cell or station, however far: say 'the nearest cell is N km away', never that it is the value "
            "here. nearest_area_outside means the point lies inside NO polygon of this layer (for drought, no "
            "drought area): that is the answer, and the nearest area and its centroid distance are context "
            "only. A containing grid cell remains a cell measurement, never a point measurement. state "
            "published_nearest means the selected day was not written and served_day, day_offset days away "
            "(negative = earlier), is the nearest published day within the lane's tolerance_days; say so. "
            "governed_absence is a published answer (for fire-detections, zero detections) and is never "
            "replaced; it and an unwritten day beyond tolerance carry nearest_published_day/nearest_day_offset "
            "as information only. static lanes are read at their current release and carry no date. History "
            "starts with the selected day and its nearest published neighbours, then the window endpoints, with "
            "explicit pagination. Unsampled days are unknown; do not infer a complete trend until all pages are "
            "read. Read day states and truncation before features; unwritten or refused data is never zero."
        ),
    }


async def _app_evidence(surface: str, selected: Selection, base: dict[str, Any]) -> dict[str, Any]:
    origin = settings.agent_map_app_url
    if not origin:
        return refusal(
            "app_reader_not_configured",
            "This surface uses the map application's reader; configure AGENT_MAP_APP_URL "
            "to admit its bounded tile evidence endpoint.",
            **base,
        )
    arguments = {
        "surface_name": surface,
        "day": selected.day.isoformat(),
        "longitude": selected.longitude,
        "latitude": selected.latitude,
        "range_start": selected.first.isoformat(),
        "range_end": selected.last.isoformat(),
        "time_scale": selected.time_scale,
        "zoom": selected.zoom,
        "page_start": selected.page_start,
    }
    try:
        async with (
            httpx.AsyncClient(timeout=12, follow_redirects=False) as client,
            client.stream("POST", f"{origin.rstrip('/')}/api/v1/map-evidence", json=arguments) as response,
        ):
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > _APP_RESPONSE_BYTES:
                    return refusal("app_reader_over_budget", "Map evidence exceeded its response budget.", **base)
        result = json.loads(body)
        if (
            not isinstance(result, dict)
            or result.get("surface_name") != surface
            or result.get("requested_day") != selected.day.isoformat()
        ):
            return refusal(
                "app_reader_invalid_response", "The map response did not preserve surface and selected day.", **base
            )
        returned_selection = result.get("selection")
        if not isinstance(returned_selection, dict) or any(
            returned_selection.get(key) != arguments[key]
            for key in ("longitude", "latitude", "range_start", "range_end", "time_scale", "zoom")
        ):
            return refusal(
                "app_reader_invalid_response",
                "The map response did not preserve the selected coordinates and active range.",
                **base,
            )
        return {**result, **base}
    except (httpx.HTTPError, ValueError) as error:
        return refusal(
            "app_reader_unavailable", f"The bounded map evidence request failed ({type(error).__name__}).", **base
        )


def parse_selection(**arguments: object) -> Selection | dict[str, Any]:
    """Convert malformed tool inputs into a typed request refusal."""
    try:
        return Selection.parse(**arguments)
    except (RequestError, ValueError, TypeError, KeyError) as error:
        return refusal("invalid_selection", str(error))
