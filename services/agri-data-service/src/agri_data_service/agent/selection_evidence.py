"""One selection-aware evidence entry point for every published map surface."""

from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Final

import httpx

from agri_data_service.agent.selection_reads import lane_selection
from agri_data_service.agent.selection_scope import (
    MAX_FEATURES,
    MAX_LANE_DAY_READS,
    MAX_RANGE_DAYS,
    PAGE_DAYS,
    Selection,
    history_page_days,
)
from agri_data_service.agent.surfaces import (
    AGENT_SURFACE_NAMES,
    APP_SURFACE_NAMES,
    BOTANICAL_SURFACE_NAMES,
    surface_lanes,
    surface_region_layer,
)
from agri_data_service.config import settings
from agri_data_service.foundation.botanical_occurrences.coordinates import haversine_meters
from agri_data_service.foundation.region import is_layer_bound, load_region
from agri_data_service.parquet_ops.coverage import registered_census_lanes
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import RequestError
from agri_data_service.planes.botanical_occurrences import (
    DETAIL_ZOOM_FLOOR,
    BotanicalOccurrenceRequest,
    BotanicalOccurrenceRequestError,
    BotanicalOccurrenceServingError,
    read_botanical_occurrences,
    read_current_botanical_release,
)

_APP_RESPONSE_BYTES: Final = 1_048_576
_GBIF_COLLECTION: Final = "gbif:pnw:vascular"


