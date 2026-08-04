"""Oregon OEM evacuation-area ingestion: the ArcGIS FeatureServer adapter and its retrying, bounded, paged job."""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
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
    FeatureIdentity,
    MissingNativeKeyError,
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

EVACUATION_ZONES_SOURCE: Final = "evacuation-zones"
EVACUATION_ZONES_CHANNEL: Final = "layer:evacuation-zones"
EVACUATION_ZONES_PROPERTY_SOURCE: Final = "Oregon OEM Fire Evacuation Areas"
EVACUATION_ZONES_LAYER_VARIABLE: Final = "EVACUATION_ZONES_LAYER_ID"
DEFAULT_EVACUATION_ZONES_LAYER_NAME: Final = "evacuation-zones"

# The producer token and its identity builder belong beside the other producers in identity.py; this adapter
# is fenced out of that file, so both live here for now. See the handover note in ingest/AGENTS.md.
EVACUATION_ZONES_PRODUCER: Final = "or-oem-evacuation-areas"

# Oregon's Fire_Evacuation_Areas_Public hosted view. Its definitionQuery keeps an automated-editor row visible
# only while the upstream integration keeps re-confirming it, so a query answers with the CURRENT statewide
# evacuation picture rather than a static zone catalogue. Coverage is Oregon only: no equivalent
# government-run aggregator was found for Washington, Idaho or western Montana, and the one vendor feed that
# reaches them carries no timestamp at all, so it cannot supply an honest observed_at. See ingest/AGENTS.md.
EVACUATION_ZONES_QUERY_URL: Final = (
    "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services"
    "/Fire_Evacuation_Areas_Public/FeatureServer/0/query"
)
EVACUATION_ZONES_OUT_FIELDS: Final = ",".join(
    (
        "GlobalID",
        "Fire_Name",
        "Fire_Evacuation_Level",
        "created_date",
        "last_edited_date",
        "County",
        "Evac_Area_Name",
        "StructuresWithin",
        "AddressesWithin",
        "PopulationWithin",
        "HazardType",
        "Editor_Name",
    )
)

EVACUATION_ZONES_BOUNDS: Final = UpstreamBounds(max_bytes=16 * 1024 * 1024, timeout_seconds=20.0)
# The service advertises maxRecordCount=1000 and supportsPagination, so a statewide emergency is paged
# rather than silently clipped to the first page.
MAX_RECORD_COUNT: Final = 1_000
MAX_PAGES: Final = 20
MAX_ATTEMPTS: Final = 3
RETRY_BASE_DELAYS_SECONDS: Final = (1.0, 2.0)
BUSY_MESSAGE_PATTERN: Final = re.compile(r"too many requests|busy|try again", re.IGNORECASE)

# JavaScript's Date range; beyond it an ArcGIS epoch field is not a real instant and is dropped rather than clamped.
MAX_JAVASCRIPT_EPOCH_MILLISECONDS: Final = 8.64e15
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

MIN_EVACUATION_LEVEL: Final = 1
MAX_EVACUATION_LEVEL: Final = 3

# Oregon's published three-level scale; a fixed public convention, derived from the numeric level, never invented.
EVACUATION_LEVEL_LABELS: Mapping[int, str] = MappingProxyType({1: "Be Ready", 2: "Be Set", 3: "Go Now"})
EVACUATION_LEVEL_SEVERITIES: Mapping[int, str] = MappingProxyType({1: "moderate", 2: "high", 3: "critical"})

POSITION_ORDINATE_COUNTS: Final = frozenset({2, 3})

UNEXPECTED_SHAPE_REASON: Final = "Oregon OEM evacuation API returned an unexpected feature collection shape"


def resolve_evacuation_zones_layer_name() -> str:
    """Read EVACUATION_ZONES_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(EVACUATION_ZONES_LAYER_VARIABLE, "").strip() or DEFAULT_EVACUATION_ZONES_LAYER_NAME


def evacuation_level(value: object) -> int | None:
    """Return Oregon's 1-3 evacuation level, rejecting anything off the published scale."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    level = float(value)
    if not math.isfinite(level) or not level.is_integer():
        return None
    whole = int(level)
    if not MIN_EVACUATION_LEVEL <= whole <= MAX_EVACUATION_LEVEL:
        return None
    return whole


