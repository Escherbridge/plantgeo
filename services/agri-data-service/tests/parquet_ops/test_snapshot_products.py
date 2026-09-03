"""Immutable snapshot products stay closed, exact-day, schema-bound, and coverage-bounded."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    partition_path,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import snapshot_products
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import BoundingBox, ReadScope
from agri_data_service.parquet_ops.snapshot_products import (
    PRODUCT_BY_LAYER,
    SIGNAL_PRODUCT_COLUMNS,
    SNAPSHOT_ID,
    SNAPSHOT_PRODUCTS,
    SOIL_TEMPERATURE_COLUMNS,
    SOIL_WETNESS_COLUMNS,
    ForwardAvailability,
    ForwardAvailabilityWithheld,
    ObjectStoreSnapshotStore,
    SnapshotProduct,
    build_snapshot_coverage,
    load_snapshot_evidence,
    resolve_snapshot_product,
    resolve_snapshot_window,
    snapshot_product_columns,
)
from agri_data_service.parquet_ops.wire import (
    DayNotWritten,
    DayRange,
    DeclaredListCell,
    GovernedAbsenceDay,
    PublishedDay,
)
from agri_data_service.pipeline.direct.climate.products import CLIMATE_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.direct.soil.products import SOIL_DIRECT_WRITER_START_DAY
from agri_data_service.warehouse.parquet.schema import get_stream_schema

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
        schema_layer="signal",
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


def _snapshot_lineage_table() -> pa.Table:
    schema = get_stream_schema("climate-field-relative-humidity", "observed").arrow_schema
    values: dict[str, pa.Array] = {}
    for schema_field in schema:
        if pa.types.is_string(schema_field.type):
            value: object = "a" * 64 if "sha256" in schema_field.name else f"test-{schema_field.name}"
        elif pa.types.is_int64(schema_field.type):
            value = 1
        elif pa.types.is_float64(schema_field.type):
            value = 52.0
        elif pa.types.is_boolean(schema_field.type):
            value = True
        elif pa.types.is_date32(schema_field.type):
            value = date(2026, 8, 1)
        elif pa.types.is_timestamp(schema_field.type):
            value = datetime(2026, 8, 1, tzinfo=UTC)
        elif pa.types.is_list(schema_field.type):
            item = 1 if pa.types.is_int64(schema_field.type.value_type) else "a" * 64
            value = [item]
        else:
            raise AssertionError(f"test fixture has no value for {schema_field.name}: {schema_field.type}")
        values[schema_field.name] = pa.array([value], type=schema_field.type)
    return pa.table(values, schema=schema)


def _snapshot_lineage_test_product() -> SnapshotProduct:
    original = _product("test-lineage-wire")
    return SnapshotProduct(
        layer=original.layer,
        layout=original.layout,
        data_root=original.data_root,
        metadata_root=original.metadata_root,
        schema_layer="climate-field-relative-humidity",
        contract_version=original.contract_version,
    )


def test_snapshot_reader_renders_the_complete_registered_lineage_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product = _snapshot_lineage_test_product()
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    local = tmp_path / "lineage.parquet"
    pq.write_table(_snapshot_lineage_table(), local)
    session = LocalSession(duckdb.connect(), {key: str(local) for key in parts})
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    try:
        answer = resolve_snapshot_product(store, session, scope=scope, day=date(2026, 8, 1))
    finally:
        session.connection.close()

    assert isinstance(answer, PublishedDay)
    schema = get_stream_schema("climate-field-relative-humidity", "observed").arrow_schema
    assert tuple(answer.rows[0]) == tuple(schema.names)
    list_columns = tuple(schema_field.name for schema_field in schema if pa.types.is_list(schema_field.type))
    assert list_columns == (
        "input_source_row_ids",
        "input_source_row_sha256s",
        "input_source_release_ids",
        "input_source_part_keys",
        "input_source_part_sha256s",
        "input_source_row_ordinals",
    )
    assert all(isinstance(answer.rows[0][name], DeclaredListCell) for name in list_columns)
    rendered = answer.to_wire()["rows"]
    assert isinstance(rendered, list)
    assert tuple(rendered[0]) == tuple(schema.names)
    assert all(isinstance(rendered[0][name], list) for name in list_columns)


@pytest.mark.parametrize(
    "drifted_values",
    [
        pa.array([["wrong-element-type"]], type=pa.list_(pa.string())),
        pa.array(["wrong-container-type"], type=pa.string()),
    ],
    ids=("list-element", "list-to-scalar"),
)
def test_snapshot_reader_refuses_same_name_lineage_type_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drifted_values: pa.Array,
) -> None:
    product = _snapshot_lineage_test_product()
    parts = [_part(product, tier, date(2026, 8, 1), monthly=True) for tier in ZOOM_TIERS]
    store = _closed_store(product, parts)
    table = _snapshot_lineage_table()
    index = table.schema.get_field_index("input_source_row_ids")
    table = table.set_column(index, "input_source_row_ids", drifted_values)
    local = tmp_path / "lineage-drift.parquet"
    pq.write_table(table, local)
    session = LocalSession(duckdb.connect(), {key: str(local) for key in parts})
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    try:
        with pytest.raises(ServingRefusalError) as raised:
            resolve_snapshot_product(store, session, scope=scope, day=date(2026, 8, 1))
    finally:
        session.connection.close()

    assert raised.value.code == "snapshot_schema_mismatch"
    assert "input_source_row_ids" in raised.value.message


def test_snapshot_arrow_list_types_bind_to_their_exact_duckdb_shape() -> None:
    assert snapshot_products._duckdb_type_for_arrow(pa.list_(pa.int64())) == "BIGINT[]"
    assert snapshot_products._duckdb_type_for_arrow(pa.large_list(pa.string())) == "VARCHAR[]"
    assert snapshot_products._duckdb_type_for_arrow(pa.list_(pa.int64(), 3)) == "BIGINT[3]"


def test_unbound_completion_never_exposes_a_snapshot() -> None:
    product = _product("test-unbound-product")
    store = _closed_store(product, [_part(product, 13, date(2026, 8, 1), monthly=True)])
    store.objects[f"{product.metadata_root}/_COMPLETE"] = b'{"manifest_sha256":"wrong"}'

    with pytest.raises(ServingRefusalError) as raised:
        load_snapshot_evidence(store, product)

    assert raised.value.code == "snapshot_unpublished"


def test_registered_product_families_pin_their_exact_top_level_schemas() -> None:
    signal = PRODUCT_BY_LAYER["climate-field-air-temperature-mean"]
    humidity = PRODUCT_BY_LAYER["climate-field-relative-humidity"]
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
    assert snapshot_product_columns(humidity) == frozenset(
        get_stream_schema(humidity.layer, "observed").arrow_schema.names
    )
    assert snapshot_product_columns(wetness) == frozenset(SOIL_WETNESS_COLUMNS)
    assert snapshot_product_columns(temperature) == frozenset(SOIL_TEMPERATURE_COLUMNS)
    assert signal.coverage_cell_grid_name == dew.coverage_cell_grid_name == "nasa-power-0.5-degree"
    assert signal.coverage_cells_per_day == dew.coverage_cells_per_day == NASA_POWER_GRID_CELL_COUNT


def test_every_product_with_a_live_writer_declares_that_writer_s_own_forward_edge() -> None:
    """Two upstreams, two release schedules, two edges -- and a product with none reports a frozen last day.

    The six climate products and the three NASA POWER soil-wetness lanes open one day after the
    canonical snapshot's 2026-08-06; the five snapshot-rooted ERA5-Land soil products one day after
    their own 2026-08-02. Borrowing one edge for the other would either hide four real days behind
    the manifest or route four days at the live lane that the manifest still owns.
    """
    power_forward = {
        "climate-field-air-temperature-mean",
        "climate-field-air-temperature-max",
        "climate-field-air-temperature-min",
        "climate-field-relative-humidity",
        "climate-field-dew-point",
        "climate-field-wind-speed",
        "soil-wetness-surface",
        "soil-wetness-root-zone",
        "soil-wetness-profile",
    }
    era5_land_forward = {
        "soil-field-vpd",
        "soil-temperature-0-to-7cm",
        "soil-temperature-7-to-28cm",
        "soil-temperature-28-to-100cm",
        "soil-temperature-100-to-255cm",
    }

    for layer in sorted(power_forward):
        assert PRODUCT_BY_LAYER[layer].forward_first_day == CLIMATE_DIRECT_WRITER_START_DAY, layer
    for layer in sorted(era5_land_forward):
        assert PRODUCT_BY_LAYER[layer].forward_first_day == SOIL_DIRECT_WRITER_START_DAY, layer
    assert CLIMATE_DIRECT_WRITER_START_DAY != SOIL_DIRECT_WRITER_START_DAY
    assert {
        product.layer for product in SNAPSHOT_PRODUCTS if product.forward_first_day is not None
    } == power_forward | era5_land_forward


def test_a_forward_day_of_a_soil_product_is_read_through_its_lane_and_not_its_manifest() -> None:
    """`forward_first_day` is the ONE boundary: below it the closed manifest, at or above it the lane."""
    product = PRODUCT_BY_LAYER["soil-field-vpd"]
    assert product.forward_first_day is not None

    assert snapshot_products.serves_from_snapshot(product.layer, product.forward_first_day - timedelta(days=1)) is True
    assert snapshot_products.serves_from_snapshot(product.layer, product.forward_first_day) is False


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
    assert all(row.coverage_authority == "census" for row in census.lanes), (
        "an immutable product owns no availability index until it is bootstrapped"
    )
    assert all(row.source_ceiling_day == date(2026, 8, 3) for row in census.lanes), (
        "the MANIFEST's declared last day, so the census's evaluated-through day is not read as a "
        "claim that a frozen snapshot is current through it"
    )
    assert all(row.withheld_reason is None and row.required_rungs == () for row in census.lanes)

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


# --- The forward edge: days a live writer owns, past the frozen manifest's reach ------------------


FORWARD_FIRST_DAY = date(2026, 8, 7)
FORWARD_DAY = date(2026, 8, 8)
CLOSED_DAY = date(2026, 8, 1)
#: A real registered stream, because a forward day is served through `DuckDbRowReader`, which reads
#: the lane's REGISTERED schema for spatial support and declared list cells.
FORWARD_LAYER = "climate-field-wind-speed"
#: Rows the forward day's own Parquet holds, so the count proves the LANE part was read.
FORWARD_DAY_ROW_COUNT = 2


def _forward_product(layer: str = FORWARD_LAYER) -> SnapshotProduct:
    """A daily product frozen only BELOW `forward_first_day`, which is the six climate products' shape."""
    return replace(_product(layer, layout="daily"), forward_first_day=FORWARD_FIRST_DAY)


