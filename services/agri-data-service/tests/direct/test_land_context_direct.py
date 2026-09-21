"""Completeness, immutable replay, state context, and the BLM source/aggregate distinction."""

from __future__ import annotations

import argparse
import io
import json
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline import source_bindings
from agri_data_service.pipeline.direct.land_context import forward, source
from agri_data_service.pipeline.direct.land_context.products import (
    ARCHIVE_ROOT,
    CENSUS_STATES,
    FIELD_OFFICES,
    PRODUCTS,
    SURFACE_IDAHO,
    SURFACE_ORWA,
    ArcgisProduct,
)
from agri_data_service.pipeline.direct.land_context.rows import snapshot_tables
from agri_data_service.pipeline.direct.land_context.source import (
    CaptureSession,
    LandContextSnapshot,
    ProductCapture,
    canonical_bytes,
    capture_product,
    digest,
    replay_snapshot,
)
from agri_data_service.pipeline.direct.land_context.watermark import STATE_KEY, read_state
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.parquet.tiers import derivation_session, derive_tier
from agri_data_service.warehouse.schemas.land_context import BOUNDARIES_STREAM, CONTACTS_STREAM, OFFICES_STREAM
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.parquet.test_objectstore_writer import RecordingBackend

CAPTURED = datetime(2026, 9, 20, 12, tzinfo=UTC)
DAY = date(2026, 9, 20)


def polygon(west: float, south: float, east: float, north: float) -> dict[str, Any]:
    return {
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
    }


def feature(identity: int, key_field: str, key: str, geometry: dict[str, Any], **attributes: object) -> dict[str, Any]:
    return {"type": "Feature", "properties": {"OBJECTID": identity, key_field: key, **attributes}, "geometry": geometry}


def product_capture(product: ArcgisProduct, features: list[dict[str, Any]]) -> ProductCapture:
    return ProductCapture(product, tuple(features), digest(canonical_bytes(features)))


def fixture_snapshot() -> LandContextSnapshot:
    states = [
        feature(index, "STUSAB", state, geometry)
        for index, (state, geometry) in enumerate(
            (
                ("OR", polygon(-125, 42, -117, 46)),
                ("WA", polygon(-125, 46, -117, 49)),
                ("ID", polygon(-117, 42, -111, 49)),
            ),
            start=1,
        )
    ]
    orwa = [
        feature(1, "GLOBALID", "washington-native", polygon(-122, 47, -121.9, 47.1), ADMIN_ST="OR"),
        feature(2, "GLOBALID", "oregon-native-a", polygon(-122, 44, -121.9, 44.1)),
        feature(3, "GLOBALID", "oregon-native-b", polygon(-121.9, 44, -121.8, 44.1)),
    ]
    idaho = [
        {
            "type": "Feature",
            "properties": {"OBJECTID": 27, "ADMIN_ST": "MT"},
            "geometry": polygon(-117.1, 44, -115.9, 44.1),
        }
    ]
    offices = [
        feature(
            1,
            "ADM_UNIT_CD",
            "ORW03000",
            polygon(-123, 46.5, -120, 48),
            ADMU_NAME="Spokane Border Field Office",
            ADMIN_ST="OR",
            PARENT_CD="ORW00000",
            ADMU_ST_URL="https://www.blm.gov/oregon-washington",
            EFF_DT=253392451200000,
        )
    ]
    offices.append(
        feature(
            2,
            "ADM_UNIT_CD",
            "ORW03000",
            polygon(-124, 46.5, -123.5, 47),
            ADMU_NAME="Spokane Border Field Office",
            ADMIN_ST="OR",
            PARENT_CD="ORW00000",
            ADMU_ST_URL="https://www.blm.gov/oregon-washington",
            EFF_DT=253392451200000,
        )
    )
    captures = (
        product_capture(SURFACE_ORWA, orwa),
        product_capture(SURFACE_IDAHO, idaho),
        product_capture(FIELD_OFFICES, offices),
        product_capture(CENSUS_STATES, states),
    )
    return LandContextSnapshot(captures, CAPTURED, CAPTURED, "a" * 64, "b" * 64)


