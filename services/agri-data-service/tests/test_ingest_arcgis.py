"""The shared ArcGIS vocabulary: the HTTP-200 error document, the transfer-limit flag, and the offset walk."""

# ruff: noqa: PLR2004

from __future__ import annotations

import pytest

from agri_data_service.ingest.arcgis import (
    ArcGisEnvelopeQuery,
    adaptive_page_offset_walk,
    optional_number,
    optional_text,
    page_exceeded_transfer_limit,
    page_offset_walk,
    parse_feature_collection,
    require_feature_properties,
    require_polygon_geometry,
)
from agri_data_service.ingest.http import UpstreamPayloadError, UpstreamPayloadTooLargeError, UpstreamTransportError

BYTE_LIMIT = 16 * 1024 * 1024
UNBOUNDED_BYTES = 1 << 40

ENDPOINT = "https://example.invalid/FeatureServer/0/query"
SHAPE_REASON = "test API returned an unexpected feature collection shape"
SQUARE_RING = [[-123.0, 44.0], [-123.0, 44.1], [-122.9, 44.1], [-122.9, 44.0], [-123.0, 44.0]]

PLAIN_QUERY = ArcGisEnvelopeQuery(endpoint=ENDPOINT, out_fields="A,B")
DETERMINISTIC_QUERY = ArcGisEnvelopeQuery(
    endpoint=ENDPOINT,
    out_fields="A,B",
    order_by_fields="A",
    geometry_precision=5,
)


def _feature(**properties: object) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [SQUARE_RING]},
        "properties": dict(properties),
    }


def _collection(*features: dict[str, object], **extra: object) -> dict[str, object]:
    return {"type": "FeatureCollection", "features": list(features), **extra}


def test_the_page_url_carries_the_shared_envelope_parameters() -> None:
    url = PLAIN_QUERY.page_url(bbox="-125,42,-111,49", offset=0, max_record_count=100)
    assert url.startswith(f"{ENDPOINT}?")
    assert "where=1%3D1" in url
    assert "geometryType=esriGeometryEnvelope" in url
    assert "spatialRel=esriSpatialRelIntersects" in url
    assert "inSR=4326" in url
    assert "outSR=4326" in url
    assert "resultOffset=0" in url
    assert "resultRecordCount=100" in url
    assert "f=geojson" in url


def test_the_determinism_knobs_are_omitted_unless_a_source_asks_for_them() -> None:
    plain = PLAIN_QUERY.page_url(bbox="-125,42,-111,49", offset=50, max_record_count=100)
    assert "orderByFields" not in plain
    assert "geometryPrecision" not in plain
    assert "resultOffset=50" in plain

    deterministic = DETERMINISTIC_QUERY.page_url(bbox="-125,42,-111,49", offset=0, max_record_count=100)
    assert "orderByFields=A" in deterministic
    assert "geometryPrecision=5" in deterministic


def test_an_error_document_answered_with_http_200_is_refused_under_the_callers_prefix() -> None:
    with pytest.raises(UpstreamPayloadError, match="test API error: Too many requests"):
        parse_feature_collection(
            {"error": {"code": 429, "message": "Too many requests"}},
            error_prefix="test API error",
            unexpected_shape_reason=SHAPE_REASON,
        )


def test_an_error_document_with_only_details_still_names_them() -> None:
    with pytest.raises(UpstreamPayloadError, match="test API error: first; second"):
        parse_feature_collection(
            {"error": {"code": 500, "details": ["first", "second"]}},
            error_prefix="test API error",
            unexpected_shape_reason=SHAPE_REASON,
        )


@pytest.mark.parametrize(
    "payload",
    [
        "not a mapping",
        {"type": "NotACollection", "features": []},
        {"type": "FeatureCollection", "features": "not a list"},
        {"type": "FeatureCollection", "features": [{"type": "NotAFeature"}]},
        {"type": "FeatureCollection", "features": ["not a mapping"]},
    ],
)
def test_an_unexpected_collection_shape_is_refused(payload: object) -> None:
    with pytest.raises(UpstreamPayloadError, match="unexpected feature collection"):
        parse_feature_collection(payload, error_prefix="test API error", unexpected_shape_reason=SHAPE_REASON)


