"""NASA FIRMS active-fire ingestion: the VIIRS constellation CSV adapter and its bounded, fresh-only job."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamPayloadError,
    fetch_bounded_text,
    upstream_client,
)
from agri_data_service.ingest.identity import (
    FIRMS_PRODUCER,
    MissingNativeKeyError,
    build_firms_identity,
    format_javascript_timestamp,
)
from agri_data_service.ingest.layer_binding import LayerBinding
from agri_data_service.ingest.policy import (
    UNCONFIGURED_BBOX_REASON,
    is_fresh_observation,
    javascript_parse_float,
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

FIRMS_SOURCE: Final = "nasa-firms"
# A separate `--source` token so an operator cannot ask the archive walk for a current window, or the
# forward job for a past one. Same producer, same layer, same identity contract; different access path.
FIRMS_ARCHIVE_SOURCE: Final = "nasa-firms-archive"
FIRMS_PROPERTY_SOURCE: Final = "NASA FIRMS"

FIRMS_LAYER: Final = LayerBinding(
    variable="FIRMS_LAYER_ID",
    default="fire-detections",
    channel="layer:fire-detections",
)
FIRMS_CHANNEL: Final = FIRMS_LAYER.channel
FIRMS_LAYER_VARIABLE: Final = FIRMS_LAYER.variable
DEFAULT_FIRMS_LAYER_NAME: Final = FIRMS_LAYER.default

FIRMS_API_KEY_VARIABLE: Final = "NASA_FIRMS_KEY"
FIRMS_DAY_RANGE_VARIABLE: Final = "FIRMS_DAY_RANGE"
FIRMS_AREA_CSV_TEMPLATE: Final = (
    "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/{source}/{area}/{day_range}"
)
# The same endpoint with a trailing start date, which is the whole of FIRMS' archive access: there is
# no separate archive host, only products whose availability window reaches further back.
FIRMS_AREA_CSV_DATED_TEMPLATE: Final = f"{FIRMS_AREA_CSV_TEMPLATE}/{{start_date}}"
FIRMS_AVAILABILITY_CSV_TEMPLATE: Final = "https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/{api_key}/all"

# The full VIIRS NRT constellation, matching `nasa-firms.ts` FIRMS_VIIRS_SOURCES. Querying a single
# satellite silently zeroes the layer whenever that satellite stops publishing -- see ingest/AGENTS.md
# "firms.py" -- so the job fans out across all three and merges rather than picking one.
FIRMS_VIIRS_SOURCES: Final[tuple[str, ...]] = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")

# Every product a history walk may draw on, NRT and standard-processing alike. Which of them actually
# answers for a given past day is decided per chunk from the live availability table, never assumed.
FIRMS_HISTORY_SOURCES: Final[tuple[str, ...]] = (
    *FIRMS_VIIRS_SOURCES,
    "VIIRS_SNPP_SP",
    "VIIRS_NOAA20_SP",
    "VIIRS_NOAA21_SP",
    "MODIS_SP",
)

FIRMS_BOUNDS: Final = UpstreamBounds(max_bytes=16 * 1024 * 1024, timeout_seconds=15.0)
FIRMS_AVAILABILITY_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024, timeout_seconds=15.0)

DEFAULT_FIRMS_DAY_RANGE: Final = 2
MIN_FIRMS_DAY_RANGE: Final = 1
# Measured against the live API on 2026-08-05, not read off the documentation: day ranges 1-5 answer
# HTTP 200 and 6, 7 and 10 all answer `400 Invalid day range. Expects [1..5].` for both the dated and
# the undated form. This constant was 10 until that measurement, which made `FIRMS_DAY_RANGE=10` a
# configuration that failed every product and therefore the whole job. See ingest/AGENTS.md "firms.py".
MAX_FIRMS_DAY_RANGE: Final = 5
# A century, so `is_fresh_observation` keeps its five-minute future-skew guard on a history record while
# imposing no lower bound: the oldest FIRMS product starts 2000-11-01. Never a way to accept an undated
# record -- that rejection is separate and unconditional.
UNBOUNDED_OBSERVATION_AGE: Final = timedelta(days=100 * 365)

# MODIS_SP's own published `min_date`, read from the live availability table on 2026-08-05 and the
# oldest floor any FIRMS_HISTORY_SOURCES member offers (VIIRS_SNPP_SP starts 2012-01-20). It bounds
# only whether a walk is refused outright; per-day coverage is still decided from the live table.
FIRMS_ARCHIVE_EARLIEST_OBSERVATION: Final = datetime(2000, 11, 1, tzinfo=UTC)
FIRMS_ARCHIVE_CURRENT_REFUSAL: Final = (
    "nasa-firms-archive serves past windows only; `agri-service data ingest-firms` owns the current window "
    "because it is the path that reports a partial-constellation outage as a reason rather than a "
    "clean empty run."
)

MIN_CSV_LINES: Final = 2
MIN_CSV_COLUMNS: Final = 4
DEFAULT_CONFIDENCE: Final = "nominal"

# The property that records which product's published coverage this record was drawn under, so a
# leakage-sensitive query gates on a field rather than on prose. `data_available_at` is the governed
# concept this stands in for until geo.features has a column to model publication lag: the FIRMS
# archive's SP series carries a months-long lag between `observedAt` (acquisition) and the day the
# product actually published, and the availability table's `max_date` is the only figure upstream
# gives for it. Absent on a forward-path record, where the product's coverage is "now" by construction.
PRODUCT_COVERAGE_THROUGH_PROPERTY: Final = "productCoverageThrough"
# Names the near-real-time product a standard-processing record displaced, when a walk saw both.
SUPERSEDED_PRODUCT_PROPERTY: Final = "supersededProduct"
SPATIAL_SUPPORT_PROPERTY: Final = "spatialSupportMeters"
NORMALIZED_CONFIDENCE_PROPERTY: Final = "confidenceNormalized"

# FIRMS' standard-processing suffix. Every product token is `<INSTRUMENT>_NRT` or `<INSTRUMENT>_SP`,
# and the SP series is the authoritative reprocessing of the same physical detections the NRT series
# already delivered -- which is why it wins a key collision rather than merely arriving later.
STANDARD_PROCESSING_SUFFIX: Final = "_SP"
STANDARD_PROCESSING_TIER: Final = 1
NEAR_REAL_TIME_TIER: Final = 0

# Nominal ground sample distance of the instrument behind each product family, in metres. VIIRS' active
# fire product is the 375 m I-band; MODIS' is 1 km. It is stored on the record because FRP is integrated
# over the pixel, so the same fire reads roughly an order of magnitude hotter through MODIS: measured on
# production 2026-08-05, MODIS_SP's median FRP over the walked window is 33.10 MW against VIIRS' 4.27.
# A consumer putting both on one colour scale is reading instrument geometry as fire behaviour.
VIIRS_SPATIAL_SUPPORT_METERS: Final = 375
MODIS_SPATIAL_SUPPORT_METERS: Final = 1000
VIIRS_PRODUCT_PREFIX: Final = "VIIRS_"
MODIS_PRODUCT_PREFIX: Final = "MODIS_"

# FIRMS publishes confidence two incompatible ways into one column: VIIRS emits the categorical
# `l`/`n`/`h` and MODIS a 0-100 percentage. Production holds 136,303 categorical rows and 12,157
# numeric ones under one `properties.confidence`. The banding below is FIRMS' own published equivalence
# for the MODIS percentage, so a consumer has one scale to filter on without re-deriving it per product.
CONFIDENCE_LOW: Final = "low"
CONFIDENCE_NOMINAL: Final = "nominal"
CONFIDENCE_HIGH: Final = "high"
MODIS_LOW_CONFIDENCE_CEILING: Final = 30.0
MODIS_NOMINAL_CONFIDENCE_CEILING: Final = 80.0
_CATEGORICAL_CONFIDENCE: Final[Mapping[str, str]] = MappingProxyType(
    {
        "l": CONFIDENCE_LOW,
        "low": CONFIDENCE_LOW,
        "n": CONFIDENCE_NOMINAL,
        "nominal": CONFIDENCE_NOMINAL,
        "h": CONFIDENCE_HIGH,
        "high": CONFIDENCE_HIGH,
    }
)

_STRICT_NONNEGATIVE_INTEGER: Final = re.compile(r"^\d+$")


def resolve_firms_layer_name() -> str:
    """Read FIRMS_LAYER_ID at call time so a cron environment change needs no restart."""
    return FIRMS_LAYER.resolve()


def firms_day_range() -> int:
    """FIRMS lookback window in days, clamped 1-5; anything but a plain integer falls back to the default."""
    raw = os.environ.get(FIRMS_DAY_RANGE_VARIABLE, "").strip()
    if _STRICT_NONNEGATIVE_INTEGER.match(raw) is None:
        return DEFAULT_FIRMS_DAY_RANGE
    return _clamp_day_range(int(raw))


def _clamp_day_range(day_range: int) -> int:
    """Clamp a requested lookback window to FIRMS' allowed 1-5 day range."""
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