def evacuation_level_label(level: int | None) -> str | None:
    """Name an evacuation level on Oregon's published scale, returning None when the level is unreported."""
    return None if level is None else EVACUATION_LEVEL_LABELS.get(level)


def evacuation_severity(level: int | None) -> str | None:
    """Grade an evacuation level into the severity vocabulary the other hazard layers already use."""
    return None if level is None else EVACUATION_LEVEL_SEVERITIES.get(level)


def epoch_milliseconds_to_datetime(value: object) -> datetime | None:
    """Read an ArcGIS epoch-millisecond field as a UTC instant, returning None when it is not a real one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    milliseconds = float(value)
    if not math.isfinite(milliseconds) or abs(milliseconds) > MAX_JAVASCRIPT_EPOCH_MILLISECONDS:
        return None
    try:
        return EPOCH + timedelta(milliseconds=math.trunc(milliseconds))
    except (OverflowError, OSError, ValueError):
        # JavaScript's Date range outruns Python's datetime range; this service never emits such a value.
        return None


def build_query_url(bbox: str, offset: int = 0, max_record_count: int = MAX_RECORD_COUNT) -> str:
    """Build the bounded ArcGIS GeoJSON query URL for one page of one bbox."""
    query = urlencode(
        {
            "where": "1=1",
            "outFields": EVACUATION_ZONES_OUT_FIELDS,
            "geometry": bbox,
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "resultOffset": str(offset),
            "resultRecordCount": str(max_record_count),
            "f": "geojson",
        }
    )
    return f"{EVACUATION_ZONES_QUERY_URL}?{query}"


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
        raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
    coordinates = geometry.get("coordinates")
    geometry_type = geometry.get("type")
    if geometry_type == "Polygon" and _is_ring_collection(coordinates, 2):
        return geometry
    if geometry_type == "MultiPolygon" and _is_ring_collection(coordinates, 3):
        return geometry
    raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)


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
class EvacuationZonePage:
    """One page of evacuation areas plus whether the upstream said more of them remain."""

    zones: list[dict[str, object]]
    exceeded_transfer_limit: bool


def _exceeded_transfer_limit(payload: Mapping[str, object]) -> bool:
    """True when ArcGIS reported it clipped this page; GeoJSON nests the flag, the JSON form keeps it at the top."""
    properties = payload.get("properties")
    if isinstance(properties, dict) and properties.get("exceededTransferLimit") is True:
        return True
    return payload.get("exceededTransferLimit") is True


def parse_evacuation_zone_collection(payload: object) -> EvacuationZonePage:
    """Parse an ArcGIS GeoJSON answer into evacuation-area records, rejecting its HTTP-200 error payload."""
    if not isinstance(payload, dict):
        raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
    error = payload.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        message = error.get("message") or ("; ".join(details) if isinstance(details, list) else None) or "unknown"
        raise UpstreamPayloadError(f"Oregon OEM evacuation API error: {message}")

    features = payload.get("features")
    if payload.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)

    zones: list[dict[str, object]] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
        global_id = properties.get("GlobalID")
        if not isinstance(global_id, str) or not global_id.strip():
            raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
        level = evacuation_level(properties.get("Fire_Evacuation_Level"))
        last_edited_at = epoch_milliseconds_to_datetime(properties.get("last_edited_date"))
        created_at = epoch_milliseconds_to_datetime(properties.get("created_date"))
        zones.append(
            {
                "globalId": global_id,
                "evacuationAreaName": _optional_text(properties, "Evac_Area_Name"),
                "fireName": _optional_text(properties, "Fire_Name"),
                "county": _optional_text(properties, "County"),
                "hazardType": _optional_text(properties, "HazardType"),
                "editorName": _optional_text(properties, "Editor_Name"),
                "evacuationLevel": level,
                "evacuationLevelLabel": evacuation_level_label(level),
                "severity": evacuation_severity(level),
                "structuresWithin": _optional_number(properties, "StructuresWithin"),
                "addressesWithin": _optional_number(properties, "AddressesWithin"),
                "populationWithin": _optional_number(properties, "PopulationWithin"),
                # The parse layer reports the upstream faithfully; `build_evacuation_zone_write` decides what is
                # stored. `createdAt` stays a datetime because it dates the identity; `last_edited_date` is
                # reported only as a string and dates nothing -- see `build_evacuation_zone_identity`.
                "createdAt": created_at,
                "createdDate": None if created_at is None else format_javascript_timestamp(created_at),
                "lastEditedDate": None if last_edited_at is None else format_javascript_timestamp(last_edited_at),
                "geometry": _validate_geometry(feature.get("geometry")),
            }
        )
    return EvacuationZonePage(zones=zones, exceeded_transfer_limit=_exceeded_transfer_limit(payload))


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


async def fetch_evacuation_zone_page(
    client: httpx.AsyncClient,
    bbox: str,
    offset: int = 0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> EvacuationZonePage:
    """Fetch one bounded page of evacuation areas, retrying only a busy or transient upstream."""
    url = build_query_url(bbox, offset)
    for attempt in range(MAX_ATTEMPTS):
        try:
            return parse_evacuation_zone_collection(await fetch_bounded_json(client, url, EVACUATION_ZONES_BOUNDS))
        except (UpstreamHttpError, UpstreamPayloadError) as error:
            if attempt == MAX_ATTEMPTS - 1 or not is_retryable_failure(error):
                raise
            logger.info("evacuation_zones_upstream_retry", attempt=attempt + 1, offset=offset, error=str(error))
            await sleep(jittered_retry_delay_seconds(attempt))
    raise UpstreamPayloadError("evacuation zone retry loop ended without a response")  # pragma: no cover - unreachable.


async def fetch_evacuation_zones(
    client: httpx.AsyncClient,
    bbox: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[list[dict[str, object]], bool]:
    """Page bounded evacuation areas until the upstream stops clipping, reporting whether more were left behind."""
    ceiling = resolve_max_source_records()
    zones: list[dict[str, object]] = []
    offset = 0
    for _ in range(MAX_PAGES):
        page = await fetch_evacuation_zone_page(client, bbox, offset, sleep)
        zones.extend(page.zones)
        if not page.zones or not page.exceeded_transfer_limit:
            return zones, False
        offset += len(page.zones)
        if len(zones) >= ceiling:
            return zones, True
    return zones, True


def build_evacuation_zone_identity(zone: Mapping[str, object]) -> FeatureIdentity:
    """Build the evacuation-area identity: the upstream GlobalID, dated by the area's own creation stamp.

    `created_date` is the only honest observation time this upstream publishes. `last_edited_date` is
    deliberately kept out of `observed_at`: Oregon's sync re-stamps an unchanged area's edit clock every
    few minutes, so a live sample finds most of the layer edited within the hour and the field is
    indistinguishable from `now()`. Dating the identity by it would open a first geometry version whose
    `version_valid_from` sits minutes before the cron ran, for areas that have existed for months -- the
    exact "row dated from a clock" failure the warehouse was audited clean of. `created_date` instead
    spans the real age range of the layer, and an area that publishes none is dated unknown rather than
    guessed. See ingest/AGENTS.md.
    """
    global_id = zone.get("globalId")
    if not isinstance(global_id, str) or not global_id.strip():
        raise MissingNativeKeyError("globalId is required and must not be blank")
    created_at = zone.get("createdAt")
    if created_at is not None and not isinstance(created_at, datetime):
        raise ValueError("createdAt must be an upstream instant when present")
    return FeatureIdentity(
        producer=EVACUATION_ZONES_PRODUCER,
        producer_local_id=global_id,
        observed_at=created_at,
    )


def build_evacuation_zone_write(zone: Mapping[str, object], layer_name: str) -> FeatureWrite | None:
    """Build one evacuation area's write, returning None when the upstream supplied no stable GlobalID."""
    try:
        identity = build_evacuation_zone_identity(zone)
    except (MissingNativeKeyError, ValueError):
        return None
    properties: dict[str, object] = {}
    if identity.observed_at is not None:
        # The read model dates every row from COALESCE(observedAt, updatedAt, polygonDateTime);
        # `createdDate` is in none of them, so without this key an area is undatable and the
        # `evacuation-zones` layer reports "Not yet observed" on every date forever. The value is
        # `createdDate` verbatim -- already the JS-formatted instant -- so the two keys can never
        # disagree, and an area the upstream dated not at all stays honestly undated rather than
        # acquiring a clock reading.
        properties["observedAt"] = zone.get("createdDate")
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            **properties,
            "globalId": zone.get("globalId"),
            "evacuationAreaName": zone.get("evacuationAreaName"),
            "fireName": zone.get("fireName"),
            "county": zone.get("county"),
            "hazardType": zone.get("hazardType"),
            "evacuationLevel": zone.get("evacuationLevel"),
            "evacuationLevelLabel": zone.get("evacuationLevelLabel"),
            "severity": zone.get("severity"),
            "structuresWithin": zone.get("structuresWithin"),
            "addressesWithin": zone.get("addressesWithin"),
            "populationWithin": zone.get("populationWithin"),
            "editorName": zone.get("editorName"),
            "createdDate": zone.get("createdDate"),
            # `lastEditedDate` is deliberately NOT stored, and deliberately does not date the row either. The
            # upstream sync re-stamps an unchanged area's edit clock every few minutes, so storing it would
            # make every poll a "changed" refresh for the whole layer -- flooding records_written and the
            # realtime channel with rows nothing actually happened to -- and dating by it would be a clock
            # reading laundered through an upstream field. The freshness signal a consumer needs to age out a
            # vanished area is geo.geometry.last_confirmed_at. See ingest/AGENTS.md.
            "source": EVACUATION_ZONES_PROPERTY_SOURCE,
            "geometry": zone.get("geometry"),
        },
        channel=EVACUATION_ZONES_CHANNEL,
    )


