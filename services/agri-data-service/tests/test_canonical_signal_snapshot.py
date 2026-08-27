"""Focused unit contract for the resumable raw signal snapshot exporter."""

# ruff: noqa: PLR2004

from __future__ import annotations

import io
from collections.abc import Iterator, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from scripts import canonical_signal_snapshot as snapshot


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.write_order: list[str] = []

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:  # noqa: ARG002
        existing = self.objects.get(key)
        if existing is not None and existing != payload:
            raise snapshot.ImmutableObjectConflictError(key)
        if existing is None:
            self.objects[key] = payload
            self.write_order.append(key)

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def list_keys(self, prefix: str) -> Iterator[str]:
        yield from sorted(key for key in self.objects if key.startswith(prefix))


class CorruptBeforeVerifyStore(MemoryStore):
    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        super().put_immutable(key, payload, content_type=content_type)
        if "/_ledger/" in key:
            part_key = next(
                candidate
                for candidate in self.objects
                if candidate.endswith(".parquet") and "/_dimensions/" not in candidate
            )
            self.objects[part_key] += b"corrupt"


class FakeSource:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.cells = sorted({str(row["cell_id"]) for row in rows} | {"00000000-0000-0000-0000-000000000002"})
        self.reads: list[tuple[str, tuple[str, ...]]] = []
        self.dimensions = _dimensions(self.cells)

    def high_watermark(self) -> int:
        return max(int(row["id"]) for row in self.rows)

    def source_schema(self) -> list[dict[str, Any]]:
        return []

    def dimension_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return deepcopy(self.dimensions)

    def enumerate_cell_ids(self, page_size: int) -> list[str]:  # noqa: ARG002
        return list(self.cells)

    def observation_extent(
        self,
        cell_ids: Sequence[str],
        high_watermark: int,
    ) -> tuple[date, date]:
        selected = [row for row in self.rows if row["cell_id"] in cell_ids and row["id"] <= high_watermark]
        days = [row["observation_day"] for row in selected]
        return min(days), max(days)

    def rows_for_month(
        self,
        cell_ids: Sequence[str],
        high_watermark: int,
        month_start: date,
    ) -> list[dict[str, Any]]:
        cell_tuple = tuple(cell_ids)
        self.reads.append((month_start.isoformat(), cell_tuple))
        return [
            deepcopy(row)
            for row in self.rows
            if row["cell_id"] in cell_ids
            and row["id"] <= high_watermark
            and (row["observation_day"].year, row["observation_day"].month) == (month_start.year, month_start.month)
        ]