def product_spatial_support_meters(product: str) -> int | None:
    """The nominal pixel size of the instrument behind a FIRMS product, or None for a product we cannot place.

    Never guessed: an unrecognised product token omits the property rather than defaulting to VIIRS,
    because a wrong spatial support is worse than an absent one -- it makes an incomparable value look
    comparable. See ingest/AGENTS.md "firms.py: MODIS is a different instrument, not a further satellite".
    """
    if product.startswith(VIIRS_PRODUCT_PREFIX):
        return VIIRS_SPATIAL_SUPPORT_METERS
    if product.startswith(MODIS_PRODUCT_PREFIX):
        return MODIS_SPATIAL_SUPPORT_METERS
    return None


def normalize_confidence(raw: str) -> str | None:
    """Map either FIRMS confidence spelling onto one low/nominal/high scale, or None when neither reads."""
    candidate = raw.strip().lower()
    categorical = _CATEGORICAL_CONFIDENCE.get(candidate)
    if categorical is not None:
        return categorical
    percentage = javascript_parse_float(candidate)
    if percentage is None:
        return None
    if percentage < MODIS_LOW_CONFIDENCE_CEILING:
        return CONFIDENCE_LOW
    return CONFIDENCE_NOMINAL if percentage <= MODIS_NOMINAL_CONFIDENCE_CEILING else CONFIDENCE_HIGH


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
        confidence = _text_column(columns, confidence_index, DEFAULT_CONFIDENCE)
        properties: dict[str, object] = {
            "confidence": confidence,
            # The TypeScript falls back to the requested product token, not a fixed label,
            # when a row's own satellite column is absent.
            "satellite": _text_column(columns, satellite_index, source),
            "acqDate": _text_column(columns, acquisition_date_index, ""),
            "acqTime": _text_column(columns, acquisition_time_index, ""),
            # The FIRMS product this row was read from, which is the discriminator between the
            # near-real-time and the standard-processing series a history walk also draws on. The
            # `satellite` column cannot serve: VIIRS_NOAA20_NRT and VIIRS_NOAA20_SP both report `N20`.
            "product": source,
        }
        # The instrument's pixel and one confidence scale, both carried so a consumer can tell an
        # incomparable value apart from a comparable one without a product lookup table of its own.
        spatial_support = product_spatial_support_meters(source)
        if spatial_support is not None:
            properties[SPATIAL_SUPPORT_PROPERTY] = spatial_support
        normalized_confidence = normalize_confidence(confidence)
        if normalized_confidence is not None:
            properties[NORMALIZED_CONFIDENCE_PROPERTY] = normalized_confidence
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


