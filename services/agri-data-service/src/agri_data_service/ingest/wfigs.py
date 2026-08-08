"""WFIGS interagency fire-perimeter ingestion: the ArcGIS FeatureServer adapter and its retrying, bounded, paged job."""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import structlog

from agri_data_service.ingest.http import (
    HTTP_SERVER_ERROR_MINIMUM,
    HTTP_TOO_MANY_REQUESTS,
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded_json,
    upstream_client,
)
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_fire_perimeter_identity,
    format_javascript_timestamp,
)
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

WFIGS_SOURCE: Final = "wfigs-fire-perimeters"
WFIGS_CHANNEL: Final = "layer:fire-perimeters"
WFIGS_PROPERTY_SOURCE: Final = "WFIGS Interagency Fire Perimeters"
FIRE_PERIMETERS_LAYER_VARIABLE: Final = "FIRE_PERIMETERS_LAYER_ID"
DEFAULT_FIRE_PERIMETERS_LAYER_NAME: Final = "fire-perimeters"

WFIGS_QUERY_URL: Final = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services"
    "/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)
WFIGS_OUT_FIELDS: Final = ",".join(
    (
        "attr_UniqueFireIdentifier",
        "attr_IrwinID",
        "poly_IncidentName",
        "attr_FireDiscoveryDateTime",
        "poly_GISAcres",
        "attr_FireCause",
        "poly_PolygonDateTime",
        "attr_IncidentTypeCategory",
        "attr_POOState",
        "attr_PercentContained",
    )
)

WFIGS_BOUNDS: Final = UpstreamBounds(max_bytes=16 * 1024 * 1024, timeout_seconds=20.0)
# Measured live against production 2026-08-08: one unpaged query over the PNW extent (114 current
# perimeters, an ordinary day, not even peak season) answered 18,091,373 bytes -- already over
# WFIGS_BOUNDS.max_bytes on its own. geometryPrecision=5 (~1.1 m; plenty for a perimeter drawn on a
# map) cut that to 10,950,562 bytes, and a single 50-record page at that precision measured
# 3,713,460 bytes (~74 KB/feature). MAX_RECORD_COUNT is sized so an average page (~96 KB/feature
# across the full sample) stays near 9.6 MB -- well inside the cap, with room for a fire season
# denser than today's. The per-request byte cap stays the backstop: a single page that is still too
# heavy (one pathologically complex perimeter) fails that page rather than silently growing the cap.
WFIGS_GEOMETRY_PRECISION: Final = 5
MAX_RECORD_COUNT: Final = 100
# Circuit breaker against an upstream that reports exceededTransferLimit forever; 200 * 100 = 20,000
# records is double DEFAULT_MAX_SOURCE_RECORDS, so the configured ceiling is what stops an ordinary
# run, not this bound.
MAX_PAGES: Final = 200
MAX_ATTEMPTS: Final = 3
RETRY_BASE_DELAYS_SECONDS: Final = (1.0, 2.0)
BUSY_MESSAGE_PATTERN: Final = re.compile(r"too many requests|busy|try again", re.IGNORECASE)

# JavaScript's Date range; beyond it `new Date(ms)` is Invalid Date and the TypeScript stored null.
MAX_JAVASCRIPT_EPOCH_MILLISECONDS: Final = 8.64e15
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

CRITICAL_CONTAINMENT: Final = 25
HIGH_CONTAINMENT: Final = 50
MODERATE_CONTAINMENT: Final = 75
MIN_CONTAINMENT: Final = 0
MAX_CONTAINMENT: Final = 100

POSITION_ORDINATE_COUNTS: Final = frozenset({2, 3})


def resolve_fire_perimeters_layer_name() -> str:
    """Read FIRE_PERIMETERS_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(FIRE_PERIMETERS_LAYER_VARIABLE, "").strip() or DEFAULT_FIRE_PERIMETERS_LAYER_NAME


def perimeter_severity(percent_contained: object) -> str | None:
    """Grade an active perimeter from its reported containment, returning None when WFIGS reports none."""
    if isinstance(percent_contained, bool) or not isinstance(percent_contained, int | float):
        return None
    value = float(percent_contained)
    if not math.isfinite(value):
        return None
    contained = min(MAX_CONTAINMENT, max(MIN_CONTAINMENT, value))
    if contained < CRITICAL_CONTAINMENT:
        return "critical"
    if contained < HIGH_CONTAINMENT:
        return "high"
    if contained < MODERATE_CONTAINMENT:
        return "moderate"
    return "low"


def epoch_milliseconds_to_iso(value: object) -> str | None:
    """Render an ArcGIS epoch-millisecond field as JavaScript `new Date(ms).toISOString()` rendered it."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    milliseconds = float(value)
    if not math.isfinite(milliseconds) or abs(milliseconds) > MAX_JAVASCRIPT_EPOCH_MILLISECONDS:
        return None
    try:
        return format_javascript_timestamp(EPOCH + timedelta(milliseconds=math.trunc(milliseconds)))
    except (OverflowError, OSError, ValueError):
        # JavaScript's Date range outruns Python's datetime range; WFIGS never emits such a value.
        return None


