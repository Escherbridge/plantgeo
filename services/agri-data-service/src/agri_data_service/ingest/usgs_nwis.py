"""USGS NWIS streamflow ingestion: the tiled instantaneous-values adapter and its bounded gauge job."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import UpstreamBounds, UpstreamPayloadError, fetch_bounded_json, upstream_client
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_streamflow_gauge_identity,
    format_javascript_fixed,
    format_javascript_timestamp,
)
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    format_javascript_number,
    javascript_parse_float,
    parse_bbox,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

USGS_STREAMFLOW_SOURCE: Final = "usgs-streamflow"
USGS_CHANNEL: Final = "layer:water-gauges"
USGS_PROPERTY_SOURCE: Final = "USGS NWIS"
WATER_GAUGES_LAYER_VARIABLE: Final = "WATER_GAUGES_LAYER_ID"
DEFAULT_WATER_GAUGES_LAYER_NAME: Final = "water-gauges"

STREAMFLOW_QUERY_TEMPLATE: Final = (
    "https://waterservices.usgs.gov/nwis/iv/?format=json&bBox={tile}&parameterCd=00060&siteType=ST&siteStatus=active"
)

NWIS_BOUNDS: Final = UpstreamBounds(max_bytes=8 * 1024 * 1024, timeout_seconds=10.0)

# NWIS rejects bBox requests wider than 25 square degrees (longitude span * latitude span).
MAX_TILE_DEGREES: Final = 4.0
MAX_CONCURRENT_TILE_REQUESTS: Final = 4
NWIS_COORDINATE_DIGITS: Final = 6

ABOVE_NORMAL_PERCENTILE: Final = 75
NORMAL_PERCENTILE: Final = 25
BELOW_NORMAL_PERCENTILE: Final = 10
LOW_PERCENTILE: Final = 5
DECLINING_QUALIFIER_CODE: Final = "e"


def resolve_water_gauges_layer_name() -> str:
    """Read WATER_GAUGES_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(WATER_GAUGES_LAYER_VARIABLE, "").strip() or DEFAULT_WATER_GAUGES_LAYER_NAME


def format_tile_ordinate(value: float) -> str:
    """Round a tile ordinate to six digits so the NWIS seven-digit limit is never exceeded."""
    return format_javascript_number(float(format_javascript_fixed(value, NWIS_COORDINATE_DIGITS)))


def tile_bbox(bbox: str, max_tile_degrees: float = MAX_TILE_DEGREES) -> list[str]:
    """Split a bbox into a grid of sub-bboxes no larger than `max_tile_degrees` square, covering the full extent."""
    west, south, east, north = parse_bbox(bbox)
    tiles: list[str] = []
    tile_south = south
    while tile_south < north:
        tile_north = min(tile_south + max_tile_degrees, north)
        tile_west = west
        while tile_west < east:
            tile_east = min(tile_west + max_tile_degrees, east)
            tiles.append(
                ",".join(format_tile_ordinate(ordinate) for ordinate in (tile_west, tile_south, tile_east, tile_north))
            )
            tile_west += max_tile_degrees
        tile_south += max_tile_degrees
    return tiles


def classify_condition(percentile: float | None) -> str:
    """Classify a streamflow condition from its percentile; NWIS instantaneous values never supply one."""
    if percentile is None:
        return "unknown"
    if percentile > ABOVE_NORMAL_PERCENTILE:
        return "above_normal"
    if percentile >= NORMAL_PERCENTILE:
        return "normal"
    if percentile >= BELOW_NORMAL_PERCENTILE:
        return "below_normal"
    if percentile >= LOW_PERCENTILE:
        return "low"
    return "critically_low"


def infer_trend(qualifiers: object) -> str:
    """Infer a trend from NWIS qualifier codes; without a historical comparison the default is stable."""
    if not isinstance(qualifiers, list):
        return "stable"
    codes = {str(qualifier.get("qualifierCode", "")).lower() for qualifier in qualifiers if isinstance(qualifier, dict)}
    return "declining" if DECLINING_QUALIFIER_CODE in codes else "stable"


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    """Return an upstream object field, rejecting a series whose required structure is absent."""
    if not isinstance(value, dict):
        raise UpstreamPayloadError(f"NWIS time series is missing {field_name}")
    return value


