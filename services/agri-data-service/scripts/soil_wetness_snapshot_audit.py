"""Independently audit the published soil-wetness lane bundle without write capability."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import soil_wetness_snapshot_breakdown as source


@dataclass(frozen=True, slots=True)
class ReadOnlyStore:
    backend: source.SnapshotStore

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes | None:
        return self.backend.get(key, max_bytes=max_bytes)

    def get_exact(self, key: str, *, expected_bytes: int) -> bytes | None:
        return self.backend.get_exact(key, expected_bytes=expected_bytes)

    def list_keys(self, prefix: str):  # type: ignore[no-untyped-def]
        return self.backend.list_keys(prefix)


def canonical_json(store: ReadOnlyStore, key: str, *, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    payload = store.get(key, max_bytes=max_bytes)
    if payload is None:
        raise source.BreakdownError(f"required audit object is missing: {key!r}")
    value = source._json_object(payload, key=key)
    if payload != source._json_bytes(value):
        raise source.BreakdownError(f"audit object is not canonical JSON: {key!r}")
    return payload, value


def receipt_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": str(receipt["key"]),
        "row_count": int(receipt["row_count"]),
        "byte_count": int(receipt["byte_count"]),
        "sha256": str(receipt["sha256"]),
    }


def require_payload(receipt: Mapping[str, Any], payload: bytes, row_count: int) -> None:
    expected = source.table_receipt(str(receipt["key"]), payload, row_count)
    if receipt_fields(receipt) != expected:
        raise source.BreakdownError(f"deterministic receipt mismatch: {receipt['key']!r}")


def day_marker_payload(
    *,
    root: str,
    product: source.Product,
    day: date,
    tier: int,
    part_receipt: Mapping[str, Any],
    input_manifest_sha256: str,
    base_lineage_sha256: str,
) -> tuple[str, bytes]:
    key = f"{source.day_directory(root, tier, day)}/_complete.json"
    marker = {
        "contract_version": source.CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "tier": tier,
        "day": day.isoformat(),
        "part_count": 1,
        "row_count": int(part_receipt["row_count"]),
        "part_key": part_receipt["key"],
        "part_sha256": part_receipt["sha256"],
        "base_lineage_sha256": base_lineage_sha256,
        "input_manifest_sha256": input_manifest_sha256,
    }
    return key, source._json_bytes(marker)


def audit_lane(
    store: ReadOnlyStore,
    contract: source.InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: source.Product,
    verify_workers: int,
) -> dict[str, Any]:
    root = source.lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    manifest_payload, manifest = canonical_json(store, manifest_key, max_bytes=2_000_000)
    _completion_payload, completion = canonical_json(store, completion_key, max_bytes=1_000_000)
    if (
        completion.get("manifest_key") != manifest_key
        or completion.get("manifest_sha256") != source._sha256(manifest_payload)
        or completion.get("input_manifest_sha256") != contract.manifest_sha256
        or manifest.get("lane") != product.lane
        or manifest.get("product_parameter") != product.parameter
        or manifest.get("signal_name") != product.signal_name
        or manifest.get("input_manifest_sha256") != contract.manifest_sha256
    ):
        raise source.BreakdownError(f"published lane identity/pin mismatch: {product.lane}")

    expected_keys: set[str] = {manifest_key, completion_key}
    consumed_raw: dict[str, Mapping[str, Any]] = {}
    aggregate = Counter()
    multiplicities: Counter[int] = Counter()
    all_days: list[str] = []
    input_month_digests: list[str] = []
    selected_month_digests: list[str] = []
    provenance_rows = 0
    provenance_bytes = 0
    tier_totals = {
        str(tier): {"row_count": 0, "part_count": 0, "byte_count": 0, "marker_count": 0}
        for tier in source.ZOOM_TIERS
    }
    verification_receipts: list[dict[str, Any]] = []

    for index, month in enumerate(source.product_months(contract), start=1):
        base_key = source.month_checkpoint_key(root, month)
        tier_key = source.tier_checkpoint_key(root, month)
        _base_payload, base = canonical_json(store, base_key, max_bytes=2_000_000)
        _tier_payload, tiers = canonical_json(store, tier_key, max_bytes=4_000_000)
        for checkpoint, phase in ((base, "base"), (tiers, "tiers")):
            if (
                checkpoint.get("contract_version") != source.CONTRACT_VERSION
                or checkpoint.get("lane") != product.lane
                or checkpoint.get("product_parameter") != product.parameter
                or checkpoint.get("observation_month") != month
                or checkpoint.get("input_manifest_sha256") != contract.manifest_sha256
            ):
                raise source.BreakdownError(f"audit checkpoint identity mismatch: {product.lane} {month} {phase}")
            source.validate_checkpoint_objects(store, checkpoint, verify_workers=verify_workers)  # type: ignore[arg-type]
            marker_key = source.verification_marker_key(root, phase, month)
            expected_marker = source._json_bytes(
                source.checkpoint_verification_marker(
                    checkpoint,
                    key=marker_key,
                    phase=phase,
                    product=product,
                    contract=contract,
                )
            )
            actual_marker = store.get_exact(marker_key, expected_bytes=len(expected_marker))
            if actual_marker != expected_marker:
                raise source.BreakdownError(f"verification marker mismatch: {marker_key!r}")
            verification_receipts.append(
                {
                    "key": marker_key,
                    "byte_count": len(expected_marker),
                    "sha256": source._sha256(expected_marker),
                }
            )
            expected_keys.add(marker_key)
            expected_keys.add(base_key if phase == "base" else tier_key)
            expected_keys.update(str(receipt["key"]) for receipt in checkpoint["output_objects"])

        raw_rows: list[dict[str, Any]] = []
        expected_parts: dict[str, Mapping[str, Any]] = {}
        for batch_index in source.month_batch_indexes(contract, month):
            ledger = source.ledger_for_unit(store, contract, month, batch_index)  # type: ignore[arg-type]
            for metadata in source.product_part_metadata(ledger, product.parameter):
                key = str(metadata["key"])
                if key in expected_parts or key in consumed_raw:
                    raise source.BreakdownError(f"canonical audit part overlaps: {key!r}")
                expected_parts[key] = metadata
                consumed_raw[key] = metadata
                raw_rows.extend(source.load_raw_part(store, metadata, product))  # type: ignore[arg-type]
        checkpoint_parts = {str(item["key"]): item for item in base["input_parts"]}
        if set(checkpoint_parts) != set(expected_parts):
            raise source.BreakdownError(f"audit input-part set mismatch: {product.lane} {month}")
        for key, expected in expected_parts.items():
            if any(checkpoint_parts[key][field] != expected[field] for field in ("row_count", "byte_count", "sha256", "row_digest")):
                raise source.BreakdownError(f"audit raw receipt mismatch: {key!r}")

        provenance, base_by_day, stats = source.classify_month(raw_rows, product, contract)
        for name in (
            "input_physical_rows",
            "eligible_rows",
            "selected_rows",
            "superseded_rows",
            "rejected_rows",
            "duplicate_group_count",
            "max_multiplicity",
            "multiplicity_histogram",
            "input_row_digest",
            "selected_lineage_digest",
        ):
            if base.get(name) != stats[name]:
                raise source.BreakdownError(f"audit classification mismatch: {product.lane} {month} {name}")
        for name in ("input_physical_rows", "eligible_rows", "selected_rows", "superseded_rows", "rejected_rows", "duplicate_group_count"):
            aggregate[name] += int(stats[name])
        for multiplicity, count in stats["multiplicity_histogram"].items():
            multiplicities[int(multiplicity)] += int(count)
        input_month_digests.append(str(stats["input_row_digest"]))
        selected_month_digests.append(str(stats["selected_lineage_digest"]))

        base_outputs = {str(receipt["key"]): receipt for receipt in base["output_objects"]}
        day_receipts = {str(receipt["day"]): receipt for receipt in base["day_parts"]}
        expected_days = [day.isoformat() for day in sorted(base_by_day)]
        if list(base["data_days"]) != expected_days or set(day_receipts) != set(expected_days):
            raise source.BreakdownError(f"audit base day inventory mismatch: {product.lane} {month}")
        all_days.extend(expected_days)
        provenance_key = f"{root}/_provenance/year={month[:4]}/month={month[5:]}/part-0.parquet"
        provenance_payload = source.serialize_table(provenance, source.PROVENANCE_SCHEMA, ("id",))
        provenance_receipt = base_outputs.get(provenance_key)
        if provenance_receipt is None:
            raise source.BreakdownError(f"audit provenance receipt missing: {product.lane} {month}")
        require_payload(provenance_receipt, provenance_payload, len(provenance))
        provenance_rows += len(provenance)
        provenance_bytes += len(provenance_payload)

        tier_outputs = {str(receipt["key"]): receipt for receipt in tiers["output_objects"]}
        tier_days = {str(report["day"]): report for report in tiers["days"]}
        if set(tier_days) != set(expected_days):
            raise source.BreakdownError(f"audit tier day inventory mismatch: {product.lane} {month}")
        expected_tier_output_keys: set[str] = set()
        for day_value, base_rows in sorted(base_by_day.items()):
            day_text = day_value.isoformat()
            base_receipt = day_receipts[day_text]
            base_payload = source.serialize_table(base_rows, source.LANE_SCHEMA, source.LANE_SORT_COLUMNS)
            require_payload(base_receipt, base_payload, len(base_rows))
            if receipt_fields(tier_days[day_text]["tiers"]["13"]) != receipt_fields(base_receipt):
                raise source.BreakdownError(f"audit z13 binding mismatch: {product.lane} {day_text}")
            base_lineage = source.lineage_digest(str(row["lineage_sha256"]) for row in base_rows)
            if tier_days[day_text]["base_lineage_sha256"] != base_lineage:
                raise source.BreakdownError(f"audit base lineage mismatch: {product.lane} {day_text}")
            tier_receipts: dict[str, Mapping[str, Any]] = {"13": base_receipt}
            tier_totals["13"]["row_count"] += len(base_rows)
            tier_totals["13"]["part_count"] += 1
            tier_totals["13"]["byte_count"] += len(base_payload)
            for tier in (9, 5, 0):
                derived = source.derive_tier_rows(base_rows, tier)
                payload = source.serialize_table(derived, source.LANE_SCHEMA, source.LANE_SORT_COLUMNS)
                receipt = tier_days[day_text]["tiers"][str(tier)]
                require_payload(receipt, payload, len(derived))
                tier_receipts[str(tier)] = receipt
                expected_tier_output_keys.add(str(receipt["key"]))
                tier_totals[str(tier)]["row_count"] += len(derived)
                tier_totals[str(tier)]["part_count"] += 1
                tier_totals[str(tier)]["byte_count"] += len(payload)
            for tier in source.ZOOM_TIERS:
                marker_key, marker_payload = day_marker_payload(
                    root=root,
                    product=product,
                    day=day_value,
                    tier=tier,
                    part_receipt=tier_receipts[str(tier)],
                    input_manifest_sha256=contract.manifest_sha256,
                    base_lineage_sha256=base_lineage,
                )
                marker_receipt = tier_outputs.get(marker_key)
                if marker_receipt is None:
                    raise source.BreakdownError(f"audit day marker missing: {marker_key!r}")
                require_payload(marker_receipt, marker_payload, int(tier_receipts[str(tier)]["row_count"]))
                expected_tier_output_keys.add(marker_key)
                tier_totals[str(tier)]["marker_count"] += 1
        if set(tier_outputs) != expected_tier_output_keys:
            raise source.BreakdownError(f"audit tier output set mismatch: {product.lane} {month}")
        print(f"lane={product.lane} audit={index}/{len(source.product_months(contract))} month={month}", file=sys.stderr)

    marker_digest = source.lineage_digest(
        f"{receipt['key']}:{receipt['byte_count']}:{receipt['sha256']}" for receipt in verification_receipts
    )
    expected_manifest_fields = {
        "physical_scope_rows": aggregate["input_physical_rows"],
        "eligible_rows": aggregate["eligible_rows"],
        "selected_rows": aggregate["selected_rows"],
        "superseded_rows": aggregate["superseded_rows"],
        "rejected_rows": aggregate["rejected_rows"],
        "duplicate_group_count": aggregate["duplicate_group_count"],
        "max_multiplicity": max(multiplicities, default=0),
        "multiplicity_histogram": {str(key): value for key, value in sorted(multiplicities.items())},
        "input_part_count": len(consumed_raw),
        "input_byte_count": sum(int(value["byte_count"]) for value in consumed_raw.values()),
        "provenance_row_count": provenance_rows,
        "provenance_byte_count": provenance_bytes,
        "data_day_count": len(all_days),
        "observation_day_min": min(all_days),
        "observation_day_max": max(all_days),
        "input_month_digest": source.lineage_digest(input_month_digests),
        "selected_month_digest": source.lineage_digest(selected_month_digests),
        "tiers": tier_totals,
        "checkpoint_count": len(source.product_months(contract)) * 2,
        "verification_marker_count": len(verification_receipts),
        "verification_marker_digest": marker_digest,
        "object_count_before_manifest": len(expected_keys - {manifest_key, completion_key}),
    }
    expected_manifest = {
        "contract_version": source.CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "signal_name": product.signal_name,
        "source_key": source.EXPECTED_SOURCE_KEY,
        "support_key": source.EXPECTED_SUPPORT_KEY,
        "normalized_unit": source.EXPECTED_UNIT,
        "lane_prefix": f"{root}/",
        "input_snapshot_id": snapshot_id,
        "input_snapshot_prefix": f"{contract.root}/",
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        **expected_manifest_fields,
        "provenance_schema": source.schema_manifest(source.PROVENANCE_SCHEMA),
        "lane_schema": source.schema_manifest(source.LANE_SCHEMA),
        "verified_at": contract.manifest["verified_at"],
        "reconciliation": {
            "physical_equals_eligible_plus_rejected": True,
            "eligible_equals_selected_plus_superseded": True,
            "provenance_equals_physical": True,
            "z13_equals_selected": True,
            "all_data_days_have_tiers": list(source.ZOOM_TIERS),
        },
    }
    if manifest != expected_manifest:
        raise source.BreakdownError(f"published lane manifest is not the exact governed document: {product.lane}")
    expected_completion = {
        "contract_version": source.CONTRACT_VERSION,
        "lane": product.lane,
        "manifest_key": manifest_key,
        "manifest_sha256": source._sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": expected_manifest["physical_scope_rows"],
        "selected_rows": expected_manifest["selected_rows"],
        "data_day_count": expected_manifest["data_day_count"],
        "completed_at": expected_manifest["verified_at"],
    }
    if completion != expected_completion:
        raise source.BreakdownError(f"published lane completion is not the exact governed document: {product.lane}")
    if set(store.list_keys(f"{root}/")) != expected_keys:
        raise source.BreakdownError(f"published lane has a missing or unexpected object: {product.lane}")
    return {
        "lane": product.lane,
        "product_parameter": product.parameter,
        "lane_prefix": f"{root}/",
        "manifest_key": manifest_key,
        "manifest_sha256": source._sha256(manifest_payload),
        **{
            field: manifest[field]
            for field in (
                "physical_scope_rows",
                "eligible_rows",
                "selected_rows",
                "superseded_rows",
                "rejected_rows",
                "data_day_count",
                "tiers",
            )
        },
        "input_part_keys": set(consumed_raw),
    }


def audit_bundle(
    store: ReadOnlyStore,
    contract: source.InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    lanes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if output_prefix.strip("/") != source.DEFAULT_OUTPUT_PREFIX:
        raise source.BreakdownError(f"soil-wetness output prefix must be {source.DEFAULT_OUTPUT_PREFIX!r}")
    root = source._prefixed(
        object_store_prefix,
        f"{output_prefix.strip('/')}/_manifests/soil-wetness/snapshot={snapshot_id}",
    )
    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    manifest_payload, manifest = canonical_json(store, manifest_key, max_bytes=1_000_000)
    _completion_payload, completion = canonical_json(store, completion_key, max_bytes=1_000_000)
    lane_by_name = {str(lane["lane"]): lane for lane in lanes}
    expected_lane_names = {product.lane for product in source.PRODUCTS}
    if len(lanes) != len(source.PRODUCTS) or set(lane_by_name) != expected_lane_names:
        raise source.BreakdownError("soil bundle audit requires exactly the three governed lanes")
    physical = sum(int(lane["physical_scope_rows"]) for lane in lanes)
    eligible = sum(int(lane["eligible_rows"]) for lane in lanes)
    selected = sum(int(lane["selected_rows"]) for lane in lanes)
    superseded = sum(int(lane["superseded_rows"]) for lane in lanes)
    rejected = sum(int(lane["rejected_rows"]) for lane in lanes)
    if physical != eligible + rejected or eligible != selected + superseded:
        raise source.BreakdownError("soil bundle aggregate reconciliation failed")
    expected_manifest = {
        "contract_version": source.CONTRACT_VERSION,
        "bundle": "nasa-soil-wetness",
        "bundle_prefix": f"{root}/",
        "input_snapshot_id": snapshot_id,
        "input_snapshot_prefix": f"{contract.root}/",
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "product_parameters": [product.parameter for product in source.PRODUCTS],
        "physical_scope_rows": physical,
        "eligible_rows": eligible,
        "selected_rows": selected,
        "superseded_rows": superseded,
        "rejected_rows": rejected,
        "lane_count": len(lanes),
        "lanes": [
            {
                "lane": lane["lane"],
                "product_parameter": lane["product_parameter"],
                "lane_prefix": lane["lane_prefix"],
                "manifest_key": lane["manifest_key"],
                "manifest_sha256": lane["manifest_sha256"],
                "physical_scope_rows": lane["physical_scope_rows"],
                "selected_rows": lane["selected_rows"],
                "superseded_rows": lane["superseded_rows"],
                "data_day_count": lane["data_day_count"],
                "tiers": lane["tiers"],
            }
            for lane in sorted(lanes, key=lambda item: str(item["lane"]))
        ],
        "reconciliation": {
            "parameters_are_pairwise_disjoint": True,
            "physical_equals_eligible_plus_rejected": True,
            "eligible_equals_selected_plus_superseded": True,
            "all_lanes_pin_same_input_manifest": True,
        },
        "verified_at": contract.manifest["verified_at"],
    }
    if manifest != expected_manifest:
        raise source.BreakdownError("published soil bundle manifest is not the exact governed document")
    expected_completion = {
        "contract_version": source.CONTRACT_VERSION,
        "bundle": "nasa-soil-wetness",
        "manifest_key": manifest_key,
        "manifest_sha256": source._sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": physical,
        "selected_rows": selected,
        "lane_count": len(lanes),
        "completed_at": expected_manifest["verified_at"],
    }
    if completion != expected_completion:
        raise source.BreakdownError("published soil bundle completion is not the exact governed document")
    if set(store.list_keys(f"{root}/")) != {manifest_key, completion_key}:
        raise source.BreakdownError("published soil bundle inventory is not exact")
    return {
        "bundle": "nasa-soil-wetness",
        "manifest_key": manifest_key,
        "manifest_sha256": source._sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": physical,
        "selected_rows": selected,
        "superseded_rows": superseded,
        "rejected_rows": rejected,
        "lane_count": len(lanes),
        "lanes": [{key: value for key, value in lane.items() if key != "input_part_keys"} for lane in lanes],
    }


def status_report(
    store: ReadOnlyStore,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
) -> dict[str, Any]:
    lanes: dict[str, Any] = {}
    for product in source.PRODUCTS:
        root = source.lane_root(object_store_prefix, output_prefix, snapshot_id, product)
        keys = set(store.list_keys(f"{root}/"))
        lanes[product.lane] = {
            "base_checkpoints": sum("/_checkpoints/base/" in key for key in keys),
            "tier_checkpoints": sum("/_checkpoints/tiers/" in key for key in keys),
            "base_verification_markers": sum("/_verification/phase=base/" in key for key in keys),
            "tier_verification_markers": sum("/_verification/phase=tiers/" in key for key in keys),
            "manifest": f"{root}/manifest.json" in keys,
            "complete": f"{root}/_COMPLETE" in keys,
            "object_count": len(keys),
        }
    return {"snapshot_id": snapshot_id, "lanes": lanes}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--env-file", type=Path, default=source.DEFAULT_ENV_FILE)
    result.add_argument("--snapshot-id", default=source.DEFAULT_SNAPSHOT_ID)
    result.add_argument("--input-prefix", default=source.DEFAULT_INPUT_PREFIX)
    result.add_argument("--input-manifest-sha256", default=source.DEFAULT_INPUT_MANIFEST_SHA256)
    result.add_argument("--output-prefix", default=source.DEFAULT_OUTPUT_PREFIX)
    result.add_argument("--retry-attempts", type=int, default=8)
    result.add_argument("--retry-base-delay", type=float, default=0.5)
    result.add_argument("--verify-workers", type=int, default=source.DEFAULT_VERIFY_WORKERS)
    result.add_argument("--status-only", action="store_true")
    result.add_argument("--json", action="store_true")
    return result


def main() -> int:
    arguments = parser().parse_args()
    if arguments.retry_attempts < 1 or arguments.retry_base_delay < 0 or not 1 <= arguments.verify_workers <= 64:
        raise SystemExit("retry attempts must be positive, retry delay nonnegative, and verify workers in [1, 64]")
    configured = source.settings_from_file(arguments.env_file)
    backend = source.SnapshotStore.from_credentials(
        configured.require_object_store(),
        retry=source.RetryPolicy(arguments.retry_attempts, arguments.retry_base_delay),
    )
    store = ReadOnlyStore(backend)
    contract = source.load_input_contract(
        store,  # type: ignore[arg-type]
        object_store_prefix=configured.object_store_prefix,
        input_prefix=arguments.input_prefix,
        snapshot_id=arguments.snapshot_id,
        expected_manifest_sha256=arguments.input_manifest_sha256,
    )
    if arguments.status_only:
        report = status_report(
            store,
            object_store_prefix=configured.object_store_prefix,
            output_prefix=arguments.output_prefix,
            snapshot_id=arguments.snapshot_id,
        )
        print(json.dumps(report, indent=2 if arguments.json else None, sort_keys=True))
        return 0
    lanes = [
        audit_lane(
            store,
            contract,
            object_store_prefix=configured.object_store_prefix,
            output_prefix=arguments.output_prefix,
            snapshot_id=arguments.snapshot_id,
            product=product,
            verify_workers=arguments.verify_workers,
        )
        for product in source.PRODUCTS
    ]
    raw_key_sets = [set(lane["input_part_keys"]) for lane in lanes]
    if any(raw_key_sets[left] & raw_key_sets[right] for left in range(3) for right in range(left + 1, 3)):
        raise source.BreakdownError("soil product audits overlap canonical raw parts")
    report = audit_bundle(
        store,
        contract,
        object_store_prefix=configured.object_store_prefix,
        output_prefix=arguments.output_prefix,
        snapshot_id=arguments.snapshot_id,
        lanes=lanes,
    )
    print(json.dumps(report, indent=2 if arguments.json else None, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