def build_query_url(bbox: str, offset: int = 0, max_record_count: int = MAX_RECORD_COUNT) -> str:
    """Build the bounded ArcGIS GeoJSON query URL for one page of one bbox."""
    query = urlencode(
        {
            "where": "1=1",
            "outFields": WFIGS_OUT_FIELDS,
            "geometry": bbox,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "resultOffset": str(offset),
            "resultRecordCount": str(max_record_count),
            "geometryPrecision": str(WFIGS_GEOMETRY_PRECISION),
            "f": "geojson",
        }
    )
    return f"{WFIGS_QUERY_URL}?{query}"


def _is_ring_collection(value: object, depth: int) -> bool:
    """True when `value` nests `depth` levels of lists down to a 2-or-3 ordinate position."""
    if not isinstance(value, list) or not value:
        return False
    if depth == 0:
        return len(value) in POSITION_ORDINATE_COUNTS and all(
            not isinstance(ordinate, bool) and isinstance(ordinate, int | float) and math.isfinite(ordinate)
            for ordinate in value
        )
    return all(_is_ring_collection(item, depth - 1) for item in value)


def _validate_geometry(geometry: object) -> Mapping[str, object]:
    """Accept only a Polygon or MultiPolygon whose rings hold finite 2D or 3D positions."""
    if not isinstance(geometry, dict):
        raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")
    coordinates = geometry.get("coordinates")
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon" and _is_ring_collection(coordinates, 2):
        return geometry
    if geometry_type == "MultiPolygon" and _is_ring_collection(coordinates, 3):
        return geometry
    raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")


def _optional_text(properties: Mapping[str, object], field_name: str) -> str | None:
    """Return an optional ArcGIS string attribute, normalising an absent one to None."""
    value = properties.get(field_name)
    return value if isinstance(value, str) else None


def _optional_number(properties: Mapping[str, object], field_name: str) -> float | None:
    """Return an optional ArcGIS numeric attribute, normalising an absent or non-finite one to None."""
    value = properties.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


@dataclass(frozen=True, slots=True)
class WfigsPerimeterPage:
    """One page of fire perimeters plus whether the upstream said more of them remain."""

    perimeters: list[dict[str, object]]
    exceeded_transfer_limit: bool


def _exceeded_transfer_limit(payload: Mapping[str, object]) -> bool:
    """True when ArcGIS reported it clipped this page; GeoJSON nests the flag, the JSON form keeps it at the top."""
    properties = payload.get("properties")
    if isinstance(properties, dict) and properties.get("exceededTransferLimit") is True:
        return True
    return payload.get("exceededTransferLimit") is True


def parse_perimeter_collection(payload: object) -> WfigsPerimeterPage:
    """Parse an ArcGIS GeoJSON answer into one page of perimeter records, rejecting its HTTP-200 error payload."""
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")
    error = payload.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        message = error.get("message") or ("; ".join(details) if isinstance(details, list) else None) or "unknown"
        raise UpstreamPayloadError(f"WFIGS API error: {message}")

    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")

    perimeters: list[dict[str, object]] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")
        fire_identifier = properties.get("attr_UniqueFireIdentifier")
        if not isinstance(fire_identifier, str) or not fire_identifier:
            raise UpstreamPayloadError("WFIGS API returned an unexpected feature collection shape")
        perimeters.append(
            {
                "uniqueFireIdentifier": fire_identifier,
                "irwinId": _optional_text(properties, "attr_IrwinID"),
                "incidentName": _optional_text(properties, "poly_IncidentName"),
                "fireDiscoveryDateTime": epoch_milliseconds_to_iso(properties.get("attr_FireDiscoveryDateTime")),
                "polygonDateTime": epoch_milliseconds_to_iso(properties.get("poly_PolygonDateTime")),
                "gisAcres": _optional_number(properties, "poly_GISAcres"),
                "fireCause": _optional_text(properties, "attr_FireCause"),
                "incidentTypeCategory": _optional_text(properties, "attr_IncidentTypeCategory"),
                "pooState": _optional_text(properties, "attr_POOState"),
                "percentContained": _optional_number(properties, "attr_PercentContained"),
                "geometry": _validate_geometry(feature.get("geometry")),
            }
        )
    return WfigsPerimeterPage(perimeters=perimeters, exceeded_transfer_limit=_exceeded_transfer_limit(payload))


