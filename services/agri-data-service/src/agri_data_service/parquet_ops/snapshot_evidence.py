"""One manifest read per product, memoised behind a per-key lock and a bounded cache.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import hashlib
import threading
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    _DAILY_PART,
    _MONTHLY_PART,
    MAX_EVIDENCE_CACHE_ENTRIES,
    SnapshotProduct,
)
from agri_data_service.parquet_ops.snapshot_receipts import (
    _daily_serving_receipts,
    _monthly_serving_receipts,
)
from agri_data_service.parquet_ops.snapshot_store import (
    SnapshotEvidence,
    SnapshotObjectReceipt,
    SnapshotStore,
    _json_object,
    _required_json_bytes,
    _verify_manifest_identity,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

type EvidenceCacheKey = tuple[tuple[object, ...], SnapshotProduct, str]

_EVIDENCE_CACHE: dict[EvidenceCacheKey, SnapshotEvidence] = {}
_EVIDENCE_LOCKS: dict[EvidenceCacheKey, threading.Lock] = {}
_EVIDENCE_CACHE_LOCK = threading.Lock()


def load_snapshot_evidence(store: SnapshotStore, product: SnapshotProduct) -> SnapshotEvidence:
    """Bind manifest bytes to `_COMPLETE`, then retain only exact serving-part paths."""
    manifest_key = f"{product.metadata_root}/manifest.json"
    complete_key = f"{product.metadata_root}/_COMPLETE"
    manifest_payload = _required_json_bytes(store, manifest_key, product)
    complete = _json_object(_required_json_bytes(store, complete_key, product), key=complete_key, product=product)
    digest = hashlib.sha256(manifest_payload).hexdigest()
    bound_manifest_key = complete.get("manifest_key")
    if (
        not isinstance(bound_manifest_key, str)
        or store.relative_key(bound_manifest_key) != manifest_key
        or complete.get("manifest_sha256") != digest
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="_COMPLETE does not bind the exact allowlisted manifest",
        )
    if product.expected_manifest_sha256 is not None and digest != product.expected_manifest_sha256:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest checksum differs from the pinned production receipt",
        )
    cache_key = (store.cache_identity(), product, digest)
    with _EVIDENCE_CACHE_LOCK:
        held = _EVIDENCE_CACHE.get(cache_key)
        lock = _EVIDENCE_LOCKS.setdefault(cache_key, threading.Lock())
    if held is not None:
        return held
    with lock:
        with _EVIDENCE_CACHE_LOCK:
            held = _EVIDENCE_CACHE.get(cache_key)
        if held is not None:
            return held
        evidence = _load_snapshot_evidence_uncached(store, product, manifest_payload, manifest_key=manifest_key)
        with _EVIDENCE_CACHE_LOCK:
            if len(_EVIDENCE_CACHE) >= MAX_EVIDENCE_CACHE_ENTRIES:
                _EVIDENCE_CACHE.clear()
                _EVIDENCE_LOCKS.clear()
                _EVIDENCE_LOCKS[cache_key] = lock
            _EVIDENCE_CACHE[cache_key] = evidence
        return evidence


def _load_snapshot_evidence_uncached(
    store: SnapshotStore,
    product: SnapshotProduct,
    manifest_payload: bytes,
    *,
    manifest_key: str,
) -> SnapshotEvidence:
    manifest = _json_object(manifest_payload, key=manifest_key, product=product)
    _verify_manifest_identity(manifest, product)
    part_receipts: Mapping[str, SnapshotObjectReceipt]
    if product.layout == "monthly":
        part_receipts = _monthly_serving_receipts(store, manifest, product)
    else:
        part_receipts = _daily_serving_receipts(store, manifest, product)
    keys = tuple(sorted(part_receipts))
    matcher = _DAILY_PART if product.layout == "daily" else _MONTHLY_PART
    parts: dict[int, list[str]] = {tier: [] for tier in ZOOM_TIERS}
    for key in keys:
        matched = matcher.search(key)
        if matched is None or not key.startswith(f"{product.data_root}/"):
            continue
        parts[int(matched.group("zoom"))].append(key)
    if not all(parts.values()):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="manifest evidence does not bind one serving population at every required tier",
        )
    return SnapshotEvidence(
        product=product,
        manifest=manifest,
        keys=keys,
        parts_by_tier={tier: tuple(values) for tier, values in parts.items()},
        part_receipts=part_receipts,
    )


def clear_snapshot_evidence_cache() -> None:
    """Clear process-lifetime immutable evidence, used by tests and explicit lifecycle resets."""
    with _EVIDENCE_CACHE_LOCK:
        _EVIDENCE_CACHE.clear()
        _EVIDENCE_LOCKS.clear()
