"""The fail-closed receipt graph: every served part traced to a checkpoint and a verification marker.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Literal

from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.snapshot_product_catalog import (
    _DAILY_PART,
    _LANE_BASE_CHECKPOINT,
    _LANE_TIER_CHECKPOINT,
    _MONTHLY_PART,
    _PRODUCT_CHECKPOINT,
    _SHA256,
    _VERIFICATION_MARKER,
    BASE_ZOOM_TIER,
    MAX_MANIFEST_BYTES,
    MAX_MONTHLY_RECEIPT_OBJECTS,
    MAX_SNAPSHOT_KEYS,
    METADATA_VERIFY_WORKERS,
    SnapshotProduct,
)
from agri_data_service.parquet_ops.snapshot_store import (
    SnapshotEvidence,
    SnapshotObjectReceipt,
    SnapshotStore,
    _IdentityStore,
    _json_object,
    _lineage_digest,
    _object_receipt,
    _read_bound_receipt,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _daily_serving_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    """Resolve each supported daily builder through its persisted cryptographic receipt family."""
    if isinstance(manifest.get("serving_parts"), list):
        return _direct_daily_receipts(store, manifest, product)
    if isinstance(manifest.get("month_checkpoints"), list):
        return _relative_humidity_receipts(store, manifest, product)
    if "verification_marker_digest" in manifest:
        return _verified_lane_daily_receipts(store, manifest, product)
    raise faults.snapshot_unpublished(
        layer=product.layer,
        snapshot_id=product.snapshot_id,
        detail="daily manifest has no supported serving receipt inventory",
    )


def _direct_daily_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_parts = manifest.get("serving_parts")
    if not isinstance(raw_parts, list) or not raw_parts or len(raw_parts) > MAX_SNAPSHOT_KEYS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily serving receipt inventory is absent or over the object limit",
        )
    expected_count = manifest.get("serving_part_count")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool) or expected_count != len(raw_parts):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily serving receipt count differs from the closed manifest",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for raw in raw_parts:
        receipt = _object_receipt(store, raw, product, context="daily manifest serving part")
        _verify_daily_part_identity(receipt, product)
        if receipt.key in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily manifest repeats serving part {receipt.key}",
            )
        result[receipt.key] = receipt
    return result


def _relative_humidity_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_checkpoints = manifest.get("month_checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="relative-humidity manifest has no monthly checkpoint receipts",
        )
    jobs: list[tuple[str, str, str]] = []
    months: set[str] = set()
    for raw in raw_checkpoints:
        if not isinstance(raw, Mapping):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="relative-humidity checkpoint summary is not an object",
            )
        raw_key = raw.get("key")
        raw_sha256 = raw.get("sha256")
        month = raw.get("month")
        if (
            not isinstance(raw_key, str)
            or not isinstance(raw_sha256, str)
            or _SHA256.fullmatch(raw_sha256) is None
            or not isinstance(month, str)
            or re.fullmatch(r"\d{4}-\d{2}", month) is None
            or month in months
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="relative-humidity checkpoint summary has an invalid key, month, or SHA-256",
            )
        key = store.relative_key(raw_key)
        expected_key = f"{product.metadata_root}/_checkpoints/month={month}.json"
        if key != expected_key:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint escaped its allowlisted month: {key}",
            )
        months.add(month)
        jobs.append((month, key, raw_sha256))

    def verify_checkpoint(job: tuple[str, str, str]) -> tuple[str, tuple[SnapshotObjectReceipt, ...]]:
        month, key, raw_sha256 = job
        payload = store.read_object(key)
        if (
            payload is None
            or not payload
            or len(payload) > MAX_MANIFEST_BYTES
            or hashlib.sha256(payload).hexdigest() != raw_sha256
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint receipt no longer matches {key}",
            )
        checkpoint = _json_object(payload, key=key, product=product)
        if (
            checkpoint.get("contract_version") != product.contract_version
            or checkpoint.get("source_snapshot_id") != product.snapshot_id
            or checkpoint.get("lane") != product.layer
            or checkpoint.get("month") != month
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint identity differs for {month}",
            )
        days = checkpoint.get("days")
        if not isinstance(days, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"relative-humidity checkpoint {month} has no day inventory",
            )
        checkpoint_parts: list[SnapshotObjectReceipt] = []
        for raw_day in days:
            if not isinstance(raw_day, Mapping) or not isinstance(raw_day.get("objects"), list):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"relative-humidity checkpoint {month} has an invalid day receipt",
                )
            for raw_part in raw_day["objects"]:
                if not isinstance(raw_part, Mapping) or raw_part.get("kind") != "part":
                    continue
                receipt = _object_receipt(store, raw_part, product, context="relative-humidity day part")
                _verify_daily_part_identity(receipt, product, expected_day=str(raw_day.get("day")))
                checkpoint_parts.append(receipt)
        return month, tuple(checkpoint_parts)

    result: dict[str, SnapshotObjectReceipt] = {}
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(jobs))) as executor:
        verified = executor.map(verify_checkpoint, jobs)
        for _month, receipts in verified:
            for receipt in receipts:
                if receipt.key in result:
                    raise faults.snapshot_unpublished(
                        layer=product.layer,
                        snapshot_id=product.snapshot_id,
                        detail=f"relative-humidity checkpoints repeat serving part {receipt.key}",
                    )
                result[receipt.key] = receipt
    return result


def _verified_lane_daily_receipts(  # noqa: PLR0912, PLR0915 - fail-closed receipt graph validation
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_count = manifest.get("verification_marker_count")
    raw_digest = manifest.get("verification_marker_digest")
    if (
        not isinstance(raw_count, int)
        or isinstance(raw_count, bool)
        or raw_count <= 0
        or raw_count > MAX_MONTHLY_RECEIPT_OBJECTS
        or not isinstance(raw_digest, str)
        or _SHA256.fullmatch(raw_digest) is None
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane manifest has an invalid verification-marker census",
        )
    marker_keys = tuple(sorted(store.iter_keys(f"{product.data_root}/_verification/")))
    if len(marker_keys) != raw_count:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane verification-marker inventory differs from its manifest",
        )

    def verify_marker(
        marker_key: str,
    ) -> tuple[str, tuple[str, str], Mapping[str, object]]:
        matched = _VERIFICATION_MARKER.search(marker_key)
        if matched is None or not marker_key.startswith(f"{product.data_root}/_verification/"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker escaped the allowlisted lane: {marker_key}",
            )
        marker_payload = store.read_object(marker_key)
        if marker_payload is None or not marker_payload or len(marker_payload) > MAX_MANIFEST_BYTES:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker is absent or over budget: {marker_key}",
            )
        marker_sha256 = hashlib.sha256(marker_payload).hexdigest()
        marker_line = f"{marker_key}:{len(marker_payload)}:{marker_sha256}"
        marker = _json_object(marker_payload, key=marker_key, product=product)
        phase = matched.group("phase")
        month = f"{matched.group('year')}-{matched.group('month')}"
        if (
            marker.get("contract_version") != product.contract_version
            or marker.get("lane") != product.layer
            or marker.get("phase") != phase
            or marker.get("observation_month") != month
            or store.relative_key(str(marker.get("marker_key"))) != marker_key
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker identity differs from its path: {marker_key}",
            )
        checkpoint_receipt = _checkpoint_receipt_from_marker(store, marker, product)
        checkpoint_payload = _read_bound_receipt(store, checkpoint_receipt, product, metadata=True)
        checkpoint = _json_object(checkpoint_payload, key=checkpoint_receipt.key, product=product)
        _verify_checkpoint_identity(checkpoint, product, month=month)
        if _checkpoint_output_digest(checkpoint, product) != marker.get("output_receipt_digest"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verification marker does not bind checkpoint outputs: {marker_key}",
            )
        identity = (phase, month)
        return marker_line, identity, checkpoint

    marker_lines: list[str] = []
    checkpoints: dict[tuple[str, str], Mapping[str, object]] = {}
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(marker_keys))) as executor:
        verified = executor.map(verify_marker, marker_keys)
        for marker_line, identity, checkpoint in verified:
            marker_lines.append(marker_line)
            if identity in checkpoints:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane repeats verification phase/month {identity}",
                )
            checkpoints[identity] = checkpoint
    if _lineage_digest(marker_lines) != raw_digest:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane verification-marker digest differs from its manifest",
        )
    months = {month for phase, month in checkpoints if phase == "base"}
    if not months or months != {month for phase, month in checkpoints if phase == "tiers"}:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="daily lane base and tier verification months differ",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for month in sorted(months):
        base = checkpoints[("base", month)]
        tiers = checkpoints[("tiers", month)]
        base_parts: dict[str, SnapshotObjectReceipt] = {}
        raw_base_parts = base.get("day_parts")
        if not isinstance(raw_base_parts, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane base checkpoint {month} has no day parts",
            )
        base_outputs = _checkpoint_output_receipts(store, base, product)
        for raw_part in raw_base_parts:
            receipt = _object_receipt(store, raw_part, product, context="daily lane base part")
            day = str(raw_part.get("day")) if isinstance(raw_part, Mapping) else ""
            _verify_daily_part_identity(receipt, product, expected_day=day, expected_tier=BASE_ZOOM_TIER)
            if base_outputs.get(receipt.key) != receipt or day in base_parts:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane base receipt differs from checkpoint output inventory: {receipt.key}",
                )
            base_parts[day] = receipt
        tier_outputs = _checkpoint_output_receipts(store, tiers, product)
        raw_days = tiers.get("days")
        if not isinstance(raw_days, list):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane tier checkpoint {month} has no day inventory",
            )
        seen_days: set[str] = set()
        for raw_day in raw_days:
            if not isinstance(raw_day, Mapping) or not isinstance(raw_day.get("tiers"), Mapping):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane tier checkpoint {month} has an invalid day",
                )
            day = str(raw_day.get("day"))
            raw_tiers = raw_day["tiers"]
            if day in seen_days or set(raw_tiers) != {str(tier) for tier in ZOOM_TIERS}:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"daily lane tier checkpoint repeats a day or omits a rung: {day}",
                )
            seen_days.add(day)
            for tier in ZOOM_TIERS:
                receipt = _object_receipt(store, raw_tiers[str(tier)], product, context="daily lane tier part")
                _verify_daily_part_identity(receipt, product, expected_day=day, expected_tier=tier)
                bound = base_parts.get(day) if tier == BASE_ZOOM_TIER else tier_outputs.get(receipt.key)
                if bound != receipt or receipt.key in result:
                    raise faults.snapshot_unpublished(
                        layer=product.layer,
                        snapshot_id=product.snapshot_id,
                        detail=f"daily lane tier receipt differs from its verified checkpoint: {receipt.key}",
                    )
                result[receipt.key] = receipt
        if seen_days != set(base_parts):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"daily lane base and tier day inventories differ for {month}",
            )
    return result


def _checkpoint_receipt_from_marker(
    store: SnapshotStore,
    marker: Mapping[str, object],
    product: SnapshotProduct,
) -> SnapshotObjectReceipt:
    value = {
        "key": marker.get("checkpoint_key"),
        "byte_count": marker.get("checkpoint_byte_count"),
        "sha256": marker.get("checkpoint_sha256"),
    }
    return _object_receipt(store, value, product, context="verification marker checkpoint")


def _checkpoint_output_receipts(
    store: SnapshotStore,
    checkpoint: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_outputs = checkpoint.get("output_objects")
    if not isinstance(raw_outputs, list):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="verified checkpoint has no output receipt inventory",
        )
    result: dict[str, SnapshotObjectReceipt] = {}
    for raw in raw_outputs:
        receipt = _object_receipt(store, raw, product, context="checkpoint output")
        if receipt.key in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"verified checkpoint repeats output receipt {receipt.key}",
            )
        result[receipt.key] = receipt
    return result


def _checkpoint_output_digest(checkpoint: Mapping[str, object], product: SnapshotProduct) -> str:
    raw_outputs = checkpoint.get("output_objects")
    if not isinstance(raw_outputs, list):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="verified checkpoint has no output receipt inventory",
        )
    lines: list[str] = []
    for raw in raw_outputs:
        receipt = _object_receipt(_IdentityStore(), raw, product, context="checkpoint output")
        raw_rows = raw.get("row_count") if isinstance(raw, Mapping) else None
        if not isinstance(raw_rows, int) or isinstance(raw_rows, bool) or raw_rows < 0:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint output has an invalid row count: {receipt.key}",
            )
        lines.append(f"{receipt.key}:{raw_rows}:{receipt.byte_count}:{receipt.sha256}")
    return _lineage_digest(lines)


def _verify_daily_part_identity(
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    expected_day: str | None = None,
    expected_tier: int | None = None,
) -> None:
    matched = _DAILY_PART.search(receipt.key)
    if matched is None or not receipt.key.startswith(f"{product.data_root}/kind=observed/"):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"daily serving receipt escaped its allowlisted product path: {receipt.key}",
        )
    day = f"{matched.group('year')}-{matched.group('month')}-{matched.group('day')}"
    tier = int(matched.group("zoom"))
    if (expected_day is not None and day != expected_day) or (expected_tier is not None and tier != expected_tier):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"daily serving receipt differs from its checkpoint day/tier: {receipt.key}",
        )


def _monthly_serving_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    """Resolve monthly parts only through the exact checkpoint family bound by the manifest."""
    if isinstance(manifest.get("checkpoints"), list):
        return _product_checkpoint_receipts(store, manifest, product)
    if isinstance(manifest.get("object_receipts"), list):
        return _lane_checkpoint_receipts(store, manifest, product)
    raise faults.snapshot_unpublished(
        layer=product.layer,
        snapshot_id=product.snapshot_id,
        detail="monthly manifest has no supported checkpoint receipt inventory",
    )


def _product_checkpoint_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_checkpoints = manifest.get("checkpoints")
    if not isinstance(raw_checkpoints, list) or not raw_checkpoints:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly product manifest has no checkpoint receipts",
        )
    if len(raw_checkpoints) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly checkpoint inventory exceeds the serving receipt limit",
        )
    expected_tiers = {str(tier) for tier in ZOOM_TIERS}

    def load_checkpoint(raw_checkpoint: object) -> tuple[str, str, tuple[SnapshotObjectReceipt, ...]]:
        checkpoint_receipt = _object_receipt(store, raw_checkpoint, product, context="manifest checkpoint")
        matched = _PRODUCT_CHECKPOINT.search(checkpoint_receipt.key)
        month = f"{matched.group('year')}-{matched.group('month')}" if matched is not None else ""
        if (
            matched is None
            or not checkpoint_receipt.key.startswith(f"{product.data_root}/_checkpoints/")
            or not isinstance(raw_checkpoint, Mapping)
            or raw_checkpoint.get("month") != month
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly checkpoint receipt has an invalid product/month identity",
            )
        checkpoint_payload = _read_bound_receipt(store, checkpoint_receipt, product, metadata=True)
        checkpoint = _json_object(checkpoint_payload, key=checkpoint_receipt.key, product=product)
        _verify_checkpoint_identity(checkpoint, product, month=month)
        raw_rungs = checkpoint.get("rungs")
        if not isinstance(raw_rungs, Mapping) or set(raw_rungs) != expected_tiers:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"checkpoint {checkpoint_receipt.key} does not bind exactly four serving rungs",
            )
        parts: list[SnapshotObjectReceipt] = []
        for tier in ZOOM_TIERS:
            part = _object_receipt(store, raw_rungs[str(tier)], product, context="checkpoint rung")
            _verify_monthly_part_identity(part, product, month=month, tier=tier)
            parts.append(part)
        return (month, checkpoint_receipt.key, tuple(parts))

    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(raw_checkpoints))) as executor:
        loaded = tuple(executor.map(load_checkpoint, raw_checkpoints))
    serving: dict[str, SnapshotObjectReceipt] = {}
    months: set[str] = set()
    checkpoint_keys: set[str] = set()
    for month, checkpoint_key, parts in loaded:
        if month in months or checkpoint_key in checkpoint_keys:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly checkpoint receipt repeats a product/month identity",
            )
        months.add(month)
        checkpoint_keys.add(checkpoint_key)
        for part in parts:
            if part.key in serving:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"checkpoint inventory repeats serving part {part.key}",
                )
            serving[part.key] = part
    return serving


def _lane_checkpoint_receipts(
    store: SnapshotStore,
    manifest: Mapping[str, object],
    product: SnapshotProduct,
) -> Mapping[str, SnapshotObjectReceipt]:
    raw_inventory = manifest.get("object_receipts")
    if not isinstance(raw_inventory, list) or not raw_inventory:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane manifest has no object receipt inventory",
        )
    if len(raw_inventory) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane object inventory exceeds the serving receipt limit",
        )
    inventory: dict[str, tuple[SnapshotObjectReceipt, str]] = {}
    for raw in raw_inventory:
        receipt = _object_receipt(store, raw, product, context="manifest object")
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        if not isinstance(kind, str) or receipt.key in inventory:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail="monthly lane manifest has an invalid or duplicate object receipt",
            )
        inventory[receipt.key] = (receipt, kind)
    base_by_month = _checkpoint_receipts_by_month(inventory, product, kind="base_checkpoint")
    tier_by_month = _checkpoint_receipts_by_month(inventory, product, kind="tier_checkpoint")
    if not base_by_month or set(base_by_month) != set(tier_by_month):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail="monthly lane base and tier checkpoint month inventories differ",
        )
    expected_tiers = {str(tier) for tier in ZOOM_TIERS}

    def load_month(month: str) -> tuple[SnapshotObjectReceipt, ...]:
        base_receipt = base_by_month[month]
        tier_receipt = tier_by_month[month]
        base_payload = _read_bound_receipt(store, base_receipt, product, metadata=True)
        tier_payload = _read_bound_receipt(store, tier_receipt, product, metadata=True)
        base = _json_object(base_payload, key=base_receipt.key, product=product)
        tiers = _json_object(tier_payload, key=tier_receipt.key, product=product)
        _verify_checkpoint_identity(base, product, month=month)
        _verify_checkpoint_identity(tiers, product, month=month)
        bound_base_key = tiers.get("base_checkpoint_key")
        if (
            not isinstance(bound_base_key, str)
            or store.relative_key(bound_base_key) != base_receipt.key
            or tiers.get("base_checkpoint_sha256") != base_receipt.sha256
        ):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"tier checkpoint {tier_receipt.key} does not bind its exact base checkpoint",
            )
        raw_tiers = tiers.get("tiers")
        if not isinstance(raw_tiers, Mapping) or set(raw_tiers) != expected_tiers:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"tier checkpoint {tier_receipt.key} does not bind exactly four serving rungs",
            )
        base_part = _object_receipt(store, base.get("base_part"), product, context="base checkpoint rung")
        parts: list[SnapshotObjectReceipt] = []
        for tier in ZOOM_TIERS:
            part = _object_receipt(store, raw_tiers[str(tier)], product, context="tier checkpoint rung")
            _verify_monthly_part_identity(part, product, month=month, tier=tier)
            manifest_part = inventory.get(part.key)
            expected_kind = "z13_data" if tier == BASE_ZOOM_TIER else "coarse_data"
            if (
                manifest_part is None
                or manifest_part[1] != expected_kind
                or manifest_part[0] != part
                or (tier == BASE_ZOOM_TIER and base_part != part)
            ):
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"checkpoint rung {part.key} differs from the exact manifest receipt chain",
                )
            parts.append(part)
        return tuple(parts)

    months = tuple(sorted(base_by_month))
    with ThreadPoolExecutor(max_workers=min(METADATA_VERIFY_WORKERS, len(months))) as executor:
        loaded = tuple(executor.map(load_month, months))
    serving: dict[str, SnapshotObjectReceipt] = {}
    for parts in loaded:
        for part in parts:
            if part.key in serving:
                raise faults.snapshot_unpublished(
                    layer=product.layer,
                    snapshot_id=product.snapshot_id,
                    detail=f"monthly lane repeats serving part {part.key}",
                )
            serving[part.key] = part
    return serving


def _checkpoint_receipts_by_month(
    inventory: Mapping[str, tuple[SnapshotObjectReceipt, str]],
    product: SnapshotProduct,
    *,
    kind: Literal["base_checkpoint", "tier_checkpoint"],
) -> Mapping[str, SnapshotObjectReceipt]:
    matcher = _LANE_BASE_CHECKPOINT if kind == "base_checkpoint" else _LANE_TIER_CHECKPOINT
    result: dict[str, SnapshotObjectReceipt] = {}
    for receipt, receipt_kind in inventory.values():
        if receipt_kind != kind:
            continue
        matched = matcher.search(receipt.key)
        if matched is None or not receipt.key.startswith(f"{product.data_root}/_checkpoints/"):
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"{kind} receipt escaped the allowlisted lane checkpoint path",
            )
        month = f"{matched.group('year')}-{matched.group('month')}"
        if month in result:
            raise faults.snapshot_unpublished(
                layer=product.layer,
                snapshot_id=product.snapshot_id,
                detail=f"monthly lane repeats the {kind} month {month}",
            )
        result[month] = receipt
    return result


def _verify_checkpoint_identity(
    checkpoint: Mapping[str, object],
    product: SnapshotProduct,
    *,
    month: str,
) -> None:
    if checkpoint.get("contract_version") != product.contract_version or checkpoint.get("observation_month") != month:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint identity differs from the allowlisted contract for {month}",
        )
    snapshot_values = tuple(checkpoint.get(name) for name in ("snapshot_id", "input_snapshot_id") if name in checkpoint)
    if not snapshot_values or any(value != product.snapshot_id for value in snapshot_values):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint does not bind snapshot {product.snapshot_id}",
        )
    identities: list[str] = []
    lane = checkpoint.get("lane")
    if isinstance(lane, str):
        identities.append(lane)
    product_block = checkpoint.get("product")
    if isinstance(product_block, Mapping):
        identities.extend(
            str(product_block[name]) for name in ("stream", "lane") if isinstance(product_block.get(name), str)
        )
    if not identities or product.layer not in identities:
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly checkpoint does not bind allowlisted product {product.layer}",
        )


def _verify_monthly_part_identity(
    receipt: SnapshotObjectReceipt,
    product: SnapshotProduct,
    *,
    month: str,
    tier: int,
) -> None:
    matched = _MONTHLY_PART.search(receipt.key)
    if (
        matched is None
        or not receipt.key.startswith(f"{product.data_root}/kind=observed/")
        or int(matched.group("zoom")) != tier
        or f"{matched.group('year')}-{matched.group('month')}" != month
    ):
        raise faults.snapshot_unpublished(
            layer=product.layer,
            snapshot_id=product.snapshot_id,
            detail=f"monthly serving receipt escaped its allowlisted product/month/tier path: {receipt.key}",
        )


def _verify_bound_parts(
    store: SnapshotStore,
    evidence: SnapshotEvidence,
    keys: Sequence[str],
    *,
    verified: set[str] | None = None,
) -> None:
    pending = tuple(dict.fromkeys(key for key in keys if verified is None or key not in verified))
    if len(pending) > MAX_MONTHLY_RECEIPT_OBJECTS:
        raise faults.snapshot_unpublished(
            layer=evidence.product.layer,
            snapshot_id=evidence.product.snapshot_id,
            detail="monthly serving receipt verification exceeds the object limit",
        )

    def verify(key: str) -> None:
        receipt = evidence.part_receipts.get(key)
        if receipt is None:
            raise faults.snapshot_unpublished(
                layer=evidence.product.layer,
                snapshot_id=evidence.product.snapshot_id,
                detail=f"serving key is not bound through a verified checkpoint receipt: {key}",
            )
        _read_bound_receipt(store, receipt, evidence.product, metadata=False)

    for key in pending:
        verify(key)
    if verified is not None:
        verified.update(pending)