def is_retryable_failure(error: Exception) -> bool:
    """True for an ArcGIS busy payload or a transient 429/5xx worth another attempt."""
    if isinstance(error, UpstreamPayloadError):
        return BUSY_MESSAGE_PATTERN.search(str(error)) is not None
    return isinstance(error, UpstreamHttpError) and (
        error.status == HTTP_TOO_MANY_REQUESTS or error.status >= HTTP_SERVER_ERROR_MINIMUM
    )


def jittered_retry_delay_seconds(attempt_index: int) -> float:
    """Exponential backoff base delay for one attempt, randomised by a 0.5-1.5x jitter."""
    return RETRY_BASE_DELAYS_SECONDS[attempt_index] * (0.5 + random.random())


async def fetch_fire_perimeters_page(
    client: httpx.AsyncClient,
    bbox: str,
    offset: int = 0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> WfigsPerimeterPage:
    """Fetch one bounded page of WFIGS perimeters, retrying only a busy or transient upstream."""
    url = build_query_url(bbox, offset)
    for attempt in range(MAX_ATTEMPTS):
        try:
            return parse_perimeter_collection(await fetch_bounded_json(client, url, WFIGS_BOUNDS))
        except (UpstreamHttpError, UpstreamPayloadError) as error:
            if attempt == MAX_ATTEMPTS - 1 or not is_retryable_failure(error):
                raise
            logger.info("wfigs_upstream_retry", attempt=attempt + 1, offset=offset, error=str(error))
            await sleep(jittered_retry_delay_seconds(attempt))
    raise UpstreamPayloadError("WFIGS retry loop ended without a response")  # pragma: no cover - unreachable.


async def fetch_fire_perimeters(
    client: httpx.AsyncClient,
    bbox: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[dict[str, object]], bool]:
    """Page bounded WFIGS perimeters until the upstream stops clipping, reporting whether more were left behind."""
    ceiling = resolve_max_source_records()
    perimeters: list[dict[str, object]] = []
    offset = 0
    for _ in range(MAX_PAGES):
        page = await fetch_fire_perimeters_page(client, bbox, offset, sleep)
        perimeters.extend(page.perimeters)
        if not page.perimeters or not page.exceeded_transfer_limit:
            return perimeters, False
        offset += len(page.perimeters)
        if len(perimeters) >= ceiling:
            return perimeters, True
    return perimeters, True


def build_perimeter_write(perimeter: Mapping[str, object], layer_name: str) -> FeatureWrite | None:
    """Build one perimeter's write, returning None when the upstream supplied no unique fire identifier."""
    try:
        identity = build_fire_perimeter_identity(perimeter)
    except (MissingNativeKeyError, ValueError):
        return None
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            "uniqueFireIdentifier": perimeter.get("uniqueFireIdentifier"),
            "irwinId": perimeter.get("irwinId"),
            "incidentName": perimeter.get("incidentName"),
            "fireDiscoveryDateTime": perimeter.get("fireDiscoveryDateTime"),
            "polygonDateTime": perimeter.get("polygonDateTime"),
            "gisAcres": perimeter.get("gisAcres"),
            "fireCause": perimeter.get("fireCause"),
            "incidentTypeCategory": perimeter.get("incidentTypeCategory"),
            "pooState": perimeter.get("pooState"),
            "percentContained": perimeter.get("percentContained"),
            "severity": perimeter_severity(perimeter.get("percentContained")),
            "source": WFIGS_PROPERTY_SOURCE,
            "geometry": perimeter.get("geometry"),
        },
        channel=WFIGS_CHANNEL,
    )


async def run_fire_perimeters_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> IngestionJobResult:
    """Fetch bounded WFIGS perimeters and refresh them in place as their polygons and containment advance."""
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(WFIGS_SOURCE, UNCONFIGURED_BBOX_REASON)

    if client is None:
        async with upstream_client(WFIGS_BOUNDS) as owned_client:
            perimeters, more_remaining = await fetch_fire_perimeters(owned_client, area)
    else:
        perimeters, more_remaining = await fetch_fire_perimeters(client, area)

    selected = perimeters[: resolve_max_source_records()]
    layer_name = resolve_fire_perimeters_layer_name()
    writes = [
        write for write in (build_perimeter_write(perimeter, layer_name) for perimeter in selected) if write is not None
    ]

    return IngestionJobResult(
        source=WFIGS_SOURCE,
        status="ingested",
        records_seen=len(perimeters),
        records_written=await write_features(writes),
        truncated=more_remaining or len(perimeters) > len(selected),
        details={"rejected": len(selected) - len(writes)},
    )