def catalogue() -> dict[str, Any]:
    """Advertise every map surface with its real reader and explicit evidence posture."""
    natures = {entry.layer: entry.nature for entry in registered_census_lanes()}
    region = load_region()
    rows = []
    for surface in AGENT_SURFACE_NAMES:
        reader = (
            "map_app"
            if surface in APP_SURFACE_NAMES
            else "botanical_occurrences"
            if surface in BOTANICAL_SURFACE_NAMES
            else "governed_parquet"
        )
        binding = surface_region_layer(surface)
        rows.append(
            {
                "surface_name": surface,
                "reader": reader,
                "tool": "surface_evidence_for_selection",
                "parquet_lanes": list(surface_lanes(surface)),
                "lane_natures": {lane: natures.get(lane) for lane in surface_lanes(surface)},
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
    if surface in BOTANICAL_SURFACE_NAMES:
        return await _botanical_evidence(surface, selection, base)
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
    page_days = history_page_days(len(lanes))
    results = []
    for lane in lanes:
        try:
            result = await lane_selection(surface, lane, natures.get(lane), selection, page_days=page_days)
        except ServingRefusalError as error:
            result = {
                "parquet_lane": lane,
                "lane_nature": natures.get(lane),
                "history": [],
                "selected": refusal(error.code, error.message, requested_day=selection.day.isoformat(), features=[]),
            }
        results.append(result)
    total = (selection.last - selection.first).days + 1
    page_count = min(page_days, total - selection.page_start)
    sampled = sorted({entry["requested_day"] for lane in results for entry in lane["history"]})
    next_offset = selection.page_start + page_count
    return {
        **base,
        "lanes": results,
        "history": {
            "requested_day_count": total,
            "days_per_page": page_days,
            "sampled_day_count": len(sampled),
            "page_start": selection.page_start,
            "next_page_start": next_offset if next_offset < total else None,
            "complete": selection.page_start == 0 and next_offset == total,
            "sampling": "published_support_then_calendar_gaps",
            "sampled_days": sampled,
        },
        "note": (
            "Evidence comes from the map's governed lane, serving rung and numeric source support intersecting "
            "the selected tile. covers_probe_point identifies support containing the coordinate; other features "
            "are spatial neighbours within that tile. A containing grid cell remains a cell measurement, never "
            "a point measurement. Exact day and served release date stay separate. History prioritizes actual "
            "published dates across the complete requested interval, including its endpoints, with explicit "
            "pagination. Unsampled days are unknown; do not infer a complete trend until all pages are read. "
            "Read day states and truncation before features; unwritten or refused data is never zero."
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


async def _botanical_evidence(surface: str, selected: Selection, base: dict[str, Any]) -> dict[str, Any]:
    def work() -> dict[str, Any]:
        current = read_current_botanical_release()
        if current.get("state") != "current":
            return {**base, **current}
        release = str(current["release_set_id"])
        try:
            if surface in {"botanical-richness", "botanical-collection-effort"}:
                aggregate = read_botanical_occurrences(
                    BotanicalOccurrenceRequest(
                        release_set_id=release,
                        bbox=selected.bbox.as_envelope_arguments,
                        zoom=min(DETAIL_ZOOM_FLOOR - 1, selected.zoom),
                        limit=MAX_FEATURES,
                        offset=selected.page_start,
                    )
                )
                return {
                    **base,
                    "release": current,
                    "lanes": [
                        {
                            "parquet_lane": "botanical-occurrences",
                            "lane_nature": "current_release_aggregate",
                            "selected": refusal(
                                "historical_publication_unsupported",
                                "Richness and collection effort are current-release aggregates, "
                                "not measurements at the selected day.",
                                requested_day=selected.day.isoformat(),
                                features=[],
                            ),
                            "history": [],
                            "snapshot_context": aggregate,
                        }
                    ],
                    "history": {"complete": False, "state": "historical_publication_unsupported"},
                    "note": (
                        "snapshot_context is the same pinned current-release aggregate the map draws. "
                        "documented_taxa describes recorded richness; record_count/event_estimate/collection_count "
                        "describe collecting effort. Neither aggregate is filtered by selected collection-event "
                        "dates, and neither proves cover, current occupancy, or historical publication state."
                    ),
                }
            collection = _GBIF_COLLECTION if surface == "gbif-occurrences" else None
            exact = read_botanical_occurrences(
                BotanicalOccurrenceRequest(
                    release_set_id=release,
                    bbox=selected.bbox.as_envelope_arguments,
                    zoom=max(DETAIL_ZOOM_FLOOR, selected.zoom),
                    event_start=selected.day,
                    event_end=selected.day,
                    limit=MAX_FEATURES,
                    collection_key=collection,
                    spatial_quality="all",
                )
            )
            history = read_botanical_occurrences(
                BotanicalOccurrenceRequest(
                    release_set_id=release,
                    bbox=selected.bbox.as_envelope_arguments,
                    zoom=max(DETAIL_ZOOM_FLOOR, selected.zoom),
                    event_start=selected.first,
                    event_end=selected.last,
                    limit=MAX_FEATURES,
                    offset=selected.page_start,
                    collection_key=collection,
                    spatial_quality="all",
                )
            )
        except (BotanicalOccurrenceRequestError, BotanicalOccurrenceServingError) as error:
            return refusal("botanical_evidence_unavailable", str(error), **base)
        return {
            **base,
            "release": current,
            "lanes": [
                {
                    "parquet_lane": "botanical-occurrences",
                    "lane_nature": "collection_event_records",
                    "selected": _botanical_day(exact, surface, selected),
                    "history": [_botanical_day(history, surface, selected)],
                }
            ],
            "history": {
                "range_start": selected.first.isoformat(),
                "range_end": selected.last.isoformat(),
                "page_start": selected.page_start,
                "next_page_start": selected.page_start + MAX_FEATURES if history.get("truncated") else None,
                "complete": selected.page_start == 0 and not history.get("truncated", True),
            },
            "reader": "botanical_occurrences",
            "surface_variant": surface,
            "note": (
                "Collection event dates overlap the selected day or exact requested history range; intervals "
                "retain their original precision. The explicitly pinned current release does not reconstruct "
                "historical publication state. GBIF and other collections remain separate map surfaces. "
                "General-occurrence pages exclude GBIF after the source page, so a truncated empty page is not "
                "an absence. Generalized coordinates retain uncertainty and do not establish exact point "
                "containment. Specimen evidence does not measure cover, abundance, present occupancy, "
                "suitability, or absence."
            ),
        }

    return await asyncio.to_thread(work)


def _botanical_day(payload: dict[str, Any], surface: str, selected: Selection) -> dict[str, Any]:
    if payload.get("state") != "detail":
        return {**payload, "requested_day": selected.day.isoformat(), "features": []}
    features = []
    for row in payload["features"]:
        if surface == "botanical-occurrences" and row.get("collection_key") == _GBIF_COLLECTION:
            continue
        interval = row.get("event_interval") or {}
        start, end = interval.get("start"), interval.get("end")
        distance_days = (
            max(0, (date.fromisoformat(start) - selected.day).days, (selected.day - date.fromisoformat(end)).days)
            if start and end
            else None
        )
        features.append(
            {
                "observed_day": start if start == end else None,
                "observed_interval": interval,
                "distance_days": distance_days,
                "distance_basis": "reported_coordinate",
                "distance_meters": haversine_meters(
                    selected.longitude, selected.latitude, row["longitude"], row["latitude"]
                ),
                "centroid_longitude": row["longitude"],
                "centroid_latitude": row["latitude"],
                "spatial_relation": "reported_coordinate_in_selection_tile",
                "properties": row,
            }
        )
    return {
        "state": "published",
        "requested_day": selected.day.isoformat(),
        "served_day": None,
        "features": features,
        "features_truncated": payload["truncated"],
        "publication": {"release_set_id": payload["release_set_id"], "published_at": payload.get("published_at")},
    }


def parse_selection(**arguments: object) -> Selection | dict[str, Any]:
    """Convert malformed tool inputs into a typed request refusal."""
    try:
        return Selection.parse(**arguments)
    except (RequestError, ValueError, TypeError, KeyError) as error:
        return refusal("invalid_selection", str(error))
