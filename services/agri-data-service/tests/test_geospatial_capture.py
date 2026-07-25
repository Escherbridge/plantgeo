"""Tests for the bounded multi-source geospatial capture contract."""

# ruff: noqa: PLR2004

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from agri_data_service.execution.geospatial_capture import (
    GeospatialCapturePlan,
    capture_geospatial_plan,
    geospatial_capture_plan_checksum,
    load_existing_geospatial_capture,
    load_geospatial_capture_plan,
    validate_geospatial_payload,
)


def _payload(key: str = "fixture-1") -> bytes:
    return json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-116.2, 43.6], [-116.1, 43.6], [-116.1, 43.7], [-116.2, 43.6]]],
                    },
                    "properties": {"KEY": key},
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


def _reference_payload() -> bytes:
    return json.dumps(
        {
            "elements": [
                {
                    "type": "way",
                    "id": 1,
                    "version": 3,
                    "timestamp": "2026-07-23T00:00:00Z",
                }
            ]
        },
        separators=(",", ":"),
    ).encode()


def _plan(
    payload: bytes | None = None,
    *,
    with_reference: bool = False,
) -> GeospatialCapturePlan:
    payload = payload or _payload()
    source = {
        "key": "fixture-source",
        "source_kind": "property",
        "url": "https://example.test/data?where=KEY%3Dfixture-1",
        "allowed_host": "example.test",
        "output_file": "fixture.geojson",
        "expected_media_type": "application/geo+json",
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
        "expected_feature_count": 1,
        "expected_geometry_types": ["Polygon"],
        "feature_key_property": "KEY",
        "expected_feature_keys": ["fixture-1"],
        "provider": "Fixture provider",
        "provider_version": "fixture-v1",
        "metadata_url": "https://example.test/metadata",
        "licence_name": "Fixture open licence",
        "licence_url": "https://example.test/licence",
        "citation": "Fixture provider v1",
        "redistribution_status": "allowed",
        "spatial_support_kind": "native_polygon",
        "native_resolution_m": None,
        "native_scale_denominator": None,
        "maximum_inference_scale": "parcel",
    }
    if with_reference:
        reference_payload = _reference_payload()
        source["reference_capture"] = {
            "url": "https://reference.example.test/way/1/3.json",
            "allowed_host": "reference.example.test",
            "output_file": "fixture-reference.json",
            "expected_media_type": "application/json",
            "expected_sha256": hashlib.sha256(reference_payload).hexdigest(),
            "expected_json_values": {
                "elements.0.type": "way",
                "elements.0.id": 1,
                "elements.0.version": 3,
                "elements.0.timestamp": "2026-07-23T00:00:00Z",
            },
        }
    return GeospatialCapturePlan.model_validate(
        {
            "schema_version": 1,
            "pilot_key": "test-pilot",
            "user_agent": "PlantGeo-test-capture/0.1",
            "timeout_seconds": 5,
            "max_attempts": 2,
            "min_interval_seconds": 0.1,
            "sources": [source],
        }
    )


def test_repository_pilot_plan_is_frozen_credential_free_and_open_only() -> None:
    service_root = Path(__file__).resolve().parents[1]
    plan = load_geospatial_capture_plan(service_root / "plans" / "boise-intervention-capture-v1.json")

    assert len(plan.sources) == 3
    assert [source.key for source in plan.sources] == sorted(source.key for source in plan.sources)
    assert {source.maximum_inference_scale for source in plan.sources} == {
        "city",
        "neighborhood",
    }
    assert {source.redistribution_status for source in plan.sources} == {"allowed"}
    assert all("token" not in source.url.lower() for source in plan.sources)
    osm = next(source for source in plan.sources if source.key == "osm-hillside-to-hollow-20260723")
    assert osm.provider_version == "OSM way 674700373 v19 at 2025-04-09T18:53:39Z"
    assert osm.reference_capture is not None
    assert osm.reference_capture.url.endswith("/way/674700373/19.json")
    assert osm.reference_capture.expected_json_values["elements.0.version"] == 19
    wui = next(source for source in plan.sources if source.key == "usfs-wui-2020-hillside-hollow")
    assert "geometryType=esriGeometryEnvelope" in wui.url
    assert "spatialRel=esriSpatialRelIntersects" in wui.url
    assert "objectIds=" not in wui.url
    assert geospatial_capture_plan_checksum(plan)


def test_capture_retries_rate_limit_then_publishes_an_atomic_reusable_cache(tmp_path: Path) -> None:
    payload = _payload()
    plan = _plan(payload)
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/geo+json", "etag": '"fixture-v1"'},
            content=payload,
            request=request,
        )

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root, manifest = await capture_geospatial_plan(
                plan,
                tmp_path,
                client=client,
                sleep=record_sleep,
                captured_at=datetime(2026, 7, 23, tzinfo=UTC),
            )
            cached_root, cached_manifest = await capture_geospatial_plan(plan, tmp_path, client=client)
        assert root == cached_root
        assert manifest == cached_manifest
        assert (root / "fixture.geojson").read_bytes() == payload
        assert not list(root.parent.glob("*.building-*"))

    asyncio.run(run())
    assert attempts == 2
    assert delays == [0]


def test_capture_persists_revalidates_and_returns_reference_bytes(
    tmp_path: Path,
) -> None:
    payload = _payload()
    reference_payload = _reference_payload()
    plan = _plan(payload, with_reference=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "reference.example.test":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=reference_payload,
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/geo+json"},
            content=payload,
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            root, manifest = await capture_geospatial_plan(
                plan,
                tmp_path,
                client=client,
                sleep=lambda _: asyncio.sleep(0),
                captured_at=datetime(2026, 7, 23, tzinfo=UTC),
            )
        loaded_manifest, payloads = load_existing_geospatial_capture(root, plan)
        assert loaded_manifest == manifest
        assert payloads["fixture-source"] == payload
        assert payloads["fixture-source:reference"] == reference_payload
        assert manifest.receipts[0].reference_receipt is not None

    asyncio.run(run())


def test_capture_rejects_changed_provider_bytes_before_writing(tmp_path: Path) -> None:
    plan = _plan()
    changed = _payload("changed")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/geo+json"},
            content=changed,
            request=request,
        )

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="checksum changed"):
                await capture_geospatial_plan(plan, tmp_path, client=client)

    asyncio.run(run())
    assert not list(tmp_path.rglob("capture-manifest.json"))


def test_payload_validation_rejects_feature_identity_or_coordinate_drift() -> None:
    plan = _plan()
    source = plan.sources[0]

    with pytest.raises(ValueError, match="checksum changed"):
        validate_geospatial_payload(source, _payload("other"))

    malformed = _payload().replace(b"-116.2", b"-216.2")
    drifted_source = source.model_copy(update={"expected_sha256": hashlib.sha256(malformed).hexdigest()})
    with pytest.raises(ValueError, match="WGS84"):
        validate_geospatial_payload(drifted_source, malformed)


def test_payload_validation_rejects_open_or_degenerate_polygon_rings() -> None:
    for coordinates in (
        [[[-116.2, 43.6], [-116.1, 43.6], [-116.1, 43.7], [-116.2, 43.7]]],
        [[[-116.2, 43.6], [-116.2, 43.6], [-116.2, 43.6], [-116.2, 43.6]]],
    ):
        payload = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": coordinates},
                        "properties": {"KEY": "fixture-1"},
                    }
                ],
            },
            separators=(",", ":"),
        ).encode()
        plan = _plan(payload)

        with pytest.raises(ValueError, match="polygon"):
            validate_geospatial_payload(plan.sources[0], payload)
