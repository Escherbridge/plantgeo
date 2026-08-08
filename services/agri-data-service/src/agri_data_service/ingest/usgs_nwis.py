"""USGS NWIS streamflow ingestion: the tiled instantaneous-values adapter and its bounded gauge job."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import UpstreamBounds, UpstreamPayloadError, fetch_bounded_json, upstream_client
from agri_data_service.ingest.identity import (
    USGS_NWIS_PRODUCER,
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
from agri_data_service.ingest.source import (
    FetchRequest,
    FreshnessRule,
    FunctionSource,
    HistoryCapability,
    HistoryWindow,
)
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.ingest.source import UpstreamRecord
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

USGS_STREAMFLOW_ARCHIVE_SOURCE: Final = "usgs-streamflow-archive"

# The DAILY-values service, not the instantaneous one the forward path uses, and that is the
# whole reason a history walk needs its own source token.
#
# `/nwis/iv/` keeps roughly 120 days. Asking it for 2022 returns a well-formed response with no
# values -- not an error -- so a walk built on it would report four years of successful,
# empty chunks and read as "the record is genuinely empty back there". `/nwis/dv/` carries the
# full period of record and is the only endpoint that can answer the axis the slider draws.
#
# `siteStatus` is deliberately ABSENT where the forward query pins it to `active`. A gauge
# discontinued in 2024 still measured real water in 2022, and filtering history to
# currently-active sites would silently delete those years from the record.
DAILY_VALUES_QUERY_TEMPLATE: Final = (
    "https://waterservices.usgs.gov/nwis/dv/?format=json&bBox={tile}&parameterCd=00060&siteType=ST"
    "&startDT={start_day}&endDT={end_day}"
)

# USGS DV nominally reaches the 1890s for a few gauges. The floor is set at the vegetation
# layer's own earliest observed day instead, because that is the axis the slider actually draws
# and a deeper walk would spend hours on years no other layer can populate. `ingest-backfill
# --since` may still name any day at or after this; the floor only refuses a walk outright.
USGS_DAILY_VALUES_EARLIEST: Final = datetime(2022, 8, 5, tzinfo=UTC)

# NWIS's missing-value sentinel. It arrives as an ordinary numeric string, so an unguarded
# parse writes -999999 ft3/s as a real reading and every downstream percentile and colour ramp
# is computed against it.
#
# BOTH parsers go through `is_missing_value_sentinel` rather than repeating the comparison, because
# the forward path shipped without the guard and wrote 680 sentinel rows into `water-gauges` before
# anyone noticed -- 669 of them in the six days to 2026-08-07. One predicate, two call sites.
#
# Tested against the sentinel's VALUE, never against its sign: `validation.py`'s
# `USGS_NO_DATA_SENTINEL` records that genuine reverse flow reaches -172,000 cfs at these gauges,
# so "negative means missing" would delete real measurements.
NWIS_MISSING_VALUE: Final = -999999.0

# The archive path's own budget, deliberately far larger than NWIS_BOUNDS above.
#
# Measured 2026-08-07 across all eight PNW tiles for a three-day daily-values window: six tiles
# answered in 5.8-7.0s, one in 12.4s, and `-117,46,-113,49` in 25.5s -- which is how the first
# probe of this walk died, exactly at the forward path's 25s edge. The variance is the bBox
# filter's, not the window's: the same tile returns 147KB either way.
#
# The forward job keeps its 25s and should: it runs every 30 minutes, and a tile that has gone
# slow needs to fail loudly rather than pile up. A backfill is the opposite case -- it runs once
# over a fixed window, every tile must succeed or the day is a hole in the record, and waiting
# 90s is far cheaper than re-walking. The byte ceiling rises with the window: a 15-day chunk
# over the densest tile is ~1.7MB and the default 8MB leaves no room to lengthen a chunk.
NWIS_ARCHIVE_BOUNDS: Final = UpstreamBounds(max_bytes=32 * 1024 * 1024, timeout_seconds=90.0)

# 25s, not the 10s this carried until 2026-08-05. NWIS's bBox/spatial-filter path degraded
# that day: measured against it live, non-spatial lookups stayed sub-second (root 0.52s,
# single-site 0.37s) while every bBox query -- any region, and a 1-degree box as readily as a
# 4-degree one -- answered in 7-14s or not at all. All eight tiles must succeed for the job to
# write anything (see _fetch_tile), so a 10s budget failed every run for hours. 25s covers the
# genuine-but-slow band; a tile that never answers still times out and the job still fails
# loudly, which is correct -- partial coverage must never be reported as a complete day. Costs
# at most ~50s more per run against a 30-minute cadence. See src/lib/server/services/
# usgs-water.ts for the user-facing sibling, deliberately left at 10s: a request someone is
# waiting on cannot afford this.
NWIS_BOUNDS: Final = UpstreamBounds(max_bytes=8 * 1024 * 1024, timeout_seconds=25.0)

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


def series_site_number(series: Mapping[str, object]) -> str:
    """Read a time series' USGS site number, empty when the upstream named none."""
    source_info = series.get("sourceInfo")
    if not isinstance(source_info, dict):
        return ""
    site_codes = source_info.get("siteCode")
    first_site_code = site_codes[0] if isinstance(site_codes, list) and site_codes else None
    return str(first_site_code.get("value", "")) if isinstance(first_site_code, dict) else ""