def _dimensions(cell_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    instant = datetime(2026, 1, 2, tzinfo=UTC)
    data_source_id = "10000000-0000-0000-0000-000000000001"
    release_id = "20000000-0000-0000-0000-000000000001"
    return {
        "data_source": [
            {
                "id": data_source_id,
                "key": "source-a",
                "name": "Source A",
                "owner": "Owner",
                "purpose": "Test",
                "base_url": None,
                "license_name": "Open",
                "license_url": None,
                "citation": "Citation",
                "refresh_policy": "{}",
                "retention_days": None,
                "allowed_client_exposure": False,
                "review_state": "approved",
                "review_due_at": None,
                "reviewed_at": instant,
                "reviewed_by": "reviewer",
                "is_active": True,
                "configuration": "{}",
                "created_at": instant,
                "updated_at": instant,
            }
        ],
        "source_release": [
            {
                "id": release_id,
                "data_source_id": data_source_id,
                "source_version": "v1",
                "retrieved_at": instant,
                "data_available_at": instant,
                "observed_from": instant,
                "observed_to": instant,
                "payload_checksum": "a" * 64,
                "payload_bytes": 100,
                "schema_version": "v1",
                "license_snapshot": "Open terms",
                "query_parameters": "{}",
                "quality_summary": "{}",
                "validation_state": "valid",
                "validated_at": instant,
                "supersedes_release_id": None,
                "retraction_reason": None,
                "created_at": instant,
                "transform_version": "source-native",
            }
        ],
        "spatial_cell": [
            {
                "id": cell_id,
                "cell_key": f"cell-{index}",
                "grid_name": "grid",
                "resolution_m": 1_000,
                "geometry": b"polygon" + bytes([index]),
                "centroid": b"point" + bytes([index]),
                "parent_cell_id": None,
                "coverage_fraction": 1.0,
                "created_at": instant,
            }
            for index, cell_id in enumerate(cell_ids)
        ],
    }


def _fact(row_id: int, *, release_id: str = "20000000-0000-0000-0000-000000000001") -> dict[str, Any]:
    instant = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    raw = {
        "id": row_id,
        "source_release_id": release_id,
        "cell_id": "00000000-0000-0000-0000-000000000001",
        "signal_name": "temperature",
        "source_parameter": "t2m",
        "support_key": "surface",
        "observed_at": instant,
        "valid_from": None,
        "valid_to": None,
        "data_available_at": instant,
        "original_value": 10.0,
        "original_unit": "C",
        "normalized_value": 10.0,
        "normalized_unit": "C",
        "quality_flag": "accepted",
        "coverage_fraction": 1.0,
        "is_observed": True,
        "metadata_json": "{}",
        "created_at": instant,
        "product_key": "t2m",
        "cell_key": "cell-0",
        "cell_grid_name": "grid",
        "cell_resolution_m": 1_000,
        "cell_parent_cell_id": None,
        "cell_centroid_wkb": b"point\x00",
        "cell_centroid_srid": 4_326,
        "cell_centroid_longitude": -116.2,
        "cell_centroid_latitude": 43.6,
        "data_source_id": "10000000-0000-0000-0000-000000000001",
        "data_source_key": "source-a",
    }
    return snapshot._normalize_row(raw)


def _exporter(
    rows: list[dict[str, Any]],
    store: MemoryStore | None = None,
) -> tuple[snapshot.CanonicalSignalExporter, FakeSource, MemoryStore]:
    source = FakeSource(rows)
    resolved_store = store or MemoryStore()
    exporter = snapshot.CanonicalSignalExporter(
        source=source,
        store=resolved_store,
        cell_batch_size=1,
        target_rows_per_part=100,
    )
    return exporter, source, resolved_store


def test_duplicate_physical_rows_survive_and_hash_round_trips() -> None:
    rows = [_fact(1), _fact(2)]
    exporter, _source, store = _exporter(rows)
    manifest = exporter.export("duplicate-test", progress=lambda _message: None)

    assert manifest["row_count"] == 2
    fact_key = next(key for key in store.objects if key.endswith(".parquet") and "/_dimensions/" not in key)
    parquet_rows = pq.read_table(io.BytesIO(store.objects[fact_key])).to_pylist()
    assert [row["id"] for row in parquet_rows] == [1, 2]
    assert all(row["canonical_row_sha256"] == snapshot.canonical_row_hash(row) for row in parquet_rows)


def test_month_cell_batch_checkpoints_resume_without_reextracting() -> None:
    exporter, source, store = _exporter([_fact(1)])
    exporter.export("resume-test", progress=lambda _message: None)
    reads_after_first_run = len(source.reads)
    exporter.export("resume-test", progress=lambda _message: None)

    ledgers = [key for key in store.objects if "/_ledger/month=2026-01/cell-batch=" in key]
    assert len(ledgers) == 2
    assert len(source.reads) == reads_after_first_run + 2  # verification only; extraction checkpoints resumed
    assert any("part-cb00000-00000.parquet" in key for key in store.objects)


def test_dimensions_are_parquet_and_complete_is_written_last() -> None:
    exporter, _source, store = _exporter([_fact(1)])
    manifest = exporter.export("dimension-test", progress=lambda _message: None)

    dimension_keys = {key for key in store.objects if "/_dimensions/" in key and key.endswith(".parquet")}
    assert len(dimension_keys) == 3
    assert set(manifest["dimension_objects"]) == {"data_source", "source_release", "spatial_cell"}
    assert store.write_order[-1].endswith("/_COMPLETE")
    assert store.write_order[-2].endswith("/manifest.json")


def test_corrupt_part_fails_closed_before_manifest_or_complete() -> None:
    store = CorruptBeforeVerifyStore()
    exporter, _source, _store = _exporter([_fact(1)], store)

    with pytest.raises(snapshot.SnapshotError, match="checksum"):
        exporter.export("corrupt-test", progress=lambda _message: None)

    assert not any(key.endswith("/manifest.json") or key.endswith("/_COMPLETE") for key in store.objects)


@pytest.mark.parametrize("prefix", ["layer=signal", "raw-canonical/layer=signal", "../raw-canonical/signal"])
def test_serving_or_traversal_prefixes_are_rejected(prefix: str) -> None:
    with pytest.raises(ValueError, match="prefix"):
        snapshot.CanonicalSignalExporter(source=FakeSource([_fact(1)]), store=MemoryStore(), raw_prefix=prefix)


def test_source_change_after_checkpoint_fails_row_reconciliation() -> None:
    exporter, source, _store = _exporter([_fact(1)])
    exporter.export("source-change-test", progress=lambda _message: None)
    source.rows[0]["normalized_value"] = 11.0
    source.rows[0]["canonical_row_sha256"] = snapshot.canonical_row_hash(source.rows[0])

    with pytest.raises(snapshot.SnapshotError, match="PostgreSQL changed"):
        exporter.verify("source-change-test", progress=lambda _message: None)
