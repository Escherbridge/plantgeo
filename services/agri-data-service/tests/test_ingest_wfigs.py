"""WFIGS ingestion: the null-severity contract, epoch timestamp parity, retry policy, and pinned production keys."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest

from agri_data_service.ingest.http import UpstreamHttpError, UpstreamPayloadError
from agri_data_service.ingest.wfigs import (
    WFIGS_SOURCE,
    build_perimeter_write,
    build_query_url,
    epoch_milliseconds_to_iso,
    fetch_fire_perimeters,
    is_retryable_failure,
    parse_perimeter_collection,
    perimeter_severity,
    run_fire_perimeters_ingestion_job,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.ingest.writer import FeatureWrite

# Captured 2026-08-03 read-only from production `geo.features` on the `fire-perimeters` layer.
RECORDED_FIRE_IDENTIFIER = "2026-ID1AX-000618"
POLYGON_EPOCH_MILLISECONDS = 1_784_087_760_000  # 2026-07-15T03:56:00.000Z

SQUARE_RING = [[-113.0, 47.0], [-113.0, 47.1], [-112.9, 47.1], [-112.9, 47.0], [-113.0, 47.0]]


class RecordingWriter:
    """A feature writer that records what a job handed it, so a job test needs no database."""

    def __init__(self) -> None:
        self.writes: list[FeatureWrite] = []

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int:
        self.writes = list(writes)
        return len(self.writes)


async def _no_sleep(_delay: float) -> None:
    """Skip the backoff wait so a retry test runs at full speed."""


def _collection(**property_overrides: object) -> dict[str, object]:
    properties: dict[str, object] = {
        "attr_UniqueFireIdentifier": RECORDED_FIRE_IDENTIFIER,
        "attr_IrwinID": "irwin-1",
        "poly_IncidentName": "Test Fire",
        "attr_FireDiscoveryDateTime": 1_783_000_000_000,
        "poly_PolygonDateTime": POLYGON_EPOCH_MILLISECONDS,
        "poly_GISAcres": 1234.5,
        "attr_FireCause": "Natural",
        "attr_IncidentTypeCategory": "WF",
        "attr_POOState": "US-ID",
        "attr_PercentContained": 10.0,
    }
    properties.update(property_overrides)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [SQUARE_RING]},
                "properties": properties,
            }
        ],
    }


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "FIRE_PERIMETERS_LAYER_ID"):
        monkeypatch.delenv(variable, raising=False)


def test_no_reported_containment_grades_as_unknown_rather_than_the_lowest_severity() -> None:
    # `src/lib/map/layers.ts:71` renders against this contract; "low" here would paint a lie.
    assert perimeter_severity(None) is None
    assert perimeter_severity("50") is None
    assert perimeter_severity(True) is None
    assert perimeter_severity(float("nan")) is None


def test_containment_grades_across_the_documented_bands() -> None:
    assert perimeter_severity(0) == "critical"
    assert perimeter_severity(24.9) == "critical"
    assert perimeter_severity(25) == "high"
    assert perimeter_severity(50) == "moderate"
    assert perimeter_severity(75) == "low"
    assert perimeter_severity(1_000) == "low"


def test_an_epoch_millisecond_field_renders_as_javascript_rendered_it() -> None:
    assert epoch_milliseconds_to_iso(POLYGON_EPOCH_MILLISECONDS) == "2026-07-15T03:56:00.000Z"
    assert epoch_milliseconds_to_iso(0) == "1970-01-01T00:00:00.000Z"


def test_an_absent_or_unreal_epoch_field_becomes_null() -> None:
    assert epoch_milliseconds_to_iso(None) is None
    assert epoch_milliseconds_to_iso("2026-07-15") is None
    assert epoch_milliseconds_to_iso(9e15) is None


def test_the_query_url_asks_for_bounded_geojson() -> None:
    url = build_query_url("-125,42,-111,49")
    assert "f=geojson" in url
    assert "resultRecordCount=2000" in url
    assert "spatialRel=esriSpatialRelIntersects" in url


def test_an_arcgis_error_payload_answered_with_http_200_is_still_a_failure() -> None:
    with pytest.raises(UpstreamPayloadError, match="WFIGS API error: Too many requests"):
        parse_perimeter_collection({"error": {"code": 429, "message": "Too many requests"}})


def test_an_unexpected_collection_shape_is_refused() -> None:
    with pytest.raises(UpstreamPayloadError, match="unexpected feature collection"):
        parse_perimeter_collection({"type": "FeatureCollection", "features": [{"type": "Feature"}]})


def test_a_perimeter_parses_into_the_stored_property_shape() -> None:
    perimeters = parse_perimeter_collection(_collection())
    assert perimeters[0]["uniqueFireIdentifier"] == RECORDED_FIRE_IDENTIFIER
    assert perimeters[0]["polygonDateTime"] == "2026-07-15T03:56:00.000Z"
    assert perimeters[0]["gisAcres"] == 1234.5


def test_a_recorded_production_perimeter_still_keys_to_the_bare_fire_identifier() -> None:
    perimeter = parse_perimeter_collection(_collection())[0]
    write = build_perimeter_write(perimeter, "fire-perimeters")
    assert write is not None
    assert write.external_id == RECORDED_FIRE_IDENTIFIER
    assert write.natural_key == f"wfigs:{RECORDED_FIRE_IDENTIFIER}"
    assert write.channel == "layer:fire-perimeters"
    assert write.properties["severity"] == "critical"
    assert write.properties["source"] == "WFIGS Interagency Fire Perimeters"


def test_a_perimeter_with_no_containment_stores_a_null_severity() -> None:
    perimeter = parse_perimeter_collection(_collection(attr_PercentContained=None))[0]
    write = build_perimeter_write(perimeter, "fire-perimeters")
    assert write is not None
    assert write.properties["severity"] is None
    assert write.properties["percentContained"] is None


def test_only_a_busy_or_transient_upstream_is_retried() -> None:
    assert is_retryable_failure(UpstreamHttpError(429))
    assert is_retryable_failure(UpstreamHttpError(503))
    assert is_retryable_failure(UpstreamPayloadError("WFIGS API error: service is busy"))
    assert not is_retryable_failure(UpstreamHttpError(400))
    assert not is_retryable_failure(UpstreamPayloadError("unexpected feature collection shape"))


async def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=json.dumps(_collection()).encode(),
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        perimeters = await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)
    assert len(attempts) == 2
    assert len(perimeters) == 1


async def test_a_non_transient_failure_is_not_retried() -> None:
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(UpstreamHttpError):
            await fetch_fire_perimeters(client, "-125,42,-111,49", _no_sleep)
    assert len(attempts) == 1


async def test_an_unset_bbox_is_skipped_and_never_failed() -> None:
    result = await run_fire_perimeters_ingestion_job(RecordingWriter())
    assert result.source == WFIGS_SOURCE
    assert result.status == "skipped"
    assert result.reason == "INGEST_BBOX is not configured"


async def test_the_job_writes_the_perimeters_it_fetched() -> None:
    response = httpx.Response(
        200,
        content=json.dumps(_collection()).encode(),
        headers={"content-type": "application/json"},
    )
    writer = RecordingWriter()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        result = await run_fire_perimeters_ingestion_job(writer, bbox="-125,42,-111,49", client=client)

    assert result.status == "ingested"
    assert result.records_seen == 1
    assert result.records_written == 1
    assert result.truncated is False
    assert [write.external_id for write in writer.writes] == [RECORDED_FIRE_IDENTIFIER]