def test_a_valid_collection_yields_its_features_and_the_unclipped_flag() -> None:
    collection = parse_feature_collection(
        _collection(_feature(name="a"), _feature(name="b")),
        error_prefix="test API error",
        unexpected_shape_reason=SHAPE_REASON,
    )
    assert len(collection.features) == 2
    assert collection.exceeded_transfer_limit is False


def test_the_transfer_limit_flag_is_read_from_both_envelopes_and_only_when_literally_true() -> None:
    assert page_exceeded_transfer_limit({"properties": {"exceededTransferLimit": True}}) is True
    assert page_exceeded_transfer_limit({"exceededTransferLimit": True}) is True
    assert page_exceeded_transfer_limit({"properties": {"exceededTransferLimit": False}}) is False
    assert page_exceeded_transfer_limit({}) is False
    # Strictly `is True`: a truthy stand-in is not the flag ArcGIS documents, and reading one as the
    # flag would keep paging an upstream that never said it clipped anything.
    assert page_exceeded_transfer_limit({"exceededTransferLimit": 1}) is False
    assert page_exceeded_transfer_limit({"exceededTransferLimit": "true"}) is False


def test_a_feature_publishing_no_properties_object_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="unexpected feature collection"):
        require_feature_properties({"type": "Feature"}, unexpected_shape_reason=SHAPE_REASON)
    assert require_feature_properties(_feature(name="a"), unexpected_shape_reason=SHAPE_REASON)["name"] == "a"


def test_only_a_polygon_or_multipolygon_of_finite_positions_is_accepted() -> None:
    polygon = {"type": "Polygon", "coordinates": [SQUARE_RING]}
    multipolygon = {"type": "MultiPolygon", "coordinates": [[SQUARE_RING]]}
    assert require_polygon_geometry(polygon, unexpected_shape_reason=SHAPE_REASON) == polygon
    assert require_polygon_geometry(multipolygon, unexpected_shape_reason=SHAPE_REASON) == multipolygon


