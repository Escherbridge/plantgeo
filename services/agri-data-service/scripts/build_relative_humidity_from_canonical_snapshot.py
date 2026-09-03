"""Build the dedicated relative-humidity lane from one pinned canonical signal snapshot."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

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
from agri_data_service.warehouse.schemas.climate_field_relative_humidity import (  # noqa: E402
    CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA,
    CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
)

CONTRACT_VERSION: Final = "climate-field-relative-humidity.snapshot-breakdown.v1"
SOURCE_SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
SOURCE_ROOT: Final = f"raw-canonical/signal-observation/snapshot={SOURCE_SNAPSHOT_ID}"
SOURCE_MANIFEST_KEY: Final = f"{SOURCE_ROOT}/manifest.json"
SOURCE_COMPLETE_KEY: Final = f"{SOURCE_ROOT}/_COMPLETE"
SOURCE_PART_PREFIX: Final = f"{SOURCE_ROOT}/source=nasa-power-daily/product=RH2M/support=surface/"

DESTINATION_ROOT: Final = f"layer={CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM}/snapshot={SOURCE_SNAPSHOT_ID}"
DESTINATION_METADATA_ROOT: Final = f"{DESTINATION_ROOT}/_breakdown"
DESTINATION_MANIFEST_KEY: Final = f"{DESTINATION_METADATA_ROOT}/manifest.json"
DESTINATION_COMPLETE_KEY: Final = f"{DESTINATION_METADATA_ROOT}/_COMPLETE"
DESTINATION_RUN_KEY: Final = f"{DESTINATION_METADATA_ROOT}/_RUN.json"
DESTINATION_SOURCE_AUDIT_KEY: Final = f"{DESTINATION_METADATA_ROOT}/source-chain-audit.json"
DESTINATION_AUDIT_COMPLETE_KEY: Final = f"{DESTINATION_METADATA_ROOT}/_AUDIT_COMPLETE"

SOURCE_KEY: Final = "nasa-power-daily"
SOURCE_PARAMETER: Final = "RH2M"
SIGNAL_NAME: Final = "relative_humidity"
SUPPORT_KEY: Final = "surface"
NORMALIZED_UNIT: Final = "%"
PRECEDENCE_CONTRACT: Final = (
    "rh2m-product-relative-humidity-signal-then-newest-release-retrieved-at-then-highest-observation-id-v1"
)
EXPECTED_SOURCE_PARTS: Final = 424
EXPECTED_PHYSICAL_ROWS: Final = 1_166_676
EXPECTED_ELIGIBLE_ROWS: Final = 1_166_676
EXPECTED_BASE_ROWS: Final = 619_320
EXPECTED_DUPLICATES_COLLAPSED: Final = 547_356
EXPECTED_DAYS: Final = 1_560
EXPECTED_CELLS_PER_DAY: Final = 397
EXPECTED_FIRST_DAY: Final = date(2022, 4, 30)
EXPECTED_LAST_DAY: Final = date(2026, 8, 6)
DEFAULT_WORKERS: Final = 8
MAX_SOURCE_LISTED_KEYS: Final = 500
MAX_DESTINATION_LISTED_KEYS: Final = 20_000
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed", "ConditionalRequestConflict"})

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
    """Raised when the pinned snapshot cannot produce an exact relative-humidity lane."""


class ImmutableObjectConflictError(BreakdownError):
    """Raised when a destination key already contains different bytes."""


def _require_frame(value: pl.DataFrame | pl.Series) -> pl.DataFrame:
    """Narrow `pl.from_arrow`'s declared union: an Arrow Table always yields a DataFrame.

    The union exists because the same call accepts a ChunkedArray and returns a Series for it. Every
    caller here passes a Table, so the Series arm is unreachable -- and saying so once beats a cast
    at each of the call sites that then does column work on the result.
    """
    if not isinstance(value, pl.DataFrame):
        raise BreakdownError(f"expected a Polars DataFrame from an Arrow table, got {type(value).__name__}")
    return value


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

    def list_keys(self, relative_prefix: str, *, max_keys: int) -> list[tuple[str, int]]:
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
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
                if len(found) > max_keys:
                    raise BreakdownError(f"bounded listing {relative_prefix!r} exceeded its {max_keys}-key budget")
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


def _destination_key(layout_key: str) -> str:
    live_prefix = f"layer={CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM}/"
    if not layout_key.startswith(live_prefix):
        raise BreakdownError(f"destination layout key escapes its lane: {layout_key!r}")
    return f"{DESTINATION_ROOT}/{layout_key[len(live_prefix) :]}"


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
    schema = CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.arrow_schema
    conformed = table.select(schema.names).cast(schema).replace_schema_metadata(schema.metadata)
    conformed = conformed.sort_by(
        [(column, "ascending") for column in CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.sort_columns]
    )
    buffer = io.BytesIO()
    pq.write_table(
        conformed,
        buffer,
        compression=CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.compression,
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
        "observation_day_min": EXPECTED_FIRST_DAY.isoformat(),
        "observation_day_max": EXPECTED_LAST_DAY.isoformat(),
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
    nasa = [row for row in data_sources if row["key"] == SOURCE_KEY]
    if len(nasa) != 1:
        raise BreakdownError(f"source snapshot contains {len(nasa)} {SOURCE_KEY!r} data-source rows")
    releases = table("source_release").to_pylist()
    by_release = {str(row["id"]): row for row in releases}
    if len(by_release) != len(releases):
        raise BreakdownError("source-release dimension contains duplicate ids")
    return nasa[0], by_release


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
    expected = f"source=nasa-power-daily/product=RH2M/support=surface/year={year:04d}/month={month:02d}/"
    parts = ledger.get("parts")
    if not isinstance(parts, list):
        raise BreakdownError("source ledger has no parts list")
    selected = [
        part for part in parts if isinstance(part, Mapping) and str(part.get("relative_path", "")).startswith(expected)
    ]
    if len(selected) != 1:
        raise BreakdownError(
            f"source ledger contains {len(selected)} relative-humidity parts under {expected!r}, expected one"
        )
    part = selected[0]
    relative_path = str(part["relative_path"])
    key = str(part["key"])
    expected_key = f"{SOURCE_ROOT}/{relative_path}"
    if key != expected_key or not key.startswith(SOURCE_PART_PREFIX):
        raise BreakdownError(f"source ledger relative-humidity part escapes the pinned prefix: {key!r}")
    return part


def _part_census_constant(part: Mapping[str, Any], column: str) -> object:
    census = part.get("census")
    if not isinstance(census, Mapping):
        raise BreakdownError(f"source part {part.get('key')!r} has no census")
    summary = census.get(column)
    if not isinstance(summary, Mapping) or summary.get("min") != summary.get("max"):
        raise BreakdownError(f"source part {part.get('key')!r} does not have one constant {column!r} value")
    return summary.get("min")


def _classify_raw_row(row: Mapping[str, Any], *, month: str) -> str | None:
    partition_contract = {
        "data_source_key": SOURCE_KEY,
        "product_key": SOURCE_PARAMETER,
        "support_key": SUPPORT_KEY,
    }
    drift = {key: (row.get(key), value) for key, value in partition_contract.items() if row.get(key) != value}
    if drift:
        raise BreakdownError(
            f"raw relative-humidity partition row {row.get('id')} in {month} violates its path: {drift}"
        )
    observed_day = row.get("observation_day")
    observed_at = row.get("observed_at")
    if (
        not isinstance(observed_day, date)
        or not isinstance(observed_at, datetime)
        or observed_at.date() != observed_day
    ):
        raise BreakdownError(f"raw relative-humidity row {row.get('id')} has inconsistent observation day")
    row_hash = row.get("canonical_row_sha256")
    if not isinstance(row_hash, str) or len(row_hash) != 64:
        raise BreakdownError(f"raw relative-humidity row {row.get('id')} has no canonical SHA-256")
    if row.get("signal_name") != SIGNAL_NAME:
        return "signal_name_not_relative_humidity"
    if row.get("source_parameter") != SOURCE_PARAMETER:
        return "source_parameter_not_rh2m"
    if row.get("normalized_unit") is None:
        return "normalized_unit_null"
    if row.get("normalized_unit") != NORMALIZED_UNIT:
        return "normalized_unit_not_percent"
    if row.get("is_observed") is not True:
        return "not_observed"
    if row.get("quality_flag") != "accepted":
        return "quality_not_accepted"
    value = row.get("normalized_value")
    if value is None:
        return "normalized_value_null"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BreakdownError(f"raw relative-humidity row {row.get('id')} has a non-numeric normalized value")
    if not 0.0 <= float(value) <= 100.0:
        return "normalized_value_outside_percent_range"
    return None


def _deduplicate_month(
    rows: list[dict[str, Any]],
    *,
    nasa_source: Mapping[str, Any],
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
            if str(release["data_source_id"]) != str(nasa_source["id"]):
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
            raise BreakdownError("selected relative-humidity row has no date-valued observation_day")
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
            "allowed_client_exposure": nasa_source["allowed_client_exposure"],
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

    schema = CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.arrow_schema
    tables: dict[date, pa.Table] = {}
    for observed_day, day_rows in by_day.items():
        if len(day_rows) != EXPECTED_CELLS_PER_DAY:
            raise BreakdownError(
                f"deduplicated relative humidity {observed_day} has {len(day_rows)} cells, "
                f"expected {EXPECTED_CELLS_PER_DAY}"
            )
        table = pa.Table.from_pylist(day_rows, schema=schema)
        if table.num_rows != EXPECTED_CELLS_PER_DAY:
            raise BreakdownError(f"Arrow conversion changed relative-humidity row count on {observed_day}")
        tables[observed_day] = table
    return tables


def _load_source_month(
    store: ImmutableS3,
    *,
    month: str,
    ledger_summaries: Sequence[Mapping[str, Any]],
    nasa_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
) -> SourceMonth:
    year, month_number = map(int, month.split("-"))
    all_rows: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []
    excluded_rows: dict[str, int] = defaultdict(int)
    receipts: list[ObjectReceipt] = []
    seen_ids: set[int] = set()
    for summary in ledger_summaries:
        _, _, ledger = _verified_ledger(store, summary)
        part = _source_part_for_ledger(ledger, year=year, month=month_number)
        key = str(part["key"])
        payload = _required_object(store, key)
        if len(payload) != int(part["byte_count"]) or _sha256(payload) != part["sha256"]:
            raise BreakdownError(f"source relative-humidity part {key!r} failed byte reconciliation")
        table = _load_parquet(payload, key=key)
        if not RAW_REQUIRED_COLUMNS.issubset(table.column_names):
            missing = sorted(RAW_REQUIRED_COLUMNS.difference(table.column_names))
            raise BreakdownError(f"source relative-humidity part {key!r} omits {missing}")
        rows = table.to_pylist()
        if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
            raise BreakdownError(f"source relative-humidity part {key!r} failed row-digest reconciliation")
        part_sha256 = str(part["sha256"])
        for row_ordinal, row in enumerate(rows):
            exclusion = _classify_raw_row(row, month=month)
            row_id = int(row["id"])
            if row_id in seen_ids:
                raise BreakdownError(f"physical relative-humidity row id {row_id} repeats within {month}")
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
    base_by_day = _deduplicate_month(eligible_rows, nasa_source=nasa_source, releases=releases)
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
        raise BreakdownError(f"base relative-humidity day {observed_day} has {base.num_rows} rows")
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
            raise BreakdownError(f"base relative-humidity lineage is not aligned for {observed_day}/{row['cell_id']}")
        if (
            row["selected_source_row_id"] != aligned[0][0]
            or row["selected_source_row_sha256"] != aligned[1][0]
            or row["selected_source_release_id"] != aligned[2][0]
            or row["selected_source_part_key"] != aligned[3][0]
            or row["selected_source_part_sha256"] != aligned[4][0]
            or row["selected_source_row_ordinal"] != aligned[5][0]
        ):
            raise BreakdownError(
                f"base relative-humidity winner is not lineage element zero for {observed_day}/{row['cell_id']}"
            )
    objects: list[ObjectReceipt] = []
    base_payload = _serialize(base)
    objects.append(
        _write_payload(
            store,
            key=_destination_key(partition_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", 13, observed_day)),
            payload=base_payload,
            row_count=base.num_rows,
            kind="part",
            zoom=13,
            content_type=PARQUET_CONTENT_TYPE,
        )
    )
    source_frame = _require_frame(pl.from_arrow(base))
    tier_rows: dict[str, int] = {"13": base.num_rows}
    for zoom in (9, 5, 0):
        derived = derive_tier(source_frame, stream=CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, tier=zoom)
        if derived.height <= 0:
            raise BreakdownError(f"relative humidity {observed_day} z{zoom} derived to no rows")
        derived_table = derived.to_arrow()
        payload = _serialize(derived_table)
        objects.append(
            _write_payload(
                store,
                key=_destination_key(
                    partition_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", zoom, observed_day)
                ),
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
                key=_destination_key(
                    completion_marker_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", zoom, observed_day)
                ),
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
            key=_destination_key(
                completion_marker_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", 13, observed_day)
            ),
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
        schema = CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.arrow_schema
        if not table.schema.equals(schema, check_metadata=False):
            raise BreakdownError(f"destination Parquet object {key!r} has the wrong schema")
    elif receipt.get("kind") == "completion":
        marker = PartitionCompletion.from_json_bytes(payload)
        if marker.part_count != 1 or marker.row_count != int(receipt["row_count"]):
            raise BreakdownError(f"destination completion marker {key!r} row count drifted")


def _verify_checkpoint(
    store: ImmutableS3,
    checkpoint: Mapping[str, Any],
    *,
    month: str,
    source: SourceMonth,
) -> None:
    expected = {
        "contract_version": CONTRACT_VERSION,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "destination_root": DESTINATION_ROOT,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        "month": month,
        "precedence_contract": PRECEDENCE_CONTRACT,
    }
    drift = {key: (checkpoint.get(key), value) for key, value in expected.items() if checkpoint.get(key) != value}
    if drift:
        raise BreakdownError(f"destination checkpoint {month} drifted: {drift}")
    days = checkpoint.get("days")
    if not isinstance(days, list):
        raise BreakdownError(f"destination checkpoint {month} has no day list")
    expected_days = {observed_day.isoformat(): table for observed_day, table in source.base_by_day.items()}
    actual_day_names = [str(day.get("day")) for day in days if isinstance(day, Mapping)]
    if len(actual_day_names) != len(days) or len(set(actual_day_names)) != len(actual_day_names):
        raise BreakdownError(f"destination checkpoint {month} has invalid or duplicate day receipts")
    if any(day_name[:7] != month for day_name in actual_day_names):
        raise BreakdownError(f"destination checkpoint {month} contains a day outside its month")
    if set(actual_day_names) != set(expected_days):
        raise BreakdownError(f"destination checkpoint {month} day coverage differs from its source rows")

    base_rows = sum(table.num_rows for table in expected_days.values())
    expected_checkpoint = {
        "physical_input_rows": source.physical_rows,
        "eligible_input_rows": source.eligible_rows,
        "excluded_input_rows": sum(source.excluded_rows.values()),
        "exclusion_counts": dict(source.excluded_rows),
        "physical_input_bytes": source.physical_bytes,
        "physical_input_digest": source.physical_digest,
        "source_parts": [receipt.to_json() for receipt in source.source_parts],
        "base_rows": base_rows,
        "duplicates_collapsed": source.eligible_rows - base_rows,
        "lineage_source_rows": source.eligible_rows,
    }
    checkpoint_drift = {
        key: (checkpoint.get(key), value) for key, value in expected_checkpoint.items() if checkpoint.get(key) != value
    }
    if checkpoint_drift:
        raise BreakdownError(f"destination checkpoint {month} differs from reconstructed source: {checkpoint_drift}")

    receipts_to_verify: list[Mapping[str, Any]] = []
    for day in days:
        if not isinstance(day, Mapping) or not isinstance(day.get("objects"), list):
            raise BreakdownError(f"destination checkpoint {month} contains an invalid day receipt")
        day_name = str(day["day"])
        observed_day = date.fromisoformat(day_name)
        base = expected_days[day_name]
        expected_rows = base.to_pylist()
        expected_physical_rows = sum(int(row["input_source_row_count"]) for row in expected_rows)
        for row in expected_rows:
            count = int(row["input_source_row_count"])
            aligned = (
                row["input_source_row_ids"],
                row["input_source_row_sha256s"],
                row["input_source_release_ids"],
                row["input_source_part_keys"],
                row["input_source_part_sha256s"],
                row["input_source_row_ordinals"],
            )
            if any(
                values is None or len(values) != count or any(value is None for value in values) for values in aligned
            ):
                raise BreakdownError(f"reconstructed base lineage is not aligned for {day_name}/{row['cell_id']}")
            if (
                row["selected_source_row_id"] != aligned[0][0]
                or row["selected_source_row_sha256"] != aligned[1][0]
                or row["selected_source_release_id"] != aligned[2][0]
                or row["selected_source_part_key"] != aligned[3][0]
                or row["selected_source_part_sha256"] != aligned[4][0]
                or row["selected_source_row_ordinal"] != aligned[5][0]
            ):
                raise BreakdownError(
                    f"reconstructed winner is not lineage element zero for {day_name}/{row['cell_id']}"
                )
            digest = hashlib.sha256()
            for row_sha256 in aligned[1]:
                try:
                    digest.update(bytes.fromhex(str(row_sha256)))
                except ValueError as error:
                    raise BreakdownError(
                        f"reconstructed lineage has a non-hex row hash for {day_name}/{row['cell_id']}"
                    ) from error
            if digest.hexdigest() != row["input_source_row_digest"]:
                raise BreakdownError(f"reconstructed lineage digest drifted for {day_name}/{row['cell_id']}")

        expected_part_payloads: dict[int, bytes] = {13: _serialize(base)}
        source_frame = _require_frame(pl.from_arrow(base))
        for zoom in (9, 5, 0):
            expected_part_payloads[zoom] = _serialize(
                derive_tier(
                    source_frame,
                    stream=CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
                    tier=zoom,
                ).to_arrow()
            )
        expected_tier_rows = {
            str(zoom): _load_parquet(payload, key=f"reconstructed:{day_name}:z{zoom}").num_rows
            for zoom, payload in expected_part_payloads.items()
        }
        expected_day = {
            "physical_input_rows": expected_physical_rows,
            "base_rows": base.num_rows,
            "base_digest": _base_row_digest(expected_rows),
            "tier_rows": expected_tier_rows,
        }
        day_drift = {key: (day.get(key), value) for key, value in expected_day.items() if day.get(key) != value}
        if day_drift:
            raise BreakdownError(
                f"destination checkpoint day {day_name} differs from reconstructed source: {day_drift}"
            )

        receipts: dict[tuple[str, int], Mapping[str, Any]] = {}
        for receipt in day["objects"]:
            if not isinstance(receipt, Mapping):
                raise BreakdownError(f"destination checkpoint day {day_name} has a non-object receipt")
            kind = str(receipt.get("kind"))
            declared_zoom = receipt.get("zoom")
            if declared_zoom is None:
                raise BreakdownError(f"destination checkpoint day {day_name} has an invalid zoom receipt")
            try:
                zoom = int(declared_zoom)
            except (TypeError, ValueError) as error:
                raise BreakdownError(f"destination checkpoint day {day_name} has an invalid zoom receipt") from error
            identity = (kind, zoom)
            if identity in receipts:
                raise BreakdownError(f"destination checkpoint day {day_name} repeats {identity}")
            receipts[identity] = receipt
        expected_receipts = {(kind, zoom) for kind in ("part", "completion") for zoom in ZOOM_TIERS}
        if set(receipts) != expected_receipts:
            raise BreakdownError(
                f"destination checkpoint day {day_name} does not have exactly one object per tier/kind"
            )

        for zoom in ZOOM_TIERS:
            part = receipts[("part", zoom)]
            expected_payload = expected_part_payloads[zoom]
            expected_part_key = _destination_key(
                partition_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", zoom, observed_day)
            )
            if (
                part.get("key") != expected_part_key
                or int(part.get("row_count", -1)) != expected_tier_rows[str(zoom)]
                or int(part.get("byte_count", -1)) != len(expected_payload)
                or part.get("sha256") != _sha256(expected_payload)
            ):
                raise BreakdownError(f"destination checkpoint day {day_name} z{zoom} part differs from reconstruction")
            marker = receipts[("completion", zoom)]
            expected_marker_key = _destination_key(
                completion_marker_path(CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM, "observed", zoom, observed_day)
            )
            if (
                marker.get("key") != expected_marker_key
                or int(marker.get("row_count", -1)) != expected_tier_rows[str(zoom)]
            ):
                raise BreakdownError(
                    f"destination checkpoint day {day_name} z{zoom} completion differs from reconstruction"
                )
            receipts_to_verify.extend((part, marker))

        z13_part = receipts[("part", 13)]
        expected_derivation = {
            "key": z13_part["key"],
            "sha256": z13_part["sha256"],
            "row_count": z13_part["row_count"],
        }
        if day.get("derived_from_base") != expected_derivation:
            raise BreakdownError(f"destination checkpoint day {day_name} has invalid base derivation lineage")

    with ThreadPoolExecutor(max_workers=DEFAULT_WORKERS) as executor:
        futures = [executor.submit(_verify_receipt, store, receipt) for receipt in receipts_to_verify]
        for future in as_completed(futures):
            future.result()


def _write_month(
    store: ImmutableS3,
    *,
    month: str,
    ledger_summaries: Sequence[Mapping[str, Any]],
    nasa_source: Mapping[str, Any],
    releases: Mapping[str, Mapping[str, Any]],
    run_id: str,
    completed_at: datetime,
    workers: int,
) -> tuple[dict[str, Any], str]:
    checkpoint_key = _checkpoint_key(month)
    existing = store.get(checkpoint_key)
    source = _load_source_month(
        store,
        month=month,
        ledger_summaries=ledger_summaries,
        nasa_source=nasa_source,
        releases=releases,
    )
    if existing is not None:
        checkpoint = _json_object(existing, key=checkpoint_key)
        _verify_checkpoint(store, checkpoint, month=month, source=source)
        return checkpoint, _sha256(existing)

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
        day_results.extend(future.result() for future in as_completed(pending))
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
        "destination_root": DESTINATION_ROOT,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
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
    schema = CLIMATE_FIELD_RELATIVE_HUMIDITY_SCHEMA.arrow_schema
    descriptor = {
        "contract_version": CONTRACT_VERSION,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        "destination_root": DESTINATION_ROOT,
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
    source_manifest: Mapping[str, Any],
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
    snapshot_parts: list[dict[str, Any]] = []
    snapshot_rows = 0
    snapshot_bytes = 0
    snapshot_selected_parts = 0
    snapshot_excluded_parts = 0
    snapshot_excluded_rows: dict[str, int] = defaultdict(int)
    for month, summaries in month_ledgers.items():
        year, month_number = map(int, month.split("-"))
        for summary in summaries:
            ledger_key, ledger_payload, ledger = _verified_ledger(store, summary)
            part = _source_part_for_ledger(ledger, year=year, month=month_number)
            part_key = str(part["key"])
            ledger_parts = ledger.get("parts")
            if not isinstance(ledger_parts, list):
                raise BreakdownError(f"source ledger {ledger_key!r} has no parts list")
            for declared in ledger_parts:
                if not isinstance(declared, Mapping):
                    raise BreakdownError(f"source ledger {ledger_key!r} contains a non-object part receipt")
                declared_key = str(declared.get("key", ""))
                relative_path = str(declared.get("relative_path", ""))
                if declared_key != f"{SOURCE_ROOT}/{relative_path}" or not declared_key.startswith(f"{SOURCE_ROOT}/"):
                    raise BreakdownError(
                        f"source ledger {ledger_key!r} declares a part outside the pinned snapshot: {declared_key!r}"
                    )
                signal_name = _part_census_constant(declared, "signal_name")
                selected = declared_key == part_key
                if selected:
                    expected_constants = {
                        "data_source_key": SOURCE_KEY,
                        "product_key": SOURCE_PARAMETER,
                        "source_parameter": SOURCE_PARAMETER,
                        "support_key": SUPPORT_KEY,
                        "signal_name": SIGNAL_NAME,
                        "normalized_unit": NORMALIZED_UNIT,
                        "quality_flag": "accepted",
                    }
                    drift = {
                        column: (_part_census_constant(declared, column), expected)
                        for column, expected in expected_constants.items()
                        if _part_census_constant(declared, column) != expected
                    }
                    if drift:
                        raise BreakdownError(
                            f"selected source part {declared_key!r} violates RH2M/relative_humidity precedence: {drift}"
                        )
                    classification = "selected_rh2m_relative_humidity"
                    snapshot_selected_parts += 1
                else:
                    if signal_name == SIGNAL_NAME:
                        raise BreakdownError(
                            f"source part {declared_key!r} carries relative_humidity outside the pinned RH2M path; "
                            "the precedence set is incomplete"
                        )
                    classification = "excluded_other_signal"
                    snapshot_excluded_parts += 1
                    snapshot_excluded_rows[classification] += int(declared["row_count"])
                row_count = int(declared["row_count"])
                byte_count = int(declared["byte_count"])
                snapshot_rows += row_count
                snapshot_bytes += byte_count
                snapshot_parts.append(
                    {
                        "key": declared_key,
                        "sha256": str(declared["sha256"]),
                        "row_count": row_count,
                        "row_digest": str(declared["row_digest"]),
                        "byte_count": byte_count,
                        "ledger_key": ledger_key,
                        "observation_month": month,
                        "cell_batch_index": int(summary["cell_batch_index"]),
                        "signal_name": signal_name,
                        "classification": classification,
                    }
                )
            if part_key in audited_parts:
                raise BreakdownError(f"source relative-humidity part {part_key!r} is repeated across ledgers")
            audited_parts.add(part_key)
            payload = _required_object(store, part_key)
            payload_sha256 = _sha256(payload)
            if len(payload) != int(part["byte_count"]) or payload_sha256 != part["sha256"]:
                raise BreakdownError(f"source relative-humidity part {part_key!r} failed ledger byte reconciliation")
            table = _load_parquet(payload, key=part_key)
            if not RAW_REQUIRED_COLUMNS.issubset(table.column_names):
                missing = sorted(RAW_REQUIRED_COLUMNS.difference(table.column_names))
                raise BreakdownError(f"source relative-humidity part {part_key!r} omits {missing}")
            rows = table.to_pylist()
            if len(rows) != int(part["row_count"]) or _row_set_digest(rows) != part["row_digest"]:
                raise BreakdownError(f"source relative-humidity part {part_key!r} failed ledger row reconciliation")
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
                    "relative_humidity_part_key": part_key,
                    "relative_humidity_part_sha256": payload_sha256,
                    "relative_humidity_part_row_count": len(rows),
                    "relative_humidity_part_row_digest": str(part["row_digest"]),
                }
            )

    listed_parts = {key for key, _ in source_inventory}
    if audited_parts != listed_parts or audited_parts != set(checkpoint_parts):
        raise BreakdownError("audited source parts, prefix inventory, and destination checkpoint receipts differ")
    excluded_rows = sum(exclusions.values())
    snapshot_expected = {
        "rows": (snapshot_rows, int(source_manifest["row_count"])),
        "parts": (len(snapshot_parts), int(source_manifest["partition_count"])),
        "bytes": (snapshot_bytes, int(source_manifest["fact_byte_count"])),
        "selected_parts": (snapshot_selected_parts, EXPECTED_SOURCE_PARTS),
        "excluded_parts": (
            snapshot_excluded_parts,
            int(source_manifest["partition_count"]) - EXPECTED_SOURCE_PARTS,
        ),
        "selected_rows": (physical_rows, EXPECTED_PHYSICAL_ROWS),
        "excluded_rows": (
            sum(snapshot_excluded_rows.values()),
            int(source_manifest["row_count"]) - EXPECTED_PHYSICAL_ROWS,
        ),
    }
    snapshot_drift = {name: values for name, values in snapshot_expected.items() if values[0] != values[1]}
    if snapshot_drift:
        raise BreakdownError(f"full snapshot part classification drifted: {snapshot_drift}")
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
        "contract_version": "climate-field-relative-humidity.source-chain-audit.v1",
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_key": SOURCE_MANIFEST_KEY,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        "ledger_binding": (
            "each constructed ledger identity and aggregate summary equals its entry in the pinned source manifest; "
            "the audit additionally records the current immutable ledger byte SHA-256"
        ),
        "ledger_count": len(ledger_receipts),
        "snapshot_row_count": snapshot_rows,
        "snapshot_part_count": len(snapshot_parts),
        "snapshot_fact_byte_count": snapshot_bytes,
        "snapshot_selected_part_count": snapshot_selected_parts,
        "snapshot_excluded_part_count": snapshot_excluded_parts,
        "snapshot_excluded_row_count": sum(snapshot_excluded_rows.values()),
        "snapshot_exclusion_counts": dict(sorted(snapshot_excluded_rows.items())),
        "physical_source_rows": physical_rows,
        "eligible_source_rows": eligible_rows,
        "excluded_source_rows": excluded_rows,
        "exclusion_counts": dict(sorted(exclusions.items())),
        "source_part_count": len(audited_parts),
        "snapshot_parts": snapshot_parts,
        "ledgers": ledger_receipts,
    }


def _publish_source_audit(
    store: ImmutableS3,
    *,
    audit: Mapping[str, Any],
) -> tuple[str, str]:
    payload = _json_bytes(audit)
    store.put_immutable(DESTINATION_SOURCE_AUDIT_KEY, payload, content_type=JSON_CONTENT_TYPE)
    audit_sha256 = _sha256(payload)
    completion = {
        "contract_version": "climate-field-relative-humidity.source-chain-audit.v1",
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "audit_key": DESTINATION_SOURCE_AUDIT_KEY,
        "audit_sha256": audit_sha256,
        "ledger_count": int(audit["ledger_count"]),
        "snapshot_part_count": int(audit["snapshot_part_count"]),
        "snapshot_row_count": int(audit["snapshot_row_count"]),
        "source_part_count": int(audit["source_part_count"]),
        "physical_source_rows": int(audit["physical_source_rows"]),
    }
    completion_payload = _json_bytes(completion)
    store.put_immutable(
        DESTINATION_AUDIT_COMPLETE_KEY,
        completion_payload,
        content_type=JSON_CONTENT_TYPE,
    )
    return audit_sha256, _sha256(completion_payload)


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
            "source relative-humidity inventory reconciliation failed: "
            f"missing={missing[:5]}, unexpected={unexpected[:5]}"
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
        raise BreakdownError(f"relative-humidity snapshot-to-lane reconciliation drifted: {drift}")
    if physical_rows != eligible_rows + excluded_rows:
        raise BreakdownError("relative-humidity physical population does not equal eligible plus excluded rows")
    if len(set(day_names)) != EXPECTED_DAYS:
        raise BreakdownError("relative-humidity checkpoint set contains duplicate days")

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
    source_audit_sha256, source_audit_complete_sha256 = _publish_source_audit(
        store,
        audit=source_chain_audit,
    )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        "destination_root": DESTINATION_ROOT,
        "kind": "observed",
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_snapshot_prefix": f"{SOURCE_ROOT}/",
        "source_manifest_key": SOURCE_MANIFEST_KEY,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_complete_key": SOURCE_COMPLETE_KEY,
        "source_completed_at": source_completion["completed_at"],
        "precedence_contract": PRECEDENCE_CONTRACT,
        "precedence_order": [
            "product_key = RH2M",
            "signal_name = relative_humidity",
            "source_release.retrieved_at DESC",
            "signal_observation.id DESC",
        ],
        "source_filter": {
            "data_source_key": SOURCE_KEY,
            "product_key": SOURCE_PARAMETER,
            "source_parameter": SOURCE_PARAMETER,
            "support_key": SUPPORT_KEY,
            "signal_name": SIGNAL_NAME,
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
        "snapshot_row_count": source_chain_audit["snapshot_row_count"],
        "snapshot_part_count": source_chain_audit["snapshot_part_count"],
        "snapshot_fact_byte_count": source_chain_audit["snapshot_fact_byte_count"],
        "snapshot_selected_part_count": source_chain_audit["snapshot_selected_part_count"],
        "snapshot_excluded_part_count": source_chain_audit["snapshot_excluded_part_count"],
        "snapshot_excluded_row_count": source_chain_audit["snapshot_excluded_row_count"],
        "snapshot_exclusion_counts": source_chain_audit["snapshot_exclusion_counts"],
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
        "required_tiers": list(ZOOM_TIERS),
        "tier_row_counts": tier_rows,
        "tier_part_counts": tier_part_counts,
        "tier_completion_counts": tier_completion_counts,
        "destination_data_object_count": len(destination_receipt_keys),
        "run_descriptor_key": DESTINATION_RUN_KEY,
        "run_started_at": run_descriptor["started_at"],
        "source_chain_audit_key": DESTINATION_SOURCE_AUDIT_KEY,
        "source_chain_audit_sha256": source_audit_sha256,
        "source_chain_audit_complete_key": DESTINATION_AUDIT_COMPLETE_KEY,
        "source_chain_audit_complete_sha256": source_audit_complete_sha256,
        "month_checkpoints": checkpoint_summaries,
        "source_snapshot_manifest_summary": {
            "row_count": source_manifest["row_count"],
            "partition_count": source_manifest["partition_count"],
            "batch_count": source_manifest["batch_count"],
            "rejected_rows": source_manifest["rejected_rows"],
        },
    }
    manifest_payload = _json_bytes(manifest)
    store.put_immutable(DESTINATION_MANIFEST_KEY, manifest_payload, content_type=JSON_CONTENT_TYPE)
    manifest_sha256 = _sha256(manifest_payload)
    completion = {
        "contract_version": CONTRACT_VERSION,
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
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
        "source_chain_audit_key": DESTINATION_SOURCE_AUDIT_KEY,
        "source_chain_audit_sha256": source_audit_sha256,
        "source_chain_audit_complete_key": DESTINATION_AUDIT_COMPLETE_KEY,
        "source_chain_audit_complete_sha256": source_audit_complete_sha256,
        "completed_at": run_descriptor["started_at"],
    }
    completion_payload = _json_bytes(completion)
    expected_inventory_without_complete = {
        *destination_receipt_keys,
        DESTINATION_RUN_KEY,
        DESTINATION_MANIFEST_KEY,
        DESTINATION_SOURCE_AUDIT_KEY,
        DESTINATION_AUDIT_COMPLETE_KEY,
        *(_checkpoint_key(str(checkpoint["month"])) for checkpoint in checkpoints),
    }
    actual_inventory = {
        key
        for key, _ in store.list_keys(
            f"{DESTINATION_ROOT}/",
            max_keys=MAX_DESTINATION_LISTED_KEYS,
        )
    }
    existing_complete = DESTINATION_COMPLETE_KEY in actual_inventory
    actual_inventory_without_complete = actual_inventory.difference({DESTINATION_COMPLETE_KEY})
    if actual_inventory_without_complete != expected_inventory_without_complete:
        missing = sorted(expected_inventory_without_complete - actual_inventory_without_complete)
        unexpected = sorted(actual_inventory_without_complete - expected_inventory_without_complete)
        raise BreakdownError(f"destination inventory is not exact: missing={missing[:5]}, unexpected={unexpected[:5]}")
    durable_manifest = _required_object(store, DESTINATION_MANIFEST_KEY)
    if _sha256(durable_manifest) != manifest_sha256:
        raise BreakdownError("destination manifest durable checksum reconciliation failed")
    durable_audit = _required_object(store, DESTINATION_SOURCE_AUDIT_KEY)
    durable_audit_complete = _required_object(store, DESTINATION_AUDIT_COMPLETE_KEY)
    if _sha256(durable_audit) != source_audit_sha256 or _sha256(durable_audit_complete) != source_audit_complete_sha256:
        raise BreakdownError("destination source-audit durable checksum reconciliation failed")
    if existing_complete:
        if _required_object(store, DESTINATION_COMPLETE_KEY) != completion_payload:
            raise ImmutableObjectConflictError(f"completion marker {DESTINATION_COMPLETE_KEY!r} conflicts")
    else:
        store.put_immutable(
            DESTINATION_COMPLETE_KEY,
            completion_payload,
            content_type=JSON_CONTENT_TYPE,
        )
    durable_complete = _json_object(
        _required_object(store, DESTINATION_COMPLETE_KEY),
        key=DESTINATION_COMPLETE_KEY,
    )
    if durable_complete.get("manifest_sha256") != manifest_sha256:
        raise BreakdownError("destination _COMPLETE durable checksum reconciliation failed")
    return {
        "status": "complete",
        "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
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
        "destination_objects": len(expected_inventory_without_complete) + 1,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= 24:
        parser.error("--workers must be between 1 and 24")
    return arguments


def main() -> int:
    arguments = _arguments()
    if not arguments.env_file.is_file():
        raise SystemExit(f"settings file does not exist: {arguments.env_file}")
    try:
        configured = Settings(_env_file=arguments.env_file)  # type: ignore[call-arg]
        store = ImmutableS3(configured.require_object_store(), configured.object_store_prefix)
        source_manifest, source_completion = _load_source_contract(store)
        source_inventory = store.list_keys(SOURCE_PART_PREFIX, max_keys=MAX_SOURCE_LISTED_KEYS)
        if len(source_inventory) != EXPECTED_SOURCE_PARTS or any(
            not key.endswith(".parquet") for key, _ in source_inventory
        ):
            raise BreakdownError(
                f"source relative-humidity prefix has {len(source_inventory)} objects, "
                f"expected {EXPECTED_SOURCE_PARTS} Parquet parts"
            )
        nasa_source, releases = _load_dimensions(store, source_manifest)
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
                nasa_source=nasa_source,
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
            source_manifest=source_manifest,
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
                    "lane": CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
                    "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                    "error": f"{type(error).__name__}: {error}",
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
