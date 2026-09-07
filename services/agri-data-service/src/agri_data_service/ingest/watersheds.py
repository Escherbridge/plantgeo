"""USGS WBD HUC12 watershed source adapter: the paged NHDPlus_HR fetch and the snapshot identity it keys on.

THE POSTGRES HALF OF THIS MODULE WAS DELETED ON 2026-09-06. `run_watersheds_ingestion_job`,
`build_watershed_write` and `WATERSHEDS_SOURCE` wrote `geo.features` for the `postgres-watersheds`
lane; that lane, the `ingest-watersheds` verb behind it, and the `pipeline/lanes/watersheds.py`
exporter that read the rows back out are all gone. What is left is the SOURCE half --
`fetch_watersheds`, `fetch_watershed_object_ids`, `build_watershed_identity`, `parse_load_date` and
`WBDHU12_BOUNDS` -- which `pipeline/direct/watersheds/` fetches through to write Parquet without ever
staging a row in PostgreSQL, and which `pipeline/validation/watersheds.py` reuses for its vintage
reconciliation. The `LayerBinding` block below survives the same way `ingest/firms.py`'s and
`ingest/vegetation.py`'s did after their own jobs were deleted: it is the layer's env-var contract,
pinned by `tests/test_ingest_layer_binding.py`, not by any writer.

Boundaries are a SNAPSHOT, not a series: one row per HUC12, refreshed in place. That is why the
identity's `producer_local_id` is the bare HUC12 code with no timestamp in it -- a re-run must land on
the same row rather than minting a new version of an unchanged polygon, which is exactly what a
timestamped local id does (see `build_streamflow_gauge_identity`, where per-reading versions ARE the
point).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import structlog

from agri_data_service.ingest.http import UpstreamBounds, UpstreamPayloadError, fetch_bounded_json
from agri_data_service.ingest.identity import FeatureIdentity, MissingNativeKeyError
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.ingest.policy import parse_bbox

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

logger = structlog.get_logger()

WATERSHEDS_PROPERTY_SOURCE: Final = "USGS NHDPlus HR WBDHU12"

WATERSHEDS_LAYER: Final = LayerBinding(
    variable="WATERSHEDS_LAYER_ID",
    default="watersheds",
    channel="layer:watersheds",
)
WATERSHEDS_CHANNEL: Final = WATERSHEDS_LAYER.channel
WATERSHEDS_LAYER_VARIABLE: Final = WATERSHEDS_LAYER.variable
DEFAULT_WATERSHEDS_LAYER_NAME: Final = WATERSHEDS_LAYER.default

# The producer token and its identity builder belong beside the other producers in identity.py; this
# adapter is fenced out of that file, so both live here -- the same handover the evacuation-zones
# adapter records in ingest/AGENTS.md.
WATERSHEDS_PRODUCER: Final = "usgs-wbd-huc12"

# Layer 12 of NHDPlus_HR is WBDHU12 (esriGeometryPolygon). NOT layer 2, which is NHDPoint and returns
# an unlabelled point cloud carrying no HUC12 attribute at all -- the exact mistake the TypeScript
# sibling documents at src/lib/server/services/hydrosheds.ts. Verified against the service catalog.
WBDHU12_LAYER_ID: Final = 12
WBDHU12_QUERY_ENDPOINT: Final = (
    f"https://hydro.nationalmap.gov/arcgis/rest/services/NHDPlus_HR/MapServer/{WBDHU12_LAYER_ID}/query"
)

# Basins per geometry request, addressed by explicit OBJECTID rather than by offset.
#
# Offset paging was tried first and does not work here. `resultOffset` needs a stable sort to be
# meaningful, and asking this layer to sort while returning geometry answers HTTP 500 over the PNW
# envelope -- the same query with `returnGeometry=false` sorts fine, so what fails is ordering
# ~9,400 detailed polygons server-side, not the ordering itself. Dropping the sort instead would
# leave offset paging over an unstable order, which silently skips and repeats basins.
#
# Naming the ids makes each batch deterministic, complete and non-overlapping regardless of what
# order the service feels like using. 200 rather than the service's 2000 maxRecordCount because
# HUC12 geometry is heavy: 500 basins measured 12.4 MB, so 200 is roughly 5 MB per request.
WBDHU12_BATCH_SIZE: Final = 200

# Generous relative to the live proxy's 15s: this is a batch job paging through thousands of polygons,
# and NHDPlus_HR answers a large envelope slowly. The byte ceiling is per PAGE, and a 500-polygon page
# of HUC12 geometry at 6-digit precision measures a few MB.
WBDHU12_BOUNDS: Final = UpstreamBounds(max_bytes=32 * 1024 * 1024, timeout_seconds=120.0)

# Rounds coordinates to ~0.1 m -- far finer than the 1:24,000 source was digitized at -- and cuts the
# payload by roughly 40%. Same value, and the same reasoning, as the TypeScript proxy uses.
WBDHU12_GEOMETRY_PRECISION: Final = 6

MILLISECONDS_PER_SECOND: Final = 1000


def resolve_watersheds_layer_name() -> str:
    """Read WATERSHEDS_LAYER_ID at call time so a cron environment change needs no restart."""
    return WATERSHEDS_LAYER.resolve()


def parse_load_date(value: object) -> datetime | None:
    """Parse WBD's `loaddate`, which arrives as epoch MILLISECONDS, into a UTC instant.

    Measured 2026-08-07: `loaddate` is 1358492970000 for the Sandy River HUC12s -- 2013-01-18, the day
    USGS loaded that boundary. It is the only date the layer carries, and using it rather than the run
    clock is what keeps a re-ingest from re-dating an unchanged 2013 polygon to today.

    Guarded rather than trusted: a null or non-numeric value yields None, and the write is then honestly
    undated instead of acquiring a clock reading.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / MILLISECONDS_PER_SECOND, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def build_watershed_identity(watershed: Mapping[str, object]) -> FeatureIdentity:
    """Build the HUC12 identity: the basin code alone, with no timestamp folded into it.

    A HUC12 code is the boundary's own permanent national key, so it serves as both the version key and
    the entity key. That collapse is deliberate and is what makes a re-run refresh one row per basin.
    """
    huc12 = watershed.get("huc12")
    if not isinstance(huc12, str) or not huc12.strip():
        raise MissingNativeKeyError("WBDHU12 feature carries no huc12 code")
    code = huc12.strip()
    return FeatureIdentity(
        producer=WATERSHEDS_PRODUCER,
        producer_local_id=code,
        observed_at=parse_load_date(watershed.get("loaddate")),
        entity_local_id=code,
    )