def _forward_lane_objects(
    layer: str,
    day: date,
    *,
    marked: bool = True,
    absent: bool = False,
) -> dict[str, bytes]:
    """Write one live lane-day in the ORDINARY layout, with or without the marker that admits it."""
    objects: dict[str, bytes] = {}
    for tier in ZOOM_TIERS:
        if absent:
            objects[absence_marker_path(layer, "observed", tier, day)] = GovernedAbsence(
                reason="every source cell answered with a fill value",
                upstream_response="{}",
                recorded_at=datetime(2026, 8, 9, tzinfo=UTC),
                run_id="forward-test",
            ).to_json_bytes()
            continue
        objects[partition_path(layer, "observed", tier, day)] = b"parquet:forward"
        if marked:
            objects[completion_marker_path(layer, "observed", tier, day)] = json.dumps(
                {"part_count": 1, "row_count": 1, "completed_at": "2026-08-09T00:00:00+00:00", "run_id": "fwd"},
                sort_keys=True,
            ).encode()
    return objects


def test_a_forward_day_enters_coverage_only_once_its_completion_marker_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half an export is not coverage. Admitting an unmarked day would put a truncated release on the map."""
    product = _forward_product("test-forward-marker")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    store.objects.update(_forward_lane_objects(product.layer, FORWARD_DAY, marked=False))
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))

    unmarked = build_snapshot_coverage(store)

    assert not unmarked.withheld
    assert {lane.latest_day for lane in unmarked.lanes} == {CLOSED_DAY}

    store.objects.update(_forward_lane_objects(product.layer, FORWARD_DAY, marked=True))
    snapshot_products.clear_snapshot_evidence_cache()
    marked = build_snapshot_coverage(store)

    assert not marked.withheld
    assert len(marked.lanes) == len(ZOOM_TIERS)
    assert {lane.latest_day for lane in marked.lanes} == {FORWARD_DAY}
    assert {lane.earliest_day for lane in marked.lanes} == {CLOSED_DAY}
    assert {lane.source_ceiling_day for lane in marked.lanes} == {FORWARD_DAY}, (
        "a ceiling below the newest day the rung proves reads as a lane serving days its source never made"
    )


def test_the_manifest_equality_check_speaks_only_for_the_closed_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The manifest is silent above the boundary, so requiring it to agree there refuses every live day."""
    product = _forward_product("test-forward-equality")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    store.objects.update(_forward_lane_objects(product.layer, FORWARD_DAY))
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))

    census = build_snapshot_coverage(store)

    assert not census.withheld, "a forward day must not read as a tier disagreeing with the manifest"
    assert all(lane.published_ranges for lane in census.lanes)


