"""Immutable snapshot products stay closed, exact-day, schema-bound, and coverage-bounded."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Literal

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import snapshot_products
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import BoundingBox, ReadScope
from agri_data_service.parquet_ops.snapshot_products import (
    PRODUCT_BY_LAYER,
    SIGNAL_PRODUCT_COLUMNS,
    SNAPSHOT_ID,
    SOIL_TEMPERATURE_COLUMNS,
    SOIL_WETNESS_COLUMNS,
    ObjectStoreSnapshotStore,
    SnapshotProduct,
    build_snapshot_coverage,
    load_snapshot_evidence,
    resolve_snapshot_product,
    resolve_snapshot_window,
    snapshot_product_columns,
)
from agri_data_service.parquet_ops.wire import DayNotWritten, PublishedDay

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


TEST_COLUMNS = ("observed_day", "cell_longitude", "cell_latitude", "normalized_value")
NASA_POWER_GRID_CELL_COUNT = 397


@pytest.fixture(autouse=True)
def _clear_snapshot_evidence_cache() -> Iterator[None]:
    snapshot_products.clear_snapshot_evidence_cache()
    yield
    snapshot_products.clear_snapshot_evidence_cache()


@dataclass
class FakeStore:
    objects: dict[str, bytes]
    listings: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    cache_namespace: object = field(default_factory=object)

    def cache_identity(self) -> tuple[object, ...]:
        return ("fake-store", self.cache_namespace)

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        self.listings.append(relative_prefix)
        yield from (key for key in sorted(self.objects) if key.startswith(relative_prefix))

    def read_object(self, relative_key: str) -> bytes | None:
        self.reads.append(relative_key)
        return self.objects.get(relative_key)

    def relative_key(self, persisted_key: str) -> str:
        return persisted_key


@dataclass
class LocalSession:
    connection: duckdb.DuckDBPyConnection
    files: dict[str, str]

    def object_uri(self, relative_key: str) -> str:
        return self.files[relative_key]


def _product(
    layer: str = "test-snapshot-product",
    *,
    layout: Literal["daily", "monthly"] = "monthly",
) -> SnapshotProduct:
    root = f"layer={layer}/snapshot={SNAPSHOT_ID}"
    return SnapshotProduct(
        layer=layer,
        layout=layout,
        data_root=root,
        metadata_root=root,
        schema_columns=TEST_COLUMNS,
        contract_version="plantgeo.test.snapshot.v1",
    )


def _part(product: SnapshotProduct, tier: int, day: date, *, monthly: bool) -> str:
    root = f"{product.data_root}/kind=observed/zoom={tier:02d}/year={day.year:04d}/month={day.month:02d}"
    return f"{root}/part-00000.parquet" if monthly else f"{root}/day={day.day:02d}/part-0.parquet"


def _receipt(key: str, payload: bytes) -> dict[str, object]:
    return {"key": key, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _closed_store(
    product: SnapshotProduct,
    parts: list[str],
    *,
    manifest_fields: dict[str, object] | None = None,
    part_payloads: dict[str, bytes] | None = None,
    part_rows: dict[str, int] | None = None,
) -> FakeStore:
    objects = {key: (part_payloads or {}).get(key, b"PAR1-test-receipt") for key in parts}
    checkpoints: list[dict[str, object]] = []
    grouped: dict[str, dict[str, dict[str, object]]] = {}
    for key in parts:
        matched = snapshot_products._MONTHLY_PART.search(key)
        assert matched is not None
        month = f"{matched.group('year')}-{matched.group('month')}"
        grouped.setdefault(month, {})[str(int(matched.group("zoom")))] = {
            **_receipt(key, objects[key]),
            "rows": (part_rows or {}).get(key, 1),
            "zoom": int(matched.group("zoom")),
        }
    for month, rungs in sorted(grouped.items()):
        checkpoint_key = f"{product.data_root}/_checkpoints/year={month[:4]}/month={month[5:]}.json"
        checkpoint = {
            "contract_version": product.contract_version,
            "snapshot_id": SNAPSHOT_ID,
            "product": {"stream": product.layer},
            "observation_month": month,
            "rungs": rungs,
        }
        checkpoint_payload = json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
        objects[checkpoint_key] = checkpoint_payload
        checkpoints.append({**_receipt(checkpoint_key, checkpoint_payload), "month": month})
    manifest = {
        "contract_version": product.contract_version,
        "snapshot_id": SNAPSHOT_ID,
        "lane": product.layer,
        "checkpoints": checkpoints,
        **(manifest_fields or {}),
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_key = f"{product.metadata_root}/manifest.json"
    complete = json.dumps(
        {"manifest_key": manifest_key, "manifest_sha256": hashlib.sha256(payload).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    objects.update({manifest_key: payload, f"{product.metadata_root}/_COMPLETE": complete})
    return FakeStore(objects)


def _closed_lane_store(product: SnapshotProduct, parts: list[str]) -> FakeStore:
    objects = {key: f"parquet:{key}".encode() for key in parts}

    def receipt(key: str, *, kind: str | None = None) -> dict[str, object]:
        value: dict[str, object] = {
            "key": key,
            "byte_count": len(objects[key]),
            "sha256": hashlib.sha256(objects[key]).hexdigest(),
        }
        if kind is not None:
            value.update({"kind": kind, "row_count": None})
        return value

    month = "2026-08"
    by_tier = {str(tier): receipt(next(key for key in parts if f"zoom={tier:02d}" in key)) for tier in ZOOM_TIERS}
    base_key = f"{product.data_root}/_checkpoints/base/year=2026/month=08.json"
    base = {
        "contract_version": product.contract_version,
        "lane": product.layer,
        "observation_month": month,
        "input_snapshot_id": SNAPSHOT_ID,
        "base_part": by_tier["13"],
    }
    objects[base_key] = json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
    base_receipt = receipt(base_key, kind="base_checkpoint")
    tier_key = f"{product.data_root}/_checkpoints/tiers/year=2026/month=08.json"
    tier = {
        "contract_version": product.contract_version,
        "lane": product.layer,
        "observation_month": month,
        "input_snapshot_id": SNAPSHOT_ID,
        "base_checkpoint_key": base_key,
        "base_checkpoint_sha256": base_receipt["sha256"],
        "tiers": by_tier,
    }
    objects[tier_key] = json.dumps(tier, sort_keys=True, separators=(",", ":")).encode()
    inventory = [
        base_receipt,
        receipt(tier_key, kind="tier_checkpoint"),
        *(receipt(key, kind="z13_data" if "zoom=13" in key else "coarse_data") for key in parts),
    ]
    manifest = {
        "contract_version": product.contract_version,
        "input_snapshot_id": SNAPSHOT_ID,
        "lane": product.layer,
        "lane_prefix": f"{product.data_root}/",
        "object_receipts": inventory,
    }
    manifest_payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_key = f"{product.metadata_root}/manifest.json"
    objects[manifest_key] = manifest_payload
    objects[f"{product.metadata_root}/_COMPLETE"] = json.dumps(
        {"manifest_key": manifest_key, "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return FakeStore(objects)


def _closed_direct_daily_store(product: SnapshotProduct, parts: list[str]) -> FakeStore:
    objects = {key: f"parquet:{key}".encode() for key in parts}
    serving_parts = [
        {
            "key": key,
            "byte_count": len(objects[key]),
            "sha256": hashlib.sha256(objects[key]).hexdigest(),
        }
        for key in parts
    ]
    manifest = {
        "contract_version": product.contract_version,
        "source_snapshot_id": SNAPSHOT_ID,
        "lane": product.layer,
        "destination_prefix": f"{product.data_root}/",
        "day_count": 1,
        "observation_day_min": "2026-08-01",
        "observation_day_max": "2026-08-01",
        "serving_part_count": len(serving_parts),
        "serving_parts": serving_parts,
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_key = f"{product.metadata_root}/manifest.json"
    objects[manifest_key] = payload
    objects[f"{product.metadata_root}/_COMPLETE"] = json.dumps(
        {"manifest_key": manifest_key, "manifest_sha256": hashlib.sha256(payload).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return FakeStore(objects)


def _closed_verified_daily_lane_store(product: SnapshotProduct, parts: list[str]) -> FakeStore:
    objects = {key: f"parquet:{key}".encode() for key in parts}

    def receipt(key: str) -> dict[str, object]:
        return {
            "key": key,
            "row_count": 1,
            "byte_count": len(objects[key]),
            "sha256": hashlib.sha256(objects[key]).hexdigest(),
        }

    day = "2026-08-01"
    month = "2026-08"
    by_tier = {str(tier): receipt(next(key for key in parts if f"zoom={tier:02d}" in key)) for tier in ZOOM_TIERS}
    base_key = f"{product.data_root}/_checkpoints/base/year=2026/month=08.json"
    base = {
        "contract_version": product.contract_version,
        "lane": product.layer,
        "observation_month": month,
        "input_snapshot_id": SNAPSHOT_ID,
        "day_parts": [{"day": day, **by_tier["13"]}],
        "output_objects": [by_tier["13"]],
    }
    objects[base_key] = json.dumps(base, sort_keys=True, separators=(",", ":")).encode()
    tier_key = f"{product.data_root}/_checkpoints/tiers/year=2026/month=08.json"
    tier = {
        "contract_version": product.contract_version,
        "lane": product.layer,
        "observation_month": month,
        "input_snapshot_id": SNAPSHOT_ID,
        "base_checkpoint_key": base_key,
        "days": [{"day": day, "tiers": by_tier}],
        "output_objects": [by_tier[str(tier)] for tier in (0, 5, 9)],
    }
    objects[tier_key] = json.dumps(tier, sort_keys=True, separators=(",", ":")).encode()

    marker_lines: list[str] = []
    for phase, checkpoint_key, _checkpoint, outputs in (
        ("base", base_key, base, base["output_objects"]),
        ("tiers", tier_key, tier, tier["output_objects"]),
    ):
        marker_key = f"{product.data_root}/_verification/phase={phase}/year=2026/month=08.json"
        checkpoint_payload = objects[checkpoint_key]
        output_lines = [f"{item['key']}:{item['row_count']}:{item['byte_count']}:{item['sha256']}" for item in outputs]
        marker = {
            "contract_version": product.contract_version,
            "lane": product.layer,
            "phase": phase,
            "observation_month": month,
            "checkpoint_key": checkpoint_key,
            "checkpoint_byte_count": len(checkpoint_payload),
            "checkpoint_sha256": hashlib.sha256(checkpoint_payload).hexdigest(),
            "output_receipt_digest": snapshot_products._lineage_digest(output_lines),
            "marker_key": marker_key,
        }
        marker_payload = json.dumps(marker, sort_keys=True, separators=(",", ":")).encode()
        objects[marker_key] = marker_payload
        marker_lines.append(f"{marker_key}:{len(marker_payload)}:{hashlib.sha256(marker_payload).hexdigest()}")
    manifest = {
        "contract_version": product.contract_version,
        "input_snapshot_id": SNAPSHOT_ID,
        "lane": product.layer,
        "lane_prefix": f"{product.data_root}/",
        "data_day_count": 1,
        "observation_day_min": day,
        "observation_day_max": day,
        "verification_marker_count": 2,
        "verification_marker_digest": snapshot_products._lineage_digest(marker_lines),
        "tiers": {str(tier): {"part_count": 1} for tier in ZOOM_TIERS},
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest_key = f"{product.metadata_root}/manifest.json"
    objects[manifest_key] = payload
    objects[f"{product.metadata_root}/_COMPLETE"] = json.dumps(
        {"manifest_key": manifest_key, "manifest_sha256": hashlib.sha256(payload).hexdigest()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return FakeStore(objects)


def _write_rows(path: Path, days: list[date], *, extra_column: bool = False) -> None:
    values: dict[str, object] = {
        "observed_day": pa.array(days, type=pa.date32()),
        "cell_longitude": pa.array([-120.0 + index for index in range(len(days))], type=pa.float64()),
        "cell_latitude": pa.array([40.0 + index for index in range(len(days))], type=pa.float64()),
        "normalized_value": pa.array([float(index) for index in range(len(days))], type=pa.float64()),
    }
    if extra_column:
        values["drifted"] = pa.array(["x"] * len(days), type=pa.string())
    pq.write_table(pa.table(values), path)


def test_monthly_reader_requires_the_exact_requested_day_without_carry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product()
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in (0, 5, 9, 13)]
    store = _closed_store(
        product, parts, manifest_fields={"rungs": {str(tier): {"parts": 1} for tier in (0, 5, 9, 13)}}
    )
    local = tmp_path / "month.parquet"
    _write_rows(local, [date(2026, 8, 1), date(2026, 8, 3)])
    session = LocalSession(duckdb.connect(), {key: str(local) for key in parts})
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    try:
        assert isinstance(resolve_snapshot_product(store, session, scope=scope, day=date(2026, 8, 2)), DayNotWritten)
        answer = resolve_snapshot_product(store, session, scope=scope, day=date(2026, 8, 3))
    finally:
        session.connection.close()

    assert isinstance(answer, PublishedDay)
    assert answer.requested_day == answer.served_day == date(2026, 8, 3)
    assert len(answer.rows) == 1
    assert store.listings == [], "a closed manifest supplies the serving population without a full-prefix LIST"


def test_monthly_window_shares_one_row_budget_and_propagates_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-window-budget")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    local = tmp_path / "window.parquet"
    _write_rows(
        local,
        [
            date(2026, 8, 1),
            date(2026, 8, 1),
            date(2026, 8, 2),
            date(2026, 8, 2),
            date(2026, 8, 3),
            date(2026, 8, 3),
        ],
    )
    session = LocalSession(duckdb.connect(), {key: str(local) for key in parts})
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    expected_budget = 3
    monkeypatch.setattr(snapshot_products, "WINDOW_ROW_BUDGET", expected_budget)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    try:
        answers = resolve_snapshot_window(
            store,
            session,
            scope=scope,
            first_day=date(2026, 8, 1),
            last_day=date(2026, 8, 3),
        )
    finally:
        session.connection.close()

    assert all(isinstance(answer, PublishedDay) for answer in answers)
    published = tuple(answer for answer in answers if isinstance(answer, PublishedDay))
    assert sum(len(answer.rows) for answer in published) == expected_budget
    assert [len(answer.rows) for answer in published] == [2, 1, 0]
    assert [answer.truncated for answer in published] == [False, True, True]
    selected_key = next(key for key in parts if "zoom=13" in key)
    assert store.reads.count(selected_key) == 1, "one verified monthly receipt is reused across the window"


def test_monthly_checkpoint_overwrite_refuses_the_closed_product() -> None:
    product = _product("test-checkpoint-overwrite")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    checkpoint_key = next(key for key in store.objects if "/_checkpoints/" in key)
    store.objects[checkpoint_key] = b'{"overwritten":true}'

    with pytest.raises(ServingRefusalError) as raised:
        load_snapshot_evidence(store, product)

    assert raised.value.code == "snapshot_unpublished"
    assert "checkpoint receipt no longer matches" in raised.value.message


def test_monthly_parquet_overwrite_refuses_before_duckdb_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-parquet-overwrite")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    selected_key = next(key for key in parts if "zoom=13" in key)
    store.objects[selected_key] = b"overwritten-parquet"

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("overwritten Parquet must be refused before DuckDB")

    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    with pytest.raises(ServingRefusalError) as raised:
        resolve_snapshot_product(
            store, LocalSession(NoQuery(), {key: key for key in parts}), scope=scope, day=date(2026, 8, 1)
        )  # type: ignore[arg-type]

    assert raised.value.code == "snapshot_schema_mismatch"
    assert "closed receipt chain" in raised.value.message


def test_monthly_parquet_overwrite_refuses_even_when_requested_day_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-absent-day-parquet-overwrite")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    selected_key = next(key for key in parts if "zoom=13" in key)
    store.objects[selected_key] = b"overwritten-month-before-absent-day-query"

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("bound month parts must be hashed before testing whether a day exists")

    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    with pytest.raises(ServingRefusalError) as raised:
        resolve_snapshot_product(
            store,
            LocalSession(NoQuery(), {key: key for key in parts}),  # type: ignore[arg-type]
            scope=scope,
            day=date(2026, 8, 2),
        )

    assert raised.value.code == "snapshot_schema_mismatch"
    assert "closed receipt chain" in raised.value.message


def test_daily_parquet_overwrite_refuses_and_coverage_never_gets_serving_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-daily-receipts", layout="daily")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("daily closed coverage and an overwritten receipt must not open DuckDB")

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    census = build_snapshot_coverage(store)
    assert len(census.lanes) == len(ZOOM_TIERS)
    assert not any(key.endswith(".parquet") for key in store.reads)

    selected_key = next(key for key in parts if "zoom=13" in key)
    store.objects[selected_key] = b"overwritten-daily-parquet"
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    with pytest.raises(ServingRefusalError) as raised:
        resolve_snapshot_product(
            store,
            LocalSession(NoQuery(), {key: key for key in parts}),  # type: ignore[arg-type]
            scope=scope,
            day=date(2026, 8, 1),
        )
    assert raised.value.code == "snapshot_schema_mismatch"


def test_verified_daily_lane_binds_markers_checkpoints_and_parts_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-verified-daily-lane", layout="daily")
    product = SnapshotProduct(
        layer=product.layer,
        layout=product.layout,
        data_root=product.data_root,
        metadata_root=product.metadata_root,
        schema_columns=product.schema_columns,
        contract_version="plantgeo.signal-product-breakdown.v1",
    )
    parts = [_part(product, tier, date(2026, 8, 1), monthly=False) for tier in ZOOM_TIERS]
    closed = _closed_verified_daily_lane_store(product, parts)

    @dataclass
    class ConcurrentMetadataStore(FakeStore):
        barrier: threading.Barrier = field(default_factory=lambda: threading.Barrier(2))
        lock: threading.Lock = field(default_factory=threading.Lock)
        active: int = 0
        max_active: int = 0

        def read_object(self, relative_key: str) -> bytes | None:
            if "/_verification/" not in relative_key:
                return super().read_object(relative_key)
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                self.barrier.wait(timeout=5)
                return super().read_object(relative_key)
            finally:
                with self.lock:
                    self.active -= 1

    store = ConcurrentMetadataStore(objects=closed.objects)
    evidence = load_snapshot_evidence(store, product)

    assert set(evidence.part_receipts) == set(parts)
    expected_marker_workers = 2
    assert store.max_active == expected_marker_workers, "verification markers must use the bounded metadata worker pool"
    assert not any(key.endswith(".parquet") for key in store.reads)

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("an overwritten verified daily part must be refused before DuckDB")

    selected_key = next(key for key in parts if "zoom=13" in key)
    store.objects[selected_key] = b"overwritten-verified-daily-part"
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    with pytest.raises(ServingRefusalError) as raised:
        resolve_snapshot_product(
            store,
            LocalSession(NoQuery(), {key: key for key in parts}),  # type: ignore[arg-type]
            scope=scope,
            day=date(2026, 8, 1),
        )
    assert raised.value.code == "snapshot_schema_mismatch"


def test_process_evidence_cache_is_reused_across_wrappers_for_one_backend() -> None:
    product = _product("test-stable-backend-cache")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    closed = _closed_store(product, parts)
    prefix = "warehouse/"

    @dataclass
    class Backend:
        objects: dict[str, bytes]
        bucket: str = "plantgeo-test"
        reads: list[str] = field(default_factory=list)

        def get(self, key: str) -> bytes | None:
            self.reads.append(key)
            return self.objects.get(key)

    backend = Backend({f"{prefix}{key}": payload for key, payload in closed.objects.items()})
    first = ObjectStoreSnapshotStore(backend=backend, prefix=prefix)  # type: ignore[arg-type]
    second = ObjectStoreSnapshotStore(backend=backend, prefix=prefix)  # type: ignore[arg-type]

    first_evidence = load_snapshot_evidence(first, product)
    first_read_count = len(backend.reads)
    second_evidence = load_snapshot_evidence(second, product)

    assert second_evidence is first_evidence
    assert backend.reads[first_read_count:] == [
        f"{prefix}{product.metadata_root}/manifest.json",
        f"{prefix}{product.metadata_root}/_COMPLETE",
    ]


def test_monthly_lane_manifest_binds_base_then_tier_checkpoint_receipts() -> None:
    layer = "test-monthly-lane-chain"
    root = f"derived-canonical/signal-observation/lane={layer}/snapshot={SNAPSHOT_ID}"
    product = SnapshotProduct(
        layer=layer,
        layout="monthly",
        data_root=root,
        metadata_root=root,
        schema_columns=TEST_COLUMNS,
        contract_version="plantgeo.signal-product-breakdown.v1",
    )
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_lane_store(product, parts)

    evidence = load_snapshot_evidence(store, product)

    assert set(evidence.part_receipts) == set(parts)
    assert set(evidence.parts_by_tier) == set(ZOOM_TIERS)
    assert all(len(keys) == 1 for keys in evidence.parts_by_tier.values())


def test_bbox_is_exact_and_schema_drift_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    product = _product("test-schema-product")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in (0, 5, 9, 13)]
    store = _closed_store(product, parts)
    local = tmp_path / "drift.parquet"
    _write_rows(local, [date(2026, 8, 1)], extra_column=True)
    session = LocalSession(duckdb.connect(), {key: str(local) for key in parts})
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(
        layer=product.layer,
        kind="observed",
        tier=13,
        bbox=BoundingBox(west=-121, south=39, east=-119, north=41),
    )
    try:
        with pytest.raises(ServingRefusalError, match="allowlisted serving schema") as raised:
            resolve_snapshot_product(store, session, scope=scope, day=date(2026, 8, 1))
    finally:
        session.connection.close()
    assert raised.value.code == "snapshot_schema_mismatch"


def test_unbound_completion_never_exposes_a_snapshot() -> None:
    product = _product("test-unbound-product")
    store = _closed_store(product, [_part(product, 13, date(2026, 8, 1), monthly=True)])
    store.objects[f"{product.metadata_root}/_COMPLETE"] = b'{"manifest_sha256":"wrong"}'

    with pytest.raises(ServingRefusalError) as raised:
        load_snapshot_evidence(store, product)

    assert raised.value.code == "snapshot_unpublished"


def test_registered_product_families_pin_their_exact_top_level_schemas() -> None:
    signal = PRODUCT_BY_LAYER["climate-field-air-temperature-mean"]
    dew = PRODUCT_BY_LAYER["climate-field-dew-point"]
    wetness = PRODUCT_BY_LAYER["soil-wetness-surface"]
    temperature = PRODUCT_BY_LAYER["soil-temperature-0-to-7cm"]

    assert SIGNAL_PRODUCT_COLUMNS == (
        "support_key",
        "signal_name",
        "normalized_unit",
        "cell_id",
        "observed_day",
        "normalized_value",
        "observation_count",
        "newest_observed_at",
        "coverage_fraction",
        "allowed_client_exposure",
        "cell_longitude",
        "cell_latitude",
    )
    assert (
        *SIGNAL_PRODUCT_COLUMNS,
        "selected_observation_id",
        "selected_canonical_row_sha256",
        "selected_source_release_id",
        "selected_release_retrieved_at",
        "physical_candidate_count",
        "lineage_sha256",
        "input_manifest_sha256",
    ) == SOIL_WETNESS_COLUMNS
    assert (
        "data_source_key",
        "source_parameter",
        *SOIL_WETNESS_COLUMNS,
    ) == SOIL_TEMPERATURE_COLUMNS
    assert snapshot_product_columns(signal) == frozenset(SIGNAL_PRODUCT_COLUMNS)
    assert snapshot_product_columns(wetness) == frozenset(SOIL_WETNESS_COLUMNS)
    assert snapshot_product_columns(temperature) == frozenset(SOIL_TEMPERATURE_COLUMNS)
    assert signal.coverage_cell_grid_name == dew.coverage_cell_grid_name == "nasa-power-0.5-degree"
    assert signal.coverage_cells_per_day == dew.coverage_cells_per_day == NASA_POWER_GRID_CELL_COUNT


def test_dew_point_is_registered_only_at_its_pinned_closed_snapshot() -> None:
    product = PRODUCT_BY_LAYER["climate-field-dew-point"]

    assert product.layout == "monthly"
    assert product.data_root == f"layer=climate-field-dew-point/snapshot={SNAPSHOT_ID}"
    assert product.metadata_root == product.data_root
    assert product.contract_version == "plantgeo.dew-point.snapshot-product.v1"
    assert product.expected_manifest_sha256 == "c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a"


def test_declared_and_fixed_lattice_coverage_use_only_bound_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = _product("test-declared-coverage")
    declared_parts = [_part(declared, tier, date(2026, 8, 1), monthly=True) for tier in (0, 5, 9, 13)]
    declared_store = _closed_store(
        declared,
        declared_parts,
        manifest_fields={
            "data_day_count": 3,
            "observation_day_min": "2026-08-01",
            "observation_day_max": "2026-08-03",
            "tiers": {str(tier): {"part_count": 1} for tier in (0, 5, 9, 13)},
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (declared,))
    census = build_snapshot_coverage(declared_store)
    assert len(census.lanes) == len(ZOOM_TIERS)
    assert not census.withheld
    assert all(row.earliest_day == date(2026, 8, 1) and row.latest_day == date(2026, 8, 3) for row in census.lanes)
    assert not any(key.endswith(".parquet") for key in declared_store.reads)

    original = _product("test-air-style")
    air_style = SnapshotProduct(
        layer=original.layer,
        layout=original.layout,
        data_root=original.data_root,
        metadata_root=original.metadata_root,
        schema_columns=original.schema_columns,
        contract_version=original.contract_version,
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=NASA_POWER_GRID_CELL_COUNT,
    )
    air_parts = [_part(air_style, tier, date(2026, 8, 1), monthly=True) for tier in (0, 5, 9, 13)]
    row_counts = dict.fromkeys(air_parts, NASA_POWER_GRID_CELL_COUNT * 3)
    air_store = _closed_store(
        air_style,
        air_parts,
        part_rows=row_counts,
        manifest_fields={
            "product": {
                "stream": air_style.layer,
                "cell_grid_name": "nasa-power-0.5-degree",
                "observed_grain": [
                    "support_key",
                    "signal_name",
                    "normalized_unit",
                    "cell_id",
                    "observation_day",
                ],
            },
            "source_observation_day_min": "2026-08-01",
            "source_observation_day_max": "2026-08-03",
            "totals": {
                "excluded_rows": 0,
                "release_winner_rows": NASA_POWER_GRID_CELL_COUNT * 3,
                "rungs": {str(tier): {"parts": 1, "rows": NASA_POWER_GRID_CELL_COUNT * 3} for tier in (0, 5, 9, 13)},
            },
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (air_style,))
    census = build_snapshot_coverage(air_store)
    assert len(census.lanes) == len(ZOOM_TIERS)
    assert not census.withheld
    assert all(row.earliest_day == date(2026, 8, 1) and row.latest_day == date(2026, 8, 3) for row in census.lanes)
    assert not any(key.endswith(".parquet") for key in air_store.reads)


def test_fixed_lattice_coverage_refuses_rows_without_the_signed_unique_cell_day_grain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _product("test-fixed-lattice-grain")
    product = SnapshotProduct(
        layer=original.layer,
        layout=original.layout,
        data_root=original.data_root,
        metadata_root=original.metadata_root,
        schema_columns=original.schema_columns,
        contract_version=original.contract_version,
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=NASA_POWER_GRID_CELL_COUNT,
    )
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(
        product,
        parts,
        part_rows=dict.fromkeys(parts, NASA_POWER_GRID_CELL_COUNT * 3),
        manifest_fields={
            "product": {
                "stream": product.layer,
                "cell_grid_name": "nasa-power-0.5-degree",
                "observed_grain": ["cell_id"],
            },
            "source_observation_day_min": "2026-08-01",
            "source_observation_day_max": "2026-08-03",
            "totals": {
                "excluded_rows": 0,
                "release_winner_rows": NASA_POWER_GRID_CELL_COUNT * 3,
                "rungs": {str(tier): {"parts": 1, "rows": NASA_POWER_GRID_CELL_COUNT * 3} for tier in ZOOM_TIERS},
            },
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    census = build_snapshot_coverage(store)

    assert not census.lanes
    assert len(census.withheld) == 1
    assert census.withheld[0].layer == product.layer
    assert "unique cell-day contract" in census.withheld[0].message
    assert not any(key.endswith(".parquet") for key in store.reads)


def test_fixed_lattice_coverage_refuses_a_short_month_without_reading_parquet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _product("test-fixed-lattice-short-month")
    product = SnapshotProduct(
        layer=original.layer,
        layout=original.layout,
        data_root=original.data_root,
        metadata_root=original.metadata_root,
        schema_columns=original.schema_columns,
        contract_version=original.contract_version,
        coverage_cell_grid_name="nasa-power-0.5-degree",
        coverage_cells_per_day=NASA_POWER_GRID_CELL_COUNT,
    )
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    short_rows = NASA_POWER_GRID_CELL_COUNT * 2
    store = _closed_store(
        product,
        parts,
        part_rows=dict.fromkeys(parts, short_rows),
        manifest_fields={
            "product": {
                "stream": product.layer,
                "cell_grid_name": "nasa-power-0.5-degree",
                "observed_grain": [
                    "support_key",
                    "signal_name",
                    "normalized_unit",
                    "cell_id",
                    "observation_day",
                ],
            },
            "source_observation_day_min": "2026-08-01",
            "source_observation_day_max": "2026-08-03",
            "totals": {
                "excluded_rows": 0,
                "release_winner_rows": short_rows,
                "rungs": {str(tier): {"parts": 1, "rows": short_rows} for tier in ZOOM_TIERS},
            },
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    census = build_snapshot_coverage(store)

    assert not census.lanes
    assert len(census.withheld) == 1
    assert "rows do not prove every 2026-08 day" in census.withheld[0].message
    assert not any(key.endswith(".parquet") for key in store.reads)


def test_cold_coverage_verifies_products_concurrently_with_a_bounded_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    products = tuple(_product(f"test-cold-product-{index}") for index in range(5))
    stores = []
    for product in products:
        parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
        stores.append(
            _closed_store(
                product,
                parts,
                manifest_fields={
                    "data_day_count": 1,
                    "observation_day_min": "2026-08-01",
                    "observation_day_max": "2026-08-01",
                    "tiers": {str(tier): {"part_count": 1} for tier in ZOOM_TIERS},
                },
            )
        )

    @dataclass
    class ConcurrentProductStore(FakeStore):
        release: threading.Event = field(default_factory=threading.Event)
        lock: threading.Lock = field(default_factory=threading.Lock)
        active: int = 0
        max_active: int = 0

        def read_object(self, relative_key: str) -> bytes | None:
            if not relative_key.endswith("/manifest.json"):
                return super().read_object(relative_key)
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.active == snapshot_products.SNAPSHOT_COVERAGE_PRODUCT_WORKERS:
                    self.release.set()
            try:
                assert self.release.wait(timeout=5)
                return super().read_object(relative_key)
            finally:
                with self.lock:
                    self.active -= 1

    store = ConcurrentProductStore(objects={key: value for held in stores for key, value in held.objects.items()})

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", products)
    census = build_snapshot_coverage(store)

    assert store.max_active == snapshot_products.SNAPSHOT_COVERAGE_PRODUCT_WORKERS
    assert len(census.lanes) == len(products) * len(ZOOM_TIERS)
    assert [census.lanes[index * len(ZOOM_TIERS)].layer for index in range(len(products))] == [
        product.layer for product in products
    ]
    assert not census.withheld
    assert not any(key.endswith(".parquet") for key in store.reads)


def test_one_unclosed_product_is_withheld_without_erasing_healthy_product_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy = _product("test-healthy-coverage")
    broken = _product("test-broken-coverage")
    healthy_parts = [_part(healthy, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(
        healthy,
        healthy_parts,
        manifest_fields={
            "data_day_count": 1,
            "observation_day_min": "2026-08-01",
            "observation_day_max": "2026-08-01",
            "tiers": {str(tier): {"part_count": 1} for tier in ZOOM_TIERS},
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (healthy, broken))
    census = build_snapshot_coverage(store)

    assert {lane.layer for lane in census.lanes} == {healthy.layer}
    assert len(census.lanes) == len(ZOOM_TIERS)
    assert len(census.withheld) == 1
    assert census.withheld[0].layer == broken.layer
    assert census.withheld[0].code == "snapshot_unpublished"
    assert broken.layer in census.withheld[0].message


def test_one_monthly_product_without_metadata_day_proof_is_withheld_without_erasing_later_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = _product("test-corrupt-coverage")
    healthy = _product("test-later-healthy-coverage")
    broken_parts = [_part(broken, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    healthy_parts = [_part(healthy, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    broken_store = _closed_store(
        broken,
        broken_parts,
        manifest_fields={"rungs": {str(tier): {"parts": 1} for tier in ZOOM_TIERS}},
    )
    healthy_store = _closed_store(
        healthy,
        healthy_parts,
        manifest_fields={
            "data_day_count": 1,
            "observation_day_min": "2026-08-01",
            "observation_day_max": "2026-08-01",
            "tiers": {str(tier): {"part_count": 1} for tier in ZOOM_TIERS},
        },
    )
    store = FakeStore({**broken_store.objects, **healthy_store.objects})

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (broken, healthy))
    census = build_snapshot_coverage(store)

    assert {lane.layer for lane in census.lanes} == {healthy.layer}
    assert len(census.withheld) == 1
    assert census.withheld[0].layer == broken.layer
    assert census.withheld[0].code == "snapshot_unpublished"
    assert "exact daily coverage range" in census.withheld[0].message


def test_a_malformed_manifest_tier_count_becomes_a_product_local_typed_withholding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _product("test-malformed-tier-count")
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(
        product,
        parts,
        manifest_fields={
            "data_day_count": 1,
            "observation_day_min": "2026-08-01",
            "observation_day_max": "2026-08-01",
            "tiers": {str(tier): {"part_count": "one"} for tier in ZOOM_TIERS},
        },
    )

    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    census = build_snapshot_coverage(store)

    assert not census.lanes
    assert len(census.withheld) == 1
    assert census.withheld[0].layer == product.layer
    assert census.withheld[0].code == "snapshot_unpublished"
    assert "part count differs" in census.withheld[0].message
