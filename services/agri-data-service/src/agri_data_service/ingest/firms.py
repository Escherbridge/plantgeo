"""NASA FIRMS active-fire ingestion: the VIIRS constellation CSV adapter and its bounded, fresh-only job."""

from __future__ import annotations

import asyncio
import os
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import UpstreamBounds, fetch_bounded_text, upstream_client
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_firms_identity,
    format_javascript_timestamp,
)
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    is_fresh_observation,
    javascript_parse_float,
    resolve_bounded_bbox,
    resolve_max_source_records,
)
from agri_data_service.ingest.results import IngestionJobResult, skipped_result
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    import httpx

    from agri_data_service.ingest.writer import FeatureWriter

logger = structlog.get_logger()

FIRMS_SOURCE: Final = "nasa-firms"
FIRMS_CHANNEL: Final = "layer:fire-detections"
FIRMS_PROPERTY_SOURCE: Final = "NASA FIRMS"
FIRMS_LAYER_VARIABLE: Final = "FIRMS_LAYER_ID"
DEFAULT_FIRMS_LAYER_NAME: Final = "fire-detections"

FIRMS_API_KEY_VARIABLE: Final = "NASA_FIRMS_KEY"
FIRMS_DAY_RANGE_VARIABLE: Final = "FIRMS_DAY_RANGE"
FIRMS_AREA_CSV_TEMPLATE: Final = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/{source}/{area}/{day_range}"
)

# The full VIIRS NRT constellation, matching `nasa-firms.ts` FIRMS_VIIRS_SOURCES. Querying a single
# satellite silently zeroes the layer whenever that satellite stops publishing -- see ingest/AGENTS.md
# "firms.py" -- so the job fans out across all three and merges rather than picking one.
FIRMS_VIIRS_SOURCES: Final[tuple[str, ...]] = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")

FIRMS_BOUNDS: Final = UpstreamBounds(max_bytes=16 * 1024 * 1024, timeout_seconds=15.0)

DEFAULT_FIRMS_DAY_RANGE: Final = 2
MIN_FIRMS_DAY_RANGE: Final = 1
MAX_FIRMS_DAY_RANGE: Final = 10
MIN_CSV_LINES: Final = 2
MIN_CSV_COLUMNS: Final = 4
DEFAULT_CONFIDENCE: Final = "nominal"

_STRICT_NONNEGATIVE_INTEGER: Final = re.compile(r"^\d+$")


def resolve_firms_layer_name() -> str:
    """Read FIRMS_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(FIRMS_LAYER_VARIABLE, "").strip() or DEFAULT_FIRMS_LAYER_NAME


def firms_day_range() -> int:
    """FIRMS lookback window in days, clamped 1-10; anything but a plain integer falls back to the default."""
    raw = os.environ.get(FIRMS_DAY_RANGE_VARIABLE, "").strip()
    if _STRICT_NONNEGATIVE_INTEGER.match(raw) is None:
        return DEFAULT_FIRMS_DAY_RANGE
    return _clamp_day_range(int(raw))


def _clamp_day_range(day_range: int) -> int:
    """Clamp a requested lookback window to FIRMS' allowed 1-10 day range."""
    return min(MAX_FIRMS_DAY_RANGE, max(MIN_FIRMS_DAY_RANGE, day_range))


def _column(columns: Sequence[str], index: int) -> str | None:
    """Return one CSV cell, or None when the header lacked the column or the row is short."""
    if index < 0 or index >= len(columns):
        return None
    return columns[index]


def _numeric_column(columns: Sequence[str], index: int) -> float | None:
    """Return a numeric CSV cell, or None when the header lacked it or the cell will not parse.

    Deliberately NOT the TypeScript's `parseFloat(cell) || 0`. That default is what put
    `brightness: 0` on all 6,297 stored detections -- the old parser looked up only
    `header.indexOf("brightness")`, which is -1 for every VIIRS product, and minted a placeholder
    for a channel it had never read. A brightness temperature of 0 K is physically impossible, so
    the read model served an unread channel as a measurement. An unread channel must be an ABSENT
    property, never a zero: `jsonb_typeof(properties->'brightness') = 'number'` then excludes it
    on its own. See ingest/AGENTS.md "firms.py".
    """
    cell = _column(columns, index)
    if cell is None:
        return None
    return javascript_parse_float(cell)