@pytest.mark.parametrize(
    "geometry",
    [
        None,
        {"type": "Point", "coordinates": [-123.0, 44.0]},
        {"type": "Polygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[-123.0]]]},
        {"type": "Polygon", "coordinates": [[[float("inf"), 44.0]]]},
        {"type": "MultiPolygon", "coordinates": [SQUARE_RING]},
    ],
)
def test_a_non_polygon_or_malformed_ring_is_refused(geometry: object) -> None:
    with pytest.raises(UpstreamPayloadError, match="unexpected feature collection"):
        require_polygon_geometry(geometry, unexpected_shape_reason=SHAPE_REASON)


def test_optional_attributes_normalise_an_absent_or_unreal_value_to_none() -> None:
    properties = {"text": "value", "number": 12.5, "wrong": 7, "unreal": float("nan"), "flag": True}
    assert optional_text(properties, "text") == "value"
    assert optional_text(properties, "wrong") is None
    assert optional_text(properties, "absent") is None
    assert optional_number(properties, "number") == 12.5
    assert optional_number(properties, "wrong") == 7.0
    assert optional_number(properties, "unreal") is None
    assert optional_number(properties, "flag") is None
    assert optional_number(properties, "absent") is None


async def test_the_walk_stops_as_soon_as_the_service_stops_clipping() -> None:
    offsets: list[int] = []

    async def fetch_page(offset: int) -> tuple[list[str], bool]:
        offsets.append(offset)
        return ([f"row-{offset}"], offset == 0)

    records, more_remaining = await page_offset_walk(fetch_page, max_pages=20, record_ceiling=100)
    assert offsets == [0, 1]
    assert records == ["row-0", "row-1"]
    assert more_remaining is False


async def test_an_empty_page_ends_the_walk_and_reports_nothing_left_behind() -> None:
    async def fetch_page(_offset: int) -> tuple[list[str], bool]:
        return ([], True)

    assert await page_offset_walk(fetch_page, max_pages=20, record_ceiling=100) == ([], False)


async def test_the_record_ceiling_stops_the_walk_and_reports_the_partial() -> None:
    pages: list[int] = []

    async def fetch_page(offset: int) -> tuple[list[str], bool]:
        pages.append(offset)
        return ([f"row-{offset}"], True)

    records, more_remaining = await page_offset_walk(fetch_page, max_pages=20, record_ceiling=2)
    assert len(pages) == 2
    assert records == ["row-0", "row-1"]
    assert more_remaining is True


async def test_the_page_circuit_breaker_stops_a_service_that_clips_forever() -> None:
    pages: list[int] = []

    async def fetch_page(offset: int) -> tuple[list[str], bool]:
        pages.append(offset)
        return ([f"row-{offset}"], True)

    records, more_remaining = await page_offset_walk(fetch_page, max_pages=3, record_ceiling=1_000)
    assert len(pages) == 3
    assert len(records) == 3
    assert more_remaining is True


# --- adaptive_page_offset_walk: the page the byte cap refuses, and the record that fits in no page ---
#
# The production shape (2026-09-02): `postgres-fire-perimeters` entered retry backoff on
# `UpstreamPayloadError: upstream response exceeded the byte limit`. A retry could never clear it,
# because the PAGE SIZE was what was wrong and a retry asks for the same page again.


def _too_large(declared: int = 20_000_000) -> UpstreamPayloadTooLargeError:
    """The refusal `http.py` raises for a body whose declared length is over the cap."""
    return UpstreamPayloadTooLargeError(limit_bytes=BYTE_LIMIT, declared_bytes=declared)


async def test_a_refused_page_is_halved_and_re_asked_at_the_same_offset() -> None:
    """100 -> 50 is the whole repair, and the offset must not move while the size is being found."""
    asked: list[tuple[int, int]] = []

    async def fetch_page(offset: int, record_count: int) -> tuple[list[str], bool, int]:
        asked.append((offset, record_count))
        if record_count > 50:
            raise _too_large()
        return ([f"row-{offset + index}" for index in range(record_count)], False, 1_000)

    records, outcome = await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=100,
        max_pages=20,
        record_ceiling=1_000,
        byte_budget=UNBOUNDED_BYTES,
    )

    assert asked == [(0, 100), (0, 50)]
    assert len(records) == 50
    assert outcome.truncated is False
    assert outcome.oversized == ()
    assert outcome.final_record_count == 50


async def test_the_shrink_sticks_so_the_walk_does_not_re_discover_the_size_every_page() -> None:
    asked: list[tuple[int, int]] = []

    async def fetch_page(offset: int, record_count: int) -> tuple[list[str], bool, int]:
        asked.append((offset, record_count))
        if record_count > 25:
            raise _too_large()
        return ([f"row-{offset + index}" for index in range(record_count)], offset == 0, 1_000)

    records, outcome = await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=100,
        max_pages=20,
        record_ceiling=1_000,
        byte_budget=UNBOUNDED_BYTES,
    )

    # 100 -> 50 -> 25 on the first page; the SECOND page opens at 25 rather than climbing back.
    assert asked == [(0, 100), (0, 50), (0, 25), (25, 25)]
    assert len(records) == 50
    assert outcome.truncated is False


async def test_a_record_that_overflows_alone_is_a_governed_refusal_and_the_walk_continues() -> None:
    """The lane must never die on one pathological polygon; it must name it and step over it."""
    asked: list[tuple[int, int]] = []
    probed: list[int] = []

    async def fetch_page(offset: int, record_count: int) -> tuple[list[str], bool, int]:
        asked.append((offset, record_count))
        if offset == 0 and record_count > 1:
            raise _too_large()
        if offset == 0:
            raise _too_large(declared=31_000_000)
        return ([f"row-{offset}"], False, 1_000)

    async def identify(offset: int) -> str | None:
        probed.append(offset)
        return "2026-ID1AX-000618"

    records, outcome = await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=4,
        max_pages=20,
        record_ceiling=1_000,
        byte_budget=UNBOUNDED_BYTES,
        identify_record=identify,
    )

    # 4 -> 2 -> 1 at offset 0, then the refusal, then offset 1 opens back at 4: one oversized RECORD
    # is evidence about that record, not about the feed's density.
    assert asked == [(0, 4), (0, 2), (0, 1), (1, 4)]
    assert probed == [0]
    assert records == ["row-1"]
    assert len(outcome.oversized) == 1
    refused = outcome.oversized[0]
    assert refused.offset == 0
    assert refused.identity == "2026-ID1AX-000618"
    assert refused.declared_bytes == 31_000_000
    assert refused.limit_bytes == BYTE_LIMIT
    assert refused.describe() == "2026-ID1AX-000618 at offset 0 (31000000 bytes)"


