"""Annual crop estimates preserve measured class areas and refuse incomplete evidence."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import rasterio
from rasterio.transform import from_origin

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import partition_path
from agri_data_service.foundation.parquet.zoom import validate_zoom_tier
from agri_data_service.pipeline.direct.crop_cover import forward
from agri_data_service.pipeline.direct.crop_cover.archive import ARCHIVE_ROOT, archive_capture, replay_capture
from agri_data_service.pipeline.direct.crop_cover.products import CDL_SERVICE, GRID_METRES, RELEASE_DAYS, metadata_url
from agri_data_service.pipeline.direct.crop_cover.rows import build_tables, derive_table
from agri_data_service.pipeline.direct.crop_cover.source import (
    CaptureConfig,
    canonical_bytes,
    catalog_record,
    planned_tiles,
    read_manifest,
    sha256,
    verify_tiff,
)
from agri_data_service.pipeline.errors import PipelineOperationError
from agri_data_service.pipeline.parquet.availability_index import AvailabilityRow, EvidenceReceipt
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.crop_cover import CROP_COVER_SCHEMA
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from pathlib import Path

TEST_BBOX = (-117.01, 46.99, -116.99, 47.01)
TEST_YEAR = 2025
TEST_SOURCE_RESOLUTION = 10
TEST_ANALYSIS_RESOLUTION = 30
TEST_RASTER_ID = 40


def _capture_fixture(directory: Path) -> Path:
    config = CaptureConfig(directory, year=2025, bbox=TEST_BBOX)
    receipts = []
    for index, bounds in enumerate(planned_tiles(config)):
        west, south, east, north = bounds
        pixels = np.zeros(((north - south) // 30, (east - west) // 30), dtype="uint8")
        pixels[:, ::4] = 1
        pixels[:, 1::4] = 61
        pixels[:, 2::4] = 111
        path = directory / f"tile-{index}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=pixels.shape[1],
            height=pixels.shape[0],
            count=1,
            dtype="uint8",
            crs="EPSG:5070",
            transform=from_origin(west, north, 30, 30),
            nodata=0,
        ) as output:
            output.write(pixels, 1)
        receipts.append(
            {"file": path.name, "bounds": bounds, "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size}
        )
    manifest = {
        "format": "plantgeo-cdl-capture/v1",
        "observed_year": 2025,
        "release_day": "2026-02-27",
        "bbox": TEST_BBOX,
        "source_url": CDL_SERVICE,
        "metadata_url": metadata_url(2025),
        "source_resolution_m": 10,
        "analysis_resolution_m": 30,
        "captured_at": "2026-09-20T12:00:00+00:00",
        "started_at": "2026-09-20T11:00:00+00:00",
        "raster": {"OBJECTID": 40, "Name": "2025_10m_cdls"},
        "class_names": {"0": "Background", "1": "Corn", "61": "Fallow/Idle Cropland", "111": "Open Water"},
        "tiles": receipts,
    }
    for name in ("catalog", "legend", "metadata"):
        suffix = ".html" if name == "metadata" else ".json"
        if name == "catalog":
            payload = canonical_bytes({"features": [{"attributes": manifest["raster"]}]})
        elif name == "legend":
            payload = canonical_bytes(
                {
                    "features": [
                        {"attributes": {"Value": int(code), "Class_Names": label}}
                        for code, label in manifest["class_names"].items()
                    ]
                }
            )
        else:
            payload = b'<meta name="dc.date" content="20260227"/>'
        (directory / (name + suffix)).write_bytes(payload)
        manifest[name + "_sha256"] = sha256(payload)
    path = directory / "manifest.json"
    path.write_bytes(canonical_bytes(manifest))
    return path


def test_class_area_estimates_count_fallow_and_keep_nodata_in_denominator(tmp_path: Path) -> None:
    tables = build_tables(_capture_fixture(tmp_path))
    base = tables[13]
    assert base.schema == CROP_COVER_SCHEMA.arrow_schema
    assert base.num_rows > 0
    for row in base.to_pylist():
        assert row["crop_fraction"] == pytest.approx(0.5)
        assert row["classified_fraction"] == pytest.approx(0.75)
        assert row["crop_area_ha"] == pytest.approx(450)
        assert row["cell_area_ha"] == pytest.approx(900)
        assert row["dominant_crop_code"] == 1
        assert row["dominant_crop_name"] == "Corn"
        assert row["estimation_method"] == "nearest_neighbor_resampled_pixel_area"
        assert row["observed_year"] == TEST_YEAR
        assert row["release_day"] == RELEASE_DAYS[TEST_YEAR]
        assert row["source_resolution_m"] == TEST_SOURCE_RESOLUTION
        assert row["analysis_resolution_m"] == TEST_ANALYSIS_RESOLUTION
        assert json.loads(row["class_areas_json"]) == {"1": 225, "61": 225, "111": 225}


def test_all_rungs_preserve_total_class_area_and_repair_matches_source_derivation(tmp_path: Path) -> None:
    tables = build_tables(_capture_fixture(tmp_path))
    agricultural_ha = sum(tables[13].column("crop_area_ha").to_pylist())
    for tier, table in tables.items():
        assert sum(table.column("crop_area_ha").to_pylist()) == pytest.approx(agricultural_ha)
        assert set(table.column("aggregation_cell_m").to_pylist()) == {GRID_METRES[tier]}
        assert all(0 <= value <= 1 for value in table.column("classified_fraction").to_pylist())
        assert derive_table(tables[13], tier).equals(table)


def test_missing_source_tile_is_not_a_complete_annual_release(tmp_path: Path) -> None:
    path = _capture_fixture(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["tiles"] = []
    path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(PipelineOperationError, match="truncated"):
        read_manifest(path)


def test_changed_source_bytes_are_rejected_before_aggregation(tmp_path: Path) -> None:
    path = _capture_fixture(tmp_path)
    manifest = json.loads(path.read_bytes())
    tile = tmp_path / manifest["tiles"][0]["file"]
    payload = tile.read_bytes()
    tile.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(PipelineOperationError, match="SHA-256"):
        build_tables(path)


def test_catalog_requires_unambiguous_locked_year_and_complete_inventory() -> None:
    feature = {"attributes": {"Name": "2025_10m_cdls", "OBJECTID": 40}}
    assert catalog_record({"features": [feature]}, TEST_YEAR)["OBJECTID"] == TEST_RASTER_ID
    with pytest.raises(PipelineOperationError, match="missing or ambiguous"):
        catalog_record({"features": [feature]}, 2024)
    with pytest.raises(PipelineOperationError, match="missing or ambiguous"):
        catalog_record({"features": [feature, feature]}, 2025)
    with pytest.raises(PipelineOperationError, match="truncated"):
        catalog_record({"features": [feature], "exceededTransferLimit": True}, 2025)


def test_rendered_rgb_is_not_accepted_as_classified_raster(tmp_path: Path) -> None:
    path = tmp_path / "rendered.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=100,
        height=100,
        count=3,
        dtype="uint8",
        crs="EPSG:5070",
        transform=from_origin(-1602000, 2403000, 30, 30),
    ) as output:
        output.write(np.ones((3, 100, 100), dtype="uint8"))
    with pytest.raises(PipelineOperationError, match="never rendered colours"):
        verify_tiff(path, (-1602000, 2400000, -1599000, 2403000), 30)


def test_coarse_rung_cannot_blend_source_generations(tmp_path: Path) -> None:
    base = build_tables(_capture_fixture(tmp_path))[13]
    rows = base.to_pylist()
    rows.append({**rows[0], "source_sha256": "f" * 64})
    with pytest.raises(PipelineOperationError, match="mix annual sources"):
        derive_table(pa.Table.from_pylist(rows, schema=base.schema), 0)


@pytest.mark.parametrize(("year", "resolution"), [(2022, 10), (2026, 30), (2025, 300)])
def test_unsupported_year_or_resolution_cannot_invent_coverage(tmp_path: Path, year: int, resolution: int) -> None:
    with pytest.raises(PipelineOperationError):
        CaptureConfig(tmp_path, year=year, resolution_m=resolution).validate()


def test_durable_archive_replays_identical_source_and_derived_tables(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    path = _capture_fixture(source)
    storage = MemoryAvailabilityStorage()
    key = archive_capture(storage, path)
    identity = sha256(path.read_bytes())
    assert key == f"{ARCHIVE_ROOT}/manifests/{identity}.json"
    restored = replay_capture(storage, identity, tmp_path / "restored")
    assert restored.read_bytes() == path.read_bytes()
    originals = build_tables(path)
    replays = build_tables(restored)
    assert all(originals[tier].equals(replays[tier]) for tier in GRID_METRES)


def test_archive_missing_a_source_blob_never_closes_a_replay(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    path = _capture_fixture(source)
    storage = MemoryAvailabilityStorage()
    archive_capture(storage, path)
    manifest = json.loads(path.read_bytes())
    storage.objects.pop(f"{ARCHIVE_ROOT}/blobs/{manifest['tiles'][0]['sha256']}")
    with pytest.raises(PipelineOperationError, match="missing or fails"):
        replay_capture(storage, sha256(path.read_bytes()), tmp_path / "incomplete")
    assert not (tmp_path / "incomplete" / "manifest.json").exists()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("source_resolution_m", 1, "native source resolution"),
        ("class_names", {"1": "Fabricated"}, "class names"),
        ("raster", {"OBJECTID": 1, "Name": "2025_10m_cdls"}, "raster identity"),
    ],
)
def test_manifest_cannot_relabel_hashed_source_evidence(tmp_path: Path, field: str, value: object, reason: str) -> None:
    path = _capture_fixture(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest[field] = value
    path.write_bytes(canonical_bytes(manifest))
    with pytest.raises(PipelineOperationError, match=reason):
        read_manifest(path)


def _indexed_crop_ladder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ObjectStore, RecordingBackend]:
    tables = build_tables(_capture_fixture(tmp_path))
    backend = RecordingBackend()
    store = ObjectStore(backend)
    day = RELEASE_DAYS[TEST_YEAR]
    completed = datetime(2026, 9, 21, tzinfo=UTC)
    rows = []
    evidence = EvidenceReceipt(key="evidence/source.json", sha256="a" * 64)
    for tier, table in tables.items():
        zoom = validate_zoom_tier(tier)
        parts = [
            store.write_partition(
                table.slice(index, 1), layer="crop-cover", kind="observed", zoom=zoom, day=day, part_index=index
            )
            for index in range(table.num_rows)
        ]
        marker = store.write_completion_marker(
            PartitionCompletion(part_count=len(parts), row_count=table.num_rows, completed_at=completed, run_id="test"),
            layer="crop-cover",
            kind="observed",
            zoom=zoom,
            day=day,
        )
        rows.append(
            AvailabilityRow(
                lane="crop-cover",
                product="crop-cover",
                nature="release_series",
                day=day,
                rung=tier,
                terminal_state="published",
                row_count=table.num_rows,
                source_receipt=evidence,
                terminal_receipt=evidence,
                data_receipts=tuple(
                    sorted(
                        (EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in parts),
                        key=lambda receipt: receipt.key,
                    )
                ),
                completion_receipt=EvidenceReceipt(key=marker.relative_path, sha256=marker.sha256),
                absence_reason=None,
                source_ceiling=day,
                published_at=completed,
            )
        )
    index = MagicMock(rows=tuple(rows))
    index.selectable_days.return_value = (day,)
    monkeypatch.setattr(forward, "read_latest_availability", lambda *_args, **_kwargs: index)
    return store, backend


@pytest.mark.parametrize("damage", ["missing_one", "missing_all", "extra", "changed"])
def test_indexed_crop_ladder_detects_physical_damage_with_unchanged_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    store, backend = _indexed_crop_ladder(tmp_path, monkeypatch)
    day = RELEASE_DAYS[TEST_YEAR]
    parts = store.list_day_parts("crop-cover", "observed", 13, day)
    assert len(parts) > 1
    marker = store.read_completion_receipt("crop-cover", "observed", 13, day)
    if damage.startswith("missing"):
        for key in parts if damage == "missing_all" else parts[:1]:
            backend.delete(store.key_for(key))
    elif damage == "extra":
        backend.put(
            store.key_for(partition_path("crop-cover", "observed", 13, day, part_index=999)),
            backend.objects[store.key_for(parts[0])],
            content_type="application/vnd.apache.parquet",
        )
    else:
        table = pq.read_table(io.BytesIO(backend.objects[store.key_for(parts[0])]))
        changed = table.set_column(
            table.schema.get_field_index("source_sha256"),
            table.schema.field("source_sha256"),
            pa.array(["f" * 64] * table.num_rows),
        )
        output = io.BytesIO()
        pq.write_table(changed, output)
        backend.put(store.key_for(parts[0]), output.getvalue(), content_type="application/vnd.apache.parquet")
    assert store.read_completion_receipt("crop-cover", "observed", 13, day) == marker
    assert not forward.indexed_ladder(store, MemoryAvailabilityStorage(), day)


@pytest.mark.asyncio
async def test_intact_indexed_crop_release_repairs_as_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, _backend = _indexed_crop_ladder(tmp_path, monkeypatch)
    monkeypatch.setattr(ObjectStore, "from_settings", lambda: store)
    monkeypatch.setattr(forward.BotoAvailabilityStorage, "from_settings", MemoryAvailabilityStorage)
    args = forward._parse_args(
        [
            "--operation",
            "repair",
            "--year",
            str(TEST_YEAR),
            "--capture-dir",
            str(tmp_path),
            "--bbox",
            ",".join(map(str, forward.capture_envelope())),
        ]
    )
    result = await forward.run(args)
    assert result["outcome"] == "idempotent_noop"


def test_crop_ladder_does_not_hide_storage_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, backend = _indexed_crop_ladder(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "get", MagicMock(side_effect=OSError("storage unavailable")))
    with pytest.raises(OSError, match="storage unavailable"):
        forward.indexed_ladder(store, MemoryAvailabilityStorage(), RELEASE_DAYS[TEST_YEAR])