def is_missing_value_sentinel(flow_cfs: float | None) -> bool:
    """Whether a parsed discharge is NWIS's no-reading marker rather than a measurement."""
    return flow_cfs is not None and flow_cfs <= NWIS_MISSING_VALUE


def _reading_is_sentinel(reading: object) -> bool:
    """Whether one instantaneous reading carries the missing-value marker in place of a discharge."""
    if not isinstance(reading, dict):
        return False
    raw_value = reading.get("value")
    return isinstance(raw_value, str) and is_missing_value_sentinel(javascript_parse_float(raw_value))


@dataclass(frozen=True, slots=True)
class StreamflowFetch:
    """One forward fetch: the gauges that reported a discharge, and the sites that reported only the sentinel."""

    gauges: list[dict[str, object]]
    sentinel_sites: int


def parse_gauge(series: Mapping[str, object], now: datetime | None = None) -> dict[str, object] | None:
    """Parse one NWIS time series into a gauge record, or None when every reading it carried was the sentinel."""
    source_info = _require_mapping(series.get("sourceInfo"), "sourceInfo")
    geo_location = _require_mapping(source_info.get("geoLocation"), "geoLocation")
    geographic_location = _require_mapping(geo_location.get("geogLocation"), "geogLocation")

    site_number = series_site_number(series)

    values = series.get("values")
    first_values = values[0] if isinstance(values, list) and values else None
    raw_readings = first_values.get("value") if isinstance(first_values, dict) else None
    readings = raw_readings if isinstance(raw_readings, list) else []
    # The archive path's rule -- a sentinel is not a reading -- applied to a parser that then asks
    # for the latest. Measured 2026-08-07 against the live tile `-125,46,-121,49`: all 194 series
    # carried exactly one reading, because the query pins no `period` and NWIS then returns only the
    # newest value. So this filter has nothing to fall back to today and the branch below is what
    # actually fires; it is written as a filter rather than a test on `readings[-1]` so that adding a
    # `period` to STREAMFLOW_QUERY_TEMPLATE keeps the real reading instead of discarding the gauge.
    reported = [reading for reading in readings if not _reading_is_sentinel(reading)]
    # Deliberately NOT the wall-clock fallback below and NOT a null flow: the gauge is simply not
    # reported this tick. Those 11-of-194 sentinel series were qualified `Ssn` -- seasonally
    # monitored, out of service -- and their last real reading is already on the layer under its own
    # real timestamp, so dropping the tick leaves the station drawn and honestly dated. See
    # ingest/AGENTS.md "usgs_nwis.py".
    if readings and not reported:
        return None
    latest = reported[-1] if reported else None

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


def site_zone_offset(source_info: Mapping[str, object]) -> str:
    """Read a site's STANDARD-time UTC offset, e.g. "-08:00", falling back to UTC.

    Daily values arrive with a naive `dateTime` ("2022-08-05T00:00:00.000") while
    `build_streamflow_gauge_identity` refuses a timestamp with no offset -- so one has to be
    supplied, and which one is not arbitrary.

    The site's own STANDARD offset is used, never the daylight one, because USGS computes a
    daily value over the site's standard-time day year-round; picking the DST offset in summer
    would name the same instant two different ways across a single walk and mint two identities
    for one reading. Either offset happens to preserve the calendar DAY at midnight, which is
    what `geo.feature_observation_day` reads, but only the standard one is stable.

    UTC is the fallback rather than a guess at a regional zone: with a midnight timestamp it
    leaves the publisher-named day exactly as named, which is the property that matters.
    """
    zone_info = source_info.get("timeZoneInfo")
    if not isinstance(zone_info, dict):
        return "+00:00"
    default_zone = zone_info.get("defaultTimeZone")
    if not isinstance(default_zone, dict):
        return "+00:00"
    offset = default_zone.get("zoneOffset")
    return offset if isinstance(offset, str) and offset.strip() else "+00:00"