def parse_firms_csv(csv_text: str, source: str) -> list[dict[str, object]]:
    """Parse one FIRMS constellation product's area CSV into GeoJSON point features."""
    lines = csv_text.strip().split("\n")
    if len(lines) < MIN_CSV_LINES:
        return []

    header = [column.strip().lower() for column in lines[0].split(",")]
    latitude_index = _header_index(header, "latitude")
    longitude_index = _header_index(header, "longitude")
    # VIIRS names its 4um channel bright_ti4; only MODIS publishes "brightness".
    brightness_index = _header_index(header, "brightness")
    if brightness_index < 0:
        brightness_index = _header_index(header, "bright_ti4")
    confidence_index = _header_index(header, "confidence")
    frp_index = _header_index(header, "frp")
    satellite_index = _header_index(header, "satellite")
    acquisition_date_index = _header_index(header, "acq_date")
    acquisition_time_index = _header_index(header, "acq_time")

    features: list[dict[str, object]] = []
    for line in lines[1:]:
        columns = line.split(",")
        if len(columns) < MIN_CSV_COLUMNS:
            continue
        latitude_cell = _column(columns, latitude_index)
        longitude_cell = _column(columns, longitude_index)
        latitude = javascript_parse_float(latitude_cell) if latitude_cell is not None else None
        longitude = javascript_parse_float(longitude_cell) if longitude_cell is not None else None
        if latitude is None or longitude is None:
            continue
        properties: dict[str, object] = {
            "confidence": _text_column(columns, confidence_index, DEFAULT_CONFIDENCE),
            # The TypeScript falls back to the requested product token, not a fixed label,
            # when a row's own satellite column is absent.
            "satellite": _text_column(columns, satellite_index, source),
            "acqDate": _text_column(columns, acquisition_date_index, ""),
            "acqTime": _text_column(columns, acquisition_time_index, ""),
        }
        # A radiometric channel this product did not publish is omitted, never zero-filled.
        brightness = _numeric_column(columns, brightness_index)
        if brightness is not None:
            properties["brightness"] = brightness
        fire_radiative_power = _numeric_column(columns, frp_index)
        if fire_radiative_power is not None:
            properties["frp"] = fire_radiative_power
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
                "properties": properties,
            }
        )
    return features


def _header_index(header: Sequence[str], name: str) -> int:
    """Return a header column's position, or -1 when the upstream did not supply it."""
    return header.index(name) if name in header else -1


def _text_column(columns: Sequence[str], index: int, fallback: str) -> str:
    """Return a trimmed CSV cell, or the TypeScript's default when the header lacked the column."""
    cell = _column(columns, index)
    return fallback if cell is None else cell.strip()


async def fetch_active_fires(
    client: httpx.AsyncClient, area: str, day_range: int, source: str
) -> list[dict[str, object]]:
    """Fetch bounded FIRMS detections for one constellation product as GeoJSON point features."""
    api_key = os.environ.get(FIRMS_API_KEY_VARIABLE, "").strip()
    if not api_key:
        raise ValueError(f"{FIRMS_API_KEY_VARIABLE} environment variable is not set")
    url = FIRMS_AREA_CSV_TEMPLATE.format(
        api_key=api_key,
        source=source,
        area=area,
        day_range=_clamp_day_range(day_range),
    )
    csv_text = await fetch_bounded_text(client, url, FIRMS_BOUNDS, {"Accept": "text/csv"})
    return parse_firms_csv(csv_text, source)


async def _gather_constellation(
    client: httpx.AsyncClient,
    area: str,
    day_range: int,
) -> list[list[dict[str, object]] | BaseException]:
    """Fetch every VIIRS constellation product, keeping one satellite's failure from discarding the rest."""
    return await asyncio.gather(
        *(fetch_active_fires(client, area, day_range, source) for source in FIRMS_VIIRS_SOURCES),
        return_exceptions=True,
    )


