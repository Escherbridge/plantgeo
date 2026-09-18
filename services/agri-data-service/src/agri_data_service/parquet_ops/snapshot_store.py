"""Bounded object reads for snapshot products, and the value types every later stage passes around.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    _SHA256,
    MAX_MANIFEST_BYTES,
    MAX_SNAPSHOT_KEYS,
    SnapshotProduct,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from datetime import date

    from agri_data_service.parquet_ops.wire import CoverageWithholding, LaneCoverage
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend


class SnapshotStore(Protocol):
    """The bounded object operations an immutable snapshot read needs."""

    def cache_identity(self) -> tuple[object, ...]: ...

    def iter_keys(self, relative_prefix: str) -> Iterator[str]: ...

    def read_object(self, relative_key: str) -> bytes | None: ...

    def relative_key(self, persisted_key: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ObjectStoreSnapshotStore:
    """A relative-key snapshot store over the configured object-store backend."""

    backend: ObjectStoreBackend
    prefix: str = ""

    def cache_identity(self) -> tuple[object, ...]:
        """Identify one process-held backend namespace across per-request wrappers."""
        return ("object-store", id(self.backend), getattr(self.backend, "bucket", None), self.prefix)

    def key_for(self, relative_key: str) -> str:
        return f"{self.prefix}{relative_key}"

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        found = 0
        absolute_prefix = self.key_for(relative_prefix)
        for listed in self.backend.list_objects(absolute_prefix):
            if not listed.key.startswith(self.prefix):
                continue
            relative = listed.key[len(self.prefix) :]
            if not relative.startswith(relative_prefix):
                continue
            found += 1
            if found > MAX_SNAPSHOT_KEYS:
                raise faults.census_budget_exhausted(listed_keys=MAX_SNAPSHOT_KEYS)
            yield relative

    def read_object(self, relative_key: str) -> bytes | None:
        return self.backend.get(self.key_for(relative_key))

    def relative_key(self, persisted_key: str) -> str:
        if self.prefix and persisted_key.startswith(self.prefix):
            return persisted_key[len(self.prefix) :]
        return persisted_key


@dataclass(frozen=True, slots=True)
class SnapshotEvidence:
    """A closed snapshot plus the exact serving objects found under its allowlisted root."""

    product: SnapshotProduct
    manifest: Mapping[str, object]
    keys: tuple[str, ...]
    parts_by_tier: Mapping[int, tuple[str, ...]]
    part_receipts: Mapping[str, SnapshotObjectReceipt]


@dataclass(frozen=True, slots=True)
class SnapshotObjectReceipt:
    """One serving object whose identity is transitively bound to the closed manifest."""

    key: str
    byte_count: int
    sha256: str
    row_count: int | None = None


@dataclass(frozen=True, slots=True)
class SnapshotCoverageWithholding:
    """Why one product is absent from coverage without being called never-written."""

    layer: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ForwardAvailability:
    """One product's forward half, proven from its lane's availability index and never by listing."""

    published_days: frozenset[date]
    absent_days: frozenset[date]
    source_ceiling: date
    generation_sha256: str
    pointer_key: str


@dataclass(frozen=True, slots=True)
class ForwardAvailabilityWithheld:
    """Why a product's forward half may not be published, in the census's own withholding vocabulary."""

    reason: CoverageWithholding
    detail: str


class ForwardAvailabilityPort(Protocol):
    """How this module asks for a forward half without importing the reader that answers.

    Declared HERE and implemented in `parquet_ops/availability_coverage.py`, because `coverage.py`
    already imports this module -- an import back the other way at module scope would close a cycle.
    """

    def forward_days(self, *, layer: str, first_day: date) -> ForwardAvailability | ForwardAvailabilityWithheld: ...


@dataclass(frozen=True, slots=True)
class SnapshotCoverageCensus:
    """Healthy rung evidence plus exact product-local reasons withheld from the frozen wire."""

    lanes: tuple[LaneCoverage, ...]
    withheld: tuple[SnapshotCoverageWithholding, ...]


def _required_json_bytes(store: SnapshotStore, key: str, product: SnapshotProduct) -> bytes:
    payload = store.read_object(key)
    if payload is None or not payload or len(payload) > MAX_MANIFEST_BYTES:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is absent, empty, or over the metadata byte limit",
        )
    return payload


def _json_object(payload: bytes, *, key: str, product: SnapshotProduct) -> Mapping[str, object]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is not valid JSON",
        ) from exc
    if not isinstance(value, dict):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{key} is not a JSON object",
        )
    return value


def _verify_manifest_identity(manifest: Mapping[str, object], product: SnapshotProduct) -> None:
    if product.contract_version is not None and manifest.get("contract_version") != product.contract_version:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest contract version is not the allowlisted builder contract",
        )
    snapshot_values = tuple(
        manifest.get(name) for name in ("snapshot_id", "source_snapshot_id", "input_snapshot_id") if name in manifest
    )
    if not snapshot_values or any(value != product.snapshot_id for value in snapshot_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest does not bind the allowlisted snapshot id",
        )
    identity_values: list[str] = []
    for name in ("lane", "destination_prefix", "destination_root", "lane_prefix"):
        value = manifest.get(name)
        if isinstance(value, str):
            identity_values.append(value)
    product_block = manifest.get("product")
    if isinstance(product_block, dict):
        identity_values.extend(str(product_block[name]) for name in ("stream", "lane") if name in product_block)
    if not any(value == product.layer or value.rstrip("/") == product.data_root for value in identity_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest does not bind the allowlisted layer and data root",
        )


class _IdentityStore:
    """Normalize already-relative checkpoint receipts without object access."""

    def cache_identity(self) -> tuple[object, ...]:
        return ("identity-only",)

    def iter_keys(self, relative_prefix: str) -> Iterator[str]:
        del relative_prefix
        return iter(())

    def read_object(self, relative_key: str) -> bytes | None:
        del relative_key
        return None

    def relative_key(self, persisted_key: str) -> str:
        return persisted_key


def _lineage_digest(values: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _object_receipt(
    store: SnapshotStore,
    value: object,
    product: SnapshotProduct,
    *,
    context: str,
) -> SnapshotObjectReceipt:
    if not isinstance(value, Mapping):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt is not an object",
        )
    raw_key = value.get("key")
    raw_sha256 = value.get("sha256")
    byte_values = [value[name] for name in ("bytes", "byte_count") if name in value]
    row_values = [value[name] for name in ("rows", "row_count") if name in value and value[name] is not None]
    if (
        not isinstance(raw_key, str)
        or not isinstance(raw_sha256, str)
        or _SHA256.fullmatch(raw_sha256) is None
        or len(byte_values) != 1
        or not isinstance(byte_values[0], int)
        or isinstance(byte_values[0], bool)
        or byte_values[0] <= 0
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt has an invalid key, byte count, or SHA-256",
        )
    if len(row_values) > 1 or (
        row_values and (not isinstance(row_values[0], int) or isinstance(row_values[0], bool) or row_values[0] < 0)
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"{context} receipt has an invalid row count",
        )
    row_count = row_values[0] if row_values else None
    assert row_count is None or isinstance(row_count, int)
    return SnapshotObjectReceipt(
        key=store.relative_key(raw_key),
        byte_count=byte_values[0],
        sha256=raw_sha256,
        row_count=row_count,
    )


def _read_bound_receipt(
    store: SnapshotStore,
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    metadata: bool,
) -> bytes:
    payload = store.read_object(receipt.key)
    if (
        payload is None
        or (metadata and len(payload) > MAX_MANIFEST_BYTES)
        or len(payload) != receipt.byte_count
        or hashlib.sha256(payload).hexdigest() != receipt.sha256
    ):
        if metadata:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint receipt no longer matches {receipt.key}",
            )
        raise faults.snapshot_schema_mismatch(
            layer=product.layer,
            key=receipt.key,
            detail="serving Parquet bytes do not match the closed receipt chain",
        )
    return payload