def mock_handler(request: httpx.Request) -> httpx.Response:
    params = dict(request.url.params)
    if not request.url.path.endswith("/query"):
        return httpx.Response(200, json={"fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"}]})
    if params.get("returnIdsOnly") == "true":
        return httpx.Response(200, json={"objectIds": [1, 2]})
    if params.get("returnCountOnly") == "true":
        return httpx.Response(200, json={"count": 2})
    return httpx.Response(
        200,
        json={
            "type": "FeatureCollection",
            "features": [
                feature(1, "GLOBALID", "one", polygon(-120, 44, -119, 45)),
                feature(2, "GLOBALID", "two", polygon(-120, 46, -119, 47)),
            ],
        },
    )


@pytest.mark.asyncio
async def test_missing_requested_feature_refuses_population() -> None:
    def incomplete(request: httpx.Request) -> httpx.Response:
        response = mock_handler(request)
        if request.url.params.get("f") == "geojson":
            body = json.loads(response.content)
            body["features"].pop()
            return httpx.Response(200, json=body)
        return response

    storage = MemoryAvailabilityStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(incomplete)) as client:
        session = CaptureSession(client, storage, time.monotonic() + 30)
        with pytest.raises(PipelineOperationError, match="omitted requested records"):
            await capture_product(session, SURFACE_ORWA)


@pytest.mark.asyncio
async def test_duplicate_native_ids_refuse_even_when_object_inventory_matches() -> None:
    def duplicate(request: httpx.Request) -> httpx.Response:
        response = mock_handler(request)
        if request.url.params.get("f") == "geojson":
            body = json.loads(response.content)
            body["features"][1]["properties"]["GLOBALID"] = "one"
            return httpx.Response(200, json=body)
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(duplicate)) as client:
        session = CaptureSession(client, MemoryAvailabilityStorage(), time.monotonic() + 30)
        with pytest.raises(PipelineOperationError, match="repeated native key"):
            await capture_product(session, SURFACE_ORWA)


@pytest.mark.asyncio
async def test_replay_verifies_exact_source_query_and_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source, "PRODUCTS", (SURFACE_ORWA,))
    storage = MemoryAvailabilityStorage()
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)) as client:
        session = CaptureSession(client, storage, time.monotonic() + 30)
        captured = await capture_product(session, SURFACE_ORWA)
    content = digest(canonical_bytes({SURFACE_ORWA.slug: captured.content_sha256}))
    manifest = {
        "schema": "blm-pnw-current-capture/v1",
        "started_at": CAPTURED.isoformat(),
        "captured_at": CAPTURED.isoformat(),
        "source_content_sha256": content,
        "responses": session.responses,
    }
    body = canonical_bytes(manifest)
    identity = digest(body)
    storage.put_immutable(f"{ARCHIVE_ROOT}/manifests/{identity}.json", body, content_type="application/json")
    replayed = await replay_snapshot(storage, identity)
    assert replayed.products == (captured,)
    assert replayed.captured_at == CAPTURED
    receipt = session.responses[0]
    storage.seed(f"{ARCHIVE_ROOT}/blobs/{receipt['sha256']}", b'{"tampered":true}')
    with pytest.raises(PipelineOperationError, match="hash/size verification"):
        await replay_snapshot(storage, identity)


def test_states_routes_and_coarse_native_identity_stay_honest() -> None:
    snapshot = fixture_snapshot()
    tables = snapshot_tables(snapshot, release_day=DAY)
    base = tables[BOUNDARIES_STREAM]
    washington = next(row for row in base.to_pylist() if row["source_native_feature_key"] == "washington-native")
    assert washington["state"] == "WA"
    assert washington["native_feature_key"] == "serving:WA:washington-native"
    assert all(row["family"] == "blm_surface_management" for row in base.to_pylist())
    office = tables[OFFICES_STREAM].to_pylist()[0]
    assert office["state"] == "WA"
    assert office["family"] == "blm_office_jurisdiction"
    assert tables[OFFICES_STREAM].num_rows == 1
    assert office["source_feature_count"] == len(snapshot.products[2].features)
    contact = tables[CONTACTS_STREAM].to_pylist()[0]
    assert tables[CONTACTS_STREAM].num_rows == 1
    assert contact["subject_id"] == "blm-field-offices:ORW03000"
    assert contact["effective_from"] is None
    assert contact["public_business_phone"] is None
    assert contact["route_status"] == "unverified"
    assert contact["forwarding_documented"] is False
    with derivation_session() as connection:
        for tier in (9, 5, 0):
            coarse = derive_tier(pl.from_arrow(base), stream=BOUNDARIES_STREAM, tier=tier, connection=connection)
            assert set(coarse["native_feature_key"]) == {"serving:OR", "serving:WA", "serving:ID"}
            assert coarse["source_native_feature_key"].null_count() == len(PRODUCTS) - 1
            assert coarse["source_feature_count"].sum() == base.num_rows