def _require_api_key() -> str:
    """Read NASA_FIRMS_KEY at call time, matching `nasa-firms.ts`'s own per-call check."""
    api_key = os.environ.get(FIRMS_API_KEY_VARIABLE, "").strip()
    if not api_key:
        raise ValueError(f"{FIRMS_API_KEY_VARIABLE} environment variable is not set")
    return api_key


async def fetch_active_fires(
    client: httpx.AsyncClient,
    area: str,
    day_range: int,
    source: str,
    start_date: date | None = None,
) -> list[dict[str, object]]:
    """Fetch bounded FIRMS detections for one product as GeoJSON point features, optionally from a past date."""
    api_key = _require_api_key()
    template = FIRMS_AREA_CSV_TEMPLATE if start_date is None else FIRMS_AREA_CSV_DATED_TEMPLATE
    url = template.format(
        api_key=api_key,
        source=source,
        area=area,
        day_range=_clamp_day_range(day_range),
        start_date=start_date.isoformat() if start_date is not None else "",
    )
    csv_text = await fetch_bounded_text(client, url, FIRMS_BOUNDS, {"Accept": "text/csv"})
    return parse_firms_csv(csv_text, source)


@dataclass(frozen=True, slots=True)
class ProductAvailability:
    """One FIRMS product's published availability window, read from the API rather than assumed."""

    product: str
    earliest: date
    latest: date

    def covers(self, day: date) -> bool:
        """True when this product publishes detections for the given acquisition day."""
        return self.earliest <= day <= self.latest


