"""Census one pinned canonical signal-observation snapshot without writing data."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Final

import boto3  # type: ignore[import-untyped]
import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CHECKOUT_ENV_FILE: Final = Path.home() / "Programming" / "plantgeo" / "services" / "agri-data-service" / ".env"
DEFAULT_ENV_FILE: Final = SERVICE_ROOT / ".env" if (SERVICE_ROOT / ".env").is_file() else CHECKOUT_ENV_FILE
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import Settings  # noqa: E402

SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
SNAPSHOT_ROOT: Final = f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}"
MANIFEST_KEY: Final = f"{SNAPSHOT_ROOT}/manifest.json"
COMPLETE_KEY: Final = f"{SNAPSHOT_ROOT}/_COMPLETE"
LEDGER_PREFIX: Final = f"{SNAPSHOT_ROOT}/_ledger/"
DEFAULT_WORKERS: Final = 8
ERA5_SOIL_PRODUCTS: Final = {
    "soil_moisture_0_to_7cm_mean": ("soil_water_content_layer_1", 4_321_672),
    "soil_moisture_7_to_28cm_mean": ("soil_water_content_layer_2", 4_321_672),
    "soil_moisture_28_to_100cm_mean": ("soil_water_content_layer_3", 4_321_672),
}
ERA5_SOIL_LANES: Final = {
    "soil_moisture_0_to_7cm_mean": ("soil-field-moisture-0-7cm", 189_525_236),
    "soil_moisture_7_to_28cm_mean": ("soil-field-moisture-7-28cm", 188_856_768),
    "soil_moisture_28_to_100cm_mean": ("soil-field-moisture-28-100cm", 187_626_927),
}


def _json_object(payload: bytes, *, key: str) -> dict[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError(f"{key} is not a JSON object")
    return value


def _row_set_digest(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    identities = sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows)
    for row_id, row_hash in identities:
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def _partition_values(relative_path: str) -> dict[str, str]:
    return {
        key: value
        for component in relative_path.split("/")
        if "=" in component
        for key, value in [component.split("=", 1)]
    }


def _stat_values(part: Mapping[str, Any], column: str) -> set[object]:
    census = part.get("census")
    if not isinstance(census, Mapping):
        return set()
    statistic = census.get(column)
    if not isinstance(statistic, Mapping):
        return set()
    values = {statistic.get("min"), statistic.get("max")}
    return {value for value in values if value is not None}


def _stat_bound(part: Mapping[str, Any], column: str, bound: str) -> object | None:
    census = part.get("census")
    statistic = census.get(column) if isinstance(census, Mapping) else None
    return statistic.get(bound) if isinstance(statistic, Mapping) else None


def _soil_candidate(population: tuple[str, str, str], signals: set[object], parameters: set[object]) -> bool:
    del signals, parameters
    source, product, support = population
    return (
        source == "open-meteo-era5-land-archive"
        and support == "era5-land-0.1deg"
        and product
        in {
            "soil_moisture_0_to_7cm_mean",
            "soil_moisture_7_to_28cm_mean",
            "soil_moisture_28_to_100cm_mean",
        }
    )


def _exclusion_class(population: tuple[str, str, str]) -> str:
    source, product, _support = population
    if source == "nasa-power-daily" and product in {"GWETTOP", "GWETROOT", "GWETPROF"}:
        return "outside_scope_nasa_soil_wetness_owner_stopped"
    return "outside_scope_non_era5_land_soil_moisture"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--contract-shape", action="store_true")
    parser.add_argument("--full-census", action="store_true")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--dedup-product", choices=tuple(ERA5_SOIL_PRODUCTS))
    parser.add_argument("--destination-status", action="store_true")
    parser.add_argument("--verify-lanes", action="store_true")
    arguments = parser.parse_args()

    settings = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
    credentials = settings.require_object_store()
    prefix = settings.object_store_prefix.strip("/")
    client = boto3.client(
        "s3",
        endpoint_url=credentials.endpoint_url,
        region_name=credentials.region,
        aws_access_key_id=credentials.access_key_id.get_secret_value(),
        aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
        config=Config(retries={"max_attempts": 12, "mode": "adaptive"}),
    )

    def full_key(relative: str) -> str:
        return f"{prefix}/{relative}" if prefix else relative

    def get(relative: str) -> bytes:
        response = client.get_object(Bucket=credentials.bucket, Key=full_key(relative))
        payload = response["Body"].read()
        return payload if isinstance(payload, bytes) else bytes(payload)

    manifest_payload = get(MANIFEST_KEY)
    actual_sha256 = hashlib.sha256(manifest_payload).hexdigest()
    if actual_sha256 != MANIFEST_SHA256:
        raise RuntimeError(f"manifest SHA-256 {actual_sha256} does not match required {MANIFEST_SHA256}")
    manifest = _json_object(manifest_payload, key=MANIFEST_KEY)
    completion = _json_object(get(COMPLETE_KEY), key=COMPLETE_KEY)
    if completion.get("manifest_sha256") != MANIFEST_SHA256:
        raise RuntimeError("_COMPLETE does not pin the required manifest SHA-256")

    def list_objects(relative_prefix: str) -> dict[str, int]:
        listed: dict[str, int] = {}
        continuation: str | None = None
        while True:
            request: dict[str, object] = {
                "Bucket": credentials.bucket,
                "Prefix": full_key(relative_prefix),
            }
            if continuation is not None:
                request["ContinuationToken"] = continuation
            response = client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                key = str(item["Key"])
                relative = key[len(prefix) + 1 :] if prefix else key
                listed[relative] = int(item["Size"])
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or not token:
                break
            continuation = token
        return listed

    ledger_inventory = list_objects(LEDGER_PREFIX)
    listed_ledgers = sum(1 for key in ledger_inventory if key.endswith(".json"))

    ledger_summaries = manifest.get("month_ledgers")
    if not isinstance(ledger_summaries, list):
        raise RuntimeError("pinned manifest has no month_ledgers list")
    summary_count = len(ledger_summaries)
    if listed_ledgers != 424 or summary_count != 424:
        raise RuntimeError(f"ledger census drift: listed={listed_ledgers}, manifest={summary_count}, expected=424")

    report: dict[str, object] = {
        "status": "pinned",
        "snapshot_id": SNAPSHOT_ID,
        "manifest_key": MANIFEST_KEY,
        "manifest_sha256": actual_sha256,
        "complete_manifest_sha256": completion.get("manifest_sha256"),
        "contract_version": manifest.get("contract_version"),
        "row_count": manifest.get("row_count"),
        "partition_count": manifest.get("partition_count"),
        "batch_count": manifest.get("batch_count"),
        "rejected_rows": manifest.get("rejected_rows"),
        "observation_day_min": manifest.get("observation_day_min"),
        "observation_day_max": manifest.get("observation_day_max"),
        "manifest_ledger_count": summary_count,
        "listed_ledger_count": listed_ledgers,
    }
    if arguments.destination_status:
        destination_status: dict[str, dict[str, object]] = {}
        report["destination_status"] = destination_status
        for product, (lane, _expected_bytes) in ERA5_SOIL_LANES.items():
            inventory = list_objects(f"layer={lane}/")
            metadata_root = f"layer={lane}/_breakdown/snapshot={SNAPSHOT_ID}"
            destination_status[product] = {
                "lane": lane,
                "object_count": len(inventory),
                "checkpoint_count": sum(
                    1
                    for key in inventory
                    if key.startswith(f"{metadata_root}/_checkpoints/month=") and key.endswith(".json")
                ),
                "manifest_exists": f"{metadata_root}/manifest.json" in inventory,
                "complete_exists": f"{metadata_root}/_COMPLETE" in inventory,
                "source_audit_exists": f"{metadata_root}/source-chain-audit.json" in inventory,
                "audit_complete_exists": f"{metadata_root}/_AUDIT_COMPLETE" in inventory,
            }
    if arguments.contract_shape:
        first_summary = ledger_summaries[0]
        ledger_key = (
            f"{LEDGER_PREFIX}month={first_summary['observation_month']}/"
            f"cell-batch={int(first_summary['cell_batch_index']):05d}.json"
        )
        first_ledger = _json_object(get(ledger_key), key=ledger_key)
        parts = first_ledger.get("parts")
        report["contract_shape"] = {
            "manifest_keys": sorted(manifest),
            "completion_keys": sorted(completion),
            "dimension_objects": manifest.get("dimension_objects"),
            "ledger_summary_keys": sorted(first_summary),
            "ledger_keys": sorted(first_ledger),
            "part_keys": sorted(parts[0]) if isinstance(parts, list) and parts else [],
            "sample_part_paths": [part.get("relative_path") for part in parts[:8]] if isinstance(parts, list) else [],
        }
    if arguments.full_census:
        if arguments.workers < 1 or arguments.workers > 32:
            raise RuntimeError("--workers must be between 1 and 32")

        dimensions = manifest.get("dimension_objects")
        if not isinstance(dimensions, Mapping):
            raise RuntimeError("manifest has no dimension object inventory")

        def dimension_rows(name: str) -> list[dict[str, Any]]:
            metadata = dimensions.get(name)
            if not isinstance(metadata, Mapping):
                raise RuntimeError(f"manifest has no {name} dimension")
            key = str(metadata["key"])
            payload = get(key)
            if len(payload) != int(metadata["byte_count"]):
                raise RuntimeError(f"{name} dimension byte count drifted")
            if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
                raise RuntimeError(f"{name} dimension SHA-256 drifted")
            table = pq.read_table(io.BytesIO(payload))
            if table.num_rows != int(metadata["row_count"]):
                raise RuntimeError(f"{name} dimension row count drifted")
            dimension: list[dict[str, Any]] = table.to_pylist()
            return dimension

        data_sources = dimension_rows("data_source")
        source_releases = dimension_rows("source_release")
        releases_by_source_id = Counter(str(row["data_source_id"]) for row in source_releases)

        def verified_ledger(summary: Mapping[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
            month = str(summary["observation_month"])
            batch_index = int(summary["cell_batch_index"])
            key = f"{LEDGER_PREFIX}month={month}/cell-batch={batch_index:05d}.json"
            payload = get(key)
            if ledger_inventory.get(key) != len(payload):
                raise RuntimeError(f"ledger {key} is absent or its listed byte count drifted")
            ledger = _json_object(payload, key=key)
            expected = {
                "contract_version": "agri.signal_observation.raw-canonical.v1",
                "snapshot_id": SNAPSHOT_ID,
                "observation_month": month,
                "cell_batch_index": batch_index,
                **{field: summary[field] for field in ("row_count", "part_count", "byte_count", "source_row_digest")},
            }
            drift = {
                field: (ledger.get(field), value) for field, value in expected.items() if ledger.get(field) != value
            }
            if drift:
                raise RuntimeError(f"ledger {key} is not bound to its manifest summary: {drift}")
            parts = ledger.get("parts")
            if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
                raise RuntimeError(f"ledger {key} has an invalid parts inventory")
            if len(parts) != int(ledger["part_count"]):
                raise RuntimeError(f"ledger {key} part count is inconsistent")
            if sum(int(part["row_count"]) for part in parts) != int(ledger["row_count"]):
                raise RuntimeError(f"ledger {key} row count is inconsistent")
            if sum(int(part["byte_count"]) for part in parts) != int(ledger["byte_count"]):
                raise RuntimeError(f"ledger {key} byte count is inconsistent")
            if int(ledger.get("rejected_rows", -1)) != 0:
                raise RuntimeError(f"ledger {key} contains rejected rows")
            return key, hashlib.sha256(payload).hexdigest(), parts

        with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
            verified = list(executor.map(verified_ledger, ledger_summaries))

        populations: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "part_count": 0,
                "row_count": 0,
                "byte_count": 0,
                "observation_day_min": None,
                "observation_day_max": None,
                "source_parameters": set(),
                "signal_names": set(),
                "normalized_units": set(),
                "quality_flags": set(),
                "is_observed_values": set(),
                "part_receipts": [],
            }
        )
        ledger_receipts: list[str] = []
        part_keys: set[str] = set()
        total_rows = 0
        total_bytes = 0
        for ledger_key, ledger_sha256, parts in verified:
            ledger_receipts.append(f"{ledger_key}\0{ledger_sha256}")
            for part in parts:
                relative_path = str(part["relative_path"])
                partition = _partition_values(relative_path)
                population_key = (
                    partition.get("source", ""),
                    partition.get("product", ""),
                    partition.get("support", ""),
                )
                if not all(population_key):
                    raise RuntimeError(f"part has an incomplete partition path: {relative_path}")
                key = str(part["key"])
                if key != f"{SNAPSHOT_ROOT}/{relative_path}":
                    raise RuntimeError(f"part key escapes the pinned snapshot: {key}")
                if key in part_keys:
                    raise RuntimeError(f"source part appears in multiple ledgers: {key}")
                part_keys.add(key)
                population = populations[population_key]
                population["part_count"] += 1
                population["row_count"] += int(part["row_count"])
                population["byte_count"] += int(part["byte_count"])
                total_rows += int(part["row_count"])
                total_bytes += int(part["byte_count"])
                population["source_parameters"].update(_stat_values(part, "source_parameter"))
                population["signal_names"].update(_stat_values(part, "signal_name"))
                population["normalized_units"].update(_stat_values(part, "normalized_unit"))
                population["quality_flags"].update(_stat_values(part, "quality_flag"))
                population["is_observed_values"].update(_stat_values(part, "is_observed"))
                day_min = _stat_bound(part, "observation_day", "min")
                day_max = _stat_bound(part, "observation_day", "max")
                if day_min is not None and (
                    population["observation_day_min"] is None or day_min < population["observation_day_min"]
                ):
                    population["observation_day_min"] = day_min
                if day_max is not None and (
                    population["observation_day_max"] is None or day_max > population["observation_day_max"]
                ):
                    population["observation_day_max"] = day_max
                population["part_receipts"].append(
                    f"{key}\0{part['sha256']}\0{part['row_count']}\0{part['byte_count']}"
                )

        if len(part_keys) != int(manifest["partition_count"]):
            raise RuntimeError(
                f"ledger parts={len(part_keys)} do not equal manifest partitions={manifest['partition_count']}"
            )
        if total_rows != int(manifest["row_count"]):
            raise RuntimeError(f"ledger rows={total_rows} do not equal manifest rows={manifest['row_count']}")
        if total_bytes != int(manifest["fact_byte_count"]):
            raise RuntimeError(
                f"ledger bytes={total_bytes} do not equal manifest fact bytes={manifest['fact_byte_count']}"
            )

        object_inventory = list_objects(f"{SNAPSHOT_ROOT}/")
        missing_parts = sorted(part_keys.difference(object_inventory))
        size_drift = sorted(
            key
            for population in populations.values()
            for receipt in population["part_receipts"]
            for key, expected_size in [(receipt.split("\0", 1)[0], int(receipt.rsplit("\0", 1)[1]))]
            if object_inventory.get(key) != expected_size
        )
        if missing_parts or size_drift:
            raise RuntimeError(
                f"live source inventory drift: missing_parts={len(missing_parts)}, size_drift={len(size_drift)}"
            )

        source_contracts = {
            str(row["key"]): {
                "name": row["name"],
                "owner": row["owner"],
                "allowed_client_exposure": row["allowed_client_exposure"],
                "review_state": row["review_state"],
                "is_active": row["is_active"],
                "source_release_count": releases_by_source_id[str(row["id"])],
            }
            for row in data_sources
        }
        population_rows: list[dict[str, Any]] = []
        soil_rows = 0
        soil_bytes = 0
        soil_parts = 0
        for population_key, values in sorted(populations.items()):
            source, product, support = population_key
            signals = values.pop("signal_names")
            parameters = values.pop("source_parameters")
            candidate = _soil_candidate(population_key, signals, parameters)
            if candidate:
                soil_rows += values["row_count"]
                soil_bytes += values["byte_count"]
                soil_parts += values["part_count"]
            receipts = sorted(values.pop("part_receipts"))
            population_rows.append(
                {
                    "source": source,
                    "product": product,
                    "support": support,
                    "source_parameters": sorted(parameters, key=str),
                    "signal_names": sorted(signals, key=str),
                    "normalized_units": sorted(values.pop("normalized_units"), key=str),
                    "quality_flags": sorted(values.pop("quality_flags"), key=str),
                    "is_observed_values": sorted(values.pop("is_observed_values"), key=str),
                    **values,
                    "allowed_client_exposure": source_contracts[source]["allowed_client_exposure"],
                    "soil_moisture_candidate": candidate,
                    "exclusion_class": None if candidate else _exclusion_class(population_key),
                    "source_part_lineage_sha256": hashlib.sha256("\n".join(receipts).encode()).hexdigest(),
                }
            )

        full_census: dict[str, object] = {
            "bounded_ledger_workers": arguments.workers,
            "verified_ledger_count": len(verified),
            "ledger_receipts_sha256": hashlib.sha256("\n".join(sorted(ledger_receipts)).encode()).hexdigest(),
            "source_part_count": len(part_keys),
            "source_part_rows": total_rows,
            "source_part_bytes": total_bytes,
            "live_snapshot_object_count": len(object_inventory),
            "soil_candidate_population_count": sum(
                1 for population in population_rows if population["soil_moisture_candidate"]
            ),
            "soil_candidate_part_count": soil_parts,
            "soil_candidate_rows": soil_rows,
            "soil_candidate_bytes": soil_bytes,
            "excluded_non_soil_part_count": len(part_keys) - soil_parts,
            "excluded_non_soil_rows": total_rows - soil_rows,
            "excluded_non_soil_bytes": total_bytes - soil_bytes,
            "data_sources": source_contracts,
            "populations": population_rows,
        }
        report["full_census"] = full_census
        if arguments.dedup_product:
            product = arguments.dedup_product
            signal_name, expected_physical_rows = ERA5_SOIL_PRODUCTS[product]
            selected_parts = [
                part
                for _ledger_key, _ledger_sha256, parts in verified
                for part in parts
                if _partition_values(str(part["relative_path"])).get("source") == "open-meteo-era5-land-archive"
                and _partition_values(str(part["relative_path"])).get("product") == product
                and _partition_values(str(part["relative_path"])).get("support") == "era5-land-0.1deg"
            ]
            if len(selected_parts) != 424:
                raise RuntimeError(f"{product} has {len(selected_parts)} parts, expected 424")

            def dedup_part(part: Mapping[str, Any]) -> dict[str, Any]:
                key = str(part["key"])
                payload = get(key)
                if len(payload) != int(part["byte_count"]):
                    raise RuntimeError(f"{key} byte count drifted")
                if hashlib.sha256(payload).hexdigest() != part["sha256"]:
                    raise RuntimeError(f"{key} SHA-256 drifted")
                table = pq.read_table(io.BytesIO(payload))
                rows = table.to_pylist()
                if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
                    raise RuntimeError(f"{key} row census drifted")
                exclusions: Counter[str] = Counter()
                eligible: list[dict[str, Any]] = []
                for row in rows:
                    reason: str | None = None
                    if row.get("data_source_key") != "open-meteo-era5-land-archive":
                        reason = "data_source_key_drift"
                    elif row.get("product_key") != product or row.get("source_parameter") != product:
                        reason = "product_drift"
                    elif row.get("support_key") != "era5-land-0.1deg":
                        reason = "support_drift"
                    elif row.get("signal_name") != signal_name:
                        reason = "signal_drift"
                    elif row.get("normalized_unit") != "m^3/m^3":
                        reason = "unit_drift"
                    elif row.get("is_observed") is not True:
                        reason = "not_observed"
                    elif row.get("quality_flag") != "accepted":
                        reason = "quality_not_accepted"
                    elif row.get("normalized_value") is None:
                        reason = "normalized_value_null"
                    if reason is None:
                        eligible.append(row)
                    else:
                        exclusions[reason] += 1
                frame = pl.DataFrame(eligible).select(
                    "support_key", "signal_name", "normalized_unit", "cell_id", "observation_day"
                )
                unique = frame.unique()
                day_counts = {
                    str(row["observation_day"]): int(row["len"])
                    for row in unique.group_by("observation_day").len().to_dicts()
                }
                return {
                    "physical_rows": len(rows),
                    "eligible_rows": len(eligible),
                    "excluded": exclusions,
                    "base_rows": unique.height,
                    "day_counts": day_counts,
                }

            with ThreadPoolExecutor(max_workers=arguments.workers) as executor:
                part_results = list(executor.map(dedup_part, selected_parts))
            physical_rows = sum(item["physical_rows"] for item in part_results)
            eligible_rows = sum(item["eligible_rows"] for item in part_results)
            base_rows = sum(item["base_rows"] for item in part_results)
            exclusions: Counter[str] = Counter()
            day_counts: Counter[str] = Counter()
            for item in part_results:
                exclusions.update(item["excluded"])
                day_counts.update(item["day_counts"])
            if physical_rows != expected_physical_rows:
                raise RuntimeError(f"{product} physical rows={physical_rows}, expected={expected_physical_rows}")
            full_census["dedup_census"] = {
                "product": product,
                "physical_rows": physical_rows,
                "eligible_rows": eligible_rows,
                "excluded_rows": sum(exclusions.values()),
                "exclusion_counts": dict(sorted(exclusions.items())),
                "selected_base_rows": base_rows,
                "duplicates_collapsed": eligible_rows - base_rows,
                "day_count": len(day_counts),
                "first_day": min(day_counts),
                "last_day": max(day_counts),
                "minimum_cells_per_day": min(day_counts.values()),
                "maximum_cells_per_day": max(day_counts.values()),
                "day_cell_count_digest": hashlib.sha256(
                    "\n".join(f"{day}:{count}" for day, count in sorted(day_counts.items())).encode()
                ).hexdigest(),
            }
        if arguments.verify_lanes:
            lane_verifications: list[dict[str, Any]] = []
            lane_source_parts: dict[str, set[str]] = {}
            for product, (lane, expected_source_bytes) in ERA5_SOIL_LANES.items():
                metadata_root = f"layer={lane}/_breakdown/snapshot={SNAPSHOT_ID}"
                lane_manifest_key = f"{metadata_root}/manifest.json"
                lane_complete_key = f"{metadata_root}/_COMPLETE"
                lane_audit_key = f"{metadata_root}/source-chain-audit.json"
                lane_audit_complete_key = f"{metadata_root}/_AUDIT_COMPLETE"
                lane_manifest_payload = get(lane_manifest_key)
                lane_manifest_sha256 = hashlib.sha256(lane_manifest_payload).hexdigest()
                lane_manifest = _json_object(lane_manifest_payload, key=lane_manifest_key)
                lane_completion = _json_object(get(lane_complete_key), key=lane_complete_key)
                lane_audit_payload = get(lane_audit_key)
                lane_audit_sha256 = hashlib.sha256(lane_audit_payload).hexdigest()
                lane_audit = _json_object(lane_audit_payload, key=lane_audit_key)
                lane_audit_completion = _json_object(get(lane_audit_complete_key), key=lane_audit_complete_key)
                expected_parts = {
                    str(part["key"])
                    for _ledger_key, _ledger_sha256, parts in verified
                    for part in parts
                    if _partition_values(str(part["relative_path"])).get("source") == "open-meteo-era5-land-archive"
                    and _partition_values(str(part["relative_path"])).get("product") == product
                    and _partition_values(str(part["relative_path"])).get("support") == "era5-land-0.1deg"
                }
                audited_parts = {str(receipt["source_part_key"]) for receipt in lane_audit["ledgers"]}
                if audited_parts != expected_parts:
                    raise RuntimeError(f"{lane} audited source-part set does not equal its canonical ledger set")
                lane_source_parts[lane] = audited_parts
                expected_manifest = {
                    "lane": lane,
                    "source_snapshot_id": SNAPSHOT_ID,
                    "source_manifest_sha256": MANIFEST_SHA256,
                    "physical_source_rows": 4_321_672,
                    "eligible_source_rows": 4_321_672,
                    "excluded_source_rows": 0,
                    "physical_source_parts": 424,
                    "physical_source_bytes": expected_source_bytes,
                    "selected_base_rows": 2_287_320,
                    "duplicates_collapsed": 2_034_352,
                    "day_count": 1_556,
                }
                drift = {
                    field: (lane_manifest.get(field), value)
                    for field, value in expected_manifest.items()
                    if lane_manifest.get(field) != value
                }
                if drift:
                    raise RuntimeError(f"{lane} manifest drifted: {drift}")
                source_filter = lane_manifest.get("source_filter")
                if not isinstance(source_filter, Mapping) or source_filter.get("product_key") != product:
                    raise RuntimeError(f"{lane} manifest is not bound to {product}")
                if lane_completion.get("manifest_sha256") != lane_manifest_sha256:
                    raise RuntimeError(f"{lane} _COMPLETE does not pin its durable manifest")
                if lane_audit_completion.get("audit_sha256") != lane_audit_sha256:
                    raise RuntimeError(f"{lane} _AUDIT_COMPLETE does not pin its durable audit")
                if lane_audit_completion.get("destination_manifest_sha256") != lane_manifest_sha256:
                    raise RuntimeError(f"{lane} audit does not bind its destination manifest")
                if lane_audit.get("source_part_count") != 424 or lane_audit.get("physical_source_rows") != 4_321_672:
                    raise RuntimeError(f"{lane} source audit totals drifted")
                if any(int(lane_manifest["tier_part_counts"][str(zoom)]) != 1_556 for zoom in (13, 9, 5, 0)):
                    raise RuntimeError(f"{lane} tier part ladder is incomplete")
                if any(int(lane_manifest["tier_completion_counts"][str(zoom)]) != 1_556 for zoom in (13, 9, 5, 0)):
                    raise RuntimeError(f"{lane} tier completion ladder is incomplete")
                inventory = list_objects(f"layer={lane}/")
                if len(inventory) != 12_506:
                    raise RuntimeError(f"{lane} has {len(inventory)} objects, expected 12506")
                lane_verifications.append(
                    {
                        "product": product,
                        "lane": lane,
                        "manifest_sha256": lane_manifest_sha256,
                        "source_audit_sha256": lane_audit_sha256,
                        "source_parts": len(audited_parts),
                        "physical_rows": lane_manifest["physical_source_rows"],
                        "physical_bytes": lane_manifest["physical_source_bytes"],
                        "base_rows": lane_manifest["selected_base_rows"],
                        "duplicates_collapsed": lane_manifest["duplicates_collapsed"],
                        "object_count": len(inventory),
                    }
                )

            lanes = sorted(lane_source_parts)
            intersections = {
                f"{left}|{right}": len(lane_source_parts[left].intersection(lane_source_parts[right]))
                for index, left in enumerate(lanes)
                for right in lanes[index + 1 :]
            }
            if any(intersections.values()):
                raise RuntimeError(f"soil-moisture lane source populations overlap: {intersections}")
            selected_parts = sum(item["source_parts"] for item in lane_verifications)
            selected_rows = sum(item["physical_rows"] for item in lane_verifications)
            selected_bytes = sum(item["physical_bytes"] for item in lane_verifications)
            excluded_parts = len(part_keys) - selected_parts
            excluded_rows = total_rows - selected_rows
            excluded_bytes = total_bytes - selected_bytes
            if (
                (selected_parts, selected_rows, selected_bytes) != (1_272, 12_965_016, 566_008_931)
                or (excluded_parts, excluded_rows, excluded_bytes) != (7_092, 33_181_552, 1_505_711_192)
                or selected_parts + excluded_parts != int(manifest["partition_count"])
                or selected_rows + excluded_rows != int(manifest["row_count"])
                or selected_bytes + excluded_bytes != int(manifest["fact_byte_count"])
            ):
                raise RuntimeError("three-lane plus outside-scope whole-snapshot conservation failed")
            full_census["lane_verification"] = {
                "status": "conserved",
                "lanes": lane_verifications,
                "pairwise_source_part_intersections": intersections,
                "selected_era5_soil_moisture": {
                    "part_count": selected_parts,
                    "row_count": selected_rows,
                    "byte_count": selected_bytes,
                },
                "outside_scope": {
                    "part_count": excluded_parts,
                    "row_count": excluded_rows,
                    "byte_count": excluded_bytes,
                    "nasa_gwet": {"part_count": 1_212, "row_count": 1_776_330, "byte_count": 90_246_049},
                },
                "whole_snapshot": {
                    "part_count": len(part_keys),
                    "row_count": total_rows,
                    "byte_count": total_bytes,
                },
            }
    elif arguments.verify_lanes:
        raise RuntimeError("--verify-lanes requires --full-census")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