def _envelope(bbox: str) -> str:
    """The bbox as the esriGeometryEnvelope JSON the query parameter expects."""
    west, south, east, north = parse_bbox(bbox)
    return f'{{"xmin":{west},"ymin":{south},"xmax":{east},"ymax":{north},"spatialReference":{{"wkid":4326}}}}'


def _object_ids_query(bbox: str) -> str:
    """Build the id-only query: which basins intersect the extent, and nothing else about them."""
    return urlencode(
        {
            "geometry": _envelope(bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "f": "json",
            "returnIdsOnly": "true",
        }
    )


def _batch_query(object_ids: list[int]) -> str:
    """Build one geometry query for an explicit batch of OBJECTIDs.

    No geometry filter and no ordering: the ids already name exactly which basins are wanted, so
    re-filtering by envelope would only ask the service to redo the work the id list records, and
    ordering is what makes this layer answer 500 when geometry is in play.
    """
    return urlencode(
        {
            "objectIds": ",".join(str(object_id) for object_id in object_ids),
            "outFields": "huc12,name,areasqkm,tohuc,states,hutype,loaddate",
            "returnGeometry": "true",
            "f": "geojson",
            "geometryPrecision": str(WBDHU12_GEOMETRY_PRECISION),
        }
    )


async def _query_json(client: httpx.AsyncClient, query: str) -> dict[str, object]:
    """Run one NHDPlus_HR query, refusing the faults ArcGIS hides behind HTTP 200."""
    payload = await fetch_bounded_json(
        client,
        f"{WBDHU12_QUERY_ENDPOINT}?{query}",
        WBDHU12_BOUNDS,
        {"Accept": "application/json"},
    )
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("NHDPlus_HR returned a non-object body")
    # ArcGIS answers some faults with HTTP 200 and an `error` object. Without this the job reports a
    # clean zero-feature success for an outage, which is the one failure mode a boundary layer must
    # never have: an empty basin map is indistinguishable from a region with no basins.
    if "error" in payload:
        raise UpstreamPayloadError(f"NHDPlus_HR returned an error object: {payload.get('error')}")
    return payload


async def fetch_watershed_object_ids(client: httpx.AsyncClient, bbox: str) -> list[int]:
    """Every WBDHU12 OBJECTID intersecting the extent, in one id-only request.

    Cheap precisely because it carries no geometry: measured 2026-08-07, the PNW envelope answers
    9,396 ids in a few seconds, where the same envelope with geometry is 12.4 MB per 500 basins.
    """
    payload = await _query_json(client, _object_ids_query(bbox))
    object_ids = payload.get("objectIds")
    if not isinstance(object_ids, list):
        raise UpstreamPayloadError("NHDPlus_HR returned no objectIds array")
    return [object_id for object_id in object_ids if isinstance(object_id, int)]


async def fetch_watersheds(client: httpx.AsyncClient, bbox: str) -> list[dict[str, object]]:
    """Every HUC12 polygon intersecting a bbox, fetched in explicit id batches.

    Sequential rather than concurrent: this runs once per WBD republication, and a boundary set that
    arrives a few minutes later is worth far more than one that trips the connection exhaustion the
    archive walks measured (169 of 298 windows lost to ConnectError when fired back to back).
    """
    object_ids = await fetch_watershed_object_ids(client, bbox)
    logger.info("wbdhu12_object_ids", count=len(object_ids))

    features: list[dict[str, object]] = []
    for start in range(0, len(object_ids), WBDHU12_BATCH_SIZE):
        batch = object_ids[start : start + WBDHU12_BATCH_SIZE]
        payload = await _query_json(client, _batch_query(batch))
        batch_features = payload.get("features")
        if not isinstance(batch_features, list):
            raise UpstreamPayloadError("NHDPlus_HR returned no feature array")
        features.extend(feature for feature in batch_features if isinstance(feature, dict))

    # The id list is the authority on how many basins exist, so a short result is a real shortfall
    # rather than the ordinary end-of-pages case an offset walk cannot tell apart.
    if len(features) < len(object_ids):
        logger.warning("wbdhu12_batch_shortfall", expected=len(object_ids), received=len(features))
    return features