def _partition_unavailable_sources(
    collections: list[list[dict[str, object]] | BaseException],
) -> tuple[list[str], BaseException | None]:
    """Name the constellation products that failed and keep the first failure to re-raise."""
    unavailable_sources: list[str] = []
    first_failure: BaseException | None = None
    for source, collection in zip(FIRMS_VIIRS_SOURCES, collections, strict=True):
        if isinstance(collection, BaseException):
            unavailable_sources.append(source)
            if first_failure is None:
                first_failure = collection
    return unavailable_sources, first_failure


def build_fire_detection_write(
    feature: dict[str, object],
    layer_name: str,
    max_observation_age: timedelta,
    now: datetime | None = None,
) -> FeatureWrite | None:
    """Build one detection's write, returning None for a record with no native key or a stale observation."""
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    try:
        identity = build_firms_identity(properties, coordinates if isinstance(coordinates, list) else None)
    except (MissingNativeKeyError, ValueError):
        return None
    if identity.observed_at is None or not is_fresh_observation(identity.observed_at, max_observation_age, now):
        return None
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            **properties,
            "observedAt": format_javascript_timestamp(identity.observed_at),
            "source": FIRMS_PROPERTY_SOURCE,
            "geometry": geometry,
        },
        channel=FIRMS_CHANNEL,
    )


async def run_fire_ingestion_job(
    write_features: FeatureWriter,
    *,
    bbox: str | None = None,
    day_range: int | None = None,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> IngestionJobResult:
    """Fetch bounded FIRMS observations across the VIIRS constellation and write the fresh, natively-keyed ones.

    Ports `runFireIngestionJob` (`ingestion-jobs.ts:118-204`): every constellation product is fetched
    concurrently, one satellite's failure does not discard the others' detections, and the job only
    raises when every product is unavailable. See ingest/AGENTS.md "firms.py".
    """
    area = resolve_bounded_bbox(bbox)
    if area is None:
        return skipped_result(FIRMS_SOURCE, UNCONFIGURED_BBOX_REASON)

    window_days = firms_day_range() if day_range is None else day_range
    if client is None:
        async with upstream_client(FIRMS_BOUNDS) as owned_client:
            collections = await _gather_constellation(owned_client, area, window_days)
    else:
        collections = await _gather_constellation(client, area, window_days)

    unavailable_sources, first_failure = _partition_unavailable_sources(collections)
    if len(unavailable_sources) == len(FIRMS_VIIRS_SOURCES) and first_failure is not None:
        raise first_failure

    max_observation_age = timedelta(days=_clamp_day_range(window_days))
    layer_name = resolve_firms_layer_name()

    # The FIRMS `satellite` column (N / N20 / N21) already namespaces the observation id, so the
    # constellation merge cannot collide across products. Keyed by external id with last-write-wins,
    # matching the TypeScript `Map<string, IngestFeatureInput>.set` semantics.
    records_seen = 0
    rejected = 0
    merged: dict[str, FeatureWrite] = {}
    for collection in collections:
        if isinstance(collection, BaseException):
            continue
        records_seen += len(collection)
        for feature in collection:
            write = build_fire_detection_write(feature, layer_name, max_observation_age, now)
            if write is None:
                rejected += 1
                continue
            merged[write.external_id] = write
    if rejected:
        logger.info("firms_observations_rejected", rejected=rejected, records_seen=records_seen)
    if unavailable_sources:
        logger.warning("firms_satellites_unavailable", unavailable=unavailable_sources)

    # Cap the merged constellation, newest first, so truncation drops the oldest detections rather
    # than whichever satellite happened to resolve last.
    max_source_records = resolve_max_source_records()
    fresh = sorted(merged.values(), key=lambda write: str(write.properties["observedAt"]), reverse=True)
    selected = fresh[:max_source_records]

    reason = f"Unavailable FIRMS products: {', '.join(unavailable_sources)}" if unavailable_sources else None
    return IngestionJobResult(
        source=FIRMS_SOURCE,
        status="ingested",
        records_seen=records_seen,
        records_written=await write_features(selected),
        truncated=len(fresh) > len(selected),
        reason=reason,
        details={"rejected": rejected},
    )