async def run_evacuation_zones_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> IngestionJobResult:
    """Fetch bounded Oregon OEM evacuation areas and refresh them in place as their levels and shapes advance."""
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(EVACUATION_ZONES_SOURCE, UNCONFIGURED_BBOX_REASON)

    if client is None:
        async with upstream_client(EVACUATION_ZONES_BOUNDS) as owned_client:
            zones, more_remaining = await fetch_evacuation_zones(owned_client, area)
    else:
        zones, more_remaining = await fetch_evacuation_zones(client, area)

    # A bitten cap drops the OLDEST areas, never an arrival slice -- the same policy
    # `source.select_writes` documents and applies for every source that goes through it. Taking
    # `zones[:cap]` dropped whichever page ArcGIS happened to return last, so which areas survived
    # a bitten cap depended on upstream paging order rather than on recency. Undated areas sort
    # last, matching `source._truncation_rank`, and `sorted` is stable so arrival breaks ties.
    def _newest_first(zone: Mapping[str, object]) -> tuple[int, float]:
        created_at = zone.get("createdAt")
        if not isinstance(created_at, datetime):
            return (0, 0.0)
        return (1, created_at.timestamp())

    selected = sorted(zones, key=_newest_first, reverse=True)[: resolve_max_source_records()]
    layer_name = resolve_evacuation_zones_layer_name()
    writes = [
        write for write in (build_evacuation_zone_write(zone, layer_name) for zone in selected) if write is not None
    ]

    return IngestionJobResult(
        source=EVACUATION_ZONES_SOURCE,
        status="ingested",
        records_seen=len(zones),
        records_written=await write_features(writes),
        truncated=more_remaining or len(zones) > len(selected),
        details={"rejected": len(selected) - len(writes)},
    )