def parse_product_availability(csv_text: str) -> dict[str, ProductAvailability]:
    """Parse the `data_availability` table, which is the only authority on how far back a product reaches."""
    lines = [line.strip() for line in csv_text.strip().split("\n") if line.strip()]
    if len(lines) < MIN_CSV_LINES:
        raise UpstreamPayloadError("NASA FIRMS returned an empty data availability table")
    header = [column.strip().lower() for column in lines[0].split(",")]
    product_index = _header_index(header, "data_id")
    earliest_index = _header_index(header, "min_date")
    latest_index = _header_index(header, "max_date")
    if min(product_index, earliest_index, latest_index) < 0:
        raise UpstreamPayloadError("NASA FIRMS data availability table is missing a required column")

    availability: dict[str, ProductAvailability] = {}
    for line in lines[1:]:
        columns = line.split(",")
        product = _column(columns, product_index)
        earliest = _column(columns, earliest_index)
        latest = _column(columns, latest_index)
        if not product or not earliest or not latest:
            continue
        try:
            window = ProductAvailability(
                product=product.strip(),
                earliest=date.fromisoformat(earliest.strip()),
                latest=date.fromisoformat(latest.strip()),
            )
        except ValueError:
            continue
        availability[window.product] = window
    return availability


async def fetch_product_availability(client: httpx.AsyncClient) -> dict[str, ProductAvailability]:
    """Fetch the live availability table once per walk, so no window is assumed for any product."""
    url = FIRMS_AVAILABILITY_CSV_TEMPLATE.format(api_key=_require_api_key())
    return parse_product_availability(await fetch_bounded_text(client, url, FIRMS_AVAILABILITY_BOUNDS))


def products_covering(
    availability: Mapping[str, ProductAvailability],
    day: date,
    candidates: Sequence[str] = FIRMS_HISTORY_SOURCES,
) -> tuple[str, ...]:
    """Name the candidate products that publish the given day, in candidate order.

    Selecting per day is what keeps the near-real-time and standard-processing series from being asked
    for the same acquisition. Their windows are disjoint upstream today -- VIIRS_NOAA20_SP ended
    2026-05-31 the day before VIIRS_NOAA20_NRT began -- but that boundary moves with every reprocessing
    campaign, so it is read per walk rather than pinned. Both series report the same `satellite` token
    and would therefore key identically; `properties.product` is what tells them apart once stored, and
    `collapse_history_records` decides which of them wins on the day that boundary moves and their
    windows genuinely overlap.
    """
    return tuple(product for product in candidates if product in availability and availability[product].covers(day))