def test_orwa_source_is_clipped_to_physical_region_without_inventing_native_ids() -> None:
    snapshot = fixture_snapshot()
    orwa = next(capture for capture in snapshot.products if capture.product == SURFACE_ORWA)
    clipped = feature(4, "GLOBALID", "crosses-southern-mask", polygon(-122, 41.9, -121.9, 42.1))
    outside = feature(5, "GLOBALID", "outside-physical-mask", polygon(-122, 40, -121.9, 40.1))
    replacement = product_capture(SURFACE_ORWA, [*orwa.features, clipped, outside])
    snapshot = replace(
        snapshot,
        products=tuple(replacement if capture.product == SURFACE_ORWA else capture for capture in snapshot.products),
    )
    boundaries = snapshot_tables(snapshot, release_day=DAY)[BOUNDARIES_STREAM]
    rows = boundaries.to_pylist()
    assert not any(row["source_native_feature_key"] == "outside-physical-mask" for row in rows)
    admitted = next(row for row in rows if row["source_native_feature_key"] == "crosses-southern-mask")
    assert admitted["native_feature_key"] == "serving:OR:crosses-southern-mask"
    assert admitted["source_feature_count"] == 1
    assert "clipped" in admitted["aggregation_basis"]
    with derivation_session() as connection:
        bounds = connection.execute("SELECT ST_YMin(ST_GeomFromWKB(?))", [admitted["geometry_wkb"]]).fetchone()
    assert bounds == (42.0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [True, False])
