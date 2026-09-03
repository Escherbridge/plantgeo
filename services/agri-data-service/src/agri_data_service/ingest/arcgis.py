"""Shared ArcGIS FeatureServer mechanics: the error document behind HTTP 200, the transfer-limit flag, offset paging."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlencode

import structlog

from agri_data_service.ingest.http import (
    UpstreamError,
    UpstreamPayloadError,
    UpstreamPayloadTooLargeError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

logger = structlog.get_logger()

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

    def page_url(
        self,
        *,
        bbox: str,
        offset: int,
        max_record_count: int,
        return_geometry: bool = True,
    ) -> str:
        """Build one bounded ArcGIS GeoJSON page URL; see ingest/AGENTS.md "arcgis.py".

        `return_geometry=False` is an IDENTITY PROBE, never a data path: it is how a walk asks who
        the record at an offset is after that record's geometry proved too big to transfer at all.
        Its answer is read for a name and discarded. Emitting the parameter only when it is false
        keeps every ordinary page URL byte-identical to the one this method built before.
        """
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
        if not return_geometry:
            parameters["returnGeometry"] = "false"
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


# The adaptive walk: offset paging plus an answer for a page the byte cap refuses. What forced it,
# the four load-bearing properties and the per-run byte budget are in ingest/AGENTS.md, "arcgis.py:
# page-size halving, and the record that fits in no page".

# One record is the floor: below it there is no page left to shrink, and the record itself is what
# does not fit.
MIN_PAGE_RECORD_COUNT: Final = 1

AdaptiveWalkStop = Literal["exhausted", "record_ceiling", "byte_budget", "page_ceiling"]

# A walk's fetch callback takes (offset, record_count) and answers (records, clipped, bytes read).
type AdaptivePageFetch[RecordT] = Callable[[int, int], Awaitable[tuple[list[RecordT], bool, int]]]

# Given an offset whose single record could not be transferred, name it -- or return None, which is
# a perfectly acceptable answer and never a failure.
type RecordIdentityProbe = Callable[[int], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class OversizedSourceRecord:
    """One record the upstream cannot deliver inside the byte bound even alone: its offset and its size."""

    offset: int
    identity: str | None
    declared_bytes: int | None
    limit_bytes: int

    def describe(self) -> str:
        """One operator-facing clause: which record, at which offset, of what declared size."""
        named = self.identity if self.identity is not None else "unidentified"
        size = "an undeclared size" if self.declared_bytes is None else f"{self.declared_bytes} bytes"
        return f"{named} at offset {self.offset} ({size})"


@dataclass(frozen=True, slots=True)
class AdaptiveWalkOutcome:
    """How one adaptive walk landed. Carries no record type, so it needs no type parameter to travel."""

    truncated: bool
    oversized: tuple[OversizedSourceRecord, ...]
    bytes_read: int
    final_record_count: int
    stop: AdaptiveWalkStop


@dataclass(frozen=True, slots=True)
class _PageAttempt:
    """The non-record half of one page fetch: what size worked, what it cost, and why it refused."""

    record_count: int
    bytes_read: int
    exceeded_transfer_limit: bool
    refusal: UpstreamPayloadTooLargeError | None


async def _fetch_page_within_byte_bound[RecordT](
    fetch_page: AdaptivePageFetch[RecordT],
    *,
    offset: int,
    record_count: int,
) -> tuple[list[RecordT], _PageAttempt]:
    """Fetch one page, halving the record count on an oversized answer until it fits or holds one record.

    Only the byte cap is handled here. Every other failure -- a throttle, a 5xx, a schema change --
    propagates untouched, because shrinking a page cannot fix any of them and pretending otherwise
    would turn a real outage into a slow, quiet walk down to one record at a time.
    """
    attempted = record_count
    spent = 0
    while True:
        try:
            page, exceeded_transfer_limit, page_bytes = await fetch_page(offset, attempted)
        except UpstreamPayloadTooLargeError as too_large:
            spent += too_large.transferred_bytes
            if attempted <= MIN_PAGE_RECORD_COUNT:
                return [], _PageAttempt(
                    record_count=attempted,
                    bytes_read=spent,
                    exceeded_transfer_limit=False,
                    refusal=too_large,
                )
            attempted = max(MIN_PAGE_RECORD_COUNT, attempted // 2)
            continue
        return page, _PageAttempt(
            record_count=attempted,
            bytes_read=spent + page_bytes,
            exceeded_transfer_limit=exceeded_transfer_limit,
            refusal=None,
        )


async def _identify_refused_record(offset: int, identify_record: RecordIdentityProbe | None) -> str | None:
    """Name the record at `offset` through the caller's probe; a probe that fails names nothing and fails nothing."""
    if identify_record is None:
        return None
    try:
        return await identify_record(offset)
    except UpstreamError:
        # The probe is a courtesy for the operator reading the refusal, never a second chance to
        # fail the run: the record is being skipped either way, and skipping it anonymously is
        # strictly better than turning a governed refusal into an outage.
        return None


async def adaptive_page_offset_walk[RecordT](  # noqa: PLR0913 - one keyword per bound this walk is held to
    fetch_page: AdaptivePageFetch[RecordT],
    *,
    max_record_count: int,
    max_pages: int,
    record_ceiling: int,
    byte_budget: int,
    identify_record: RecordIdentityProbe | None = None,
) -> tuple[list[RecordT], AdaptiveWalkOutcome]:
    """Walk `resultOffset` pages under a byte cap, shrinking the page rather than failing the lane.

    `max_pages` bounds REQUESTS THAT RETURNED A PAGE OR REFUSED A RECORD, exactly as before; the
    halving re-asks inside one iteration and cannot spend the circuit breaker faster than the
    ladder's own depth. `byte_budget` is the new ceiling and the one `page_offset_walk` never had:
    the per-request cap bounds each page, and nothing bounded a run of two hundred of them.
    """
    records: list[RecordT] = []
    oversized: list[OversizedSourceRecord] = []
    offset = 0
    record_count = max_record_count
    bytes_read = 0
    stop: AdaptiveWalkStop = "page_ceiling"

    for _ in range(max_pages):
        page, attempt = await _fetch_page_within_byte_bound(fetch_page, offset=offset, record_count=record_count)
        bytes_read += attempt.bytes_read
        if attempt.refusal is not None:
            refused = OversizedSourceRecord(
                offset=offset,
                identity=await _identify_refused_record(offset, identify_record),
                declared_bytes=attempt.refusal.size_bytes,
                limit_bytes=attempt.refusal.limit_bytes,
            )
            logger.warning(
                "arcgis_oversized_record_skipped",
                offset=refused.offset,
                identity=refused.identity,
                declared_bytes=refused.declared_bytes,
                limit_bytes=refused.limit_bytes,
            )
            oversized.append(refused)
            offset += MIN_PAGE_RECORD_COUNT
            continue
        # The shrink sticks; the walk never asks for a bigger page than the last one that worked.
        record_count = attempt.record_count
        records.extend(page)
        if not page or not attempt.exceeded_transfer_limit:
            stop = "exhausted"
            break
        offset += len(page)
        if len(records) >= record_ceiling:
            stop = "record_ceiling"
            break
        if bytes_read >= byte_budget:
            stop = "byte_budget"
            break

    return records, AdaptiveWalkOutcome(
        truncated=stop != "exhausted",
        oversized=tuple(oversized),
        bytes_read=bytes_read,
        final_record_count=record_count,
        stop=stop,
    )
