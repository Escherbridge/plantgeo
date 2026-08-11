"""Shared ArcGIS FeatureServer mechanics: the error document behind HTTP 200, the transfer-limit flag, offset paging."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

from agri_data_service.ingest.http import UpstreamPayloadError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

# A GeoJSON position is (x, y) or (x, y, z); any other arity is not a coordinate.
POSITION_ORDINATE_COUNTS: Final = frozenset({2, 3})

POLYGON_RING_DEPTH: Final = 2
MULTIPOLYGON_RING_DEPTH: Final = 3


@dataclass(frozen=True, slots=True)
class ArcGisFeatureCollection:
    """One validated ArcGIS GeoJSON page: its features, and whether the service said it clipped them."""

    features: tuple[Mapping[str, object], ...]
    exceeded_transfer_limit: bool


@dataclass(frozen=True, slots=True)
class ArcGisEnvelopeQuery:
    """One source's fixed half of an envelope query; `order_by_fields` is the paging-determinism knob."""

    endpoint: str
    out_fields: str
    where: str = "1=1"
    order_by_fields: str | None = None
    geometry_precision: int | None = None

    def page_url(self, *, bbox: str, offset: int, max_record_count: int) -> str:
        """Build one bounded ArcGIS GeoJSON page URL; see ingest/AGENTS.md "arcgis.py"."""
        parameters: dict[str, str] = {
            "where": self.where,
            "outFields": self.out_fields,
            "geometry": bbox,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
        }
        if self.order_by_fields is not None:
            parameters["orderByFields"] = self.order_by_fields
        parameters["resultOffset"] = str(offset)
        parameters["resultRecordCount"] = str(max_record_count)
        if self.geometry_precision is not None:
            parameters["geometryPrecision"] = str(self.geometry_precision)
        parameters["f"] = "geojson"
        return f"{self.endpoint}?{urlencode(parameters)}"


def page_exceeded_transfer_limit(payload: Mapping[str, object]) -> bool:
    """True when ArcGIS reported it clipped this page; GeoJSON nests the flag, the JSON form keeps it at the top."""
    properties = payload.get("properties")
    if isinstance(properties, dict) and properties.get("exceededTransferLimit") is True:
        return True
    return payload.get("exceededTransferLimit") is True


def parse_feature_collection(
    payload: object,
    *,
    error_prefix: str,
    unexpected_shape_reason: str,
) -> ArcGisFeatureCollection:
    """Validate an ArcGIS GeoJSON answer into its features, rejecting the error document it hides behind HTTP 200."""
    if not isinstance(payload, dict):
        raise UpstreamPayloadError(unexpected_shape_reason)
    error = payload.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        message = error.get("message") or ("; ".join(details) if isinstance(details, list) else None) or "unknown"
        raise UpstreamPayloadError(f"{error_prefix}: {message}")

    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise UpstreamPayloadError(unexpected_shape_reason)
    validated: list[Mapping[str, object]] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise UpstreamPayloadError(unexpected_shape_reason)
        validated.append(feature)
    return ArcGisFeatureCollection(
        features=tuple(validated),
        exceeded_transfer_limit=page_exceeded_transfer_limit(payload),
    )


def require_feature_properties(feature: Mapping[str, object], *, unexpected_shape_reason: str) -> Mapping[str, object]:
    """Return one feature's `properties` object, refusing a feature that publishes none."""
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise UpstreamPayloadError(unexpected_shape_reason)
    return properties


def is_ring_collection(value: object, depth: int) -> bool:
    """True when `value` nests `depth` levels of lists down to a 2-or-3 ordinate position."""
    if not isinstance(value, list) or not value:
        return False
    if depth == 0:
        return len(value) in POSITION_ORDINATE_COUNTS and all(
            not isinstance(ordinate, bool) and isinstance(ordinate, int | float) and math.isfinite(ordinate)
            for ordinate in value
        )
    return all(is_ring_collection(item, depth - 1) for item in value)


def require_polygon_geometry(geometry: object, *, unexpected_shape_reason: str) -> Mapping[str, object]:
    """Accept only a Polygon or MultiPolygon whose rings hold finite 2D or 3D positions."""
    if not isinstance(geometry, dict):
        raise UpstreamPayloadError(unexpected_shape_reason)
    coordinates = geometry.get("coordinates")
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon" and is_ring_collection(coordinates, POLYGON_RING_DEPTH):
        return geometry
    if geometry_type == "MultiPolygon" and is_ring_collection(coordinates, MULTIPOLYGON_RING_DEPTH):
        return geometry
    raise UpstreamPayloadError(unexpected_shape_reason)


def optional_text(properties: Mapping[str, object], field_name: str) -> str | None:
    """Return an optional ArcGIS string attribute, normalising an absent one to None."""
    value = properties.get(field_name)
    return value if isinstance(value, str) else None


def optional_number(properties: Mapping[str, object], field_name: str) -> float | None:
    """Return an optional ArcGIS numeric attribute, normalising an absent or non-finite one to None."""
    value = properties.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


async def page_offset_walk[RecordT](
    fetch_page: Callable[[int], Awaitable[tuple[list[RecordT], bool]]],
    *,
    max_pages: int,
    record_ceiling: int,
) -> tuple[list[RecordT], bool]:
    """Walk `resultOffset` pages until the service stops clipping, reporting whether records were left behind."""
    records: list[RecordT] = []
    offset = 0
    for _ in range(max_pages):
        page, exceeded_transfer_limit = await fetch_page(offset)
        records.extend(page)
        if not page or not exceeded_transfer_limit:
            return records, False
        offset += len(page)
        if len(records) >= record_ceiling:
            return records, True
    return records, True
