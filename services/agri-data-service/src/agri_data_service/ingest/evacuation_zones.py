"""Oregon OEM evacuation-area ingestion: the ArcGIS FeatureServer adapter and its retrying, bounded, paged job."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.arcgis import (
    ArcGisEnvelopeQuery,
    optional_number,
    optional_text,
    page_offset_walk,
    parse_feature_collection,
    require_feature_properties,
    require_polygon_geometry,
)
from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamPayloadError,
    fetch_bounded_json,
    upstream_client,
)
from agri_data_service.ingest.identity import (
    FeatureIdentity,
    MissingNativeKeyError,
    format_javascript_timestamp,
)
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.source import HistoryCapability
from agri_data_service.ingest.upstream_retry import UpstreamRetryPolicy, retry_upstream
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

EVACUATION_ZONES_SOURCE: Final = "evacuation-zones"
EVACUATION_ZONES_PROPERTY_SOURCE: Final = "Oregon OEM Fire Evacuation Areas"

EVACUATION_ZONES_LAYER: Final = LayerBinding(
    variable="EVACUATION_ZONES_LAYER_ID",
    default="evacuation-zones",
    channel="layer:evacuation-zones",
)
EVACUATION_ZONES_CHANNEL: Final = EVACUATION_ZONES_LAYER.channel
EVACUATION_ZONES_LAYER_VARIABLE: Final = EVACUATION_ZONES_LAYER.variable
DEFAULT_EVACUATION_ZONES_LAYER_NAME: Final = EVACUATION_ZONES_LAYER.default

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

# Widened 2026-08-10 onto the shared ladder. This module carried the pre-incident 3-attempt fixed
# `(1.0, 2.0)` tuple -- the exact shape that lost every hourly `plantgeo-cron-fire-perimeters` run
# through a sustained ArcGIS throttle. See ingest/AGENTS.md "upstream_retry.py".
EVACUATION_ZONES_RETRY: Final = UpstreamRetryPolicy(
    event="evacuation_zones_upstream_retry",
    exhausted_message="evacuation zone retry loop ended without a response",
)
MAX_ATTEMPTS: Final = EVACUATION_ZONES_RETRY.ladder.max_attempts
RETRY_WALL_CLOCK_CEILING_SECONDS: Final = EVACUATION_ZONES_RETRY.ladder.wall_clock_ceiling_seconds

# Oregon publishes this layer as a current-state hosted view and no archive of past evacuation
# levels; see ingest/AGENTS.md "history declarations, wave 2026-08-10" for the full argument.
EVACUATION_ZONES_NO_HISTORY_REASON: Final = (
    "Oregon OEM publishes Fire_Evacuation_Areas_Public as a current-state hosted view: its "
    "definition query drops an area once the upstream integration stops re-confirming it, and no "
    "attribute records when an area's level was raised, lowered or retired, so a past evacuation "
    "level cannot be reconstructed from it. No archive service of historical Oregon evacuation "
    "levels is published (checked 2026-08-10), and no equivalent government-run aggregator exists "
    "for Washington, Idaho or western Montana. The only history this layer has is what "
    "geo.geometry has accumulated since ingestion began."
)
EVACUATION_ZONES_HISTORY_CAPABILITY: Final = HistoryCapability(
    supported=False,
    reason=EVACUATION_ZONES_NO_HISTORY_REASON,
)

# JavaScript's Date range; beyond it an ArcGIS epoch field is not a real instant and is dropped rather than clamped.
MAX_JAVASCRIPT_EPOCH_MILLISECONDS: Final = 8.64e15
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

MIN_EVACUATION_LEVEL: Final = 1
MAX_EVACUATION_LEVEL: Final = 3

# Oregon's published three-level scale; a fixed public convention, derived from the numeric level, never invented.
EVACUATION_LEVEL_LABELS: Mapping[int, str] = MappingProxyType({1: "Be Ready", 2: "Be Set", 3: "Go Now"})
EVACUATION_LEVEL_SEVERITIES: Mapping[int, str] = MappingProxyType({1: "moderate", 2: "high", 3: "critical"})

EVACUATION_ZONES_ERROR_PREFIX: Final = "Oregon OEM evacuation API error"
UNEXPECTED_SHAPE_REASON: Final = "Oregon OEM evacuation API returned an unexpected feature collection shape"

# `order_by_fields` unset for the same reason WFIGS leaves it unset; see ingest/AGENTS.md "arcgis.py".
EVACUATION_ZONES_PAGE_QUERY: Final = ArcGisEnvelopeQuery(
    endpoint=EVACUATION_ZONES_QUERY_URL,
    out_fields=EVACUATION_ZONES_OUT_FIELDS,
)


def resolve_evacuation_zones_layer_name() -> str:
    """Read EVACUATION_ZONES_LAYER_ID at call time so a cron environment change needs no restart."""
    return EVACUATION_ZONES_LAYER.resolve()


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
    return EVACUATION_ZONES_PAGE_QUERY.page_url(bbox=bbox, offset=offset, max_record_count=max_record_count)


@dataclass(frozen=True, slots=True)
class EvacuationZonePage:
    """One page of evacuation areas plus whether the upstream said more of them remain."""

    zones: list[dict[str, object]]
    exceeded_transfer_limit: bool


def parse_evacuation_zone_collection(payload: object) -> EvacuationZonePage:
    """Parse an ArcGIS GeoJSON answer into evacuation-area records, rejecting its HTTP-200 error payload."""
    collection = parse_feature_collection(
        payload,
        error_prefix=EVACUATION_ZONES_ERROR_PREFIX,
        unexpected_shape_reason=UNEXPECTED_SHAPE_REASON,
    )

    zones: list[dict[str, object]] = []
    for feature in collection.features:
        properties = require_feature_properties(feature, unexpected_shape_reason=UNEXPECTED_SHAPE_REASON)
        global_id = properties.get("GlobalID")
        if not isinstance(global_id, str) or not global_id.strip():
            raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
        level = evacuation_level(properties.get("Fire_Evacuation_Level"))
        last_edited_at = epoch_milliseconds_to_datetime(properties.get("last_edited_date"))
        created_at = epoch_milliseconds_to_datetime(properties.get("created_date"))
        zones.append(
            {
                "globalId": global_id,
                "evacuationAreaName": optional_text(properties, "Evac_Area_Name"),
                "fireName": optional_text(properties, "Fire_Name"),
                "county": optional_text(properties, "County"),
                "hazardType": optional_text(properties, "HazardType"),
                "editorName": optional_text(properties, "Editor_Name"),
                "evacuationLevel": level,
                "evacuationLevelLabel": evacuation_level_label(level),
                "severity": evacuation_severity(level),
                "structuresWithin": optional_number(properties, "StructuresWithin"),
                "addressesWithin": optional_number(properties, "AddressesWithin"),
                "populationWithin": optional_number(properties, "PopulationWithin"),
                # The parse layer reports the upstream faithfully; `build_evacuation_zone_write` decides what is
                # stored. `createdAt` stays a datetime because it dates the identity; `last_edited_date` is
                # reported only as a string and dates nothing -- see `build_evacuation_zone_identity`.
                "createdAt": created_at,
                "createdDate": None if created_at is None else format_javascript_timestamp(created_at),
                "lastEditedDate": None if last_edited_at is None else format_javascript_timestamp(last_edited_at),
                "geometry": require_polygon_geometry(
                    feature.get("geometry"), unexpected_shape_reason=UNEXPECTED_SHAPE_REASON
                ),
            }
        )
    return EvacuationZonePage(zones=zones, exceeded_transfer_limit=collection.exceeded_transfer_limit)


async def fetch_evacuation_zone_page(
    client: httpx.AsyncClient,
    bbox: str,
    offset: int = 0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> EvacuationZonePage:
    """Fetch one bounded page of evacuation areas, retrying only a busy or transient upstream."""
    url = build_query_url(bbox, offset)

    async def attempt_once() -> EvacuationZonePage:
        return parse_evacuation_zone_collection(await fetch_bounded_json(client, url, EVACUATION_ZONES_BOUNDS))

    return await retry_upstream(
        attempt_once,
        EVACUATION_ZONES_RETRY,
        context={"offset": offset},
        sleep=sleep,
        monotonic=monotonic,
    )


async def fetch_evacuation_zones(
    client: httpx.AsyncClient,
    bbox: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, object]], bool]:
    """Page bounded evacuation areas until the upstream stops clipping, reporting whether more were left behind."""

    async def fetch_page(offset: int) -> tuple[list[dict[str, object]], bool]:
        page = await fetch_evacuation_zone_page(client, bbox, offset, sleep, monotonic)
        return page.zones, page.exceeded_transfer_limit

    return await page_offset_walk(fetch_page, max_pages=MAX_PAGES, record_ceiling=resolve_max_source_records())


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