def parse_daily_value_series(series: Mapping[str, object]) -> list[dict[str, object]]:
    """Parse one NWIS daily-values time series into one gauge record per day it reported.

    The forward path's `parse_gauge` keeps only `readings[-1]`, because the instantaneous feed
    is asked "what is it now". A history walk wants every day the series carries, each as its
    own observation -- `build_streamflow_gauge_identity` keys on `{siteNo}:{updatedAt}`, so one
    record per day is exactly what makes them distinct versions of one gauge entity rather than
    repeated overwrites of a single row.
    """
    source_info = _require_mapping(series.get("sourceInfo"), "sourceInfo")
    geo_location = _require_mapping(source_info.get("geoLocation"), "geoLocation")
    geographic_location = _require_mapping(geo_location.get("geogLocation"), "geogLocation")

    site_number = series_site_number(series)
    if not site_number:
        return []

    values = series.get("values")
    first_values = values[0] if isinstance(values, list) and values else None
    readings = first_values.get("value") if isinstance(first_values, dict) else None
    if not isinstance(readings, list):
        return []

    zone_offset = site_zone_offset(source_info)
    site_name = str(source_info.get("siteName", "") or "")
    trend = infer_trend(first_values.get("qualifier") if isinstance(first_values, dict) else None)

    records: list[dict[str, object]] = []
    for reading in readings:
        if not isinstance(reading, dict):
            continue
        reading_time = reading.get("dateTime")
        if not isinstance(reading_time, str) or not reading_time.strip():
            continue
        raw_value = reading.get("value")
        flow_cfs = javascript_parse_float(raw_value) if isinstance(raw_value, str) else None
        # A sentinel day is dropped entirely rather than written with a null flow: the gauge
        # reported nothing, and a row claiming it reported "no value" is a fabricated
        # observation of an absence.
        if flow_cfs is None or is_missing_value_sentinel(flow_cfs):
            continue
        records.append(
            {
                "siteNo": site_number,
                "siteName": site_name,
                "lat": geographic_location.get("latitude"),
                "lon": geographic_location.get("longitude"),
                "flowCfs": flow_cfs,
                "percentile": None,
                "condition": classify_condition(None),
                "trend": trend,
                # The publisher-named day, offset-stamped but never shifted.
                "updatedAt": f"{reading_time.strip()}{zone_offset}",
                # Always false: a daily value names its own day, so the wall-clock fallback
                # that `parse_gauge` needs for a silent instantaneous gauge cannot arise here.
                "updatedAtIsWallClock": False,
            }
        )
    return records


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
) -> StreamflowFetch:
    """Fetch NWIS time series across every tile of a bbox, deduped by site number, as gauge records."""
    gate = asyncio.Semaphore(MAX_CONCURRENT_TILE_REQUESTS)
    tile_results = await asyncio.gather(*(_fetch_tile(client, tile, gate) for tile in tile_bbox(bbox)))

    gauges: list[dict[str, object]] = []
    seen_site_numbers: set[str] = set()
    # Deduped by site for the same reason the kept gauges are -- tiles overlap at their shared edges,
    # so a boundary gauge reporting the sentinel would otherwise be counted once per tile it sits in.
    sentinel_site_numbers: set[str] = set()
    for tile_series in tile_results:
        for series in tile_series:
            if not isinstance(series, dict):
                continue
            gauge = parse_gauge(series, now)
            if gauge is None:
                sentinel_site_numbers.add(series_site_number(series))
                continue
            site_number = str(gauge["siteNo"])
            if site_number in seen_site_numbers:
                continue
            seen_site_numbers.add(site_number)
            gauges.append(gauge)
    return StreamflowFetch(gauges=gauges, sentinel_sites=len(sentinel_site_numbers))


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


async def _fetch_daily_values_tile(
    client: httpx.AsyncClient,
    tile: str,
    window: HistoryWindow,
    gate: asyncio.Semaphore,
) -> list[object]:
    """Fetch one tile's daily-values series for a window; a tile failure propagates, never partial coverage."""
    async with gate:
        payload = await fetch_bounded_json(
            client,
            DAILY_VALUES_QUERY_TEMPLATE.format(
                tile=tile,
                start_day=window.start.date().isoformat(),
                # endDT is INCLUSIVE at NWIS while a HistoryWindow's end is exclusive, so the
                # last day is stepped back. Without this every chunk re-fetches its successor's
                # first day -- harmless for correctness, since the writer dedupes by identity,
                # but it is a whole extra day of rows per chunk across a 1,460-day walk.
                end_day=(window.end.date() - timedelta(days=1)).isoformat(),
            ),
            NWIS_ARCHIVE_BOUNDS,
            {"Accept": "application/json"},
        )
    if not isinstance(payload, dict):
        return []
    value = payload.get("value")
    if not isinstance(value, dict):
        return []
    time_series = value.get("timeSeries")
    return time_series if isinstance(time_series, list) else []


