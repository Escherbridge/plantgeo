"""Build three dedicated ERA5-Land soil-moisture lanes from one pinned snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Mapping, Sequence

import boto3  # type: ignore[import-untyped]
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CHECKOUT_ENV_FILE: Final = Path.home() / "Programming" / "plantgeo" / "services" / "agri-data-service" / ".env"
DEFAULT_ENV_FILE: Final = SERVICE_ROOT / ".env" if (SERVICE_ROOT / ".env").is_file() else CHECKOUT_ENV_FILE
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import ObjectStoreCredentials, Settings  # noqa: E402
from agri_data_service.foundation.parquet.completion import PartitionCompletion  # noqa: E402
from agri_data_service.foundation.parquet.paths import completion_marker_path, partition_path  # noqa: E402
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS  # noqa: E402
from agri_data_service.warehouse.parquet.tiers import derive_tier  # noqa: E402
from agri_data_service.warehouse.schemas.soil_field_moisture import SOIL_FIELD_MOISTURE_STREAMS  # noqa: E402
from agri_data_service.warehouse.schemas.soil_field_moisture_0_7cm import (  # noqa: E402
    SOIL_FIELD_MOISTURE_0_7CM_SCHEMA,
)
from agri_data_service.warehouse.schemas.soil_field_moisture_7_28cm import (  # noqa: E402
    SOIL_FIELD_MOISTURE_7_28CM_SCHEMA,
)
from agri_data_service.warehouse.schemas.soil_field_moisture_28_100cm import (  # noqa: E402
    SOIL_FIELD_MOISTURE_28_100CM_SCHEMA,
)

SOIL_FIELD_MOISTURE_SCHEMAS: Final = {
    "soil-field-moisture-0-7cm": SOIL_FIELD_MOISTURE_0_7CM_SCHEMA,
    "soil-field-moisture-7-28cm": SOIL_FIELD_MOISTURE_7_28CM_SCHEMA,
    "soil-field-moisture-28-100cm": SOIL_FIELD_MOISTURE_28_100CM_SCHEMA,
}

SOURCE_SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
SOURCE_ROOT: Final = f"raw-canonical/signal-observation/snapshot={SOURCE_SNAPSHOT_ID}"
SOURCE_MANIFEST_KEY: Final = f"{SOURCE_ROOT}/manifest.json"
SOURCE_COMPLETE_KEY: Final = f"{SOURCE_ROOT}/_COMPLETE"
PRECEDENCE_CONTRACT: Final = "newest-release-retrieved-at-then-highest-observation-id-v1"
SOURCE_KEY: Final = "open-meteo-era5-land-archive"
SUPPORT_KEY: Final = "era5-land-0.1deg"
NORMALIZED_UNIT: Final = "m^3/m^3"
EXPECTED_SOURCE_PARTS: Final = 424
EXPECTED_PHYSICAL_ROWS: Final = 4_321_672
EXPECTED_ELIGIBLE_ROWS: Final = 4_321_672
EXPECTED_BASE_ROWS: Final = 2_287_320
EXPECTED_DUPLICATES_COLLAPSED: Final = 2_034_352
EXPECTED_DAYS: Final = 1_556
EXPECTED_CELLS_PER_DAY: Final = 1_470
EXPECTED_FIRST_DAY: Final = date(2022, 4, 30)
EXPECTED_LAST_DAY: Final = date(2026, 8, 2)
SNAPSHOT_FIRST_DAY: Final = date(2022, 4, 30)
SNAPSHOT_LAST_DAY: Final = date(2026, 8, 6)
DEFAULT_WORKERS: Final = 8
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed", "ConditionalRequestConflict"})


@dataclass(frozen=True, slots=True)
class ProductSpec:
    product: str
    stream: str
    signal_name: str
    depth_band: str
    expected_source_bytes: int


PRODUCTS: Final = {
    "soil_moisture_0_to_7cm_mean": ProductSpec(
        product="soil_moisture_0_to_7cm_mean",
        stream=SOIL_FIELD_MOISTURE_STREAMS["soil_moisture_0_to_7cm_mean"],
        signal_name="soil_water_content_layer_1",
        depth_band="0-7cm",
        expected_source_bytes=189_525_236,
    ),
    "soil_moisture_7_to_28cm_mean": ProductSpec(
        product="soil_moisture_7_to_28cm_mean",
        stream=SOIL_FIELD_MOISTURE_STREAMS["soil_moisture_7_to_28cm_mean"],
        signal_name="soil_water_content_layer_2",
        depth_band="7-28cm",
        expected_source_bytes=188_856_768,
    ),
    "soil_moisture_28_to_100cm_mean": ProductSpec(
        product="soil_moisture_28_to_100cm_mean",
        stream=SOIL_FIELD_MOISTURE_STREAMS["soil_moisture_28_to_100cm_mean"],
        signal_name="soil_water_content_layer_3",
        depth_band="28-100cm",
        expected_source_bytes=187_626_927,
    ),
}

ACTIVE_PRODUCT = PRODUCTS["soil_moisture_0_to_7cm_mean"]
ACTIVE_STREAM = ACTIVE_PRODUCT.stream
ACTIVE_SCHEMA = SOIL_FIELD_MOISTURE_SCHEMAS[ACTIVE_STREAM]
SOURCE_PARAMETER = ACTIVE_PRODUCT.product
SIGNAL_NAME = ACTIVE_PRODUCT.signal_name
CONTRACT_VERSION = f"{ACTIVE_STREAM}.snapshot-breakdown.v1"
SOURCE_PART_PREFIX = f"{SOURCE_ROOT}/source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/"
DESTINATION_METADATA_ROOT = f"layer={ACTIVE_STREAM}/_breakdown/snapshot={SOURCE_SNAPSHOT_ID}"
DESTINATION_MANIFEST_KEY = f"{DESTINATION_METADATA_ROOT}/manifest.json"
DESTINATION_COMPLETE_KEY = f"{DESTINATION_METADATA_ROOT}/_COMPLETE"
DESTINATION_RUN_KEY = f"{DESTINATION_METADATA_ROOT}/_RUN.json"
DESTINATION_SOURCE_AUDIT_KEY = f"{DESTINATION_METADATA_ROOT}/source-chain-audit.json"
DESTINATION_AUDIT_COMPLETE_KEY = f"{DESTINATION_METADATA_ROOT}/_AUDIT_COMPLETE"


def _activate(product: ProductSpec) -> None:
    global ACTIVE_PRODUCT, ACTIVE_STREAM, ACTIVE_SCHEMA
    global SOURCE_PARAMETER, SIGNAL_NAME, CONTRACT_VERSION, SOURCE_PART_PREFIX
    global DESTINATION_METADATA_ROOT, DESTINATION_MANIFEST_KEY, DESTINATION_COMPLETE_KEY
    global DESTINATION_RUN_KEY, DESTINATION_SOURCE_AUDIT_KEY, DESTINATION_AUDIT_COMPLETE_KEY

    ACTIVE_PRODUCT = product
    ACTIVE_STREAM = product.stream
    ACTIVE_SCHEMA = SOIL_FIELD_MOISTURE_SCHEMAS[ACTIVE_STREAM]
    SOURCE_PARAMETER = product.product
    SIGNAL_NAME = product.signal_name
    CONTRACT_VERSION = f"{ACTIVE_STREAM}.snapshot-breakdown.v1"
    SOURCE_PART_PREFIX = f"{SOURCE_ROOT}/source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/"
    DESTINATION_METADATA_ROOT = f"layer={ACTIVE_STREAM}/_breakdown/snapshot={SOURCE_SNAPSHOT_ID}"
    DESTINATION_MANIFEST_KEY = f"{DESTINATION_METADATA_ROOT}/manifest.json"
    DESTINATION_COMPLETE_KEY = f"{DESTINATION_METADATA_ROOT}/_COMPLETE"
    DESTINATION_RUN_KEY = f"{DESTINATION_METADATA_ROOT}/_RUN.json"
    DESTINATION_SOURCE_AUDIT_KEY = f"{DESTINATION_METADATA_ROOT}/source-chain-audit.json"
    DESTINATION_AUDIT_COMPLETE_KEY = f"{DESTINATION_METADATA_ROOT}/_AUDIT_COMPLETE"


RAW_REQUIRED_COLUMNS: Final = frozenset(
    {
        "id",
        "source_release_id",
        "cell_id",
        "signal_name",
        "source_parameter",
        "support_key",
        "observed_at",
        "data_available_at",
        "normalized_value",
        "normalized_unit",
        "quality_flag",
        "coverage_fraction",
        "is_observed",
        "observation_day",
        "product_key",
        "cell_key",
        "cell_centroid_longitude",
        "cell_centroid_latitude",
        "data_source_id",
        "data_source_key",
        "canonical_row_sha256",
    }
)


class BreakdownError(RuntimeError):
    """Raised when the pinned snapshot cannot produce an exact soil moisture lane."""


class ImmutableObjectConflictError(BreakdownError):
    """Raised when a destination key already contains different bytes."""


@dataclass(frozen=True, slots=True)
class ObjectReceipt:
    key: str
    row_count: int
    byte_count: int
    sha256: str
    kind: str
    zoom: int | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "key": self.key,
            "row_count": self.row_count,
            "byte_count": self.byte_count,
            "sha256": self.sha256,
            "kind": self.kind,
            "zoom": self.zoom,
        }


@dataclass(frozen=True, slots=True)
class SourceMonth:
    month: str
    physical_rows: int
    eligible_rows: int
    excluded_rows: Mapping[str, int]
    physical_bytes: int
    physical_digest: str
    source_parts: tuple[ObjectReceipt, ...]
    base_by_day: Mapping[date, pa.Table]


class ImmutableS3:
    """Small conditional-write S3 surface scoped to the configured warehouse prefix."""

    def __init__(self, credentials: ObjectStoreCredentials, object_store_prefix: str) -> None:
        self.bucket = credentials.bucket
        self.object_store_prefix = object_store_prefix.strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(retries={"max_attempts": 12, "mode": "adaptive"}),
        )

    def _key(self, relative: str) -> str:
        clean = relative.strip("/")
        return f"{self.object_store_prefix}/{clean}" if self.object_store_prefix else clean

    def get(self, relative: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(relative))
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        payload = response["Body"].read()
        return payload if isinstance(payload, bytes) else bytes(payload)

    def list_keys(self, relative_prefix: str) -> list[tuple[str, int]]:
        full_prefix = self._key(relative_prefix)
        token: str | None = None
        found: list[tuple[str, int]] = []
        while True:
            request: dict[str, object] = {"Bucket": self.bucket, "Prefix": full_prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            for item in response.get("Contents", []):
                full_key = str(item["Key"])
                relative = full_key[len(self.object_store_prefix) + 1 :] if self.object_store_prefix else full_key
                found.append((relative, int(item["Size"])))
            token_value = response.get("NextContinuationToken")
            if not isinstance(token_value, str) or not token_value:
                break
            token = token_value
        return sorted(found)

    def put_immutable(self, relative: str, payload: bytes, *, content_type: str) -> ObjectReceipt:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._key(relative),
                Body=payload,
                ContentType=content_type,
                IfNoneMatch="*",
            )
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in PRECONDITION_CODES:
                raise
            existing = self.get(relative)
            if existing != payload:
                raise ImmutableObjectConflictError(
                    f"immutable key {relative!r} holds sha256={_sha256(existing or b'')}, "
                    f"attempted sha256={_sha256(payload)}"
                ) from error
        durable = self.get(relative)
        if durable != payload:
            raise BreakdownError(f"durable read-back failed for {relative!r}")
        return ObjectReceipt(
            key=relative,
            row_count=0,
            byte_count=len(payload),
            sha256=_sha256(payload),
            kind="metadata",
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _json_object(payload: bytes, *, key: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BreakdownError(f"{key!r} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise BreakdownError(f"{key!r} must contain a JSON object")
    return value


def _required_object(store: ImmutableS3, key: str) -> bytes:
    payload = store.get(key)
    if payload is None:
        raise BreakdownError(f"required immutable object {key!r} is missing")
    return payload


def _row_set_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    identities = sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows)
    for row_id, row_hash in identities:
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def _precedence_lineage_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        row_hash = str(row["canonical_row_sha256"])
        try:
            digest.update(bytes.fromhex(row_hash))
        except ValueError as error:
            raise BreakdownError(f"source row {row.get('id')} has a non-hex canonical hash") from error
    return digest.hexdigest()


def _base_row_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    identities = sorted(
        (
            str(row["observed_day"]),
            str(row["cell_id"]),
            int(row["selected_source_row_id"]),
            str(row["selected_source_row_sha256"]),
            str(row["input_source_row_digest"]),
        )
        for row in rows
    )
    for identity in identities:
        digest.update(("|".join(map(str, identity)) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _serialize(table: pa.Table) -> bytes:
    schema = ACTIVE_SCHEMA.arrow_schema
    conformed = table.select(schema.names).cast(schema).replace_schema_metadata(schema.metadata)
    conformed = conformed.sort_by([(column, "ascending") for column in ACTIVE_SCHEMA.sort_columns])
    buffer = io.BytesIO()
    pq.write_table(
        conformed,
        buffer,
        compression=ACTIVE_SCHEMA.compression,
        write_statistics=True,
        row_group_size=64_000,
    )
    return buffer.getvalue()


def _load_parquet(payload: bytes, *, key: str) -> pa.Table:
    try:
        return pq.read_table(io.BytesIO(payload))
    except Exception as error:
        raise BreakdownError(f"{key!r} is not readable Parquet: {type(error).__name__}: {error}") from error


def _load_source_contract(store: ImmutableS3) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_payload = _required_object(store, SOURCE_MANIFEST_KEY)
    actual_manifest_sha256 = _sha256(manifest_payload)
    if actual_manifest_sha256 != SOURCE_MANIFEST_SHA256:
        raise BreakdownError(f"source manifest sha256 is {actual_manifest_sha256}, expected {SOURCE_MANIFEST_SHA256}")
    manifest = _json_object(manifest_payload, key=SOURCE_MANIFEST_KEY)
    completion = _json_object(_required_object(store, SOURCE_COMPLETE_KEY), key=SOURCE_COMPLETE_KEY)
    expected_manifest = {
        "contract_version": "agri.signal_observation.raw-canonical.v1",
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "row_count": 46_146_568,
        "partition_count": 8_364,
        "batch_count": 424,
        "rejected_rows": 0,
        "observation_day_min": SNAPSHOT_FIRST_DAY.isoformat(),
        "observation_day_max": SNAPSHOT_LAST_DAY.isoformat(),
    }
    drift = {key: (manifest.get(key), value) for key, value in expected_manifest.items() if manifest.get(key) != value}
    if drift:
        raise BreakdownError(f"pinned source manifest contract drifted: {drift}")
    if completion.get("manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise BreakdownError("source _COMPLETE does not pin the required manifest sha256")
    if (
        completion.get("row_count") != manifest["row_count"]
        or completion.get("partition_count") != manifest["partition_count"]
    ):
        raise BreakdownError("source manifest and _COMPLETE counts disagree")
    return manifest, completion


def _load_dimensions(
    store: ImmutableS3, manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    dimensions = manifest.get("dimension_objects")
    if not isinstance(dimensions, Mapping):
        raise BreakdownError("source manifest has no dimension object inventory")

    def table(name: str) -> pa.Table:
        metadata = dimensions.get(name)
        if not isinstance(metadata, Mapping):
            raise BreakdownError(f"source manifest has no {name} dimension")
        key = str(metadata["key"])
        expected_key = f"{SOURCE_ROOT}/_dimensions/{name}.parquet"
        if key != expected_key:
            raise BreakdownError(f"source manifest dimension {name!r} escapes the pinned snapshot: {key!r}")
        payload = _required_object(store, key)
        if len(payload) != int(metadata["byte_count"]) or _sha256(payload) != metadata["sha256"]:
            raise BreakdownError(f"source dimension {name!r} failed checksum reconciliation")
        loaded = _load_parquet(payload, key=key)
        if loaded.num_rows != int(metadata["row_count"]):
            raise BreakdownError(f"source dimension {name!r} row count drifted")
        return loaded

    data_sources = table("data_source").to_pylist()
    selected_sources = [row for row in data_sources if row["key"] == SOURCE_KEY]
    if len(selected_sources) != 1:
        raise BreakdownError(f"source snapshot contains {len(selected_sources)} {SOURCE_KEY!r} data-source rows")
    releases = table("source_release").to_pylist()
    by_release = {str(row["id"]): row for row in releases}
    if len(by_release) != len(releases):
        raise BreakdownError("source-release dimension contains duplicate ids")
    source = selected_sources[0]
    if source.get("allowed_client_exposure") is not False:
        raise BreakdownError("pinned ERA5-Land source exposure policy is no longer false")
    return source, by_release


def _verified_ledger(
    store: ImmutableS3,
    summary: Mapping[str, Any],
) -> tuple[str, bytes, dict[str, Any]]:
    month = str(summary["observation_month"])
    batch_index = int(summary["cell_batch_index"])
    ledger_key = f"{SOURCE_ROOT}/_ledger/month={month}/cell-batch={batch_index:05d}.json"
    payload = _required_object(store, ledger_key)
    ledger = _json_object(payload, key=ledger_key)
    identity = {
        "contract_version": "agri.signal_observation.raw-canonical.v1",
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "observation_month": month,
        "cell_batch_index": batch_index,
    }
    summary_fields = ("row_count", "part_count", "byte_count", "source_row_digest")
    drift = {
        key: (ledger.get(key), value)
        for key, value in {**identity, **{field: summary[field] for field in summary_fields}}.items()
        if ledger.get(key) != value
    }
    if drift:
        raise BreakdownError(f"source ledger {ledger_key!r} is not bound to the pinned manifest summary: {drift}")
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise BreakdownError(f"source ledger {ledger_key!r} has no parts list")
    if len(parts) != int(ledger["part_count"]):
        raise BreakdownError(f"source ledger {ledger_key!r} part count is internally inconsistent")
    if sum(int(part["row_count"]) for part in parts) != int(ledger["row_count"]):
        raise BreakdownError(f"source ledger {ledger_key!r} row count is internally inconsistent")
    if sum(int(part["byte_count"]) for part in parts) != int(ledger["byte_count"]):
        raise BreakdownError(f"source ledger {ledger_key!r} byte count is internally inconsistent")
    if int(ledger.get("rejected_rows", -1)) != 0:
        raise BreakdownError(f"source ledger {ledger_key!r} contains rejected rows")
    return ledger_key, payload, ledger


def _source_part_for_ledger(ledger: Mapping[str, Any], *, year: int, month: int) -> Mapping[str, Any]:
    expected = (
        f"source={SOURCE_KEY}/product={SOURCE_PARAMETER}/support={SUPPORT_KEY}/year={year:04d}/month={month:02d}/"
    )
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise BreakdownError("source ledger has no parts list")
    selected = [
        part for part in parts if isinstance(part, Mapping) and str(part.get("relative_path", "")).startswith(expected)
    ]
    if len(selected) != 1:
        raise BreakdownError(
            f"source ledger contains {len(selected)} soil moisture parts under {expected!r}, expected one"
        )
    part = selected[0]
    relative_path = str(part["relative_path"])
    key = str(part["key"])
    expected_key = f"{SOURCE_ROOT}/{relative_path}"
    if key != expected_key or not key.startswith(SOURCE_PART_PREFIX):
        raise BreakdownError(f"source ledger soil moisture part escapes the pinned prefix: {key!r}")
    return part


def _classify_raw_row(row: Mapping[str, Any], *, month: str) -> str | None:
    partition_contract = {
        "data_source_key": SOURCE_KEY,
        "product_key": SOURCE_PARAMETER,
        "support_key": SUPPORT_KEY,
    }
    drift = {key: (row.get(key), value) for key, value in partition_contract.items() if row.get(key) != value}
    if drift:
        raise BreakdownError(f"raw soil moisture partition row {row.get('id')} in {month} violates its path: {drift}")
    observed_day = row.get("observation_day")
    observed_at = row.get("observed_at")
    if (
        not isinstance(observed_day, date)
        or not isinstance(observed_at, datetime)
        or observed_at.date() != observed_day
    ):
        raise BreakdownError(f"raw soil moisture row {row.get('id')} has inconsistent observation day")
    row_hash = row.get("canonical_row_sha256")
    if not isinstance(row_hash, str) or len(row_hash) != 64:
        raise BreakdownError(f"raw soil moisture row {row.get('id')} has no canonical SHA-256")
    if row.get("signal_name") != SIGNAL_NAME:
        return "signal_name_drift"
    if row.get("source_parameter") != SOURCE_PARAMETER:
        return "source_parameter_drift"
    if row.get("normalized_unit") is None:
        return "normalized_unit_null"
    if row.get("normalized_unit") != NORMALIZED_UNIT:
        return "normalized_unit_not_cubic_metre_per_cubic_metre"
    if row.get("is_observed") is not True:
        return "not_observed"
    if row.get("quality_flag") != "accepted":
        return "quality_not_accepted"
    value = row.get("normalized_value")
    if value is None:
        return "normalized_value_null"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BreakdownError(f"raw soil moisture row {row.get('id')} has a non-numeric normalized value")
    return None


def _deduplicate_month(
    rows: list[dict[str, Any]],
    *,
    data_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
) -> Mapping[date, pa.Table]:
    groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                row["support_key"],
                row["signal_name"],
                row["normalized_unit"],
                row["cell_id"],
                row["observation_day"],
            )
        ].append(row)

    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for group_rows in groups.values():
        release_rows: list[tuple[datetime, int, dict[str, Any], Mapping[str, Any]]] = []
        for row in group_rows:
            release_id = str(row["source_release_id"])
            release = releases.get(release_id)
            if release is None:
                raise BreakdownError(f"source release {release_id!r} is absent from the frozen dimension")
            if str(release["data_source_id"]) != str(data_source["id"]):
                raise BreakdownError(f"source release {release_id!r} does not belong to {SOURCE_KEY}")
            retrieved_at = release.get("retrieved_at")
            if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
                raise BreakdownError(f"source release {release_id!r} has no zoned retrieved_at")
            release_rows.append((retrieved_at, int(row["id"]), row, release))
        release_rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _, _, selected, selected_release = release_rows[0]
        lineage = [item[2] for item in release_rows]
        observed_day = selected["observation_day"]
        if not isinstance(observed_day, date):
            raise BreakdownError("selected soil moisture row has no date-valued observation_day")
        input_digest = _precedence_lineage_digest(lineage)
        output = {
            "support_key": selected["support_key"],
            "signal_name": selected["signal_name"],
            "normalized_unit": selected["normalized_unit"],
            "cell_id": selected["cell_id"],
            "observed_day": observed_day,
            "normalized_value": selected["normalized_value"],
            "observation_count": len(lineage),
            "newest_observed_at": max(row["observed_at"] for row in lineage),
            "coverage_fraction": selected["coverage_fraction"],
            "allowed_client_exposure": data_source["allowed_client_exposure"],
            "cell_longitude": selected["cell_centroid_longitude"],
            "cell_latitude": selected["cell_centroid_latitude"],
            "source_key": SOURCE_KEY,
            "source_parameter": SOURCE_PARAMETER,
            "source_snapshot_id": SOURCE_SNAPSHOT_ID,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
            "precedence_contract": PRECEDENCE_CONTRACT,
            "selected_source_row_id": selected["id"],
            "selected_source_row_sha256": selected["canonical_row_sha256"],
            "selected_source_release_id": selected["source_release_id"],
            "selected_source_release_retrieved_at": selected_release["retrieved_at"],
            "selected_source_release_payload_checksum": selected_release["payload_checksum"],
            "selected_source_part_key": selected["_source_part_key"],
            "selected_source_part_sha256": selected["_source_part_sha256"],
            "selected_source_row_ordinal": selected["_source_row_ordinal"],
            "input_source_row_count": len(lineage),
            "input_source_row_digest": input_digest,
            "input_source_row_ids": [row["id"] for row in lineage],
            "input_source_row_sha256s": [row["canonical_row_sha256"] for row in lineage],
            "input_source_release_ids": [row["source_release_id"] for row in lineage],
            "input_source_part_keys": [row["_source_part_key"] for row in lineage],
            "input_source_part_sha256s": [row["_source_part_sha256"] for row in lineage],
            "input_source_row_ordinals": [row["_source_row_ordinal"] for row in lineage],
        }
        by_day[observed_day].append(output)

    schema = ACTIVE_SCHEMA.arrow_schema
    tables: dict[date, pa.Table] = {}
    for observed_day, day_rows in by_day.items():
        if len(day_rows) != EXPECTED_CELLS_PER_DAY:
            raise BreakdownError(
                f"deduplicated soil moisture {observed_day} has {len(day_rows)} cells, "
                f"expected {EXPECTED_CELLS_PER_DAY}"
            )
        table = pa.Table.from_pylist(day_rows, schema=schema)
        if table.num_rows != EXPECTED_CELLS_PER_DAY:
            raise BreakdownError(f"Arrow conversion changed soil moisture row count on {observed_day}")
        tables[observed_day] = table
    return tables


def _load_source_month(
    store: ImmutableS3,
    *,
    month: str,
    ledger_summaries: Sequence[Mapping[str, Any]],
    data_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
) -> SourceMonth:
    year, month_number = map(int, month.split("-"))
    all_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    excluded_rows: dict[str, int] = defaultdict(int)
    receipts: list[ObjectReceipt] = []
    seen_ids: set[int] = set()
    for summary in ledger_summaries:
        batch_index = int(summary["cell_batch_index"])
        ledger_key, _, ledger = _verified_ledger(store, summary)
        part = _source_part_for_ledger(ledger, year=year, month=month_number)
        key = str(part["key"])
        payload = _required_object(store, key)
        if len(payload) != int(part["byte_count"]) or _sha256(payload) != part["sha256"]:
            raise BreakdownError(f"source soil moisture part {key!r} failed byte reconciliation")
        table = _load_parquet(payload, key=key)
        if not RAW_REQUIRED_COLUMNS.issubset(table.column_names):
            missing = sorted(RAW_REQUIRED_COLUMNS.difference(table.column_names))
            raise BreakdownError(f"source soil moisture part {key!r} omits {missing}")
        rows = table.to_pylist()
        if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
            raise BreakdownError(f"source soil moisture part {key!r} failed row-digest reconciliation")
        part_sha256 = str(part["sha256"])
        for row_ordinal, row in enumerate(rows):
            exclusion = _classify_raw_row(row, month=month)
            row_id = int(row["id"])
            if row_id in seen_ids:
                raise BreakdownError(f"physical soil moisture row id {row_id} repeats within {month}")
            seen_ids.add(row_id)
            row["_source_part_key"] = key
            row["_source_part_sha256"] = part_sha256
            row["_source_row_ordinal"] = row_ordinal
            if exclusion is None:
                eligible_rows.append(row)
            else:
                excluded_rows[exclusion] += 1
        all_rows.extend(rows)
        receipts.append(
            ObjectReceipt(
                key=key,
                row_count=len(rows),
                byte_count=len(payload),
                sha256=_sha256(payload),
                kind="source-part",
            )
        )
    base_by_day = _deduplicate_month(eligible_rows, data_source=data_source, releases=releases)
    return SourceMonth(
        month=month,
        physical_rows=len(all_rows),
        eligible_rows=len(eligible_rows),
        excluded_rows=dict(sorted(excluded_rows.items())),
        physical_bytes=sum(receipt.byte_count for receipt in receipts),
        physical_digest=_row_set_digest(all_rows),
        source_parts=tuple(receipts),
        base_by_day=base_by_day,
    )


def _write_payload(
    store: ImmutableS3,
    *,
    key: str,
    payload: bytes,
    row_count: int,
    kind: str,
    zoom: int | None,
    content_type: str,
) -> ObjectReceipt:
    stored = store.put_immutable(key, payload, content_type=content_type)
    return ObjectReceipt(
        key=key,
        row_count=row_count,
        byte_count=stored.byte_count,
        sha256=stored.sha256,
        kind=kind,
        zoom=zoom,
    )


def _write_day(
    store: ImmutableS3,
    *,
    observed_day: date,
    base: pa.Table,
    run_id: str,
    completed_at: datetime,
) -> dict[str, Any]:
    if base.num_rows != EXPECTED_CELLS_PER_DAY:
        raise BreakdownError(f"base soil moisture day {observed_day} has {base.num_rows} rows")
    for row in base.to_pylist():
        count = int(row["input_source_row_count"])
        aligned = (
            row["input_source_row_ids"],
            row["input_source_row_sha256s"],
            row["input_source_release_ids"],
            row["input_source_part_keys"],
            row["input_source_part_sha256s"],
            row["input_source_row_ordinals"],
        )
        if any(values is None or len(values) != count or any(value is None for value in values) for values in aligned):
            raise BreakdownError(f"base soil moisture lineage is not aligned for {observed_day}/{row['cell_id']}")
        if (
            row["selected_source_row_id"] != aligned[0][0]
            or row["selected_source_row_sha256"] != aligned[1][0]
            or row["selected_source_release_id"] != aligned[2][0]
            or row["selected_source_part_key"] != aligned[3][0]
            or row["selected_source_part_sha256"] != aligned[4][0]
            or row["selected_source_row_ordinal"] != aligned[5][0]
        ):
            raise BreakdownError(
                f"base soil moisture winner is not lineage element zero for {observed_day}/{row['cell_id']}"
            )
    objects: list[ObjectReceipt] = []
    base_payload = _serialize(base)
    objects.append(
        _write_payload(
            store,
            key=partition_path(ACTIVE_STREAM, "observed", 13, observed_day),
            payload=base_payload,
            row_count=base.num_rows,
            kind="part",
            zoom=13,
            content_type=PARQUET_CONTENT_TYPE,
        )
    )
    source_frame = pl.from_arrow(base)
    tier_rows: dict[str, int] = {"13": base.num_rows}
    for zoom in (9, 5, 0):
        derived = derive_tier(source_frame, stream=ACTIVE_STREAM, tier=zoom)
        if derived.height <= 0:
            raise BreakdownError(f"soil moisture {observed_day} z{zoom} derived to no rows")
        derived_table = derived.to_arrow()
        payload = _serialize(derived_table)
        objects.append(
            _write_payload(
                store,
                key=partition_path(ACTIVE_STREAM, "observed", zoom, observed_day),
                payload=payload,
                row_count=derived.height,
                kind="part",
                zoom=zoom,
                content_type=PARQUET_CONTENT_TYPE,
            )
        )
        marker = PartitionCompletion(
            part_count=1,
            row_count=derived.height,
            completed_at=completed_at,
            run_id=run_id,
        ).to_json_bytes()
        objects.append(
            _write_payload(
                store,
                key=completion_marker_path(ACTIVE_STREAM, "observed", zoom, observed_day),
                payload=marker,
                row_count=derived.height,
                kind="completion",
                zoom=zoom,
                content_type=JSON_CONTENT_TYPE,
            )
        )
        tier_rows[str(zoom)] = derived.height
    base_marker = PartitionCompletion(
        part_count=1,
        row_count=base.num_rows,
        completed_at=completed_at,
        run_id=run_id,
    ).to_json_bytes()
    objects.append(
        _write_payload(
            store,
            key=completion_marker_path(ACTIVE_STREAM, "observed", 13, observed_day),
            payload=base_marker,
            row_count=base.num_rows,
            kind="completion",
            zoom=13,
            content_type=JSON_CONTENT_TYPE,
        )
    )
    rows = base.to_pylist()
    base_receipt = next(receipt for receipt in objects if receipt.kind == "part" and receipt.zoom == 13)
    return {
        "day": observed_day.isoformat(),
        "physical_input_rows": sum(int(row["input_source_row_count"]) for row in rows),
        "base_rows": base.num_rows,
        "base_digest": _base_row_digest(rows),
        "derived_from_base": {
            "key": base_receipt.key,
            "sha256": base_receipt.sha256,
            "row_count": base_receipt.row_count,
        },
        "tier_rows": tier_rows,
        "objects": [receipt.to_json() for receipt in objects],
    }


def _checkpoint_key(month: str) -> str:
    return f"{DESTINATION_METADATA_ROOT}/_checkpoints/month={month}.json"


def _verify_receipt(store: ImmutableS3, receipt: Mapping[str, Any]) -> None:
    key = str(receipt["key"])
    payload = _required_object(store, key)
    if len(payload) != int(receipt["byte_count"]) or _sha256(payload) != receipt["sha256"]:
        raise BreakdownError(f"destination object {key!r} failed checkpoint checksum verification")
    if receipt.get("kind") == "part":
        table = _load_parquet(payload, key=key)
        if table.num_rows != int(receipt["row_count"]):
            raise BreakdownError(f"destination Parquet object {key!r} row count drifted")
        schema = ACTIVE_SCHEMA.arrow_schema
        if not table.schema.equals(schema, check_metadata=False):
            raise BreakdownError(f"destination Parquet object {key!r} has the wrong schema")
    elif receipt.get("kind") == "completion":
        marker = PartitionCompletion.from_json_bytes(payload)
        if marker.row_count != int(receipt["row_count"]):
            raise BreakdownError(f"destination completion marker {key!r} row count drifted")


def _verify_checkpoint(store: ImmutableS3, checkpoint: Mapping[str, Any], *, month: str) -> None:
    expected = {
        "contract_version": CONTRACT_VERSION,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": ACTIVE_STREAM,
        "month": month,
        "precedence_contract": PRECEDENCE_CONTRACT,
    }
    drift = {key: (checkpoint.get(key), value) for key, value in expected.items() if checkpoint.get(key) != value}
    if drift:
        raise BreakdownError(f"destination checkpoint {month} drifted: {drift}")
    days = checkpoint.get("days")
    if not isinstance(days, list):
        raise BreakdownError(f"destination checkpoint {month} has no day list")
    physical = int(checkpoint.get("physical_input_rows", -1))
    eligible = int(checkpoint.get("eligible_input_rows", -1))
    excluded = int(checkpoint.get("excluded_input_rows", -1))
    if physical != eligible + excluded:
        raise BreakdownError(f"destination checkpoint {month} does not account for every physical row")
    exclusion_counts = checkpoint.get("exclusion_counts")
    if not isinstance(exclusion_counts, Mapping) or sum(int(value) for value in exclusion_counts.values()) != excluded:
        raise BreakdownError(f"destination checkpoint {month} has inconsistent exclusion counts")
    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
        futures = []
        for day in days:
            if not isinstance(day, Mapping) or not isinstance(day.get("objects"), list):
                raise BreakdownError(f"destination checkpoint {month} contains an invalid day receipt")
            futures.extend(executor.submit(_verify_receipt, store, receipt) for receipt in day["objects"])
        for future in as_completed(futures):
            future.result()


def _write_month(
    store: ImmutableS3,
    *,
    month: str,
    ledger_summaries: Sequence[Mapping[str, Any]],
    data_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
    run_id: str,
    completed_at: datetime,
    workers: int,
) -> tuple[dict[str, Any], str]:
    checkpoint_key = _checkpoint_key(month)
    existing = store.get(checkpoint_key)
    if existing is not None:
        checkpoint = _json_object(existing, key=checkpoint_key)
        _verify_checkpoint(store, checkpoint, month=month)
        return checkpoint, _sha256(existing)

    source = _load_source_month(
        store,
        month=month,
        ledger_summaries=ledger_summaries,
        data_source=data_source,
        releases=releases,
    )
    day_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = {
            executor.submit(
                _write_day,
                store,
                observed_day=observed_day,
                base=table,
                run_id=run_id,
                completed_at=completed_at,
            ): observed_day
            for observed_day, table in source.base_by_day.items()
        }
        for future in as_completed(pending):
            day_results.append(future.result())
    day_results.sort(key=lambda item: str(item["day"]))
    base_rows = sum(int(day["base_rows"]) for day in day_results)
    lineage_rows = sum(int(day["physical_input_rows"]) for day in day_results)
    if lineage_rows != source.eligible_rows:
        raise BreakdownError(
            f"{month} lineage covers {lineage_rows} eligible rows, source supplied {source.eligible_rows}"
        )
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": ACTIVE_STREAM,
        "month": month,
        "precedence_contract": PRECEDENCE_CONTRACT,
        "physical_input_rows": source.physical_rows,
        "eligible_input_rows": source.eligible_rows,
        "excluded_input_rows": sum(source.excluded_rows.values()),
        "exclusion_counts": source.excluded_rows,
        "physical_input_bytes": source.physical_bytes,
        "physical_input_digest": source.physical_digest,
        "source_parts": [receipt.to_json() for receipt in source.source_parts],
        "base_rows": base_rows,
        "duplicates_collapsed": source.eligible_rows - base_rows,
        "lineage_source_rows": lineage_rows,
        "days": day_results,
    }
    payload = _json_bytes(checkpoint)
    store.put_immutable(checkpoint_key, payload, content_type=JSON_CONTENT_TYPE)
    return checkpoint, _sha256(payload)


def _run_descriptor(store: ImmutableS3, *, completed_at: datetime) -> dict[str, Any]:
    schema = ACTIVE_SCHEMA.arrow_schema
    descriptor = {
        "contract_version": CONTRACT_VERSION,
        "lane": ACTIVE_STREAM,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_key": SOURCE_MANIFEST_KEY,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "precedence_contract": PRECEDENCE_CONTRACT,
        "required_tiers": list(ZOOM_TIERS),
        "started_at": completed_at.astimezone(UTC).isoformat(),
        "schema": [{"name": field.name, "type": str(field.type), "nullable": field.nullable} for field in schema],
    }
    payload = _json_bytes(descriptor)
    existing = store.get(DESTINATION_RUN_KEY)
    if existing is not None:
        if existing != payload:
            raise ImmutableObjectConflictError(f"run descriptor {DESTINATION_RUN_KEY!r} conflicts")
        return _json_object(existing, key=DESTINATION_RUN_KEY)
    store.put_immutable(DESTINATION_RUN_KEY, payload, content_type=JSON_CONTENT_TYPE)
    return descriptor


def _month_ledgers(manifest: Mapping[str, Any]) -> dict[str, tuple[Mapping[str, Any], ...]]:
    rows = manifest.get("month_ledgers")
    if not isinstance(rows, list) or len(rows) != EXPECTED_SOURCE_PARTS:
        raise BreakdownError("source manifest does not describe the expected 424 month/cell batches")
    by_month: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    seen: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise BreakdownError("source manifest contains a non-object month ledger summary")
        required = {
            "observation_month",
            "cell_batch_index",
            "row_count",
            "part_count",
            "byte_count",
            "source_row_digest",
        }
        if not required.issubset(row):
            raise BreakdownError(f"source manifest ledger summary omits {sorted(required.difference(row))}")
        month = str(row["observation_month"])
        batch_index = int(row["cell_batch_index"])
        identity = (month, batch_index)
        if identity in seen:
            raise BreakdownError(f"source manifest repeats ledger summary {identity}")
        seen.add(identity)
        by_month[month].append(row)
    for month, summaries in by_month.items():
        indices = sorted(int(summary["cell_batch_index"]) for summary in summaries)
        if sorted(indices) != list(range(len(indices))):
            raise BreakdownError(f"source manifest cell-batch indices are not contiguous for {month}")
    return {
        month: tuple(sorted(summaries, key=lambda summary: int(summary["cell_batch_index"])))
        for month, summaries in sorted(by_month.items())
    }


def _destination_receipt_keys(checkpoints: Sequence[Mapping[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for checkpoint in checkpoints:
        for day in checkpoint["days"]:
            for receipt in day["objects"]:
                key = str(receipt["key"])
                if key in keys:
                    raise BreakdownError(f"destination object {key!r} appears in more than one checkpoint")
                keys.add(key)
    return keys


def _audit_source_chain(
    store: ImmutableS3,
    *,
    month_ledgers: Mapping[str, Sequence[Mapping[str, Any]]],
    checkpoints: Sequence[Mapping[str, Any]],
    source_inventory: Sequence[tuple[str, int]],
) -> dict[str, Any]:
    checkpoint_parts: dict[str, Mapping[str, Any]] = {}
    for checkpoint in checkpoints:
        for receipt in checkpoint["source_parts"]:
            key = str(receipt["key"])
            if key in checkpoint_parts:
                raise BreakdownError(f"checkpoint source part {key!r} is repeated")
            checkpoint_parts[key] = receipt

    ledger_receipts: list[dict[str, Any]] = []
    audited_parts: set[str] = set()
    physical_rows = 0
    eligible_rows = 0
    exclusions: dict[str, int] = defaultdict(int)
    for month, summaries in month_ledgers.items():
        year, month_number = map(int, month.split("-"))
        for summary in summaries:
            ledger_key, ledger_payload, ledger = _verified_ledger(store, summary)
            part = _source_part_for_ledger(ledger, year=year, month=month_number)
            part_key = str(part["key"])
            if part_key in audited_parts:
                raise BreakdownError(f"source soil moisture part {part_key!r} is repeated across ledgers")
            audited_parts.add(part_key)
            payload = _required_object(store, part_key)
            payload_sha256 = _sha256(payload)
            if len(payload) != int(part["byte_count"]) or payload_sha256 != part["sha256"]:
                raise BreakdownError(f"source soil moisture part {part_key!r} failed ledger byte reconciliation")
            table = _load_parquet(payload, key=part_key)
            if not RAW_REQUIRED_COLUMNS.issubset(table.column_names):
                missing = sorted(RAW_REQUIRED_COLUMNS.difference(table.column_names))
                raise BreakdownError(f"source soil moisture part {part_key!r} omits {missing}")
            rows = table.to_pylist()
            if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
                raise BreakdownError(f"source soil moisture part {part_key!r} failed ledger row reconciliation")
            for row in rows:
                exclusion = _classify_raw_row(row, month=month)
                if exclusion is None:
                    eligible_rows += 1
                else:
                    exclusions[exclusion] += 1
            physical_rows += len(rows)
            checkpoint_receipt = checkpoint_parts.get(part_key)
            expected_receipt = {
                "row_count": len(rows),
                "byte_count": len(payload),
                "sha256": payload_sha256,
            }
            if checkpoint_receipt is None or any(
                checkpoint_receipt.get(field) != value for field, value in expected_receipt.items()
            ):
                raise BreakdownError(f"destination checkpoint does not bind audited source part {part_key!r}")
            ledger_receipts.append(
                {
                    "key": ledger_key,
                    "byte_count": len(ledger_payload),
                    "sha256": _sha256(ledger_payload),
                    "observation_month": month,
                    "cell_batch_index": int(summary["cell_batch_index"]),
                    "manifest_row_count": int(summary["row_count"]),
                    "manifest_part_count": int(summary["part_count"]),
                    "manifest_byte_count": int(summary["byte_count"]),
                    "manifest_source_row_digest": str(summary["source_row_digest"]),
                    "source_part_key": part_key,
                    "source_part_sha256": payload_sha256,
                    "source_part_row_count": len(rows),
                    "source_part_row_digest": str(part["row_digest"]),
                }
            )

    listed_parts = {key for key, _ in source_inventory}
    if audited_parts != listed_parts or audited_parts != set(checkpoint_parts):
        raise BreakdownError("audited source parts, prefix inventory, and destination checkpoint receipts differ")
    excluded_rows = sum(exclusions.values())
    if (
        len(ledger_receipts) != EXPECTED_SOURCE_PARTS
        or physical_rows != EXPECTED_PHYSICAL_ROWS
        or eligible_rows != EXPECTED_ELIGIBLE_ROWS
        or excluded_rows != EXPECTED_PHYSICAL_ROWS - EXPECTED_ELIGIBLE_ROWS
    ):
        raise BreakdownError(
            "source-chain audit totals drifted: "
            f"ledgers={len(ledger_receipts)}, physical={physical_rows}, eligible={eligible_rows}, "
            f"excluded={excluded_rows}"
        )
    return {
        "contract_version": f"{ACTIVE_STREAM}.source-chain-audit.v1",
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_key": SOURCE_MANIFEST_KEY,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": ACTIVE_STREAM,
        "ledger_binding": (
            "each constructed ledger identity and aggregate summary equals its entry in the pinned source manifest; "
            "the audit additionally records the current immutable ledger byte SHA-256"
        ),
        "ledger_count": len(ledger_receipts),
        "physical_source_rows": physical_rows,
        "eligible_source_rows": eligible_rows,
        "excluded_source_rows": excluded_rows,
        "exclusion_counts": dict(sorted(exclusions.items())),
        "source_part_count": len(audited_parts),
        "ledgers": ledger_receipts,
    }


def _publish_source_audit(
    store: ImmutableS3,
    *,
    audit: Mapping[str, Any],
    destination_manifest_sha256: str,
) -> str:
    bound = {
        **audit,
        "destination_manifest_key": DESTINATION_MANIFEST_KEY,
        "destination_manifest_sha256": destination_manifest_sha256,
    }
    payload = _json_bytes(bound)
    store.put_immutable(DESTINATION_SOURCE_AUDIT_KEY, payload, content_type=JSON_CONTENT_TYPE)
    audit_sha256 = _sha256(payload)
    completion = {
        "contract_version": f"{ACTIVE_STREAM}.source-chain-audit.v1",
        "lane": ACTIVE_STREAM,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "destination_manifest_sha256": destination_manifest_sha256,
        "audit_key": DESTINATION_SOURCE_AUDIT_KEY,
        "audit_sha256": audit_sha256,
        "ledger_count": int(audit["ledger_count"]),
        "source_part_count": int(audit["source_part_count"]),
        "physical_source_rows": int(audit["physical_source_rows"]),
    }
    store.put_immutable(
        DESTINATION_AUDIT_COMPLETE_KEY,
        _json_bytes(completion),
        content_type=JSON_CONTENT_TYPE,
    )
    return audit_sha256


def _finalize(
    store: ImmutableS3,
    *,
    source_manifest: Mapping[str, Any],
    source_completion: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
    checkpoint_hashes: Sequence[str],
    source_inventory: Sequence[tuple[str, int]],
    run_descriptor: Mapping[str, Any],
    source_chain_audit: Mapping[str, Any],
) -> dict[str, Any]:
    physical_rows = sum(int(checkpoint["physical_input_rows"]) for checkpoint in checkpoints)
    eligible_rows = sum(int(checkpoint["eligible_input_rows"]) for checkpoint in checkpoints)
    excluded_rows = sum(int(checkpoint["excluded_input_rows"]) for checkpoint in checkpoints)
    base_rows = sum(int(checkpoint["base_rows"]) for checkpoint in checkpoints)
    duplicates = sum(int(checkpoint["duplicates_collapsed"]) for checkpoint in checkpoints)
    lineage_rows = sum(int(checkpoint["lineage_source_rows"]) for checkpoint in checkpoints)
    days = [day for checkpoint in checkpoints for day in checkpoint["days"]]
    day_names = sorted(str(day["day"]) for day in days)
    source_part_keys = {str(receipt["key"]) for checkpoint in checkpoints for receipt in checkpoint["source_parts"]}
    listed_source_keys = {key for key, _ in source_inventory}
    if source_part_keys != listed_source_keys:
        missing = sorted(listed_source_keys - source_part_keys)
        unexpected = sorted(source_part_keys - listed_source_keys)
        raise BreakdownError(
            f"source soil moisture inventory reconciliation failed: missing={missing[:5]}, unexpected={unexpected[:5]}"
        )
    expected = {
        "physical_rows": (physical_rows, EXPECTED_PHYSICAL_ROWS),
        "eligible_rows": (eligible_rows, EXPECTED_ELIGIBLE_ROWS),
        "excluded_rows": (excluded_rows, EXPECTED_PHYSICAL_ROWS - EXPECTED_ELIGIBLE_ROWS),
        "base_rows": (base_rows, EXPECTED_BASE_ROWS),
        "duplicates": (duplicates, EXPECTED_DUPLICATES_COLLAPSED),
        "lineage_rows": (lineage_rows, EXPECTED_ELIGIBLE_ROWS),
        "days": (len(days), EXPECTED_DAYS),
        "source_parts": (len(source_part_keys), EXPECTED_SOURCE_PARTS),
        "first_day": (day_names[0] if day_names else None, EXPECTED_FIRST_DAY.isoformat()),
        "last_day": (day_names[-1] if day_names else None, EXPECTED_LAST_DAY.isoformat()),
    }
    drift = {name: value for name, value in expected.items() if value[0] != value[1]}
    if drift:
        raise BreakdownError(f"soil moisture snapshot-to-lane reconciliation drifted: {drift}")
    if physical_rows != eligible_rows + excluded_rows:
        raise BreakdownError("soil moisture physical population does not equal eligible plus excluded rows")
    if len(set(day_names)) != EXPECTED_DAYS:
        raise BreakdownError("soil moisture checkpoint set contains duplicate days")

    tier_rows = {str(zoom): sum(int(day["tier_rows"][str(zoom)]) for day in days) for zoom in ZOOM_TIERS}
    destination_receipt_keys = _destination_receipt_keys(checkpoints)
    tier_part_counts = {
        str(zoom): sum(
            1
            for checkpoint in checkpoints
            for day in checkpoint["days"]
            for receipt in day["objects"]
            if receipt["kind"] == "part" and int(receipt["zoom"]) == zoom
        )
        for zoom in ZOOM_TIERS
    }
    tier_completion_counts = {
        str(zoom): sum(
            1
            for checkpoint in checkpoints
            for day in checkpoint["days"]
            for receipt in day["objects"]
            if receipt["kind"] == "completion" and int(receipt["zoom"]) == zoom
        )
        for zoom in ZOOM_TIERS
    }
    if any(count != EXPECTED_DAYS for count in tier_part_counts.values()) or any(
        count != EXPECTED_DAYS for count in tier_completion_counts.values()
    ):
        raise BreakdownError(
            f"required tier ladder is incomplete: parts={tier_part_counts}, completions={tier_completion_counts}"
        )

    checkpoint_summaries = [
        {
            "month": checkpoint["month"],
            "key": _checkpoint_key(str(checkpoint["month"])),
            "sha256": checksum,
            "physical_input_rows": checkpoint["physical_input_rows"],
            "eligible_input_rows": checkpoint["eligible_input_rows"],
            "excluded_input_rows": checkpoint["excluded_input_rows"],
            "exclusion_counts": checkpoint["exclusion_counts"],
            "base_rows": checkpoint["base_rows"],
            "duplicates_collapsed": checkpoint["duplicates_collapsed"],
            "day_count": len(checkpoint["days"]),
        }
        for checkpoint, checksum in zip(checkpoints, checkpoint_hashes, strict=True)
    ]
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "lane": ACTIVE_STREAM,
        "kind": "observed",
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_snapshot_prefix": f"{SOURCE_ROOT}/",
        "source_manifest_key": SOURCE_MANIFEST_KEY,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_complete_key": SOURCE_COMPLETE_KEY,
        "source_completed_at": source_completion["completed_at"],
        "precedence_contract": PRECEDENCE_CONTRACT,
        "precedence_order": ["source_release.retrieved_at DESC", "signal_observation.id DESC"],
        "source_filter": {
            "data_source_key": SOURCE_KEY,
            "product_key": SOURCE_PARAMETER,
            "source_parameter": SOURCE_PARAMETER,
            "support_key": SUPPORT_KEY,
            "signal_name": SIGNAL_NAME,
            "depth_band": ACTIVE_PRODUCT.depth_band,
            "normalized_unit": NORMALIZED_UNIT,
            "quality_flag": "accepted",
            "is_observed": True,
            "normalized_value": "non-null",
        },
        "physical_source_rows": physical_rows,
        "eligible_source_rows": eligible_rows,
        "excluded_source_rows": excluded_rows,
        "exclusion_counts": {
            reason: sum(int(checkpoint["exclusion_counts"].get(reason, 0)) for checkpoint in checkpoints)
            for reason in sorted({reason for checkpoint in checkpoints for reason in checkpoint["exclusion_counts"]})
        },
        "physical_source_parts": len(source_part_keys),
        "physical_source_bytes": sum(size for _, size in source_inventory),
        "selected_base_rows": base_rows,
        "duplicates_collapsed": duplicates,
        "lineage_source_rows": lineage_rows,
        "lineage_reconciliation": (
            "every eligible physical row is represented once in precedence-ordered base lineage arrays; "
            "every other physical row is assigned one exclusion reason"
        ),
        "first_day": EXPECTED_FIRST_DAY.isoformat(),
        "last_day": EXPECTED_LAST_DAY.isoformat(),
        "day_count": EXPECTED_DAYS,
        "cells_per_day": EXPECTED_CELLS_PER_DAY,
        "depth_band": ACTIVE_PRODUCT.depth_band,
        "required_tiers": list(ZOOM_TIERS),
        "tier_row_counts": tier_rows,
        "tier_part_counts": tier_part_counts,
        "tier_completion_counts": tier_completion_counts,
        "destination_data_object_count": len(destination_receipt_keys),
        "run_descriptor_key": DESTINATION_RUN_KEY,
        "run_started_at": run_descriptor["started_at"],
        "month_checkpoints": checkpoint_summaries,
        "source_snapshot_manifest_summary": {
            "row_count": source_manifest["row_count"],
            "partition_count": source_manifest["partition_count"],
            "batch_count": source_manifest["batch_count"],
            "rejected_rows": source_manifest["rejected_rows"],
        },
    }
    manifest_payload = _json_bytes(manifest)
    manifest_sha256 = _sha256(manifest_payload)
    source_audit_sha256 = _publish_source_audit(
        store,
        audit=source_chain_audit,
        destination_manifest_sha256=manifest_sha256,
    )
    store.put_immutable(DESTINATION_MANIFEST_KEY, manifest_payload, content_type=JSON_CONTENT_TYPE)
    completion = {
        "contract_version": CONTRACT_VERSION,
        "lane": ACTIVE_STREAM,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "manifest_key": DESTINATION_MANIFEST_KEY,
        "manifest_sha256": manifest_sha256,
        "physical_source_rows": physical_rows,
        "eligible_source_rows": eligible_rows,
        "excluded_source_rows": excluded_rows,
        "selected_base_rows": base_rows,
        "duplicates_collapsed": duplicates,
        "day_count": EXPECTED_DAYS,
        "required_tiers": list(ZOOM_TIERS),
        "completed_at": run_descriptor["started_at"],
    }
    store.put_immutable(DESTINATION_COMPLETE_KEY, _json_bytes(completion), content_type=JSON_CONTENT_TYPE)
    expected_inventory = {
        *destination_receipt_keys,
        DESTINATION_RUN_KEY,
        DESTINATION_MANIFEST_KEY,
        DESTINATION_COMPLETE_KEY,
        DESTINATION_SOURCE_AUDIT_KEY,
        DESTINATION_AUDIT_COMPLETE_KEY,
        *(_checkpoint_key(str(checkpoint["month"])) for checkpoint in checkpoints),
    }
    actual_inventory = {key for key, _ in store.list_keys(f"layer={ACTIVE_STREAM}/")}
    if actual_inventory != expected_inventory:
        missing = sorted(expected_inventory - actual_inventory)
        unexpected = sorted(actual_inventory - expected_inventory)
        raise BreakdownError(f"destination inventory is not exact: missing={missing[:5]}, unexpected={unexpected[:5]}")
    durable_manifest = _required_object(store, DESTINATION_MANIFEST_KEY)
    durable_complete = _json_object(_required_object(store, DESTINATION_COMPLETE_KEY), key=DESTINATION_COMPLETE_KEY)
    if _sha256(durable_manifest) != manifest_sha256 or durable_complete.get("manifest_sha256") != manifest_sha256:
        raise BreakdownError("destination manifest/_COMPLETE durable checksum reconciliation failed")
    return {
        "status": "complete",
        "lane": ACTIVE_STREAM,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "destination_manifest_key": DESTINATION_MANIFEST_KEY,
        "destination_manifest_sha256": manifest_sha256,
        "destination_complete_key": DESTINATION_COMPLETE_KEY,
        "source_audit_key": DESTINATION_SOURCE_AUDIT_KEY,
        "source_audit_sha256": source_audit_sha256,
        "source_audit_complete_key": DESTINATION_AUDIT_COMPLETE_KEY,
        "physical_source_rows": physical_rows,
        "eligible_source_rows": eligible_rows,
        "excluded_source_rows": excluded_rows,
        "selected_base_rows": base_rows,
        "duplicates_collapsed": duplicates,
        "days": EXPECTED_DAYS,
        "source_parts": len(source_part_keys),
        "tier_rows": tier_rows,
        "tier_parts": tier_part_counts,
        "tier_completions": tier_completion_counts,
        "destination_objects": len(actual_inventory),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--product", choices=tuple(PRODUCTS), required=True)
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= 24:
        parser.error("--workers must be between 1 and 24")
    return arguments


def main() -> int:
    arguments = _arguments()
    _activate(PRODUCTS[arguments.product])
    if not arguments.env_file.is_file():
        raise SystemExit(f"settings file does not exist: {arguments.env_file}")
    try:
        configured = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
        store = ImmutableS3(configured.require_object_store(), configured.object_store_prefix)
        source_manifest, source_completion = _load_source_contract(store)
        source_inventory = store.list_keys(SOURCE_PART_PREFIX)
        if (
            len(source_inventory) != EXPECTED_SOURCE_PARTS
            or sum(size for _, size in source_inventory) != ACTIVE_PRODUCT.expected_source_bytes
            or any(not key.endswith(".parquet") for key, _ in source_inventory)
        ):
            raise BreakdownError(
                f"source soil moisture prefix has {len(source_inventory)} objects and "
                f"{sum(size for _, size in source_inventory)} bytes; expected {EXPECTED_SOURCE_PARTS} parts and "
                f"{ACTIVE_PRODUCT.expected_source_bytes} bytes"
            )
        data_source, releases = _load_dimensions(store, source_manifest)
        completed_at = datetime.fromisoformat(str(source_completion["completed_at"]))
        if completed_at.tzinfo is None:
            raise BreakdownError("source completion timestamp is not timezone-aware")
        run_descriptor = _run_descriptor(store, completed_at=completed_at)
        run_id = f"{CONTRACT_VERSION}:{SOURCE_MANIFEST_SHA256}"
        month_ledgers = _month_ledgers(source_manifest)
        checkpoints: list[dict[str, Any]] = []
        checkpoint_hashes: list[str] = []
        for index, (month, ledger_summaries) in enumerate(month_ledgers.items(), start=1):
            checkpoint, checksum = _write_month(
                store,
                month=month,
                ledger_summaries=ledger_summaries,
                data_source=data_source,
                releases=releases,
                run_id=run_id,
                completed_at=completed_at,
                workers=arguments.workers,
            )
            checkpoints.append(checkpoint)
            checkpoint_hashes.append(checksum)
            print(
                f"checkpoint {index}/{len(month_ledgers)} month={month} "
                f"physical={checkpoint['physical_input_rows']} base={checkpoint['base_rows']} "
                f"duplicates={checkpoint['duplicates_collapsed']}",
                file=sys.stderr,
                flush=True,
            )
        source_chain_audit = _audit_source_chain(
            store,
            month_ledgers=month_ledgers,
            checkpoints=checkpoints,
            source_inventory=source_inventory,
        )
        print(
            f"source-chain audit ledgers={source_chain_audit['ledger_count']} "
            f"parts={source_chain_audit['source_part_count']} "
            f"physical={source_chain_audit['physical_source_rows']}",
            file=sys.stderr,
            flush=True,
        )
        report = _finalize(
            store,
            source_manifest=source_manifest,
            source_completion=source_completion,
            checkpoints=checkpoints,
            checkpoint_hashes=checkpoint_hashes,
            source_inventory=source_inventory,
            run_descriptor=run_descriptor,
            source_chain_audit=source_chain_audit,
        )
        print(json.dumps(report, sort_keys=True))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "lane": ACTIVE_STREAM,
                    "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                    "error": f"{type(error).__name__}: {error}",
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