async def test_static_publication_finalizes_only_verified_ladders_without_daily_index(
    monkeypatch: pytest.MonkeyPatch, complete: bool
) -> None:
    snapshot = fixture_snapshot()
    tables = snapshot_tables(snapshot, release_day=DAY)
    storage = MemoryAvailabilityStorage()
    store = MagicMock()
    store.read_partition.side_effect = lambda layer, _kind, _zoom, _day: tables[layer].take(
        list(reversed(range(tables[layer].num_rows)))
    )
    filled = AsyncMock(return_value=("written", 1, 4, 1000, None))
    monkeypatch.setattr(forward, "fill_one_lane_day", filled)
    monkeypatch.setattr(forward, "ladder_complete", lambda *_args, **_kwargs: complete)
    monkeypatch.setattr(forward, "replay_snapshot", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(forward, "snapshot_tables", lambda *_args, **_kwargs: tables)
    monkeypatch.setattr(source_bindings, "resolve_land_context_source", MagicMock())
    args = argparse.Namespace(
        capture_manifest=snapshot.manifest_sha256, mode="backfill", run_id="proof", time_budget_seconds=30
    )
    if complete:
        result = await forward._owned_turn(MagicMock(), store, storage, args)
        assert result["status"] == "completed"
        assert all(product["verified_static_snapshot"] for product in result["products"])
        assert all(product["source"]["manifest_sha256"] == snapshot.manifest_sha256 for product in result["products"])
        assert all(call.kwargs["extend_availability"] is False for call in filled.call_args_list)
        _stored, state = read_state(storage)
        assert state["published"]["manifest_sha256"] == snapshot.manifest_sha256
        assert state["pending"] is None
    else:
        with pytest.raises(ValueError, match="publication remains incomplete"):
            await forward._owned_turn(MagicMock(), store, storage, args)
        _stored, state = read_state(storage)
        assert state["published"] is None
        assert state["pending"]["manifest_sha256"] == snapshot.manifest_sha256


def _published_blm_ladders() -> tuple[ObjectStore, RecordingBackend, LandContextSnapshot]:
    snapshot = fixture_snapshot()
    backend = RecordingBackend()
    store = ObjectStore(backend)
    for layer, table in snapshot_tables(snapshot, release_day=DAY).items():
        for tier in ZOOM_TIERS:
            receipts = [
                store.write_partition(
                    table.slice(index, 1), layer=layer, kind="observed", zoom=tier, day=DAY, part_index=index
                )
                for index in range(table.num_rows)
            ]
            parts = (
                tuple(
                    CompletedPart(
                        relative_path=part.relative_path,
                        row_count=part.row_count,
                        byte_count=part.byte_count,
                        sha256=part.sha256,
                    )
                    for part in receipts
                )
                if tier != max(ZOOM_TIERS)
                else ()
            )
            store.write_completion_marker(
                PartitionCompletion(
                    part_count=len(receipts),
                    row_count=table.num_rows,
                    completed_at=CAPTURED,
                    run_id="test",
                    parts=parts,
                ),
                layer=layer,
                kind="observed",
                zoom=tier,
                day=DAY,
            )
    return store, backend, snapshot


@pytest.mark.parametrize("damage", ["missing_one", "missing_all", "extra", "foreign_source", "changed_derived"])
def test_blm_ladder_detects_physical_damage_with_unchanged_markers(damage: str) -> None:
    store, backend, snapshot = _published_blm_ladders()
    tier = 9 if damage == "changed_derived" else 13
    parts = store.list_day_parts(BOUNDARIES_STREAM, "observed", tier, DAY)
    assert len(parts) > 1
    marker = store.read_completion_receipt(BOUNDARIES_STREAM, "observed", tier, DAY)
    if damage.startswith("missing"):
        for key in parts if damage == "missing_all" else parts[:1]:
            backend.delete(store.key_for(key))
    elif damage == "extra":
        backend.put(
            store.key_for(partition_path(BOUNDARIES_STREAM, "observed", tier, DAY, part_index=999)),
            backend.objects[store.key_for(parts[0])],
            content_type="application/vnd.apache.parquet",
        )
    else:
        table = pq.read_table(io.BytesIO(backend.objects[store.key_for(parts[0])]))
        column = "label" if damage == "changed_derived" else "source_manifest_sha256"
        changed = table.set_column(
            table.schema.get_field_index(column), table.schema.field(column), pa.array(["f" * 64] * table.num_rows)
        )
        output = io.BytesIO()
        pq.write_table(changed, output)
        backend.put(store.key_for(parts[0]), output.getvalue(), content_type="application/vnd.apache.parquet")
    assert store.read_completion_receipt(BOUNDARIES_STREAM, "observed", tier, DAY) == marker
    assert not forward.ladder_complete(store, BOUNDARIES_STREAM, day=DAY, manifest=snapshot.manifest_sha256)


@pytest.mark.asyncio
async def test_intact_blm_ladders_reconcile_without_capture_or_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    store, _backend, snapshot = _published_blm_ladders()
    storage = MemoryAvailabilityStorage()
    storage.seed(
        STATE_KEY,
        canonical_bytes(
            {"schema": "blm-pnw-publication-state/v1", "pending": None, "published": forward._coordinates(snapshot)}
        ),
    )
    source_capture = AsyncMock()
    replay = AsyncMock()
    monkeypatch.setattr(source_bindings, "resolve_land_context_source", lambda: MagicMock(capture=source_capture))
    monkeypatch.setattr(forward, "replay_snapshot", replay)
    args = argparse.Namespace(capture_manifest=None, mode="reconcile", run_id="test", time_budget_seconds=30)
    result = await forward._owned_turn(MagicMock(), store, storage, args)
    assert result["status"] == "completed"
    assert result["outcome"] == "unchanged"
    source_capture.assert_not_called()
    replay.assert_not_called()


def test_blm_ladder_does_not_hide_storage_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    store, backend, snapshot = _published_blm_ladders()
    monkeypatch.setattr(backend, "get", MagicMock(side_effect=OSError("storage unavailable")))
    with pytest.raises(OSError, match="storage unavailable"):
        forward.ladder_complete(store, BOUNDARIES_STREAM, day=DAY, manifest=snapshot.manifest_sha256)