def products_covering_span(
    availability: Mapping[str, ProductAvailability],
    start_day: date,
    day_range: int,
    candidates: Sequence[str] = FIRMS_HISTORY_SOURCES,
) -> tuple[str, ...]:
    """Name every candidate product publishing ANY day of a span, in candidate order.

    Resolving from `start_day` alone was a silent hole. A span is up to five days wide, so a product
    whose coverage begins on day three of it was never asked for the two days it does cover, and the
    walk reported `records_seen=0` for them -- indistinguishable from "no fires that week". Over-asking
    is safe and cheap: a product asked for a day it lacks simply answers with fewer rows, and every row
    it does answer with carries the acquisition day FIRMS itself named. See ingest/AGENTS.md "firms.py".
    """
    days = tuple(start_day + timedelta(days=offset) for offset in range(max(day_range, 1)))
    return tuple(
        product
        for product in candidates
        if product in availability and any(availability[product].covers(day) for day in days)
    )


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
    feature: Mapping[str, object],
    layer_name: str,
    max_observation_age: timedelta | None,
    now: datetime | None = None,
) -> FeatureWrite | None:
    """Build one detection's write, returning None for a record with no native key or a stale observation.

    `max_observation_age=None` is the history caller: a walk over a past window has already bounded the
    age it asked for, so re-imposing the forward path's rolling window would reject every archive
    record. The future-skew guard still applies either way -- an acquisition FIRMS dates after the run
    clock is garbage in both directions of time.
    """
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    try:
        identity = build_firms_identity(properties, coordinates if isinstance(coordinates, list) else None)
    except (MissingNativeKeyError, ValueError):
        return None
    if identity.observed_at is None:
        return None
    age_bound = max_observation_age if max_observation_age is not None else UNBOUNDED_OBSERVATION_AGE
    if not is_fresh_observation(identity.observed_at, age_bound, now):
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

    # Keyed by external id with last-write-wins, matching the TypeScript
    # `Map<string, IngestFeatureInput>.set` semantics. No tier precedence is applied here and none is
    # needed: `FIRMS_VIIRS_SOURCES` is near-real-time only, so the three products this job merges carry
    # three distinct `satellite` tokens and cannot collide. That is a property of THIS product list, not
    # of the key -- `collapse_history_records` carries the rule for the walk that mixes both series.
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
        details={"rejected": rejected, "dropped": len(fresh) - len(selected)},
    )


def history_day_spans(window: HistoryWindow) -> tuple[tuple[date, int], ...]:
    """Cut one chunk into `(start_day, day_range)` requests no wider than the five days FIRMS allows.

    Days come from the window bounds' own UTC calendar dates, so a chunk boundary lands on the day the
    publisher names rather than on whatever local date a recast would produce. The end bound is
    exclusive, matching `HistoryWindow`, so a chunk ending at midnight does not fetch the next day.
    """
    first_day = window.start.astimezone(UTC).date()
    last_day = (window.end.astimezone(UTC) - timedelta(microseconds=1)).date()
    spans: list[tuple[date, int]] = []
    cursor = first_day
    while cursor <= last_day:
        remaining = (last_day - cursor).days + 1
        span = min(MAX_FIRMS_DAY_RANGE, remaining)
        spans.append((cursor, span))
        cursor += timedelta(days=span)
    return tuple(spans)


@dataclass(frozen=True, slots=True)
class HistorySpanFetch:
    """One dated span's answer: its records, the products that failed, and whether any product covered it."""

    records: list[dict[str, object]]
    unavailable: tuple[str, ...]
    covered: bool


def _stamp_product_coverage(records: Sequence[dict[str, object]], coverage_through: date) -> None:
    """Record the coverage frontier the answering product published under, on every row it answered with."""
    for record in records:
        properties = record.get("properties")
        if isinstance(properties, dict):
            properties[PRODUCT_COVERAGE_THROUGH_PROPERTY] = coverage_through.isoformat()


