"""Census and break down VPD from the pinned canonical signal snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.pipeline.parquet.objectstore import ObjectStore, conform_to_stream_schema  # noqa: E402
from agri_data_service.warehouse.parquet.schema import observed_stream_schema  # noqa: E402
from agri_data_service.warehouse.parquet.tiers import (  # noqa: E402
    BASE_ZOOM_TIER,
    DERIVED_ZOOM_TIERS,
    derive_tier,
)

SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
SNAPSHOT_PREFIX: Final = f"raw-canonical/signal-observation/snapshot={SNAPSHOT_ID}/"
SNAPSHOT_MANIFEST_KEY: Final = f"{SNAPSHOT_PREFIX}manifest.json"
REQUIRED_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
EXPECTED_CONTRACT_VERSION: Final = "agri.signal_observation.raw-canonical.v1"
MAX_LEDGER_COUNT: Final = 500
MAX_SOURCE_PART_COUNT: Final = 10_000
MAX_CONCURRENCY: Final = 8
VERIFICATION_MODES: Final = ("sparse", "full")
OUTPUT_CONTRACT_VERSION: Final = "plantgeo.vpd.snapshot-product.v1"
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
GRAIN: Final = ("support_key", "signal_name", "normalized_unit", "cell_id", "observation_day")
EXPECTED_SOURCE_PARTS: Final = 424
EXPECTED_SOURCE_BYTES: Final = 186_032_189
EXPECTED_PHYSICAL_ROWS: Final = 4_298_280
EXPECTED_WINNER_ROWS: Final = 2_287_320
EXPECTED_SUPERSEDED_ROWS: Final = 2_010_960
EXPECTED_DAY_COUNT: Final = 1_556
EXPECTED_CELLS_PER_DAY: Final = 1_470
EXPECTED_FIRST_DAY: Final = date(2022, 4, 30)
EXPECTED_LAST_DAY: Final = date(2026, 8, 2)
EXPECTED_MONTH_COUNT: Final = 53


@dataclass(frozen=True, slots=True)
class ProductContract:
    """The dedicated non-overlapping physical VPD product contract."""

    product_id: str
    stream: str
    source_parameter: str
    signal_name: str
    data_source_key: str = "open-meteo-era5-land-archive"
    support_key: str = "era5-land-0.1deg"
    normalized_unit: str = "kPa"
    original_unit: str = "kPa"
    cell_grid_name: str = "sentinel2-ndvi-0p25deg"


PRODUCTS: Final = (
    ProductContract(
        product_id="vpd",
        stream="soil-field-vpd",
        source_parameter="vapour_pressure_deficit_max",
        signal_name="vapor_pressure_deficit",
    ),
)
PRODUCT_BY_PARAMETER: Final = {product.source_parameter: product for product in PRODUCTS}
PRODUCT_BY_SIGNAL: Final = {product.signal_name: product for product in PRODUCTS}


class SnapshotContractError(RuntimeError):
    """Raised when immutable snapshot evidence violates the pinned contract."""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    census_parser = subcommands.add_parser("census", help="read only the pinned manifest and monthly ledgers")
    census_parser.add_argument(
        "--include-lineage",
        action="store_true",
        help="include every source-part lineage record instead of only its count and digest",
    )
    build_parser = subcommands.add_parser("build", help="write the immutable VPD snapshot product")
    build_parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY)
    build_parser.add_argument("--verification", choices=VERIFICATION_MODES, default="sparse")
    verify_parser = subcommands.add_parser("verify", help="reconcile completed immutable snapshot products")
    verify_parser.add_argument("--mode", choices=VERIFICATION_MODES, default="sparse")
    arguments = parser.parse_args()
    if getattr(arguments, "concurrency", 1) < 1 or getattr(arguments, "concurrency", 1) > MAX_CONCURRENCY:
        parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    return arguments


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_object(payload: bytes, *, key: str) -> dict[str, object]:
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotContractError(f"{key} is not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise SnapshotContractError(f"{key} must contain a JSON object")
    return decoded


def _get_required(store: ObjectStore, relative_key: str) -> bytes:
    payload = store._backend.get(store.key_for(relative_key))  # noqa: SLF001 - raw snapshot paths are not lanes.
    if payload is None:
        raise SnapshotContractError(f"required snapshot object is absent: {relative_key}")
    return payload


def _require_scalar(mapping: Mapping[str, object], key: str) -> object:
    if key not in mapping:
        raise SnapshotContractError(f"required field {key!r} is absent")
    return mapping[key]


def _load_pinned_manifest(store: ObjectStore) -> dict[str, object]:
    complete_key = f"{SNAPSHOT_PREFIX}_COMPLETE"
    complete = _json_object(_get_required(store, complete_key), key=complete_key)
    expected_complete = {
        "snapshot_id": SNAPSHOT_ID,
        "manifest_key": SNAPSHOT_MANIFEST_KEY,
        "manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "contract_version": EXPECTED_CONTRACT_VERSION,
    }
    for field, expected in expected_complete.items():
        actual = _require_scalar(complete, field)
        if actual != expected:
            raise SnapshotContractError(
                f"{complete_key} binds {field}={actual!r}, expected the pinned value {expected!r}"
            )
    manifest_payload = _get_required(store, SNAPSHOT_MANIFEST_KEY)
    actual_sha256 = _sha256(manifest_payload)
    if actual_sha256 != REQUIRED_MANIFEST_SHA256:
        raise SnapshotContractError(
            f"{SNAPSHOT_MANIFEST_KEY} SHA-256 is {actual_sha256}, expected {REQUIRED_MANIFEST_SHA256}"
        )
    manifest = _json_object(manifest_payload, key=SNAPSHOT_MANIFEST_KEY)
    expected_manifest = {
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_prefix": SNAPSHOT_PREFIX,
        "contract_version": EXPECTED_CONTRACT_VERSION,
    }
    for field, expected in expected_manifest.items():
        actual = _require_scalar(manifest, field)
        if actual != expected:
            raise SnapshotContractError(f"{SNAPSHOT_MANIFEST_KEY} binds {field}={actual!r}, expected {expected!r}")
    return manifest


def _ledger_key(month: str, cell_batch_index: int) -> str:
    return f"{SNAPSHOT_PREFIX}_ledger/month={month}/cell-batch={cell_batch_index:05d}.json"


def _constant_census_value(part: Mapping[str, object], column: str) -> object:
    census = part.get("census")
    if not isinstance(census, Mapping):
        raise SnapshotContractError(f"part {part.get('key')!r} has no census object")
    column_census = census.get(column)
    if not isinstance(column_census, Mapping):
        raise SnapshotContractError(f"part {part.get('key')!r} has no census for {column!r}")
    minimum = column_census.get("min")
    maximum = column_census.get("max")
    null_count = column_census.get("null_count")
    nan_count = column_census.get("nan_count")
    if minimum != maximum or null_count != 0 or nan_count != 0:
        raise SnapshotContractError(
            f"part {part.get('key')!r} does not carry one non-null finite {column!r}: "
            f"min={minimum!r}, max={maximum!r}, null_count={null_count!r}, nan_count={nan_count!r}"
        )
    return minimum


def _date_bounds(part: Mapping[str, object]) -> tuple[str, str]:
    census = part.get("census")
    if not isinstance(census, Mapping):
        raise SnapshotContractError(f"part {part.get('key')!r} has no census object")
    observed = census.get("observation_day")
    if not isinstance(observed, Mapping):
        raise SnapshotContractError(f"part {part.get('key')!r} has no observation_day census")
    first_day = observed.get("min")
    last_day = observed.get("max")
    if not isinstance(first_day, str) or not isinstance(last_day, str):
        raise SnapshotContractError(f"part {part.get('key')!r} has invalid observation_day bounds")
    return first_day, last_day


def _contract_dimensions(part: Mapping[str, object]) -> dict[str, object]:
    return {
        column: _constant_census_value(part, column)
        for column in (
            "source_parameter",
            "signal_name",
            "data_source_key",
            "normalized_unit",
            "original_unit",
            "cell_grid_name",
            "support_key",
            "product_key",
        )
    }


def _classification(dimensions: Mapping[str, object]) -> tuple[ProductContract | None, str]:
    parameter = dimensions["source_parameter"]
    signal = dimensions["signal_name"]
    by_parameter = PRODUCT_BY_PARAMETER.get(str(parameter))
    by_signal = PRODUCT_BY_SIGNAL.get(str(signal))
    if by_parameter is None and by_signal is None:
        return None, "outside-vpd-scope"
    if by_parameter is None:
        return by_signal, "vpd-signal-with-ungoverned-parameter"
    if by_signal is None:
        return by_parameter, "governed-parameter-with-ungoverned-signal"
    if by_parameter != by_signal:
        return by_parameter, "parameter-signal-contract-conflict"
    expected = {
        "source_parameter": by_parameter.source_parameter,
        "signal_name": by_parameter.signal_name,
        "data_source_key": by_parameter.data_source_key,
        "normalized_unit": by_parameter.normalized_unit,
        "original_unit": by_parameter.original_unit,
        "cell_grid_name": by_parameter.cell_grid_name,
        "support_key": by_parameter.support_key,
        "product_key": by_parameter.source_parameter,
    }
    differences = [field for field, value in expected.items() if dimensions[field] != value]
    if differences:
        return by_parameter, "off-contract:" + ",".join(differences)
    return by_parameter, "included-physical-row"


def _validate_ledger_summary(entry: Mapping[str, object], ledger: Mapping[str, object], *, key: str) -> None:
    expected = {
        "observation_month": entry.get("observation_month"),
        "cell_batch_index": entry.get("cell_batch_index"),
        "part_count": entry.get("part_count"),
        "row_count": entry.get("row_count"),
        "byte_count": entry.get("byte_count"),
        "source_row_digest": entry.get("source_row_digest"),
    }
    for field, value in expected.items():
        if ledger.get(field) != value:
            raise SnapshotContractError(f"{key} field {field!r} is {ledger.get(field)!r}, manifest declares {value!r}")
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise SnapshotContractError(f"{key} has no parts list")
    if sum(int(part["row_count"]) for part in parts) != int(ledger["row_count"]):
        raise SnapshotContractError(f"{key} part row counts do not equal the ledger row count")
    if sum(int(part["byte_count"]) for part in parts) != int(ledger["byte_count"]):
        raise SnapshotContractError(f"{key} part byte counts do not equal the ledger byte count")
    if len(parts) != int(ledger["part_count"]):
        raise SnapshotContractError(f"{key} part count does not equal the ledger part count")


def census(store: ObjectStore, *, include_lineage: bool = False) -> dict[str, object]:
    """Return the exact VPD population recorded by the pinned ledgers."""
    manifest = _load_pinned_manifest(store)
    ledger_entries = manifest.get("month_ledgers")
    if not isinstance(ledger_entries, list):
        raise SnapshotContractError("pinned manifest has no month_ledgers list")
    if len(ledger_entries) > MAX_LEDGER_COUNT:
        raise SnapshotContractError(
            f"pinned manifest names {len(ledger_entries)} ledgers, above the bounded limit {MAX_LEDGER_COUNT}"
        )

    products: dict[str, dict[str, object]] = {
        product.product_id: {
            "contract": {
                "stream": product.stream,
                "source_parameter": product.source_parameter,
                "signal_name": product.signal_name,
                "data_source_key": product.data_source_key,
                "support_key": product.support_key,
                "normalized_unit": product.normalized_unit,
                "original_unit": product.original_unit,
                "cell_grid_name": product.cell_grid_name,
            },
            "physical_rows": 0,
            "source_parts": 0,
            "source_bytes": 0,
            "observation_day_min": None,
            "observation_day_max": None,
            "monthly_populations": defaultdict(lambda: {"rows": 0, "parts": 0, "bytes": 0}),
            "dimension_populations": defaultdict(lambda: {"rows": 0, "parts": 0}),
            "source_part_lineage": [],
        }
        for product in PRODUCTS
    }
    exclusions: dict[str, dict[str, object]] = defaultdict(
        lambda: {"rows": 0, "parts": 0, "dimension_populations": defaultdict(lambda: {"rows": 0, "parts": 0})}
    )
    total_ledger_parts = 0
    total_ledger_rows = 0

    for entry in ledger_entries:
        if not isinstance(entry, Mapping):
            raise SnapshotContractError("manifest month_ledgers contains a non-object entry")
        month = entry.get("observation_month")
        batch_index = entry.get("cell_batch_index")
        if not isinstance(month, str) or not isinstance(batch_index, int):
            raise SnapshotContractError("manifest ledger entry has invalid month or cell_batch_index")
        key = _ledger_key(month, batch_index)
        ledger = _json_object(_get_required(store, key), key=key)
        _validate_ledger_summary(entry, ledger, key=key)
        parts = ledger["parts"]
        if not isinstance(parts, list):
            raise SnapshotContractError(f"{key} has no parts list")
        total_ledger_parts += len(parts)
        total_ledger_rows += int(ledger["row_count"])
        if total_ledger_parts > MAX_SOURCE_PART_COUNT:
            raise SnapshotContractError(
                f"ledger scan reached {total_ledger_parts} parts, above the bounded limit {MAX_SOURCE_PART_COUNT}"
            )
        for part in parts:
            if not isinstance(part, Mapping):
                raise SnapshotContractError(f"{key} contains a non-object part")
            dimensions = _contract_dimensions(part)
            product, classification = _classification(dimensions)
            if product is None:
                continue
            row_count = int(part["row_count"])
            byte_count = int(part["byte_count"])
            first_day, last_day = _date_bounds(part)
            population_key = json.dumps(dimensions, sort_keys=True, separators=(",", ":"))
            if classification != "included-physical-row":
                excluded = exclusions[classification]
                excluded["rows"] = int(excluded["rows"]) + row_count
                excluded["parts"] = int(excluded["parts"]) + 1
                population = excluded["dimension_populations"][population_key]
                population["rows"] += row_count
                population["parts"] += 1
                continue
            target = products[product.product_id]
            target["physical_rows"] = int(target["physical_rows"]) + row_count
            target["source_parts"] = int(target["source_parts"]) + 1
            target["source_bytes"] = int(target["source_bytes"]) + byte_count
            current_min = target["observation_day_min"]
            current_max = target["observation_day_max"]
            target["observation_day_min"] = first_day if current_min is None else min(str(current_min), first_day)
            target["observation_day_max"] = last_day if current_max is None else max(str(current_max), last_day)
            monthly = target["monthly_populations"][month]
            monthly["rows"] += row_count
            monthly["parts"] += 1
            monthly["bytes"] += byte_count
            monthly["observation_day_min"] = min(str(monthly.get("observation_day_min", first_day)), first_day)
            monthly["observation_day_max"] = max(str(monthly.get("observation_day_max", last_day)), last_day)
            population = target["dimension_populations"][population_key]
            population["rows"] += row_count
            population["parts"] += 1
            target["source_part_lineage"].append(
                {
                    "ledger_key": key,
                    "ledger_source_row_digest": ledger["source_row_digest"],
                    "source_part_key": part["key"],
                    "source_part_sha256": part["sha256"],
                    "source_part_row_digest": part["row_digest"],
                    "rows": row_count,
                    "bytes": byte_count,
                    "observation_day_min": first_day,
                    "observation_day_max": last_day,
                }
            )

    for target in products.values():
        target["monthly_populations"] = dict(sorted(target["monthly_populations"].items()))
        target["dimension_populations"] = [
            {"dimensions": json.loads(key), **population}
            for key, population in sorted(target["dimension_populations"].items())
        ]
        lineage = sorted(target["source_part_lineage"], key=lambda value: str(value["source_part_key"]))
        target["source_part_lineage_count"] = len(lineage)
        target["source_part_lineage_sha256"] = _sha256(
            json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if include_lineage:
            target["source_part_lineage"] = lineage
        else:
            target.pop("source_part_lineage")
    rendered_exclusions = {
        classification: {
            "rows": excluded["rows"],
            "parts": excluded["parts"],
            "dimension_populations": [
                {"dimensions": json.loads(key), **population}
                for key, population in sorted(excluded["dimension_populations"].items())
            ],
        }
        for classification, excluded in sorted(exclusions.items())
    }
    report = {
        "status": "clean" if not rendered_exclusions else "classified-exclusions",
        "snapshot": {
            "snapshot_id": SNAPSHOT_ID,
            "snapshot_prefix": SNAPSHOT_PREFIX,
            "manifest_key": SNAPSHOT_MANIFEST_KEY,
            "manifest_sha256": REQUIRED_MANIFEST_SHA256,
            "manifest_rows": manifest["row_count"],
            "manifest_parts": manifest["partition_count"],
            "manifest_bytes": manifest["byte_count"],
            "manifest_day_min": manifest["observation_day_min"],
            "manifest_day_max": manifest["observation_day_max"],
            "ledger_count": len(ledger_entries),
            "ledger_rows_reconciled": total_ledger_rows,
            "ledger_parts_reconciled": total_ledger_parts,
        },
        "products": products,
        "exclusions": rendered_exclusions,
    }
    _assert_vpd_census(report)
    return report


def _assert_vpd_census(report: Mapping[str, object]) -> None:
    """Bind every build and verification pass to the measured VPD snapshot population."""
    products = report.get("products")
    exclusions = report.get("exclusions")
    if not isinstance(products, Mapping) or not isinstance(exclusions, Mapping):
        raise SnapshotContractError("VPD census is structurally incomplete")
    if exclusions:
        raise SnapshotContractError(f"VPD census contains classified exclusions: {sorted(exclusions)}")
    target = products.get("vpd")
    if not isinstance(target, Mapping):
        raise SnapshotContractError("VPD census has no dedicated product population")
    expected = {
        "physical_rows": EXPECTED_PHYSICAL_ROWS,
        "source_parts": EXPECTED_SOURCE_PARTS,
        "source_bytes": EXPECTED_SOURCE_BYTES,
        "observation_day_min": EXPECTED_FIRST_DAY.isoformat(),
        "observation_day_max": EXPECTED_LAST_DAY.isoformat(),
    }
    drift = {field: (target.get(field), value) for field, value in expected.items() if target.get(field) != value}
    months = target.get("monthly_populations")
    if not isinstance(months, Mapping) or len(months) != EXPECTED_MONTH_COUNT:
        drift["month_count"] = (len(months) if isinstance(months, Mapping) else None, EXPECTED_MONTH_COUNT)
    if drift:
        raise SnapshotContractError(f"pinned VPD census drifted: {drift}")


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) + "\n").encode("utf-8")


def _product_contract(product: ProductContract) -> dict[str, object]:
    return {
        "product_id": product.product_id,
        "stream": product.stream,
        "source_parameter": product.source_parameter,
        "signal_name": product.signal_name,
        "data_source_key": product.data_source_key,
        "support_key": product.support_key,
        "normalized_unit": product.normalized_unit,
        "original_unit": product.original_unit,
        "cell_grid_name": product.cell_grid_name,
        "physical_policy": "byte-identical-copy-of-every-canonical-source-part",
        "observed_grain": list(GRAIN),
        "release_precedence": ["source_release.retrieved_at DESC", "canonical.id DESC"],
        "zoom_tiers": [int(BASE_ZOOM_TIER), *(int(tier) for tier in DERIVED_ZOOM_TIERS)],
    }


def _product_root(product: ProductContract) -> str:
    return f"layer={product.stream}/snapshot={SNAPSHOT_ID}/"


def _put_immutable(
    store: ObjectStore,
    *,
    product: ProductContract,
    relative_key: str,
    payload: bytes,
    content_type: str,
) -> dict[str, object]:
    root = _product_root(product)
    if not relative_key.startswith(root):
        raise SnapshotContractError(f"output key {relative_key!r} escapes the dedicated product root {root!r}")
    absolute_key = store.key_for(relative_key)
    existing_size = store._backend.size_of(absolute_key)  # noqa: SLF001 - immutable snapshot layout.
    if existing_size is not None:
        existing = store._backend.get(absolute_key)  # noqa: SLF001 - immutable snapshot layout.
        if existing != payload:
            raise SnapshotContractError(
                f"immutable output {relative_key} already exists with different bytes; refusing overwrite"
            )
    else:
        store._backend.put(absolute_key, payload, content_type=content_type)  # noqa: SLF001 - no-delete writer.
    return {
        "key": relative_key,
        "bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _read_and_verify_receipt(store: ObjectStore, receipt: Mapping[str, object]) -> bytes:
    key = receipt.get("key")
    expected_sha256 = receipt.get("sha256")
    expected_bytes = receipt.get("bytes")
    if not isinstance(key, str) or not isinstance(expected_sha256, str) or not isinstance(expected_bytes, int):
        raise SnapshotContractError("output receipt has an invalid key, SHA-256, or byte count")
    payload = _get_required(store, key)
    if len(payload) != expected_bytes or _sha256(payload) != expected_sha256:
        raise SnapshotContractError(f"output receipt no longer matches immutable object {key}")
    return payload


def _dimension_table(
    store: ObjectStore,
    manifest: Mapping[str, object],
    name: str,
) -> pa.Table:
    dimensions = manifest.get("dimension_objects")
    if not isinstance(dimensions, Mapping):
        raise SnapshotContractError("pinned manifest has no dimension_objects map")
    descriptor = dimensions.get(name)
    if not isinstance(descriptor, Mapping):
        raise SnapshotContractError(f"pinned manifest has no descriptor for dimension {name!r}")
    key = descriptor.get("key")
    expected_sha256 = descriptor.get("sha256")
    expected_bytes = descriptor.get("byte_count")
    expected_rows = descriptor.get("row_count")
    if (
        not isinstance(key, str)
        or not isinstance(expected_sha256, str)
        or not isinstance(expected_bytes, int)
        or not isinstance(expected_rows, int)
    ):
        raise SnapshotContractError(f"dimension descriptor {name!r} is incomplete")
    payload = _get_required(store, key)
    if len(payload) != expected_bytes or _sha256(payload) != expected_sha256:
        raise SnapshotContractError(f"dimension object {key} does not match the pinned manifest")
    table = pq.read_table(io.BytesIO(payload))
    if table.num_rows != expected_rows:
        raise SnapshotContractError(f"dimension object {key} has {table.num_rows} rows, expected {expected_rows}")
    return table


def _read_source_part(
    store: ObjectStore,
    lineage: Mapping[str, object],
) -> tuple[dict[str, object], bytes, pa.Table]:
    key = lineage.get("source_part_key")
    expected_sha256 = lineage.get("source_part_sha256")
    expected_rows = lineage.get("rows")
    expected_bytes = lineage.get("bytes")
    if (
        not isinstance(key, str)
        or not isinstance(expected_sha256, str)
        or not isinstance(expected_rows, int)
        or not isinstance(expected_bytes, int)
    ):
        raise SnapshotContractError("source lineage has an invalid key, digest, row count, or byte count")
    payload = _get_required(store, key)
    if len(payload) != expected_bytes or _sha256(payload) != expected_sha256:
        raise SnapshotContractError(f"canonical source part {key} does not match its pinned ledger")
    table = pq.read_table(io.BytesIO(payload))
    if table.num_rows != expected_rows:
        raise SnapshotContractError(f"canonical source part {key} has {table.num_rows} rows, expected {expected_rows}")
    return dict(lineage), payload, table


def _serialize_parquet(table: pa.Table, *, compression: str) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression=compression, write_statistics=True)
    return buffer.getvalue()


def _month_for_lineage(lineage: Mapping[str, object]) -> str:
    first_day = lineage.get("observation_day_min")
    last_day = lineage.get("observation_day_max")
    if not isinstance(first_day, str) or not isinstance(last_day, str) or first_day[:7] != last_day[:7]:
        raise SnapshotContractError(f"source part {lineage.get('source_part_key')!r} crosses a monthly boundary")
    return first_day[:7]


def _observed_base_table(
    physical: pl.DataFrame,
    *,
    product: ProductContract,
    releases: pl.DataFrame,
    data_sources: pl.DataFrame,
) -> tuple[pa.Table, dict[str, int], int, int]:
    enriched = physical.join(
        releases.select(
            pl.col("id").alias("source_release_id"),
            pl.col("retrieved_at").alias("release_retrieved_at"),
        ),
        on="source_release_id",
        how="left",
        validate="m:1",
    ).join(
        data_sources.select(
            pl.col("id").alias("data_source_id"),
            pl.col("allowed_client_exposure"),
        ),
        on="data_source_id",
        how="left",
        validate="m:1",
    )
    exclusion = (
        pl.when(pl.col("source_parameter") != product.source_parameter)
        .then(pl.lit("off-contract:source_parameter"))
        .when(pl.col("signal_name") != product.signal_name)
        .then(pl.lit("off-contract:signal_name"))
        .when(pl.col("product_key") != product.source_parameter)
        .then(pl.lit("off-contract:product_key"))
        .when(pl.col("data_source_key") != product.data_source_key)
        .then(pl.lit("off-contract:data_source_key"))
        .when(pl.col("support_key") != product.support_key)
        .then(pl.lit("off-contract:support_key"))
        .when(pl.col("normalized_unit") != product.normalized_unit)
        .then(pl.lit("off-contract:normalized_unit"))
        .when(pl.col("original_unit") != product.original_unit)
        .then(pl.lit("off-contract:original_unit"))
        .when(pl.col("cell_grid_name") != product.cell_grid_name)
        .then(pl.lit("off-contract:cell_grid_name"))
        .when(pl.col("quality_flag") != "accepted")
        .then(pl.lit("quality:not-accepted"))
        .when(~pl.col("is_observed"))
        .then(pl.lit("observation:not-observed"))
        .when(~pl.col("normalized_value").is_finite().fill_null(False))
        .then(pl.lit("value:missing-or-non-finite"))
        .when(pl.col("release_retrieved_at").is_null())
        .then(pl.lit("lineage:missing-source-release"))
        .when(pl.col("allowed_client_exposure").is_null())
        .then(pl.lit("lineage:missing-data-source-policy"))
        .otherwise(None)
        .alias("_exclusion")
    )
    classified = enriched.with_columns(exclusion)
    excluded_frame = (
        classified.filter(pl.col("_exclusion").is_not_null())
        .group_by("_exclusion")
        .agg(pl.len().alias("rows"))
        .sort("_exclusion")
    )
    exclusions = {str(row["_exclusion"]): int(row["rows"]) for row in excluded_frame.to_dicts()}
    included = classified.filter(pl.col("_exclusion").is_null())
    included_rows = included.height
    if included_rows == 0:
        raise SnapshotContractError(f"{product.product_id} month has no governed rows after explicit exclusions")
    counts = included.group_by(list(GRAIN)).agg(
        pl.len().cast(pl.Int64).alias("observation_count"),
        pl.col("observed_at").max().alias("newest_observed_at"),
    )
    order = [*GRAIN, "release_retrieved_at", "id"]
    winners = (
        included.sort(order, descending=[False] * len(GRAIN) + [True, True], nulls_last=True)
        .unique(subset=list(GRAIN), keep="first", maintain_order=True)
        .join(counts, on=list(GRAIN), how="left", validate="1:1")
    )
    selected = winners.select(
        pl.col("support_key"),
        pl.col("signal_name"),
        pl.col("normalized_unit"),
        pl.col("cell_id"),
        pl.col("observation_day").alias("observed_day"),
        pl.col("normalized_value"),
        pl.col("observation_count"),
        pl.col("newest_observed_at"),
        pl.col("coverage_fraction"),
        pl.col("allowed_client_exposure"),
        pl.col("cell_centroid_longitude").alias("cell_longitude"),
        pl.col("cell_centroid_latitude").alias("cell_latitude"),
    )
    stream = observed_stream_schema(product.stream)
    table = conform_to_stream_schema(selected.to_arrow(), stream)
    if table.num_rows != winners.height:
        raise SnapshotContractError(f"{product.product_id} z13 conformity changed its winner row count")
    return table, exclusions, included_rows, included_rows - table.num_rows


def _physical_output_key(product: ProductContract, month: str, source_key: str) -> str:
    if not source_key.startswith(SNAPSHOT_PREFIX):
        raise SnapshotContractError(f"source part {source_key!r} escapes the pinned snapshot")
    relative_source_key = source_key.removeprefix(SNAPSHOT_PREFIX)
    if f"year={month[:4]}/month={month[5:7]}/" not in relative_source_key:
        raise SnapshotContractError(f"source part {source_key!r} does not belong to checkpoint month {month}")
    return f"{_product_root(product)}kind=physical/{relative_source_key}"


def _observed_output_key(product: ProductContract, month: str, zoom: int) -> str:
    year, month_number = month.split("-", maxsplit=1)
    return f"{_product_root(product)}kind=observed/zoom={zoom:02d}/year={year}/month={month_number}/part-00000.parquet"


def _checkpoint_key(product: ProductContract, month: str) -> str:
    year, month_number = month.split("-", maxsplit=1)
    return f"{_product_root(product)}_checkpoints/year={year}/month={month_number}.json"


def _write_observed_rung(
    store: ObjectStore,
    *,
    product: ProductContract,
    month: str,
    zoom: int,
    table: pa.Table,
) -> dict[str, object]:
    stream = observed_stream_schema(product.stream)
    conformed = conform_to_stream_schema(table, stream)
    payload = _serialize_parquet(conformed, compression=stream.compression)
    receipt = _put_immutable(
        store,
        product=product,
        relative_key=_observed_output_key(product, month, zoom),
        payload=payload,
        content_type=PARQUET_CONTENT_TYPE,
    )
    receipt["rows"] = conformed.num_rows
    receipt["zoom"] = zoom
    return receipt


def _checkpoint_if_complete(
    store: ObjectStore,
    *,
    product: ProductContract,
    month: str,
    expected_lineage: list[dict[str, object]],
) -> tuple[dict[str, object], bytes] | None:
    key = _checkpoint_key(product, month)
    payload = store._backend.get(store.key_for(key))  # noqa: SLF001 - bounded checkpoint read.
    if payload is None:
        return None
    checkpoint = _json_object(payload, key=key)
    expected = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "observation_month": month,
        "source_lineage": expected_lineage,
    }
    for field, value in expected.items():
        if checkpoint.get(field) != value:
            raise SnapshotContractError(f"checkpoint {key} no longer matches its pinned {field!r}")
    physical_outputs = checkpoint.get("physical_outputs")
    rungs = checkpoint.get("rungs")
    if not isinstance(physical_outputs, list) or not isinstance(rungs, Mapping):
        raise SnapshotContractError(f"checkpoint {key} has no physical outputs or rung receipts")
    for receipt in physical_outputs:
        if not isinstance(receipt, Mapping):
            raise SnapshotContractError(f"checkpoint {key} contains an invalid physical receipt")
        _read_and_verify_receipt(store, receipt)
    for receipt in rungs.values():
        if not isinstance(receipt, Mapping):
            raise SnapshotContractError(f"checkpoint {key} contains an invalid rung receipt")
        _read_and_verify_receipt(store, receipt)
    return checkpoint, payload


def _build_month(
    store: ObjectStore,
    *,
    product: ProductContract,
    month: str,
    lineages: list[dict[str, object]],
    releases: pl.DataFrame,
    data_sources: pl.DataFrame,
    concurrency: int,
) -> tuple[dict[str, object], dict[str, object]]:
    expected_lineage = sorted(lineages, key=lambda value: str(value["source_part_key"]))
    completed = _checkpoint_if_complete(
        store,
        product=product,
        month=month,
        expected_lineage=expected_lineage,
    )
    if completed is not None:
        checkpoint, payload = completed
        return checkpoint, {
            "key": _checkpoint_key(product, month),
            "bytes": len(payload),
            "sha256": _sha256(payload),
            "month": month,
        }

    with ThreadPoolExecutor(max_workers=min(concurrency, len(expected_lineage))) as executor:
        loaded = list(executor.map(lambda lineage: _read_source_part(store, lineage), expected_lineage))
    physical_frames: list[pl.DataFrame] = []
    physical_outputs: list[dict[str, object]] = []
    for lineage, payload, table in loaded:
        source_key = str(lineage["source_part_key"])
        output = _put_immutable(
            store,
            product=product,
            relative_key=_physical_output_key(product, month, source_key),
            payload=payload,
            content_type=PARQUET_CONTENT_TYPE,
        )
        output.update(
            {
                "rows": int(lineage["rows"]),
                "source_part_key": source_key,
                "source_part_sha256": lineage["source_part_sha256"],
                "source_part_row_digest": lineage["source_part_row_digest"],
                "ledger_key": lineage["ledger_key"],
                "ledger_source_row_digest": lineage["ledger_source_row_digest"],
            }
        )
        if output["sha256"] != lineage["source_part_sha256"]:
            raise SnapshotContractError(f"physical copy of {source_key} is not byte-identical")
        physical_outputs.append(output)
        physical_frames.append(pl.from_arrow(table).with_columns(pl.lit(source_key).alias("_source_part_key")))

    physical = pl.concat(physical_frames, how="vertical_relaxed", rechunk=True)
    expected_physical_rows = sum(int(lineage["rows"]) for lineage in expected_lineage)
    if physical.height != expected_physical_rows:
        raise SnapshotContractError(
            f"{product.product_id} {month} assembled {physical.height} physical rows, expected {expected_physical_rows}"
        )
    base, exclusions, included_rows, superseded_rows = _observed_base_table(
        physical,
        product=product,
        releases=releases,
        data_sources=data_sources,
    )
    exclusion_rows = sum(exclusions.values())
    if physical.height != included_rows + exclusion_rows:
        raise SnapshotContractError(f"{product.product_id} {month} physical inclusion/exclusion equation failed")
    if included_rows != base.num_rows + superseded_rows:
        raise SnapshotContractError(f"{product.product_id} {month} release-precedence equation failed")

    base_frame = pl.from_arrow(base)
    day_counts = base_frame.group_by("observed_day").agg(pl.len().alias("rows")).sort("observed_day")
    bad_days = day_counts.filter(pl.col("rows") != EXPECTED_CELLS_PER_DAY)
    if bad_days.height:
        raise SnapshotContractError(
            f"{product.product_id} {month} has non-{EXPECTED_CELLS_PER_DAY}-cell winner days: "
            f"{bad_days.head(5).to_dicts()}"
        )
    month_day_count = day_counts.height
    month_first_day = day_counts["observed_day"].min()
    month_last_day = day_counts["observed_day"].max()

    rungs: dict[str, dict[str, object]] = {}
    rungs[str(int(BASE_ZOOM_TIER))] = _write_observed_rung(
        store,
        product=product,
        month=month,
        zoom=int(BASE_ZOOM_TIER),
        table=base,
    )
    for zoom in DERIVED_ZOOM_TIERS:
        derived = derive_tier(base_frame, stream=product.stream, tier=zoom)
        rungs[str(int(zoom))] = _write_observed_rung(
            store,
            product=product,
            month=month,
            zoom=int(zoom),
            table=derived.to_arrow(),
        )

    checkpoint = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_key": SNAPSHOT_MANIFEST_KEY,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "observation_month": month,
        "source_lineage": expected_lineage,
        "physical_outputs": sorted(physical_outputs, key=lambda value: str(value["key"])),
        "reconciliation": {
            "physical_rows": physical.height,
            "included_governed_rows": included_rows,
            "excluded_rows": exclusion_rows,
            "exclusions": exclusions,
            "release_winner_rows": base.num_rows,
            "release_superseded_rows": superseded_rows,
            "winner_day_count": month_day_count,
            "winner_day_min": str(month_first_day),
            "winner_day_max": str(month_last_day),
            "winner_cells_per_day": EXPECTED_CELLS_PER_DAY,
            "physical_equals_included_plus_excluded": physical.height == included_rows + exclusion_rows,
            "included_equals_winners_plus_superseded": included_rows == base.num_rows + superseded_rows,
        },
        "rungs": rungs,
    }
    checkpoint_payload = _canonical_json_bytes(checkpoint)
    receipt = _put_immutable(
        store,
        product=product,
        relative_key=_checkpoint_key(product, month),
        payload=checkpoint_payload,
        content_type=JSON_CONTENT_TYPE,
    )
    receipt["month"] = month
    return checkpoint, receipt


def _sum_checkpoint_counts(checkpoints: list[Mapping[str, object]]) -> dict[str, object]:
    physical_rows = included_rows = excluded_rows = winner_rows = superseded_rows = 0
    winner_day_count = 0
    winner_day_min: str | None = None
    winner_day_max: str | None = None
    source_parts = 0
    exclusion_classes: dict[str, int] = defaultdict(int)
    rung_totals: dict[str, dict[str, int]] = defaultdict(lambda: {"rows": 0, "parts": 0, "bytes": 0})
    for checkpoint in checkpoints:
        reconciliation = checkpoint.get("reconciliation")
        lineages = checkpoint.get("source_lineage")
        rungs = checkpoint.get("rungs")
        if not isinstance(reconciliation, Mapping) or not isinstance(lineages, list) or not isinstance(rungs, Mapping):
            raise SnapshotContractError("checkpoint is incomplete during product aggregation")
        physical_rows += int(reconciliation["physical_rows"])
        included_rows += int(reconciliation["included_governed_rows"])
        excluded_rows += int(reconciliation["excluded_rows"])
        winner_rows += int(reconciliation["release_winner_rows"])
        superseded_rows += int(reconciliation["release_superseded_rows"])
        winner_day_count += int(reconciliation["winner_day_count"])
        checkpoint_min = str(reconciliation["winner_day_min"])
        checkpoint_max = str(reconciliation["winner_day_max"])
        winner_day_min = checkpoint_min if winner_day_min is None else min(winner_day_min, checkpoint_min)
        winner_day_max = checkpoint_max if winner_day_max is None else max(winner_day_max, checkpoint_max)
        if int(reconciliation["winner_cells_per_day"]) != EXPECTED_CELLS_PER_DAY:
            raise SnapshotContractError("checkpoint changed the VPD cells-per-day contract")
        source_parts += len(lineages)
        exclusions = reconciliation.get("exclusions")
        if not isinstance(exclusions, Mapping):
            raise SnapshotContractError("checkpoint reconciliation has no exclusion classes")
        for reason, rows in exclusions.items():
            exclusion_classes[str(reason)] += int(rows)
        for zoom, receipt in rungs.items():
            if not isinstance(receipt, Mapping):
                raise SnapshotContractError("checkpoint contains an invalid rung receipt")
            rung_totals[str(zoom)]["rows"] += int(receipt["rows"])
            rung_totals[str(zoom)]["parts"] += 1
            rung_totals[str(zoom)]["bytes"] += int(receipt["bytes"])
    return {
        "physical_rows": physical_rows,
        "source_parts": source_parts,
        "included_governed_rows": included_rows,
        "excluded_rows": excluded_rows,
        "exclusions": dict(sorted(exclusion_classes.items())),
        "release_winner_rows": winner_rows,
        "release_superseded_rows": superseded_rows,
        "winner_day_count": winner_day_count,
        "winner_day_min": winner_day_min,
        "winner_day_max": winner_day_max,
        "winner_cells_per_day": EXPECTED_CELLS_PER_DAY,
        "rungs": dict(sorted(rung_totals.items(), key=lambda value: int(value[0]), reverse=True)),
        "physical_equals_included_plus_excluded": physical_rows == included_rows + excluded_rows,
        "included_equals_winners_plus_superseded": included_rows == winner_rows + superseded_rows,
    }


def _assert_vpd_totals(totals: Mapping[str, object]) -> None:
    """Require the completed product to close the measured snapshot equations exactly."""
    expected = {
        "physical_rows": EXPECTED_PHYSICAL_ROWS,
        "source_parts": EXPECTED_SOURCE_PARTS,
        "included_governed_rows": EXPECTED_PHYSICAL_ROWS,
        "excluded_rows": 0,
        "release_winner_rows": EXPECTED_WINNER_ROWS,
        "release_superseded_rows": EXPECTED_SUPERSEDED_ROWS,
        "winner_day_count": EXPECTED_DAY_COUNT,
        "winner_day_min": EXPECTED_FIRST_DAY.isoformat(),
        "winner_day_max": EXPECTED_LAST_DAY.isoformat(),
        "winner_cells_per_day": EXPECTED_CELLS_PER_DAY,
    }
    drift = {field: (totals.get(field), value) for field, value in expected.items() if totals.get(field) != value}
    if totals.get("exclusions") != {}:
        drift["exclusions"] = (totals.get("exclusions"), {})
    rungs = totals.get("rungs")
    expected_rung_rows = {
        "13": EXPECTED_WINNER_ROWS,
        "9": EXPECTED_WINNER_ROWS,
        "5": EXPECTED_WINNER_ROWS,
        "0": EXPECTED_DAY_COUNT * 6,
    }
    if not isinstance(rungs, Mapping):
        drift["rungs"] = (rungs, "mapping")
    else:
        for zoom, rows in expected_rung_rows.items():
            receipt = rungs.get(zoom)
            if not isinstance(receipt, Mapping):
                drift[f"rung_z{zoom}"] = (receipt, "receipt")
                continue
            if int(receipt.get("rows", -1)) != rows:
                drift[f"rung_z{zoom}_rows"] = (receipt.get("rows"), rows)
            if int(receipt.get("parts", -1)) != EXPECTED_MONTH_COUNT:
                drift[f"rung_z{zoom}_parts"] = (receipt.get("parts"), EXPECTED_MONTH_COUNT)
    if drift:
        raise SnapshotContractError(f"completed VPD totals drifted: {drift}")


def _family_reconciliation(report: Mapping[str, object]) -> dict[str, object]:
    snapshot = report.get("snapshot")
    products = report.get("products")
    if not isinstance(snapshot, Mapping) or not isinstance(products, Mapping):
        raise SnapshotContractError("census is missing snapshot or product populations")
    product_rows = {
        product.product_id: int(products[product.product_id]["physical_rows"])  # type: ignore[index]
        for product in PRODUCTS
    }
    vpd_rows = sum(product_rows.values())
    snapshot_rows = int(snapshot["manifest_rows"])
    outside_rows = snapshot_rows - vpd_rows
    return {
        "snapshot_rows": snapshot_rows,
        "vpd_physical_rows": vpd_rows,
        "product_physical_rows": product_rows,
        "outside_vpd_rows": outside_rows,
        "snapshot_equals_products_plus_outside": snapshot_rows == vpd_rows + outside_rows,
    }


def _verify_product(
    store: ObjectStore,
    *,
    product: ProductContract,
    census_target: Mapping[str, object],
    mode: str,
) -> dict[str, object]:
    root = _product_root(product)
    complete_key = f"{root}_COMPLETE"
    complete_payload = _get_required(store, complete_key)
    complete = _json_object(complete_payload, key=complete_key)
    manifest_key = f"{root}manifest.json"
    expected_complete = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "manifest_key": manifest_key,
    }
    for field, value in expected_complete.items():
        if complete.get(field) != value:
            raise SnapshotContractError(f"{complete_key} no longer matches {field!r}")
    manifest_payload = _get_required(store, manifest_key)
    if _sha256(manifest_payload) != complete.get("manifest_sha256"):
        raise SnapshotContractError(f"{manifest_key} does not match {complete_key}")
    manifest = _json_object(manifest_payload, key=manifest_key)
    expected_manifest = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_key": SNAPSHOT_MANIFEST_KEY,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "source_part_lineage_count": census_target["source_part_lineage_count"],
        "source_part_lineage_sha256": census_target["source_part_lineage_sha256"],
    }
    for field, value in expected_manifest.items():
        if manifest.get(field) != value:
            raise SnapshotContractError(f"{manifest_key} no longer matches {field!r}")
    checkpoint_receipts = manifest.get("checkpoints")
    if not isinstance(checkpoint_receipts, list):
        raise SnapshotContractError(f"{manifest_key} has no checkpoint list")
    sample_indexes = (
        {round(index * (len(checkpoint_receipts) - 1) / 4) for index in range(5)} if checkpoint_receipts else set()
    )
    checkpoints: list[dict[str, object]] = []
    lineage: list[dict[str, object]] = []
    sampled_physical_count = 0
    sampled_rung_count = 0
    for checkpoint_index, receipt in enumerate(checkpoint_receipts):
        if not isinstance(receipt, Mapping):
            raise SnapshotContractError(f"{manifest_key} has an invalid checkpoint receipt")
        payload = _read_and_verify_receipt(store, receipt)
        checkpoint = _json_object(payload, key=str(receipt["key"]))
        if checkpoint.get("product") != _product_contract(product):
            raise SnapshotContractError(f"checkpoint {receipt['key']} belongs to another product")
        physical_outputs = checkpoint.get("physical_outputs")
        rungs = checkpoint.get("rungs")
        source_lineage = checkpoint.get("source_lineage")
        if (
            not isinstance(physical_outputs, list)
            or not isinstance(rungs, Mapping)
            or not isinstance(source_lineage, list)
        ):
            raise SnapshotContractError(f"checkpoint {receipt['key']} is structurally incomplete")
        sampled_physical_indexes = {
            0,
            len(physical_outputs) // 2,
            len(physical_outputs) - 1,
        }
        for output_index, output in enumerate(physical_outputs):
            if not isinstance(output, Mapping):
                raise SnapshotContractError(f"checkpoint {receipt['key']} has an invalid physical output")
            if mode == "full" or (checkpoint_index in sample_indexes and output_index in sampled_physical_indexes):
                payload_bytes = _read_and_verify_receipt(store, output)
                if _sha256(payload_bytes) != output.get("source_part_sha256"):
                    raise SnapshotContractError(f"physical output {output['key']} is not byte-identical to its source")
                sampled_physical_count += 1
        for output in rungs.values():
            if not isinstance(output, Mapping):
                raise SnapshotContractError(f"checkpoint {receipt['key']} has an invalid rung output")
            if mode == "full" or checkpoint_index in sample_indexes:
                _read_and_verify_receipt(store, output)
                sampled_rung_count += 1
        checkpoints.append(checkpoint)
        lineage.extend(dict(item) for item in source_lineage if isinstance(item, Mapping))
    lineage = sorted(lineage, key=lambda value: str(value["source_part_key"]))
    lineage_sha256 = _sha256(json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if len(lineage) != int(census_target["source_part_lineage_count"]):
        raise SnapshotContractError(f"{product.product_id} checkpoint lineage count does not match the census")
    if lineage_sha256 != census_target["source_part_lineage_sha256"]:
        raise SnapshotContractError(f"{product.product_id} checkpoint lineage digest does not match the census")
    totals = _sum_checkpoint_counts(checkpoints)
    _assert_vpd_totals(totals)
    if totals != manifest.get("totals"):
        raise SnapshotContractError(f"{manifest_key} totals do not equal its checkpoint population")
    if int(totals["physical_rows"]) != int(census_target["physical_rows"]):
        raise SnapshotContractError(f"{product.product_id} physical rows do not match the canonical census")
    if not totals["physical_equals_included_plus_excluded"] or not totals["included_equals_winners_plus_superseded"]:
        raise SnapshotContractError(f"{product.product_id} reconciliation equations are false")
    return {
        "status": "clean",
        "product_id": product.product_id,
        "stream": product.stream,
        "root": root,
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "complete_key": complete_key,
        "complete_sha256": _sha256(complete_payload),
        "checkpoint_count": len(checkpoints),
        "verification_mode": mode,
        "sampled_checkpoint_count": len(sample_indexes) if mode == "sparse" else len(checkpoints),
        "sampled_physical_object_count": sampled_physical_count,
        "sampled_rung_object_count": sampled_rung_count,
        "totals": totals,
    }


def _build_product(
    store: ObjectStore,
    *,
    product: ProductContract,
    census_target: Mapping[str, object],
    family_reconciliation: Mapping[str, object],
    releases: pl.DataFrame,
    data_sources: pl.DataFrame,
    concurrency: int,
    verification_mode: str,
) -> dict[str, object]:
    complete_key = f"{_product_root(product)}_COMPLETE"
    if store._backend.size_of(store.key_for(complete_key)) is not None:  # noqa: SLF001 - immutable resume check.
        return _verify_product(
            store,
            product=product,
            census_target=census_target,
            mode=verification_mode,
        )
    lineage = census_target.get("source_part_lineage")
    if not isinstance(lineage, list):
        raise SnapshotContractError(f"{product.product_id} census omitted source-part lineage")
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in lineage:
        if not isinstance(item, Mapping):
            raise SnapshotContractError(f"{product.product_id} census contains invalid source lineage")
        grouped[_month_for_lineage(item)].append(dict(item))
    checkpoints: list[dict[str, object]] = []
    checkpoint_receipts: list[dict[str, object]] = []
    for month, month_lineage in sorted(grouped.items()):
        checkpoint, receipt = _build_month(
            store,
            product=product,
            month=month,
            lineages=month_lineage,
            releases=releases,
            data_sources=data_sources,
            concurrency=concurrency,
        )
        checkpoints.append(checkpoint)
        checkpoint_receipts.append(receipt)
        reconciliation = checkpoint["reconciliation"]
        print(
            json.dumps(
                {
                    "event": "vpd-product-month",
                    "product_id": product.product_id,
                    "month": month,
                    "physical_rows": reconciliation["physical_rows"],  # type: ignore[index]
                    "winner_rows": reconciliation["release_winner_rows"],  # type: ignore[index]
                    "excluded_rows": reconciliation["excluded_rows"],  # type: ignore[index]
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
    totals = _sum_checkpoint_counts(checkpoints)
    _assert_vpd_totals(totals)
    if int(totals["physical_rows"]) != int(census_target["physical_rows"]):
        raise SnapshotContractError(f"{product.product_id} built rows do not equal its pinned physical census")
    manifest = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_key": SNAPSHOT_MANIFEST_KEY,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "source_part_lineage_count": census_target["source_part_lineage_count"],
        "source_part_lineage_sha256": census_target["source_part_lineage_sha256"],
        "source_observation_day_min": census_target["observation_day_min"],
        "source_observation_day_max": census_target["observation_day_max"],
        "family_reconciliation": dict(family_reconciliation),
        "checkpoints": sorted(checkpoint_receipts, key=lambda value: str(value["month"])),
        "totals": totals,
    }
    manifest_key = f"{_product_root(product)}manifest.json"
    manifest_payload = _canonical_json_bytes(manifest)
    manifest_receipt = _put_immutable(
        store,
        product=product,
        relative_key=manifest_key,
        payload=manifest_payload,
        content_type=JSON_CONTENT_TYPE,
    )
    complete = {
        "contract_version": OUTPUT_CONTRACT_VERSION,
        "snapshot_id": SNAPSHOT_ID,
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "product": _product_contract(product),
        "manifest_key": manifest_key,
        "manifest_sha256": manifest_receipt["sha256"],
        "manifest_bytes": manifest_receipt["bytes"],
        "checkpoint_count": len(checkpoint_receipts),
        "physical_rows": totals["physical_rows"],
        "completed_at": datetime.now(UTC).isoformat(),
    }
    _put_immutable(
        store,
        product=product,
        relative_key=complete_key,
        payload=_canonical_json_bytes(complete),
        content_type=JSON_CONTENT_TYPE,
    )
    return _verify_product(
        store,
        product=product,
        census_target=census_target,
        mode=verification_mode,
    )


def _census_products(report: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    products = report.get("products")
    if not isinstance(products, Mapping):
        raise SnapshotContractError("census has no product populations")
    return products  # type: ignore[return-value]


def build(store: ObjectStore, *, concurrency: int, verification_mode: str) -> dict[str, object]:
    report = census(store, include_lineage=True)
    census_products = _census_products(report)
    all_keys: set[str] = set()
    for product in PRODUCTS:
        target = census_products[product.product_id]
        lineage = target.get("source_part_lineage")
        if not isinstance(lineage, list):
            raise SnapshotContractError(f"{product.product_id} census has no source lineage")
        keys = {str(item["source_part_key"]) for item in lineage if isinstance(item, Mapping)}
        overlap = all_keys.intersection(keys)
        if overlap:
            raise SnapshotContractError(f"VPD products overlap on source parts: {sorted(overlap)[:3]}")
        all_keys.update(keys)
    manifest = _load_pinned_manifest(store)
    releases = pl.from_arrow(_dimension_table(store, manifest, "source_release"))
    data_sources = pl.from_arrow(_dimension_table(store, manifest, "data_source"))
    family = _family_reconciliation(report)
    products = [
        _build_product(
            store,
            product=product,
            census_target=census_products[product.product_id],
            family_reconciliation=family,
            releases=releases,
            data_sources=data_sources,
            concurrency=concurrency,
            verification_mode=verification_mode,
        )
        for product in PRODUCTS
    ]
    return {
        "status": "completed",
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "family_reconciliation": family,
        "products": products,
    }


def verify(store: ObjectStore, *, mode: str) -> dict[str, object]:
    report = census(store, include_lineage=True)
    census_products = _census_products(report)
    products = [
        _verify_product(
            store,
            product=product,
            census_target=census_products[product.product_id],
            mode=mode,
        )
        for product in PRODUCTS
    ]
    return {
        "status": "clean",
        "snapshot_manifest_sha256": REQUIRED_MANIFEST_SHA256,
        "family_reconciliation": _family_reconciliation(report),
        "products": products,
    }


def main() -> int:
    arguments = _arguments()
    try:
        if arguments.command == "census":
            report = census(ObjectStore.from_settings(), include_lineage=arguments.include_lineage)
        elif arguments.command == "build":
            report = build(
                ObjectStore.from_settings(),
                concurrency=arguments.concurrency,
                verification_mode=arguments.verification,
            )
        elif arguments.command == "verify":
            report = verify(ObjectStore.from_settings(), mode=arguments.mode)
        else:  # pragma: no cover - argparse enforces the command vocabulary.
            raise SnapshotContractError(f"unknown command {arguments.command!r}")
    except Exception as error:
        print(
            json.dumps(
                {"status": "failed", "error_type": type(error).__name__, "error": str(error)},
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
