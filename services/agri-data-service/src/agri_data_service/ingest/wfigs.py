"""WFIGS interagency fire-perimeter ingestion: the ArcGIS FeatureServer adapter and its retrying, bounded, paged job."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.arcgis import (
    ArcGisEnvelopeQuery,
    adaptive_page_offset_walk,
    optional_number,
    optional_text,
    parse_feature_collection,
    require_feature_properties,
    require_polygon_geometry,
)
from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamPayloadError,
    fetch_bounded_json,
    fetch_bounded_json_sized,
    upstream_client,
)
from agri_data_service.ingest.identity import (
    MissingNativeKeyError,
    build_fire_perimeter_identity,
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
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    import httpx

    from agri_data_service.ingest.arcgis import AdaptiveWalkOutcome, OversizedSourceRecord
    from agri_data_service.ingest.writer import FeatureWriter

WFIGS_SOURCE: Final = "wfigs-fire-perimeters"
WFIGS_PROPERTY_SOURCE: Final = "WFIGS Interagency Fire Perimeters"

FIRE_PERIMETERS_LAYER: Final = LayerBinding(
    variable="FIRE_PERIMETERS_LAYER_ID",
    default="fire-perimeters",
    channel="layer:fire-perimeters",
)
WFIGS_CHANNEL: Final = FIRE_PERIMETERS_LAYER.channel
FIRE_PERIMETERS_LAYER_VARIABLE: Final = FIRE_PERIMETERS_LAYER.variable
DEFAULT_FIRE_PERIMETERS_LAYER_NAME: Final = FIRE_PERIMETERS_LAYER.default

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

# ONE RUN'S TOTAL TRANSFER, which nothing bounded before. `WFIGS_BOUNDS.max_bytes` caps each
# REQUEST; with MAX_PAGES at 200 a pathological feed could still pull 200 x 16 MiB = 3.2 GB through
# a container sized for none of it, and the adaptive walk below makes that MORE reachable rather
# than less, because it now keeps going where it used to raise. 128 MiB is eight full pages at the
# per-request cap and roughly twelve times the 10,950,562 bytes the whole PNW extent measured at
# `geometryPrecision=5` on 2026-08-08 -- ample for a fire season far denser than any measured, and
# still a real ceiling. Hitting it reports `truncated=True`, exactly like the record ceiling.
WFIGS_TOTAL_BYTE_BUDGET: Final = 128 * 1024 * 1024

# How many refused records the run outcome names before it stops listing and only counts. The whole
# list is in the structured log (`arcgis_oversized_record_skipped`, one line each); this bound is on
# the single-line `reason` string an operator reads first.
MAX_NAMED_OVERSIZED_RECORDS: Final = 5

# The retry ladder itself is shared (widened 2026-08-10 after the throttle incident); this source
# only names its own budget. See ingest/AGENTS.md "upstream_retry.py".
WFIGS_RETRY: Final = UpstreamRetryPolicy(
    event="wfigs_upstream_retry",
    exhausted_message="WFIGS retry loop ended without a response",
)
MAX_ATTEMPTS: Final = WFIGS_RETRY.ladder.max_attempts
RETRY_BASE_DELAY_SECONDS: Final = WFIGS_RETRY.ladder.base_delay_seconds
RETRY_MAX_DELAY_SECONDS: Final = WFIGS_RETRY.ladder.max_delay_seconds
RETRY_WALL_CLOCK_CEILING_SECONDS: Final = WFIGS_RETRY.ladder.wall_clock_ceiling_seconds

# WFIGS' IRWIN-integrated interagency perimeter record begins with the 2020 fire year, which is what
# the historical services NIFC publishes beside `_Current` cover; documentation-derived and NOT
# live-probed as of 2026-08-10. See ingest/AGENTS.md "history declarations, wave 2026-08-10".
WFIGS_PERIMETER_HISTORY_EARLIEST: Final = datetime(2020, 1, 1, tzinfo=UTC)
WFIGS_HISTORY_CAPABILITY: Final = HistoryCapability(supported=True, earliest=WFIGS_PERIMETER_HISTORY_EARLIEST)

# JavaScript's Date range; beyond it `new Date(ms)` is Invalid Date and the TypeScript stored null.
MAX_JAVASCRIPT_EPOCH_MILLISECONDS: Final = 8.64e15
EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)

CRITICAL_CONTAINMENT: Final = 25
HIGH_CONTAINMENT: Final = 50
MODERATE_CONTAINMENT: Final = 75
MIN_CONTAINMENT: Final = 0
MAX_CONTAINMENT: Final = 100

WFIGS_ERROR_PREFIX: Final = "WFIGS API error"
UNEXPECTED_SHAPE_REASON: Final = "WFIGS API returned an unexpected feature collection shape"

# `order_by_fields` is deliberately unset: ArcGIS paging without a deterministic sort may repeat or
# skip rows, and turning it on here changes which perimeters survive a bitten record cap on a live
# feed. See ingest/AGENTS.md "arcgis.py" for the un-enabled upgrades and what would justify them.
WFIGS_PAGE_QUERY: Final = ArcGisEnvelopeQuery(
    endpoint=WFIGS_QUERY_URL,
    out_fields=WFIGS_OUT_FIELDS,
    geometry_precision=WFIGS_GEOMETRY_PRECISION,
)


def resolve_fire_perimeters_layer_name() -> str:
    """Read FIRE_PERIMETERS_LAYER_ID at call time so a cron environment change needs no restart."""
    return FIRE_PERIMETERS_LAYER.resolve()


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


def build_query_url(
    bbox: str,
    offset: int = 0,
    max_record_count: int = MAX_RECORD_COUNT,
    *,
    return_geometry: bool = True,
) -> str:
    """Build the bounded ArcGIS GeoJSON query URL for one page of one bbox."""
    return WFIGS_PAGE_QUERY.page_url(
        bbox=bbox,
        offset=offset,
        max_record_count=max_record_count,
        return_geometry=return_geometry,
    )


@dataclass(frozen=True, slots=True)
class WfigsPerimeterPage:
    """One page of fire perimeters plus whether the upstream said more of them remain."""

    perimeters: list[dict[str, object]]
    exceeded_transfer_limit: bool
    # What this page cost on the wire. Defaulted so every hand-built page in a test stays valid; the
    # adaptive walk is the only caller that supplies a real number, to hold one run inside
    # WFIGS_TOTAL_BYTE_BUDGET rather than only each request inside WFIGS_BOUNDS.
    byte_count: int = 0


def parse_perimeter_collection(payload: object, *, byte_count: int = 0) -> WfigsPerimeterPage:
    """Parse an ArcGIS GeoJSON answer into one page of perimeter records, rejecting its HTTP-200 error payload."""
    collection = parse_feature_collection(
        payload,
        error_prefix=WFIGS_ERROR_PREFIX,
        unexpected_shape_reason=UNEXPECTED_SHAPE_REASON,
    )
    perimeters: list[dict[str, object]] = []
    for feature in collection.features:
        properties = require_feature_properties(feature, unexpected_shape_reason=UNEXPECTED_SHAPE_REASON)
        fire_identifier = properties.get("attr_UniqueFireIdentifier")
        if not isinstance(fire_identifier, str) or not fire_identifier:
            raise UpstreamPayloadError(UNEXPECTED_SHAPE_REASON)
        perimeters.append(
            {
                "uniqueFireIdentifier": fire_identifier,
                "irwinId": optional_text(properties, "attr_IrwinID"),
                "incidentName": optional_text(properties, "poly_IncidentName"),
                "fireDiscoveryDateTime": epoch_milliseconds_to_iso(properties.get("attr_FireDiscoveryDateTime")),
                "polygonDateTime": epoch_milliseconds_to_iso(properties.get("poly_PolygonDateTime")),
                "gisAcres": optional_number(properties, "poly_GISAcres"),
                "fireCause": optional_text(properties, "attr_FireCause"),
                "incidentTypeCategory": optional_text(properties, "attr_IncidentTypeCategory"),
                "pooState": optional_text(properties, "attr_POOState"),
                "percentContained": optional_number(properties, "attr_PercentContained"),
                "geometry": require_polygon_geometry(
                    feature.get("geometry"), unexpected_shape_reason=UNEXPECTED_SHAPE_REASON
                ),
            }
        )
    return WfigsPerimeterPage(
        perimeters=perimeters,
        exceeded_transfer_limit=collection.exceeded_transfer_limit,
        byte_count=byte_count,
    )


async def fetch_fire_perimeters_page(  # noqa: PLR0913 - the page size joins the existing clock seam
    client: httpx.AsyncClient,
    bbox: str,
    offset: int = 0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    *,
    record_count: int = MAX_RECORD_COUNT,
) -> WfigsPerimeterPage:
    """Fetch one bounded page of WFIGS perimeters, retrying only a busy or transient upstream.

    `record_count` is keyword-only and defaulted, so the positional `(client, bbox, offset, sleep,
    monotonic)` call shape every existing caller and test uses is untouched.
    """
    url = build_query_url(bbox, offset, record_count)

    async def attempt_once() -> WfigsPerimeterPage:
        answer = await fetch_bounded_json_sized(client, url, WFIGS_BOUNDS)
        return parse_perimeter_collection(answer.payload, byte_count=answer.byte_count)

    return await retry_upstream(
        attempt_once,
        WFIGS_RETRY,
        context={"offset": offset, "record_count": record_count},
        sleep=sleep,
        monotonic=monotonic,
    )


async def probe_perimeter_identity(client: httpx.AsyncClient, bbox: str, offset: int) -> str | None:
    """Name the one perimeter at `offset` by asking for it WITHOUT its geometry.

    The geometry is what made this record undeliverable, so dropping it is what makes the record
    nameable at all -- a `returnGeometry=false` answer for one record is attributes only, kilobytes
    rather than megabytes. Nothing here is ever written: the answer is read for
    `attr_UniqueFireIdentifier` and discarded, so this is a diagnostic, not a lower-fidelity data
    path, and it is emphatically NOT the `geometryPrecision` reduction ingest/AGENTS.md refuses to
    pull silently.

    No retry ladder, deliberately: one extra request to put a name on a record already being skipped
    is the whole budget this is worth, and `arcgis.py::_identify_refused_record` turns any
    `UpstreamError` raised here back into an anonymous refusal rather than a failed run.
    """
    url = build_query_url(bbox, offset, 1, return_geometry=False)
    collection = parse_feature_collection(
        await fetch_bounded_json(client, url, WFIGS_BOUNDS),
        error_prefix=WFIGS_ERROR_PREFIX,
        unexpected_shape_reason=UNEXPECTED_SHAPE_REASON,
    )
    if not collection.features:
        return None
    properties = require_feature_properties(collection.features[0], unexpected_shape_reason=UNEXPECTED_SHAPE_REASON)
    return optional_text(properties, "attr_UniqueFireIdentifier")


async def fetch_fire_perimeters_walk(
    client: httpx.AsyncClient,
    bbox: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, object]], AdaptiveWalkOutcome]:
    """Page bounded WFIGS perimeters adaptively: shrink a page the byte cap refuses, skip a record it cannot hold.

    This is the whole repair for the `postgres-fire-perimeters` retry backoff. The old walk asked for
    100 records a page and had one answer to `UpstreamPayloadError` -- fail the lane -- which a retry
    could never clear, because the page size was the thing that was wrong. See ingest/AGENTS.md
    "wfigs.py: the page that cannot be delivered, and the record that cannot be delivered either".
    """

    async def fetch_page(offset: int, record_count: int) -> tuple[list[dict[str, object]], bool, int]:
        page = await fetch_fire_perimeters_page(client, bbox, offset, sleep, monotonic, record_count=record_count)
        return page.perimeters, page.exceeded_transfer_limit, page.byte_count

    async def identify(offset: int) -> str | None:
        return await probe_perimeter_identity(client, bbox, offset)

    return await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=MAX_RECORD_COUNT,
        max_pages=MAX_PAGES,
        record_ceiling=resolve_max_source_records(),
        byte_budget=WFIGS_TOTAL_BYTE_BUDGET,
        identify_record=identify,
    )


async def fetch_fire_perimeters(
    client: httpx.AsyncClient,
    bbox: str,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, object]], bool]:
    """Page bounded WFIGS perimeters until the upstream stops clipping, reporting whether more were left behind.

    The unchanged two-tuple contract `pipeline/validation/fire_perimeters.py` calls. It runs the
    adaptive walk underneath and keeps only "were records left behind" -- and a governed
    oversized-record SKIP is exactly that: the caller compares perimeter SETS, and a skipped record
    is missing from this one, so it reports as truncated for the same reason
    `run_fire_perimeters_ingestion_job` counts it. Only the STRUCTURED detail is dropped; the named
    refusal is still in the walk's own structured log.
    """
    records, outcome = await fetch_fire_perimeters_walk(client, bbox, sleep, monotonic)
    return records, outcome.truncated or bool(outcome.oversized)


def oversized_refusal_reason(oversized: Sequence[OversizedSourceRecord]) -> str | None:
    """State, in one operator-facing line, which perimeters WFIGS could not deliver at all."""
    if not oversized:
        return None
    named = ", ".join(record.describe() for record in oversized[:MAX_NAMED_OVERSIZED_RECORDS])
    unnamed = len(oversized) - min(len(oversized), MAX_NAMED_OVERSIZED_RECORDS)
    tail = f", and {unnamed} more" if unnamed else ""
    return (
        f"skipped {len(oversized)} source record(s) WFIGS could not deliver inside the "
        f"{WFIGS_BOUNDS.max_bytes}-byte response bound: {named}{tail}"
    )


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
            perimeters, walk = await fetch_fire_perimeters_walk(owned_client, area)
    else:
        perimeters, walk = await fetch_fire_perimeters_walk(client, area)

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
        # An oversized-record refusal is a real, named loss, so it reports as truncated alongside the
        # two truncation causes that were already here. `truncated` answers "are you seeing all of
        # it", and after a skip the answer is no -- for a reason `reason` states by name.
        truncated=walk.truncated or bool(walk.oversized) or len(perimeters) > len(selected),
        reason=oversized_refusal_reason(walk.oversized),
        details={
            "rejected": len(selected) - len(writes),
            "oversized_records": len(walk.oversized),
            "bytes_read": walk.bytes_read,
        },
    )