async def _fetch_history_span(
    client: httpx.AsyncClient,
    area: str,
    availability: Mapping[str, ProductAvailability],
    start_day: date,
    day_range: int,
) -> HistorySpanFetch:
    """Fetch one dated span from every product publishing any of its days, reporting what failed and what covered it."""
    products = products_covering_span(availability, start_day, day_range)
    if not products:
        return HistorySpanFetch(records=[], unavailable=(), covered=False)
    collections = await asyncio.gather(
        *(fetch_active_fires(client, area, day_range, product, start_day) for product in products),
        return_exceptions=True,
    )
    records: list[dict[str, object]] = []
    unavailable: list[str] = []
    for product, collection in zip(products, collections, strict=True):
        if isinstance(collection, BaseException):
            unavailable.append(product)
            continue
        _stamp_product_coverage(collection, availability[product].latest)
        records.extend(collection)
    return HistorySpanFetch(records=records, unavailable=tuple(unavailable), covered=True)


async def fetch_firms_history_records(request: FetchRequest, window: HistoryWindow) -> Sequence[UpstreamRecord]:
    """Fetch one past chunk from every FIRMS product whose live availability window covers each of its days.

    A product that answers with a header and no rows is NOT evidence the sky was empty -- VIIRS_SNPP_SP
    answered exactly that for 2022-08-05 while VIIRS_NOAA20_SP returned 1,649 detections for the same
    five days. The walk therefore never treats one product's silence as coverage; it queries every
    product the availability table says publishes the day and merges what answered.
    """
    if request.client is None:
        async with upstream_client(FIRMS_BOUNDS) as owned_client:
            return await _walk_history(owned_client, request, window)
    return await _walk_history(request.client, request, window)


async def _walk_history(
    client: httpx.AsyncClient,
    request: FetchRequest,
    window: HistoryWindow,
) -> Sequence[UpstreamRecord]:
    """Walk one chunk's five-day spans against a single availability read, merging every product's answer."""
    availability = await fetch_product_availability(client)
    records: list[dict[str, object]] = []
    unavailable: list[str] = []
    uncovered: list[str] = []
    for start_day, day_range in history_day_spans(window):
        span = await _fetch_history_span(client, request.bbox, availability, start_day, day_range)
        records.extend(span.records)
        unavailable.extend(span.unavailable)
        if not span.covered:
            uncovered.append(f"{start_day.isoformat()}+{day_range}")
    if unavailable:
        logger.warning("firms_history_products_unavailable", unavailable=sorted(set(unavailable)))
    if uncovered:
        # No product publishes these days at all, so the walk answered zero records for a reason that is
        # NOT "no fires". Unnamed, that reads as a clean empty week -- the "gap certified as complete"
        # failure the availability table exists to prevent.
        logger.warning("firms_history_spans_uncovered", spans=uncovered, candidates=list(FIRMS_HISTORY_SOURCES))
    return collapse_history_records(records)


def processing_tier(product: object) -> int:
    """Rank a FIRMS product's processing tier: standard processing outranks near-real-time."""
    if isinstance(product, str) and product.endswith(STANDARD_PROCESSING_SUFFIX):
        return STANDARD_PROCESSING_TIER
    return NEAR_REAL_TIME_TIER


def _product_of(record: Mapping[str, object]) -> str | None:
    """Read the product token a parsed record was drawn from, or None when it carries none."""
    properties = record.get("properties")
    product = properties.get("product") if isinstance(properties, dict) else None
    return product if isinstance(product, str) else None


def _supersede(winner: dict[str, object], loser: Mapping[str, object]) -> dict[str, object]:
    """Record on the surviving record which other product it displaced, when a different one lost."""
    winning_product = _product_of(winner)
    losing_product = _product_of(loser)
    properties = winner.get("properties")
    if losing_product is not None and losing_product != winning_product and isinstance(properties, dict):
        properties[SUPERSEDED_PRODUCT_PROPERTY] = losing_product
    return winner