def test_a_frozen_product_lists_nothing_while_a_forward_one_lists_its_lane_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The census's metadata-only promise still holds for every product with no live edge."""
    frozen = _product("test-frozen-no-listing", layout="daily")
    frozen_parts = [_part(frozen, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(frozen, frozen_parts)
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (frozen,))

    build_snapshot_coverage(store)

    assert store.listings == []

    forward = _forward_product("test-forward-one-listing")
    forward_store = _closed_direct_daily_store(
        forward, [_part(forward, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    )
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (forward,))
    snapshot_products.clear_snapshot_evidence_cache()

    build_snapshot_coverage(forward_store)

    assert forward_store.listings == [f"layer={forward.layer}/kind=observed/"], (
        "one listing serves all four rungs; one per rung would quadruple the census's object-store cost"
    )


def test_a_forward_day_is_read_through_the_lane_path_and_a_closed_day_through_its_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The boundary decides which proof a day is served under, and both halves answer the same layer."""
    product = _forward_product()
    closed_parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, closed_parts)
    forward_objects = _forward_lane_objects(product.layer, FORWARD_DAY)
    store.objects.update(forward_objects)

    closed_local = tmp_path / "closed.parquet"
    _write_rows(closed_local, [CLOSED_DAY])
    forward_local = tmp_path / "forward.parquet"
    _write_rows(forward_local, [FORWARD_DAY, FORWARD_DAY])
    files = {key: str(closed_local) for key in closed_parts}
    files.update({key: str(forward_local) for key in forward_objects if key.endswith(".parquet")})
    session = LocalSession(duckdb.connect(), files)
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)
    try:
        closed = resolve_snapshot_product(store, session, scope=scope, day=CLOSED_DAY)
        assert store.listings == [], "a closed day is proven by its receipts and lists nothing"
        forward = resolve_snapshot_product(store, session, scope=scope, day=FORWARD_DAY)
    finally:
        session.connection.close()

    assert isinstance(closed, PublishedDay)
    assert len(closed.rows) == 1

    assert isinstance(forward, PublishedDay)
    assert forward.requested_day == forward.served_day == FORWARD_DAY
    assert len(forward.rows) == FORWARD_DAY_ROW_COUNT
    assert store.listings == [f"layer={product.layer}/kind=observed/zoom=13/year=2026/month=08/day=08/"], (
        "a forward day lists ONE day prefix, never the whole tier"
    )


def test_a_forward_day_with_no_objects_is_day_not_written_rather_than_a_snapshot_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The closed half already proves the lane published, so `lane_never_written` would be a lie."""
    product = _forward_product("test-forward-empty")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("an unwritten forward day must never open DuckDB")

    answer = resolve_snapshot_product(
        store,
        LocalSession(NoQuery(), {}),  # type: ignore[arg-type]
        scope=scope,
        day=FORWARD_DAY,
    )

    assert isinstance(answer, DayNotWritten)


def test_a_forward_governed_absence_is_served_as_one_rather_than_as_an_unwritten_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POWER answers whole fill-value days; serving one as `day_not_written` hides a real upstream fact."""
    product = _forward_product("test-forward-absence")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    store.objects.update(_forward_lane_objects(product.layer, FORWARD_DAY, absent=True))
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)

    class NoQuery:
        def execute(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("a governed absence must never open DuckDB")

    answer = resolve_snapshot_product(
        store,
        LocalSession(NoQuery(), {}),  # type: ignore[arg-type]
        scope=scope,
        day=FORWARD_DAY,
    )

    assert isinstance(answer, GovernedAbsenceDay)
    assert answer.absence.run_id == "forward-test"


# --- The forward half is authority-aware: an index answer, or a withholding, but never a LIST -----

#: The lane's own horizon, deliberately ahead of the newest forward day it has published.
FORWARD_CEILING = date(2026, 8, 10)
FORWARD_GENERATION_SHA256 = "9b1f0c4d2a7e63518c0dfb2e94a7150c3d6b8e2f41905ac7db3e6f82c150a4d7"


class ExplodingListingStore(FakeStore):
    """A snapshot store that fails the test the instant the coverage path asks it for object keys."""

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        # Deliberately NOT a generator: a generator body would only run once something iterated it,
        # and a listing that is built and dropped is exactly the regression this must catch.
        raise AssertionError(f"the availability path listed {relative_prefix!r}")


@dataclass
class ScriptedForwardAvailability:
    """The `ForwardAvailabilityPort` a coverage test scripts in one line."""

    answer: ForwardAvailability | ForwardAvailabilityWithheld
    asked: list[tuple[str, date]] = field(default_factory=list)

    def forward_days(self, *, layer: str, first_day: date) -> ForwardAvailability | ForwardAvailabilityWithheld:
        self.asked.append((layer, first_day))
        return self.answer


def _availability_store(product: SnapshotProduct, parts: list[str]) -> ExplodingListingStore:
    """The same closed manifest evidence, behind a store that refuses every listing."""
    listing = _closed_direct_daily_store(product, parts)
    return ExplodingListingStore(objects=listing.objects)


def test_the_forward_half_under_availability_authority_lists_nothing_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tripwire: every `iter_keys` raises, so a surviving census proves no prefix was walked."""
    product = _forward_product("test-forward-no-listing")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _availability_store(product, parts)
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    port = ScriptedForwardAvailability(
        ForwardAvailability(
            published_days=frozenset({FORWARD_DAY}),
            absent_days=frozenset(),
            source_ceiling=FORWARD_CEILING,
            generation_sha256=FORWARD_GENERATION_SHA256,
            pointer_key=f"layer={product.layer}/kind=observed/availability/_LATEST.json",
        )
    )

    census = build_snapshot_coverage(store, policy="availability", forward_availability=port)

    assert port.asked == [(product.layer, FORWARD_FIRST_DAY)]
    assert not census.withheld
    assert {lane.coverage_authority for lane in census.lanes} == {"availability"}
    assert {lane.latest_day for lane in census.lanes} == {FORWARD_DAY}
    assert {lane.source_ceiling_day for lane in census.lanes} == {FORWARD_CEILING}
    assert {lane.availability_generation_sha256 for lane in census.lanes} == {FORWARD_GENERATION_SHA256}


def test_a_product_with_no_forward_index_withholds_its_forward_half_rather_than_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `availability` a missing index is evidence to withhold, never permission to scan."""
    product = _forward_product("test-forward-unpublished")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _availability_store(product, parts)
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    port = ScriptedForwardAvailability(
        ForwardAvailabilityWithheld(reason="availability_unpublished", detail="no pointer has been published")
    )

    census = build_snapshot_coverage(store, policy="availability", forward_availability=port)

    assert {lane.withheld_reason for lane in census.lanes} == {"availability_unpublished"}
    # THE WHOLE PRODUCT IS WITHHELD, closed half included. The client withholds a capability on ANY
    # non-null `withheld_reason`, so a row shipping the manifest's bounds beside one publishes days
    # nothing will ever draw -- and `tests/contract/test_wire_contract.py`'s "a withheld lane
    # publishes no selectable days" is exactly the shape this row has to hold to.
    assert {lane.earliest_day for lane in census.lanes} == {None}
    assert {lane.latest_day for lane in census.lanes} == {None}
    assert {lane.published_ranges for lane in census.lanes} == {()}
    assert {lane.gap_ranges for lane in census.lanes} == {()}
    assert {lane.governed_absence_ranges for lane in census.lanes} == {()}


def test_a_withheld_product_row_is_shaped_exactly_like_a_withheld_lane_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The producer-side mirror of the wire contract: one shape for withholding, not two."""
    product = _forward_product("test-forward-withheld-shape")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _availability_store(product, parts)
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))
    port = ScriptedForwardAvailability(
        ForwardAvailabilityWithheld(reason="availability_checksum_invalid", detail="bytes disagree")
    )

    census = build_snapshot_coverage(store, policy="availability", forward_availability=port)

    assert census.lanes, "a withheld product stays ON the wire, offering nothing"
    for lane in census.lanes:
        assert lane.withheld_reason == "availability_checksum_invalid"
        assert lane.earliest_day is None
        assert lane.latest_day is None
        assert lane.published_ranges == ()
        assert lane.gap_ranges == ()
        assert lane.governed_absence_ranges == ()


