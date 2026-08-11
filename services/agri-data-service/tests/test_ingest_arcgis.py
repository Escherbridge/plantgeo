"""The shared ArcGIS vocabulary: the HTTP-200 error document, the transfer-limit flag, and the offset walk."""

# ruff: noqa: PLR2004

from __future__ import annotations

import pytest

from agri_data_service.ingest.arcgis import (
    ArcGisEnvelopeQuery,
    optional_number,
    optional_text,
    page_exceeded_transfer_limit,
    page_offset_walk,
    parse_feature_collection,
    require_feature_properties,
    require_polygon_geometry,
)
from agri_data_service.ingest.http import UpstreamPayloadError

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