def parse_gauge(series: Mapping[str, object], now: datetime | None = None) -> dict[str, object]:
    """Parse one NWIS time series into the gauge record the warehouse stores."""
    source_info = _require_mapping(series.get("sourceInfo"), "sourceInfo")
    geo_location = _require_mapping(source_info.get("geoLocation"), "geoLocation")
    geographic_location = _require_mapping(geo_location.get("geogLocation"), "geogLocation")

    site_codes = source_info.get("siteCode")
    first_site_code = site_codes[0] if isinstance(site_codes, list) and site_codes else None
    site_number = str(first_site_code.get("value", "")) if isinstance(first_site_code, dict) else ""

    values = series.get("values")
    first_values = values[0] if isinstance(values, list) and values else None
    readings = first_values.get("value") if isinstance(first_values, dict) else None
    latest = readings[-1] if isinstance(readings, list) and readings else None

    flow_cfs: float | None = None
    updated_at: str | None = None
    if isinstance(latest, dict):
        reading = latest.get("value")
        flow_cfs = javascript_parse_float(reading) if isinstance(reading, str) else None
        reading_time = latest.get("dateTime")
        updated_at = reading_time if isinstance(reading_time, str) and reading_time else None

    return {
        "siteNo": site_number,
        "siteName": str(source_info.get("siteName", "") or ""),
        "lat": geographic_location.get("latitude"),
        "lon": geographic_location.get("longitude"),
        "flowCfs": flow_cfs,
        "percentile": None,
        "condition": classify_condition(None),
        "trend": infer_trend(first_values.get("qualifier") if isinstance(first_values, dict) else None),
        # Ported unchanged from `usgs-water.ts:183`: a silent gauge keeps a wall-clock reading time, which
        # mints a fresh identity every run. See ingest/AGENTS.md "usgs_nwis.py".
        "updatedAt": updated_at
        if updated_at is not None
        else format_javascript_timestamp(now if now is not None else datetime.now(UTC)),
        "updatedAtIsWallClock": updated_at is None,
    }


async def _fetch_tile(client: httpx.AsyncClient, tile: str, gate: asyncio.Semaphore) -> list[object]:
    """Fetch one tile's NWIS time series; a tile failure propagates rather than yielding partial coverage."""
    async with gate:
        payload = await fetch_bounded_json(
            client,
            STREAMFLOW_QUERY_TEMPLATE.format(tile=tile),
            NWIS_BOUNDS,
            {"Accept": "application/json"},
        )
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    if not isinstance(value, dict):
        return []
    time_series = value.get("timeSeries")
    return time_series if isinstance(time_series, list) else []


async def fetch_streamflow_gauges(
    client: httpx.AsyncClient,
    bbox: str,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """Fetch NWIS time series across every tile of a bbox, deduped by site number, as gauge records."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_TILE_REQUESTS)
    tile_results = await asyncio.gather(*(_fetch_tile(client, tile, gate) for tile in tile_bbox(bbox)))

    gauges: list[dict[str, object]] = []
    seen_site_numbers: set[str] = set()
    for tile_series in tile_results:
        for series in tile_series:
            if not isinstance(series, dict):
                continue
            gauge = parse_gauge(series, now)
            site_number = str(gauge["siteNo"])
            if site_number in seen_site_numbers:
                continue
            seen_site_numbers.add(site_number)
            gauges.append(gauge)
    return gauges


def build_gauge_write(gauge: Mapping[str, object], layer_name: str) -> FeatureWrite | None:
    """Build one gauge's write, returning None when the upstream supplied no site number to key it by."""
    try:
        identity = build_streamflow_gauge_identity(gauge)
    except (MissingNativeKeyError, ValueError):
        return None
    properties = {key: value for key, value in gauge.items() if key != "updatedAtIsWallClock"}
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            **properties,
            "source": USGS_PROPERTY_SOURCE,
            "geometry": {"type": "Point", "coordinates": [gauge.get("lon"), gauge.get("lat")]},
        },
        channel=USGS_CHANNEL,
    )


async def run_water_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Fetch bounded USGS gauges and write them as timestamped source observations."""
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(USGS_STREAMFLOW_SOURCE, UNCONFIGURED_BBOX_REASON)

    if client is None:
        async with upstream_client(NWIS_BOUNDS) as owned_client:
            gauges = await fetch_streamflow_gauges(owned_client, area, now)
    else:
        gauges = await fetch_streamflow_gauges(client, area, now)

    selected = gauges[: resolve_max_source_records()]
    layer_name = resolve_water_gauges_layer_name()
    writes = [write for write in (build_gauge_write(gauge, layer_name) for gauge in selected) if write is not None]
    wall_clock_gauges = sum(1 for gauge in selected if gauge.get("updatedAtIsWallClock"))
    if wall_clock_gauges:
        logger.info("streamflow_wall_clock_identities", gauges=wall_clock_gauges, selected=len(selected))

    return IngestionJobResult(
        source=USGS_STREAMFLOW_SOURCE,
        status="ingested",
        records_seen=len(gauges),
        records_written=await write_features(writes),
        truncated=len(gauges) > len(selected),
        details={
            "rejected": len(selected) - len(writes),
            "wall_clock_identities": wall_clock_gauges,
        },
    )