async def test_a_failing_identity_probe_leaves_the_record_anonymous_rather_than_failing_the_walk() -> None:
    async def fetch_page(offset: int, _record_count: int) -> tuple[list[str], bool, int]:
        if offset == 0:
            raise _too_large()
        return ([f"row-{offset}"], False, 1_000)

    async def identify(_offset: int) -> str | None:
        raise UpstreamTransportError("upstream request failed (ConnectError)")

    records, outcome = await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=1,
        max_pages=20,
        record_ceiling=1_000,
        byte_budget=UNBOUNDED_BYTES,
        identify_record=identify,
    )

    assert records == ["row-1"]
    assert outcome.oversized[0].identity is None
    assert outcome.oversized[0].describe() == "unidentified at offset 0 (20000000 bytes)"


async def test_the_byte_budget_stops_a_walk_no_page_ceiling_would_have() -> None:
    """The bound `page_offset_walk` never had: each page was capped, a run of two hundred was not."""

    async def fetch_page(offset: int, record_count: int) -> tuple[list[str], bool, int]:
        return ([f"row-{offset + index}" for index in range(record_count)], True, 400)

    records, outcome = await adaptive_page_offset_walk(
        fetch_page,
        max_record_count=2,
        max_pages=200,
        record_ceiling=10_000,
        byte_budget=1_000,
    )

    assert outcome.bytes_read == 1_200
    assert outcome.stop == "byte_budget"
    assert outcome.truncated is True
    assert len(records) == 6


async def test_the_record_and_page_ceilings_still_bound_the_adaptive_walk() -> None:
    async def fetch_page(offset: int, record_count: int) -> tuple[list[str], bool, int]:
        return ([f"row-{offset + index}" for index in range(record_count)], True, 1)

    _, by_records = await adaptive_page_offset_walk(
        fetch_page, max_record_count=2, max_pages=200, record_ceiling=4, byte_budget=UNBOUNDED_BYTES
    )
    assert by_records.stop == "record_ceiling"

    _, by_pages = await adaptive_page_offset_walk(
        fetch_page, max_record_count=2, max_pages=3, record_ceiling=10_000, byte_budget=UNBOUNDED_BYTES
    )
    assert by_pages.stop == "page_ceiling"
    assert by_pages.truncated is True


async def test_only_the_byte_refusal_is_absorbed_and_every_other_failure_still_raises() -> None:
    """Shrinking a page cannot fix a throttle, a 5xx or a schema change, so it must not try."""

    async def fetch_page(_offset: int, _record_count: int) -> tuple[list[str], bool, int]:
        raise UpstreamPayloadError("ArcGIS API error: Too many requests")

    with pytest.raises(UpstreamPayloadError, match="Too many requests"):
        await adaptive_page_offset_walk(
            fetch_page,
            max_record_count=100,
            max_pages=20,
            record_ceiling=1_000,
            byte_budget=UNBOUNDED_BYTES,
        )


def test_the_identity_probe_url_drops_the_geometry_and_nothing_else() -> None:
    """`returnGeometry=false` is a diagnostic, and it is NOT the geometryPrecision reduction."""
    probe = DETERMINISTIC_QUERY.page_url(bbox="-125,42,-111,49", offset=7, max_record_count=1, return_geometry=False)
    assert "returnGeometry=false" in probe
    assert "resultOffset=7" in probe
    assert "resultRecordCount=1" in probe
    assert "geometryPrecision=5" in probe

    ordinary = DETERMINISTIC_QUERY.page_url(bbox="-125,42,-111,49", offset=7, max_record_count=1)
    assert "returnGeometry" not in ordinary