def collapse_history_records(records: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Collapse records that key identically, standard processing superseding near-real-time, deterministically.

    This is a precedence rule, not a backstop, and the earlier rationale for it was wrong on every
    count: `_INSERT_FEATURES` has no `ON CONFLICT` clause, and `writer.py`'s `_ingest_resolved_batch`
    already collapses each batch by `external_id` before it inserts, so a repeated key inside one batch
    was never going to fail a chunk.

    What is real is the collision the key itself allows. `build_firms_identity` keys on
    `satellite:acqDate:acqTime:lat4dp:lon4dp` and `product` is deliberately NOT in it -- the key is
    contractually byte-identical to the TypeScript `properties->>'id'` that 148,460 stored rows depend
    on, so it cannot be widened. VIIRS_NOAA20_NRT and VIIRS_NOAA20_SP both report satellite `N20`, so
    the same acquisition delivered by both series keys to one row. Their availability windows are
    disjoint upstream today, but `products_covering_span` deliberately over-asks across a span's whole
    width and a reprocessing campaign moves that boundary, so the overlap is reachable rather than
    hypothetical. When it happens the standard-processing record must win: SP IS the reprocessing of the
    same physical detection, so keeping the NRT copy would be keeping the draft over the final. The
    outcome must not depend on `FIRMS_HISTORY_SOURCES` order, which is why the tier is compared rather
    than relied on -- and the survivor records `supersededProduct` so a row that changed series says so.

    Keys come from `build_firms_identity` and are never re-derived here; a record that cannot be keyed
    passes through untouched so `select_writes` counts it as a rejection instead of this function
    silently dropping it.
    """
    merged: dict[str, dict[str, object]] = {}
    unkeyable: list[dict[str, object]] = []
    for record in records:
        properties = record.get("properties")
        geometry = record.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        try:
            if not isinstance(properties, dict):
                raise MissingNativeKeyError("record carries no properties")
            key = build_firms_identity(properties, coordinates if isinstance(coordinates, list) else None).natural_key
        except (MissingNativeKeyError, ValueError):
            unkeyable.append(record)
            continue
        held = merged.get(key)
        if held is None:
            merged[key] = record
            continue
        # A strictly lower tier loses regardless of arrival; equal tiers keep the later arrival, which
        # is the forward job's `Map.set` semantics and the behaviour every existing test pins.
        if processing_tier(_product_of(record)) < processing_tier(_product_of(held)):
            merged[key] = _supersede(held, record)
            continue
        merged[key] = _supersede(record, held)
    return [*merged.values(), *unkeyable]


def build_firms_history_write(record: UpstreamRecord, request: FetchRequest) -> FeatureWrite | None:
    """Map one archive detection through the same builder the forward path uses, without its rolling window."""
    return build_fire_detection_write(record, resolve_firms_layer_name(), None, request.now)


def firms_archive_source() -> FunctionSource:
    """Compose the FIRMS source used for history walks, pinning its floor to the oldest product on offer.

    `earliest` is deliberately a static floor rather than the availability table's own minimum: reading
    the table needs a client, and `HistoryCapability` is consulted before any fetch happens. The table
    is still authoritative per chunk -- a day no product covers simply yields no records -- so this
    constant only decides whether the walk is refused outright.
    """
    return FunctionSource(
        source_name=FIRMS_ARCHIVE_SOURCE,
        producer=FIRMS_PRODUCER,
        channel=FIRMS_CHANNEL,
        # No maximum age: a history walk's window IS its age bound, and the write builder keeps the
        # future-skew guard. Undated records stay refused -- FIRMS always names an acquisition instant.
        freshness=FreshnessRule(max_observation_age=None, accepts_undated_records=False),
        resolve_layer_reference=resolve_firms_layer_name,
        fetch_current_records=_refuse_current_window,
        build_feature_write=build_firms_history_write,
        history=HistoryCapability(supported=True, earliest=FIRMS_ARCHIVE_EARLIEST_OBSERVATION),
        fetch_history_records=fetch_firms_history_records,
        shape="feature",
    )


async def _refuse_current_window(_request: FetchRequest) -> Sequence[UpstreamRecord]:
    """Refuse a current-window fetch: `ingest-firms` owns that path and reports partial-constellation outages."""
    raise NotImplementedError(FIRMS_ARCHIVE_CURRENT_REFUSAL)