async def fetch_streamflow_history(
    client: httpx.AsyncClient,
    bbox: str,
    window: HistoryWindow,
) -> list[dict[str, object]]:
    """Fetch every gauge-day in a window across every tile of a bbox.

    Deduped by (site, day) rather than by site alone, which is the one substantive difference
    from `fetch_streamflow_gauges`: the forward path wants one row per gauge, this wants one row
    per gauge per day, and collapsing to the site would keep a single day out of the window.
    Tiles still overlap at their shared edges, so the dedupe is doing real work.
    """
    gate = asyncio.Semaphore(MAX_CONCURRENT_TILE_REQUESTS)
    tile_results = await asyncio.gather(
        *(_fetch_daily_values_tile(client, tile, window, gate) for tile in tile_bbox(bbox))
    )

    gauge_days: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for tile_series in tile_results:
        for series in tile_series:
            if not isinstance(series, dict):
                continue
            for record in parse_daily_value_series(series):
                key = (str(record["siteNo"]), str(record["updatedAt"]))
                if key in seen:
                    continue
                seen.add(key)
                gauge_days.append(record)
    return gauge_days


async def fetch_streamflow_history_records(
    request: FetchRequest,
    window: HistoryWindow,
) -> Sequence[UpstreamRecord]:
    """Fetch one past chunk of daily gauge values, owning a client only when the walk supplied none."""
    if request.client is None:
        async with upstream_client(NWIS_ARCHIVE_BOUNDS) as owned_client:
            return await fetch_streamflow_history(owned_client, request.bbox, window)
    return await fetch_streamflow_history(request.client, request.bbox, window)


def build_gauge_history_write(record: UpstreamRecord, _request: FetchRequest) -> FeatureWrite | None:
    """Map one archived gauge-day through the same builder the forward path uses."""
    return build_gauge_write(record, resolve_water_gauges_layer_name())


def usgs_streamflow_archive_source() -> FunctionSource:
    """Compose the USGS source used for history walks over the daily-values service.

    A second source token over the same producer, layer and identity contract as
    `usgs-streamflow` -- not a second producer. It exists as its own token for the reason the
    FIRMS archive does: the two read different endpoints with different retention, so
    `ingest-backfill` must not be able to ask the instantaneous job for a past window, nor this
    walk for the current one.
    """
    return FunctionSource(
        source_name=USGS_STREAMFLOW_ARCHIVE_SOURCE,
        producer=USGS_NWIS_PRODUCER,
        channel=USGS_CHANNEL,
        # No maximum age -- a history walk's window IS its age bound. Undated records stay
        # refused: a daily value that names no day is not a day's observation.
        freshness=FreshnessRule(max_observation_age=None, accepts_undated_records=False),
        resolve_layer_reference=resolve_water_gauges_layer_name,
        fetch_current_records=_refuse_current_window,
        build_feature_write=build_gauge_history_write,
        history=HistoryCapability(supported=True, earliest=USGS_DAILY_VALUES_EARLIEST),
        fetch_history_records=fetch_streamflow_history_records,
        shape="feature",
    )


async def _refuse_current_window(_request: FetchRequest) -> Sequence[UpstreamRecord]:
    """Refuse a current-window fetch: `ingest-streamflow` owns that path and reads the live IV feed."""
    raise NotImplementedError(
        "usgs-streamflow-archive serves history only; use ingest-streamflow for the current window"
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
            fetched = await fetch_streamflow_gauges(owned_client, area, now)
    else:
        fetched = await fetch_streamflow_gauges(client, area, now)

    gauges = fetched.gauges
    selected = gauges[: resolve_max_source_records()]
    layer_name = resolve_water_gauges_layer_name()
    writes = [write for write in (build_gauge_write(gauge, layer_name) for gauge in selected) if write is not None]
    wall_clock_gauges = sum(1 for gauge in selected if gauge.get("updatedAtIsWallClock"))
    if wall_clock_gauges:
        logger.info("streamflow_wall_clock_identities", gauges=wall_clock_gauges, selected=len(selected))
    # The metric that would have caught this in a day rather than in six. It sits next to
    # `wall_clock_identities` because it answers the opposite half of the same question: how many
    # gauges NWIS named but did not measure.
    if fetched.sentinel_sites:
        logger.info("streamflow_sentinel_gauges_dropped", sites=fetched.sentinel_sites, kept=len(gauges))

    return IngestionJobResult(
        source=USGS_STREAMFLOW_SOURCE,
        status="ingested",
        # Counts only the gauges that reported a discharge, which is what `truncated` and
        # `resolve_max_source_records` are both measured against. A sentinel-only gauge was never a
        # writable record, so it is reported on its own axis rather than inflating this one.
        records_seen=len(gauges),
        records_written=await write_features(writes),
        truncated=len(gauges) > len(selected),
        details={
            "rejected": len(selected) - len(writes),
            "wall_clock_identities": wall_clock_gauges,
            "sentinel_gauges": fetched.sentinel_sites,
        },
    )