def test_a_forward_governed_absence_is_a_governed_absence_in_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settled empty day reported as a gap tells a client to keep asking for a day the lane closed."""
    product = _forward_product("test-forward-absence-coverage")
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    store.objects.update(_forward_lane_objects(product.layer, FORWARD_DAY, absent=True))
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))

    census = build_snapshot_coverage(store)

    assert not census.withheld
    for lane in census.lanes:
        assert lane.governed_absence_ranges == (DayRange(first_day=FORWARD_DAY, last_day=FORWARD_DAY),)
        assert all(entry.first_day > FORWARD_DAY or entry.last_day < FORWARD_DAY for entry in lane.gap_ranges), (
            "a governed absence is accounted for, so it may not also be reported as a hole"
        )


def test_a_manifest_that_claims_a_forward_day_refuses_the_whole_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A day excluded from the equality check and unioned in anyway is an unverified claim published as closed."""
    product = _forward_product("test-forward-manifest-conflict")
    parts = [_part(product, tier, FORWARD_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    monkeypatch.setattr(snapshot_products, "SNAPSHOT_PRODUCTS", (product,))

    census = build_snapshot_coverage(store)

    assert census.lanes == ()
    assert [entry.code for entry in census.withheld] == ["snapshot_manifest_conflict"]


def test_a_straddling_window_returns_both_halves_in_one_row_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two differently-ordered halves in one answer is a client-visible ordering bug at the boundary."""
    product = _forward_product()
    parts = [_part(product, tier, CLOSED_DAY, monthly=False) for tier in ZOOM_TIERS]
    store = _closed_direct_daily_store(product, parts)
    forward_objects = _forward_lane_objects(product.layer, FORWARD_DAY)
    store.objects.update(forward_objects)
    monkeypatch.setitem(snapshot_products.PRODUCT_BY_LAYER, product.layer, product)

    forward_local = tmp_path / "forward-unordered.parquet"
    _write_unordered_rows(forward_local, FORWARD_DAY)
    files = {key: str(tmp_path / "closed.parquet") for key in store.objects if key.endswith(".parquet")}
    _write_rows(tmp_path / "closed.parquet", [CLOSED_DAY])
    files.update({key: str(forward_local) for key in forward_objects if key.endswith(".parquet")})
    session = LocalSession(duckdb.connect(), files)
    scope = ReadScope(layer=product.layer, kind="observed", tier=13, bbox=None)

    try:
        answer = resolve_snapshot_product(store, session, scope=scope, day=FORWARD_DAY)
    finally:
        session.connection.close()

    assert isinstance(answer, PublishedDay)
    longitudes = [row["cell_longitude"] for row in answer.rows]
    assert longitudes == sorted(longitudes), "the forward half must arrive in the closed half's own order"


def _write_unordered_rows(path: Path, day: date) -> None:
    """Write one forward day whose PHYSICAL order is not its lon/lat order."""
    table = pa.table(
        {
            "observed_day": pa.array([day, day, day], type=pa.date32()),
            "cell_longitude": pa.array([-116.5, -120.25, -118.0], type=pa.float64()),
            "cell_latitude": pa.array([44.0, 44.0, 44.0], type=pa.float64()),
            "normalized_value": pa.array([1.0, 2.0, 3.0], type=pa.float64()),
        }
    )
    pq.write_table(table, path)
