"""Build three immutable soil-wetness lanes from a pinned canonical signal snapshot.

This operator never opens PostgreSQL and never writes the shared signal layer. Run from
services/agri-data-service:

    uv run python scripts/soil_wetness_snapshot_breakdown.py inventory
    uv run python scripts/soil_wetness_snapshot_breakdown.py build

See scripts/AGENTS.md for the immutable checkpoint and reconciliation contract.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import struct
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, Protocol

import boto3  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CHECKOUT_ENV_FILE: Final = Path.home() / "Programming" / "plantgeo" / "services" / "agri-data-service" / ".env"
DEFAULT_ENV_FILE: Final = SERVICE_ROOT / ".env" if (SERVICE_ROOT / ".env").is_file() else CHECKOUT_ENV_FILE
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import ObjectStoreCredentials, Settings  # noqa: E402

CONTRACT_VERSION: Final = "plantgeo.signal-product-breakdown.v1"
INPUT_CONTRACT_VERSION: Final = "agri.signal_observation.raw-canonical.v1"
DEFAULT_SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
DEFAULT_INPUT_PREFIX: Final = "raw-canonical/signal-observation"
DEFAULT_INPUT_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
DEFAULT_OUTPUT_PREFIX: Final = "derived-canonical/signal-observation"
EXPECTED_SOURCE_KEY: Final = "nasa-power-daily"
EXPECTED_SUPPORT_KEY: Final = "surface"
EXPECTED_UNIT: Final = "fraction_of_saturation"
ZOOM_RESOLUTIONS: Final[dict[int, float]] = {9: 0.01, 5: 0.2, 0: 5.0}
ZOOM_TIERS: Final = (13, 9, 5, 0)
DEFAULT_VERIFY_WORKERS: Final = 16
MAX_OUTPUT_RECEIPT_BYTES: Final = 16_000_000
MAX_OUTPUT_RECEIPT_ROWS: Final = 1_000_000
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed", "ConditionalRequestConflict"})
_RETRYABLE_CODES: Final = frozenset(
    {
        "408",
        "429",
        "500",
        "502",
        "503",
        "504",
        "InternalError",
        "RequestTimeout",
        "SlowDown",
        "ServiceUnavailable",
        "Throttling",
    }
)


def _timestamp() -> pa.TimestampType:
    return pa.timestamp("us", tz="UTC")


RAW_SCHEMA: Final = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("source_release_id", pa.string(), nullable=False),
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("signal_name", pa.string(), nullable=False),
        pa.field("source_parameter", pa.string(), nullable=False),
        pa.field("support_key", pa.string(), nullable=False),
        pa.field("observed_at", _timestamp(), nullable=False),
        pa.field("valid_from", _timestamp()),
        pa.field("valid_to", _timestamp()),
        pa.field("data_available_at", _timestamp(), nullable=False),
        pa.field("original_value", pa.float64()),
        pa.field("original_unit", pa.string()),
        pa.field("normalized_value", pa.float64()),
        pa.field("normalized_unit", pa.string()),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("coverage_fraction", pa.float64(), nullable=False),
        pa.field("is_observed", pa.bool_(), nullable=False),
        pa.field("metadata_json", pa.string(), nullable=False),
        pa.field("created_at", _timestamp(), nullable=False),
        pa.field("observation_day", pa.date32(), nullable=False),
        pa.field("product_key", pa.string(), nullable=False),
        pa.field("cell_key", pa.string(), nullable=False),
        pa.field("cell_grid_name", pa.string(), nullable=False),
        pa.field("cell_resolution_m", pa.int32(), nullable=False),
        pa.field("cell_parent_cell_id", pa.string()),
        pa.field("cell_centroid_wkb", pa.binary(), nullable=False),
        pa.field("cell_centroid_srid", pa.int32(), nullable=False),
        pa.field("cell_centroid_longitude", pa.float64(), nullable=False),
        pa.field("cell_centroid_latitude", pa.float64(), nullable=False),
        pa.field("data_source_id", pa.string(), nullable=False),
        pa.field("data_source_key", pa.string(), nullable=False),
        pa.field("canonical_row_sha256", pa.string(), nullable=False),
    ],
    metadata={b"plantgeo_contract": INPUT_CONTRACT_VERSION.encode("ascii")},
)

RAW_HASHED_COLUMNS: Final = tuple(field.name for field in RAW_SCHEMA if field.name != "canonical_row_sha256")

PROVENANCE_SCHEMA: Final = pa.schema(
    [
        *RAW_SCHEMA,
        pa.field("lane", pa.string(), nullable=False),
        pa.field("disposition", pa.string(), nullable=False),
        pa.field("disposition_reason", pa.string(), nullable=False),
        pa.field("precedence_rank", pa.int32()),
        pa.field("selected_observation_id", pa.int64()),
        pa.field("release_retrieved_at", _timestamp(), nullable=False),
        pa.field("release_source_version", pa.string(), nullable=False),
        pa.field("release_payload_checksum", pa.string(), nullable=False),
        pa.field("release_transform_version", pa.string(), nullable=False),
        pa.field("release_license_snapshot", pa.string(), nullable=False),
        pa.field("source_allowed_client_exposure", pa.bool_(), nullable=False),
        pa.field("input_manifest_sha256", pa.string(), nullable=False),
    ],
    metadata={b"plantgeo_contract": b"plantgeo.signal-product-lineage.v1"},
)

LANE_SCHEMA: Final = pa.schema(
    [
        pa.field("support_key", pa.string(), nullable=False),
        pa.field("signal_name", pa.string(), nullable=False),
        pa.field("normalized_unit", pa.string(), nullable=False),
        pa.field("cell_id", pa.string()),
        pa.field("observed_day", pa.date32(), nullable=False),
        pa.field("normalized_value", pa.float64(), nullable=False),
        pa.field("observation_count", pa.int64(), nullable=False),
        pa.field("newest_observed_at", _timestamp(), nullable=False),
        pa.field("coverage_fraction", pa.float64()),
        pa.field("allowed_client_exposure", pa.bool_()),
        pa.field("cell_longitude", pa.float64(), nullable=False),
        pa.field("cell_latitude", pa.float64(), nullable=False),
        pa.field("selected_observation_id", pa.int64()),
        pa.field("selected_canonical_row_sha256", pa.string()),
        pa.field("selected_source_release_id", pa.string()),
        pa.field("selected_release_retrieved_at", _timestamp()),
        pa.field("physical_candidate_count", pa.int64(), nullable=False),
        pa.field("lineage_sha256", pa.string(), nullable=False),
        pa.field("input_manifest_sha256", pa.string(), nullable=False),
    ],
    metadata={b"plantgeo_contract": b"plantgeo.signal-product-lane.v1"},
)

LANE_SORT_COLUMNS: Final = ("support_key", "signal_name", "normalized_unit", "cell_id", "observed_day")


@dataclass(frozen=True, slots=True)
class Product:
    parameter: str
    lane: str
    signal_name: str


PRODUCTS: Final = (
    Product("GWETTOP", "soil-wetness-surface", "soil_wetness_surface"),
    Product("GWETROOT", "soil-wetness-root-zone", "soil_wetness_root_zone"),
    Product("GWETPROF", "soil-wetness-profile", "soil_wetness_profile"),
)


class BreakdownError(RuntimeError):
    """The pinned input or an immutable output failed a closed contract."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _json_object(payload: bytes, *, key: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise BreakdownError(f"{key!r} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise BreakdownError(f"{key!r} is not a JSON object")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise BreakdownError(f"timezone-naive timestamp in canonical input: {value!r}")
    return value.astimezone(UTC)


def _hash_value(value: Any) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"b1" if value else b"b0"
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii")
    if isinstance(value, float):
        return b"f" + struct.pack(">d", value)
    if isinstance(value, datetime):
        return b"t" + _as_utc(value).isoformat(timespec="microseconds").encode("ascii")
    if isinstance(value, date):
        return b"d" + value.isoformat().encode("ascii")
    if isinstance(value, bytes):
        return b"x" + value
    return b"s" + str(value).encode("utf-8")


def canonical_row_hash(row: Mapping[str, Any]) -> str:
    digest = hashlib.sha256()
    for column in RAW_HASHED_COLUMNS:
        name = column.encode("utf-8")
        value = _hash_value(row[column])
        digest.update(struct.pack(">I", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", len(value)))
        digest.update(value)
    return digest.hexdigest()


def row_set_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row_id, row_hash in sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows):
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def lineage_digest(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def schema_manifest(schema: pa.Schema) -> list[dict[str, Any]]:
    return [{"name": field.name, "type": str(field.type), "nullable": field.nullable} for field in schema]


def serialize_table(rows: Sequence[Mapping[str, Any]], schema: pa.Schema, sort_columns: Sequence[str]) -> bytes:
    table = pa.Table.from_pylist(list(rows), schema=schema)
    if table.num_rows:
        table = table.sort_by([(column, "ascending") for column in sort_columns])
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", write_statistics=True, row_group_size=64_000)
    return buffer.getvalue()


def table_receipt(key: str, payload: bytes, row_count: int) -> dict[str, Any]:
    return {"key": key, "row_count": row_count, "byte_count": len(payload), "sha256": _sha256(payload)}


def _client_error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 8
    base_delay_seconds: float = 0.5

    def run(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(self.attempts):
            try:
                return operation()
            except (ConnectionClosedError, EndpointConnectionError, ReadTimeoutError) as exc:
                error: Exception = exc
            except ClientError as exc:
                if _client_error_code(exc) not in _RETRYABLE_CODES:
                    raise
                error = exc
            if attempt + 1 == self.attempts:
                raise error
            time.sleep(self.base_delay_seconds * (2**attempt))
        raise AssertionError("retry loop did not return or raise")


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(slots=True)
class SnapshotStore:
    bucket: str
    client: S3Client
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_credentials(cls, credentials: ObjectStoreCredentials, *, retry: RetryPolicy) -> SnapshotStore:
        client: S3Client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(
                retries={"max_attempts": retry.attempts, "mode": "adaptive"},
                max_pool_connections=64,
            ),
        )
        return cls(bucket=credentials.bucket, client=client, retry=retry)

    def get(self, key: str, *, max_bytes: int | None = None) -> bytes | None:
        if max_bytes is not None:
            size = self.size(key)
            if size is None:
                return None
            if size > max_bytes:
                raise BreakdownError(f"object {key!r} is {size} bytes; budget is {max_bytes}")

        def load() -> bytes | None:
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _client_error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            body = response.get("Body")
            if body is None:
                return None
            payload = body.read()  # type: ignore[attr-defined]
            return payload if isinstance(payload, bytes) else bytes(payload)

        payload = self.retry.run(load)
        if payload is not None and max_bytes is not None and len(payload) > max_bytes:
            raise BreakdownError(f"object {key!r} exceeded its {max_bytes}-byte read budget")
        return payload

    def get_exact(self, key: str, *, expected_bytes: int) -> bytes | None:
        """Read one receipt with a single bounded request."""

        def load() -> bytes | None:
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _client_error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            content_length = response.get("ContentLength")
            if not isinstance(content_length, int) or content_length != expected_bytes:
                body = response.get("Body")
                if body is not None and hasattr(body, "close"):
                    body.close()  # type: ignore[attr-defined]
                raise BreakdownError(f"object {key!r} has declared size {content_length!r}; expected {expected_bytes}")
            body = response.get("Body")
            if body is None:
                return None
            try:
                payload = body.read(expected_bytes + 1)  # type: ignore[attr-defined]
                return payload if isinstance(payload, bytes) else bytes(payload)
            finally:
                if hasattr(body, "close"):
                    body.close()  # type: ignore[attr-defined]

        payload = self.retry.run(load)
        if payload is not None and len(payload) != expected_bytes:
            raise BreakdownError(f"object {key!r} returned {len(payload)} bytes; expected {expected_bytes}")
        return payload

    def size(self, key: str) -> int | None:
        def inspect() -> int | None:
            try:
                response = self.client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if _client_error_code(exc) in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            value = response.get("ContentLength")
            return int(value) if isinstance(value, int) else None

        return self.retry.run(inspect)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        try:
            self.retry.run(
                lambda: self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=payload,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
            )
        except ClientError as exc:
            if _client_error_code(exc) not in _PRECONDITION_CODES:
                raise
            existing = self.get(key, max_bytes=len(payload) + 1)
            if existing != payload:
                raise BreakdownError(
                    f"immutable object conflict for {key!r}: "
                    f"existing={_sha256(existing or b'')} attempted={_sha256(payload)}"
                ) from exc

    def list_keys(self, prefix: str) -> Iterator[str]:
        token: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self.retry.run(lambda request=request: self.client.list_objects_v2(**request))
            contents = response.get("Contents")
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, Mapping) and isinstance(item.get("Key"), str):
                        yield str(item["Key"])
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                return
            token = next_token


@dataclass(frozen=True, slots=True)
class InputContract:
    manifest: Mapping[str, Any]
    manifest_sha256: str
    manifest_key: str
    completion_key: str
    root: str
    units: Mapping[tuple[str, int], Mapping[str, Any]]
    releases: Mapping[str, Mapping[str, Any]]
    sources: Mapping[str, Mapping[str, Any]]
    ledger_cache: dict[tuple[str, int], dict[str, Any]] = field(default_factory=dict, compare=False)


def _prefixed(prefix: str, relative: str) -> str:
    cleaned = prefix.strip("/")
    return f"{cleaned}/{relative.lstrip('/')}" if cleaned else relative.lstrip("/")


def load_input_contract(
    store: SnapshotStore,
    *,
    object_store_prefix: str,
    input_prefix: str,
    snapshot_id: str,
    expected_manifest_sha256: str,
) -> InputContract:
    if not _SHA256.fullmatch(expected_manifest_sha256):
        raise BreakdownError("input manifest SHA-256 must be one lowercase digest")
    root = _prefixed(object_store_prefix, f"{input_prefix.strip('/')}/snapshot={snapshot_id}")
    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    completion_payload = store.get(completion_key, max_bytes=64_000)
    if completion_payload is None:
        raise BreakdownError(f"canonical snapshot has no completion marker at {completion_key!r}")
    completion = _json_object(completion_payload, key=completion_key)
    if (
        completion.get("contract_version") != INPUT_CONTRACT_VERSION
        or completion.get("snapshot_id") != snapshot_id
        or completion.get("manifest_key") != manifest_key
        or completion.get("manifest_sha256") != expected_manifest_sha256
    ):
        raise BreakdownError("canonical completion marker does not bind the requested snapshot and manifest")
    manifest_payload = store.get(manifest_key, max_bytes=1_000_000)
    if manifest_payload is None or _sha256(manifest_payload) != expected_manifest_sha256:
        raise BreakdownError("canonical manifest bytes do not match the external SHA-256 pin")
    manifest = _json_object(manifest_payload, key=manifest_key)
    if (
        manifest.get("contract_version") != INPUT_CONTRACT_VERSION
        or manifest.get("snapshot_id") != snapshot_id
        or manifest.get("snapshot_prefix") != f"{root}/"
        or int(manifest.get("row_count", -1)) != int(completion.get("row_count", -2))
        or int(manifest.get("partition_count", -1)) != int(completion.get("partition_count", -2))
        or int(manifest.get("byte_count", -1)) != int(completion.get("byte_count", -2))
        or int(manifest.get("rejected_rows", -1)) != 0
    ):
        raise BreakdownError("canonical manifest and completion counts or identity disagree")
    if manifest.get("parquet_schema") != schema_manifest(RAW_SCHEMA):
        raise BreakdownError("canonical fact Arrow schema differs from the soil breakdown contract")
    raw_units = manifest.get("month_ledgers")
    if not isinstance(raw_units, list) or len(raw_units) != int(manifest.get("batch_count", -1)):
        raise BreakdownError("canonical manifest has an invalid bounded ledger summary")
    units: dict[tuple[str, int], Mapping[str, Any]] = {}
    for summary in raw_units:
        if not isinstance(summary, Mapping):
            raise BreakdownError("canonical month ledger summary is not an object")
        identity = (str(summary["observation_month"]), int(summary["cell_batch_index"]))
        if identity in units:
            raise BreakdownError(f"duplicate canonical ledger summary {identity}")
        units[identity] = summary
    dimensions = manifest.get("dimension_objects")
    if not isinstance(dimensions, Mapping):
        raise BreakdownError("canonical manifest omits frozen companion dimensions")

    def dimension_rows(name: str) -> list[dict[str, Any]]:
        metadata = dimensions.get(name)
        if not isinstance(metadata, Mapping):
            raise BreakdownError(f"canonical manifest omits dimension {name!r}")
        key = str(metadata["key"])
        size = int(metadata["byte_count"])
        payload = store.get(key, max_bytes=size)
        if payload is None or len(payload) != size or _sha256(payload) != metadata["sha256"]:
            raise BreakdownError(f"canonical dimension {name!r} failed byte reconciliation")
        table = pq.read_table(io.BytesIO(payload))
        if table.num_rows != int(metadata["row_count"]):
            raise BreakdownError(f"canonical dimension {name!r} failed row reconciliation")
        return table.to_pylist()

    release_rows = dimension_rows("source_release")
    source_rows = dimension_rows("data_source")
    releases = {str(row["id"]): row for row in release_rows}
    sources = {str(row["id"]): row for row in source_rows}
    if len(releases) != len(release_rows) or len(sources) != len(source_rows):
        raise BreakdownError("frozen companion dimensions contain duplicate identities")
    return InputContract(
        manifest=manifest,
        manifest_sha256=expected_manifest_sha256,
        manifest_key=manifest_key,
        completion_key=completion_key,
        root=root,
        units=units,
        releases=releases,
        sources=sources,
    )


def ledger_for_unit(store: SnapshotStore, contract: InputContract, month: str, batch_index: int) -> dict[str, Any]:
    cached = contract.ledger_cache.get((month, batch_index))
    if cached is not None:
        return cached
    summary = contract.units[(month, batch_index)]
    key = f"{contract.root}/_ledger/month={month}/cell-batch={batch_index:05d}.json"
    payload = store.get(key, max_bytes=256_000)
    if payload is None:
        raise BreakdownError(f"canonical checkpoint is missing: {key}")
    ledger = _json_object(payload, key=key)
    checks = {
        "observation_month": month,
        "cell_batch_index": batch_index,
        "row_count": int(summary["row_count"]),
        "part_count": int(summary["part_count"]),
        "byte_count": int(summary["byte_count"]),
        "source_row_digest": str(summary["source_row_digest"]),
    }
    if any(ledger.get(name) != value for name, value in checks.items()):
        raise BreakdownError(f"canonical checkpoint {key!r} disagrees with its pinned manifest summary")
    if not isinstance(ledger.get("parts"), list) or len(ledger["parts"]) != checks["part_count"]:
        raise BreakdownError(f"canonical checkpoint {key!r} has an invalid part inventory")
    contract.ledger_cache[(month, batch_index)] = ledger
    return ledger


def product_part_metadata(ledger: Mapping[str, Any], parameter: str) -> list[Mapping[str, Any]]:
    segment = f"/product={parameter}/"
    return [part for part in ledger["parts"] if isinstance(part, Mapping) and segment in f"/{part['relative_path']}"]


def load_raw_part(store: SnapshotStore, metadata: Mapping[str, Any], product: Product) -> list[dict[str, Any]]:
    key = str(metadata["key"])
    size = int(metadata["byte_count"])
    payload = store.get(key, max_bytes=size)
    if payload is None or len(payload) != size or _sha256(payload) != metadata["sha256"]:
        raise BreakdownError(f"canonical soil part {key!r} failed byte reconciliation")
    table = pq.read_table(io.BytesIO(payload))
    if not table.schema.equals(RAW_SCHEMA, check_metadata=False):
        raise BreakdownError(f"canonical soil part {key!r} has the wrong Arrow schema")
    rows = table.to_pylist()
    if len(rows) != int(metadata["row_count"]) or row_set_digest(rows) != metadata["row_digest"]:
        raise BreakdownError(f"canonical soil part {key!r} failed physical-row reconciliation")
    for row in rows:
        if row["source_parameter"] != product.parameter or row["product_key"] != product.parameter:
            raise BreakdownError(f"canonical product partition {key!r} contains a row for another product")
        if row["canonical_row_sha256"] != canonical_row_hash(row):
            raise BreakdownError(f"canonical soil part {key!r} contains a row with a bad canonical hash")
    return rows


def lane_root(object_store_prefix: str, output_prefix: str, snapshot_id: str, product: Product) -> str:
    normalized_output = output_prefix.strip("/")
    if normalized_output != DEFAULT_OUTPUT_PREFIX:
        raise BreakdownError(f"soil-wetness output prefix must be {DEFAULT_OUTPUT_PREFIX!r}")
    root = _prefixed(
        object_store_prefix,
        f"{normalized_output}/lane={product.lane}/snapshot={snapshot_id}",
    )
    if "layer=signal" in root.split("/"):
        raise BreakdownError("soil-wetness output must never enter layer=signal")
    return root


def day_directory(root: str, tier: int, day: date) -> str:
    return f"{root}/kind=observed/zoom={tier:02d}/year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"


def verify_receipt(store: SnapshotStore, receipt: Mapping[str, Any], *, schema: pa.Schema | None = None) -> bytes:
    key = receipt.get("key")
    size = receipt.get("byte_count")
    row_count = receipt.get("row_count")
    sha256 = receipt.get("sha256")
    if not isinstance(key, str) or not key:
        raise BreakdownError("output receipt has no valid key")
    if not isinstance(size, int) or not 0 < size <= MAX_OUTPUT_RECEIPT_BYTES:
        raise BreakdownError(f"output receipt {key!r} exceeds its byte bound")
    if not isinstance(row_count, int) or not 0 <= row_count <= MAX_OUTPUT_RECEIPT_ROWS:
        raise BreakdownError(f"output receipt {key!r} exceeds its row bound")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise BreakdownError(f"output receipt {key!r} has an invalid SHA-256")
    payload = store.get_exact(key, expected_bytes=size)
    if payload is None or len(payload) != size or _sha256(payload) != sha256:
        raise BreakdownError(f"output receipt failed byte reconciliation: {key!r}")
    if schema is not None:
        table = pq.read_table(io.BytesIO(payload))
        if not table.schema.equals(schema, check_metadata=False) or table.num_rows != row_count:
            raise BreakdownError(f"output receipt failed schema/row reconciliation: {key!r}")
    return payload


def put_table(
    store: SnapshotStore,
    *,
    key: str,
    rows: Sequence[Mapping[str, Any]],
    schema: pa.Schema,
    sort_columns: Sequence[str],
) -> dict[str, Any]:
    if not rows:
        raise BreakdownError(f"refusing to write empty Parquet object {key!r}")
    payload = serialize_table(rows, schema, sort_columns)
    store.put_immutable(key, payload, content_type=PARQUET_CONTENT_TYPE)
    return table_receipt(key, payload, len(rows))


def product_months(contract: InputContract) -> tuple[str, ...]:
    return tuple(sorted({month for month, _batch in contract.units}))


def month_batch_indexes(contract: InputContract, month: str) -> tuple[int, ...]:
    return tuple(sorted(batch for unit_month, batch in contract.units if unit_month == month))


def _eligibility_reason(
    row: Mapping[str, Any],
    product: Product,
    release: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str | None:
    if row["data_source_key"] != EXPECTED_SOURCE_KEY or source["key"] != EXPECTED_SOURCE_KEY:
        return "wrong_data_source"
    if row["support_key"] != EXPECTED_SUPPORT_KEY:
        return "wrong_support"
    if row["signal_name"] != product.signal_name:
        return "wrong_signal"
    if row["normalized_unit"] != EXPECTED_UNIT:
        return "wrong_normalized_unit"
    if not row["is_observed"]:
        return "not_observed"
    if row["quality_flag"] != "accepted":
        return "quality_not_accepted"
    value = row["normalized_value"]
    if value is None:
        return "normalized_value_null"
    if not math.isfinite(float(value)):
        return "normalized_value_non_finite"
    if float(value) < 0.0 or float(value) > 1.0:
        return "normalized_value_out_of_domain"
    if str(release["data_source_id"]) != str(source["id"]) or str(row["data_source_id"]) != str(source["id"]):
        return "source_dimension_mismatch"
    return None


def classify_month(
    rows: Sequence[Mapping[str, Any]],
    product: Product,
    contract: InputContract,
) -> tuple[list[dict[str, Any]], dict[date, list[dict[str, Any]]], dict[str, Any]]:
    release_by_row: dict[int, Mapping[str, Any]] = {}
    source_by_row: dict[int, Mapping[str, Any]] = {}
    rejection_by_row: dict[int, str | None] = {}
    candidates: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        row_id = int(row["id"])
        release = contract.releases.get(str(row["source_release_id"]))
        if release is None:
            raise BreakdownError(f"soil row {row_id} references a release absent from the frozen dimension")
        source = contract.sources.get(str(release["data_source_id"]))
        if source is None:
            raise BreakdownError(f"soil row {row_id} references a source absent from the frozen dimension")
        release_by_row[row_id] = release
        source_by_row[row_id] = source
        reason = _eligibility_reason(row, product, release, source)
        rejection_by_row[row_id] = reason
        if reason is None:
            grain = (
                row["support_key"],
                row["signal_name"],
                row["normalized_unit"],
                row["cell_id"],
                row["observation_day"],
            )
            candidates[grain].append(row)

    ranks: dict[int, int] = {}
    winner_by_row: dict[int, int] = {}
    winners: list[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]]]] = []
    multiplicities: Counter[int] = Counter()
    for grain_rows in candidates.values():
        ordered = sorted(
            grain_rows,
            key=lambda row: (_as_utc(release_by_row[int(row["id"])]["retrieved_at"]), int(row["id"])),
            reverse=True,
        )
        winner_id = int(ordered[0]["id"])
        multiplicities[len(ordered)] += 1
        for rank, row in enumerate(ordered, start=1):
            ranks[int(row["id"])] = rank
            winner_by_row[int(row["id"])] = winner_id
        winners.append((ordered[0], ordered))

    provenance: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for raw in rows:
        row = dict(raw)
        row_id = int(row["id"])
        release = release_by_row[row_id]
        source = source_by_row[row_id]
        reason = rejection_by_row[row_id]
        if reason is None:
            rank = ranks[row_id]
            disposition = "selected" if rank == 1 else "superseded"
            disposition_reason = "release_precedence_winner" if rank == 1 else "newer_release_or_observation_id"
            selected_id: int | None = winner_by_row[row_id]
        else:
            rejection_counts[reason] += 1
            rank = None
            disposition = "rejected"
            disposition_reason = reason
            selected_id = None
        row.update(
            {
                "lane": product.lane,
                "disposition": disposition,
                "disposition_reason": disposition_reason,
                "precedence_rank": rank,
                "selected_observation_id": selected_id,
                "release_retrieved_at": release["retrieved_at"],
                "release_source_version": release["source_version"],
                "release_payload_checksum": release["payload_checksum"],
                "release_transform_version": release["transform_version"],
                "release_license_snapshot": release["license_snapshot"],
                "source_allowed_client_exposure": source["allowed_client_exposure"],
                "input_manifest_sha256": contract.manifest_sha256,
            }
        )
        provenance.append(row)

    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for winner, grain_rows in winners:
        winner_id = int(winner["id"])
        release = release_by_row[winner_id]
        source = source_by_row[winner_id]
        candidate_hashes = [str(row["canonical_row_sha256"]) for row in grain_rows]
        output = {
            "support_key": winner["support_key"],
            "signal_name": winner["signal_name"],
            "normalized_unit": winner["normalized_unit"],
            "cell_id": winner["cell_id"],
            "observed_day": winner["observation_day"],
            "normalized_value": winner["normalized_value"],
            "observation_count": len(grain_rows),
            "newest_observed_at": max(_as_utc(row["observed_at"]) for row in grain_rows),
            "coverage_fraction": winner["coverage_fraction"],
            "allowed_client_exposure": source["allowed_client_exposure"],
            "cell_longitude": winner["cell_centroid_longitude"],
            "cell_latitude": winner["cell_centroid_latitude"],
            "selected_observation_id": winner_id,
            "selected_canonical_row_sha256": winner["canonical_row_sha256"],
            "selected_source_release_id": winner["source_release_id"],
            "selected_release_retrieved_at": release["retrieved_at"],
            "physical_candidate_count": len(grain_rows),
            "lineage_sha256": lineage_digest(candidate_hashes),
            "input_manifest_sha256": contract.manifest_sha256,
        }
        by_day[winner["observation_day"]].append(output)

    selected_count = len(winners)
    eligible_count = sum(len(value) for value in candidates.values())
    rejected_count = sum(rejection_counts.values())
    if len(rows) != eligible_count + rejected_count or eligible_count != selected_count + (
        eligible_count - selected_count
    ):
        raise AssertionError("soil month classification did not close")
    stats = {
        "input_physical_rows": len(rows),
        "eligible_rows": eligible_count,
        "selected_rows": selected_count,
        "superseded_rows": eligible_count - selected_count,
        "rejected_rows": rejected_count,
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "duplicate_group_count": sum(count for multiplicity, count in multiplicities.items() if multiplicity > 1),
        "max_multiplicity": max(multiplicities, default=0),
        "multiplicity_histogram": {str(key): value for key, value in sorted(multiplicities.items())},
        "input_row_digest": row_set_digest(rows),
        "selected_lineage_digest": lineage_digest(
            str(winner["canonical_row_sha256"]) for winner, _grain_rows in winners
        ),
    }
    return provenance, dict(by_day), stats


def month_checkpoint_key(root: str, month: str) -> str:
    return f"{root}/_checkpoints/base/year={month[:4]}/month={month[5:]}.json"


def tier_checkpoint_key(root: str, month: str) -> str:
    return f"{root}/_checkpoints/tiers/year={month[:4]}/month={month[5:]}.json"


def verification_marker_key(root: str, phase: str, month: str) -> str:
    return f"{root}/_verification/phase={phase}/year={month[:4]}/month={month[5:]}.json"


def checkpoint_verification_marker(
    checkpoint: Mapping[str, Any],
    *,
    key: str,
    phase: str,
    product: Product,
    contract: InputContract,
) -> dict[str, Any]:
    objects = checkpoint.get("output_objects")
    if not isinstance(objects, list) or any(not isinstance(receipt, Mapping) for receipt in objects):
        raise BreakdownError("breakdown checkpoint has an invalid output receipt inventory")
    month = str(checkpoint["observation_month"])
    checkpoint_key = (
        month_checkpoint_key(lane_root_from_marker(key), month)
        if phase == "base"
        else tier_checkpoint_key(lane_root_from_marker(key), month)
    )
    checkpoint_payload = _json_bytes(checkpoint)
    receipt_lines = [
        f"{receipt['key']}:{receipt['row_count']}:{receipt['byte_count']}:{receipt['sha256']}" for receipt in objects
    ]
    return {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "phase": phase,
        "observation_month": checkpoint["observation_month"],
        "checkpoint_key": checkpoint_key,
        "checkpoint_byte_count": len(checkpoint_payload),
        "checkpoint_sha256": _sha256(checkpoint_payload),
        "output_object_count": len(objects),
        "output_row_count": sum(int(receipt["row_count"]) for receipt in objects),
        "output_byte_count": sum(int(receipt["byte_count"]) for receipt in objects),
        "output_receipt_digest": lineage_digest(receipt_lines),
        "input_manifest_sha256": contract.manifest_sha256,
        "marker_key": key,
        "verified_at": contract.manifest["verified_at"],
    }


def lane_root_from_marker(marker_key: str) -> str:
    separator = "/_verification/"
    if separator not in marker_key:
        raise BreakdownError(f"verification marker key has no lane root: {marker_key!r}")
    return marker_key.split(separator, 1)[0]


def verify_checkpoint_once(
    store: SnapshotStore,
    checkpoint: Mapping[str, Any],
    *,
    root: str,
    phase: str,
    product: Product,
    contract: InputContract,
    verify_workers: int,
) -> dict[str, Any]:
    month = str(checkpoint["observation_month"])
    key = verification_marker_key(root, phase, month)
    expected_payload = _json_bytes(
        checkpoint_verification_marker(
            checkpoint,
            key=key,
            phase=phase,
            product=product,
            contract=contract,
        )
    )
    existing = store.get(key, max_bytes=len(expected_payload) + 1)
    if existing is not None:
        if existing != expected_payload:
            raise BreakdownError(f"verification marker {key!r} conflicts with its checkpoint receipts")
        return marker_receipt(
            key,
            expected_payload,
            int(
                checkpoint_verification_marker(
                    checkpoint,
                    key=key,
                    phase=phase,
                    product=product,
                    contract=contract,
                )["output_row_count"]
            ),
        )
    validate_checkpoint_objects(store, checkpoint, verify_workers=verify_workers)
    store.put_immutable(key, expected_payload, content_type=JSON_CONTENT_TYPE)
    marker = checkpoint_verification_marker(
        checkpoint,
        key=key,
        phase=phase,
        product=product,
        contract=contract,
    )
    return marker_receipt(key, expected_payload, int(marker["output_row_count"]))


def validate_checkpoint_objects(
    store: SnapshotStore,
    checkpoint: Mapping[str, Any],
    *,
    verify_workers: int = DEFAULT_VERIFY_WORKERS,
) -> None:
    objects = checkpoint.get("output_objects")
    if not isinstance(objects, list):
        raise BreakdownError("breakdown checkpoint has no output_objects list")
    lane = checkpoint.get("lane")
    snapshot_id = checkpoint.get("input_snapshot_id")
    if not isinstance(lane, str) or not isinstance(snapshot_id, str):
        raise BreakdownError("breakdown checkpoint has no lane/snapshot identity")
    expected_fragment = f"/lane={lane}/snapshot={snapshot_id}/"
    seen_keys: set[str] = set()

    for receipt in objects:
        if not isinstance(receipt, Mapping):
            raise BreakdownError("breakdown checkpoint contains an invalid output receipt")
        key = receipt.get("key")
        sha256 = receipt.get("sha256")
        row_count = receipt.get("row_count")
        byte_count = receipt.get("byte_count")
        if not isinstance(key, str) or expected_fragment not in f"/{key}" or key in seen_keys:
            raise BreakdownError("breakdown checkpoint contains an invalid or duplicate output key")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            raise BreakdownError(f"output receipt {key!r} has an invalid SHA-256")
        if not isinstance(row_count, int) or not 0 <= row_count <= MAX_OUTPUT_RECEIPT_ROWS:
            raise BreakdownError(f"output receipt {key!r} exceeds its row bound")
        if not isinstance(byte_count, int) or not 0 < byte_count <= MAX_OUTPUT_RECEIPT_BYTES:
            raise BreakdownError(f"output receipt {key!r} exceeds its byte bound")
        seen_keys.add(key)

    def verify(receipt: object) -> None:
        if not isinstance(receipt, Mapping):
            raise BreakdownError("breakdown checkpoint contains an invalid output receipt")
        key = str(receipt["key"])
        schema = PROVENANCE_SCHEMA if "/_provenance/" in key else LANE_SCHEMA if key.endswith(".parquet") else None
        verify_receipt(store, receipt, schema=schema)

    with ThreadPoolExecutor(max_workers=verify_workers, thread_name_prefix="soil-receipt") as executor:
        tuple(executor.map(verify, objects))


def build_base_month(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: Product,
    month: str,
) -> dict[str, Any]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    checkpoint_key = month_checkpoint_key(root, month)
    existing_payload = store.get(checkpoint_key, max_bytes=2_000_000)
    if existing_payload is not None:
        existing = _json_object(existing_payload, key=checkpoint_key)
        if existing_payload != _json_bytes(existing):
            raise BreakdownError(f"base checkpoint {checkpoint_key!r} is not canonical JSON")
        if (
            existing.get("contract_version") != CONTRACT_VERSION
            or existing.get("lane") != product.lane
            or existing.get("product_parameter") != product.parameter
            or existing.get("observation_month") != month
            or existing.get("input_manifest_sha256") != contract.manifest_sha256
        ):
            raise BreakdownError(f"base checkpoint {checkpoint_key!r} belongs to another input or lane")
        return existing

    input_parts: list[Mapping[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    for batch_index in month_batch_indexes(contract, month):
        ledger = ledger_for_unit(store, contract, month, batch_index)
        for metadata in product_part_metadata(ledger, product.parameter):
            input_parts.append(metadata)
            raw_rows.extend(load_raw_part(store, metadata, product))
    provenance, base_by_day, stats = classify_month(raw_rows, product, contract)
    if stats["rejected_rows"]:
        raise BreakdownError(
            f"{product.lane} {month} has {stats['rejected_rows']} off-contract physical rows: "
            f"{stats['rejection_counts']}"
        )
    output_objects: list[dict[str, Any]] = []
    if provenance:
        provenance_key = f"{root}/_provenance/year={month[:4]}/month={month[5:]}/part-0.parquet"
        output_objects.append(
            put_table(
                store,
                key=provenance_key,
                rows=provenance,
                schema=PROVENANCE_SCHEMA,
                sort_columns=("id",),
            )
        )
    day_receipts: list[dict[str, Any]] = []
    for day, day_rows in sorted(base_by_day.items()):
        key = f"{day_directory(root, 13, day)}/part-0.parquet"
        receipt = put_table(store, key=key, rows=day_rows, schema=LANE_SCHEMA, sort_columns=LANE_SORT_COLUMNS)
        day_receipts.append({"day": day.isoformat(), **receipt})
        output_objects.append(receipt)
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "signal_name": product.signal_name,
        "normalized_unit": EXPECTED_UNIT,
        "observation_month": month,
        "input_snapshot_id": snapshot_id,
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "input_parts": [
            {
                "key": part["key"],
                "row_count": int(part["row_count"]),
                "byte_count": int(part["byte_count"]),
                "sha256": part["sha256"],
                "row_digest": part["row_digest"],
            }
            for part in input_parts
        ],
        **stats,
        "data_days": [receipt["day"] for receipt in day_receipts],
        "day_parts": day_receipts,
        "output_objects": output_objects,
    }
    store.put_immutable(checkpoint_key, _json_bytes(checkpoint), content_type=JSON_CONTENT_TYPE)
    return checkpoint


def derive_tier_rows(base_rows: Sequence[Mapping[str, Any]], tier: int) -> list[dict[str, Any]]:
    resolution = ZOOM_RESOLUTIONS[tier]
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in base_rows:
        longitude = math.floor(float(row["cell_longitude"]) / resolution) * resolution
        latitude = math.floor(float(row["cell_latitude"]) / resolution) * resolution
        if longitude == 0.0:
            longitude = 0.0
        if latitude == 0.0:
            latitude = 0.0
        key = (
            row["support_key"],
            row["signal_name"],
            row["normalized_unit"],
            row["observed_day"],
            longitude,
            latitude,
        )
        groups[key].append(row)
    result: list[dict[str, Any]] = []
    for key, rows in groups.items():
        support_key, signal_name, normalized_unit, observed_day, longitude, latitude = key
        coverages = [float(row["coverage_fraction"]) for row in rows if row["coverage_fraction"] is not None]
        exposure_values = [
            bool(row["allowed_client_exposure"]) for row in rows if row["allowed_client_exposure"] is not None
        ]
        result.append(
            {
                "support_key": support_key,
                "signal_name": signal_name,
                "normalized_unit": normalized_unit,
                "cell_id": None,
                "observed_day": observed_day,
                "normalized_value": sum(float(row["normalized_value"]) for row in rows) / len(rows),
                "observation_count": sum(int(row["observation_count"]) for row in rows),
                "newest_observed_at": max(_as_utc(row["newest_observed_at"]) for row in rows),
                "coverage_fraction": sum(coverages) / len(coverages) if coverages else None,
                "allowed_client_exposure": all(exposure_values) if exposure_values else None,
                "cell_longitude": longitude,
                "cell_latitude": latitude,
                "selected_observation_id": None,
                "selected_canonical_row_sha256": None,
                "selected_source_release_id": None,
                "selected_release_retrieved_at": None,
                "physical_candidate_count": sum(int(row["physical_candidate_count"]) for row in rows),
                "lineage_sha256": lineage_digest(str(row["lineage_sha256"]) for row in rows),
                "input_manifest_sha256": str(rows[0]["input_manifest_sha256"]),
            }
        )
    return result


def marker_receipt(key: str, payload: bytes, row_count: int) -> dict[str, Any]:
    return {"key": key, "row_count": row_count, "byte_count": len(payload), "sha256": _sha256(payload)}


def write_day_marker(
    store: SnapshotStore,
    *,
    root: str,
    product: Product,
    day: date,
    tier: int,
    part_receipt: Mapping[str, Any],
    input_manifest_sha256: str,
    base_lineage_sha256: str,
) -> dict[str, Any]:
    key = f"{day_directory(root, tier, day)}/_complete.json"
    marker = {
        "contract_version": CONTRACT_VERSION,
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
    payload = _json_bytes(marker)
    store.put_immutable(key, payload, content_type=JSON_CONTENT_TYPE)
    return marker_receipt(key, payload, int(part_receipt["row_count"]))


def load_lane_table(store: SnapshotStore, receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = verify_receipt(store, receipt, schema=LANE_SCHEMA)
    return pq.read_table(io.BytesIO(payload)).to_pylist()


def build_tier_month(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: Product,
    month: str,
    base_checkpoint: Mapping[str, Any],
) -> dict[str, Any]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    checkpoint_key = tier_checkpoint_key(root, month)
    existing_payload = store.get(checkpoint_key, max_bytes=4_000_000)
    if existing_payload is not None:
        existing = _json_object(existing_payload, key=checkpoint_key)
        if existing_payload != _json_bytes(existing):
            raise BreakdownError(f"tier checkpoint {checkpoint_key!r} is not canonical JSON")
        if (
            existing.get("contract_version") != CONTRACT_VERSION
            or existing.get("lane") != product.lane
            or existing.get("product_parameter") != product.parameter
            or existing.get("observation_month") != month
            or existing.get("input_manifest_sha256") != contract.manifest_sha256
        ):
            raise BreakdownError(f"tier checkpoint {checkpoint_key!r} belongs to another input or lane")
        return existing

    output_objects: list[dict[str, Any]] = []
    days: list[dict[str, Any]] = []
    day_parts = base_checkpoint.get("day_parts")
    if not isinstance(day_parts, list):
        raise BreakdownError("base checkpoint has no day_parts inventory")
    for base_receipt in day_parts:
        if not isinstance(base_receipt, Mapping):
            raise BreakdownError("base checkpoint contains an invalid day receipt")
        day = date.fromisoformat(str(base_receipt["day"]))
        base_rows = load_lane_table(store, base_receipt)
        if not base_rows:
            raise BreakdownError(f"base part for {product.lane} {day} is empty")
        base_grain = {
            (row["support_key"], row["signal_name"], row["normalized_unit"], row["cell_id"], row["observed_day"])
            for row in base_rows
        }
        if len(base_grain) != len(base_rows):
            raise BreakdownError(f"base part for {product.lane} {day} violates its unique grain")
        if any(
            row["signal_name"] != product.signal_name
            or row["normalized_unit"] != EXPECTED_UNIT
            or row["support_key"] != EXPECTED_SUPPORT_KEY
            or row["observed_day"] != day
            or row["input_manifest_sha256"] != contract.manifest_sha256
            for row in base_rows
        ):
            raise BreakdownError(f"base part for {product.lane} {day} contains another product or input")
        base_lineage = lineage_digest(str(row["lineage_sha256"]) for row in base_rows)
        tier_receipts: dict[str, Mapping[str, Any]] = {"13": base_receipt}
        for tier in (9, 5, 0):
            derived_rows = derive_tier_rows(base_rows, tier)
            key = f"{day_directory(root, tier, day)}/part-0.parquet"
            receipt = put_table(store, key=key, rows=derived_rows, schema=LANE_SCHEMA, sort_columns=LANE_SORT_COLUMNS)
            tier_receipts[str(tier)] = receipt
            output_objects.append(receipt)
            marker = write_day_marker(
                store,
                root=root,
                product=product,
                day=day,
                tier=tier,
                part_receipt=receipt,
                input_manifest_sha256=contract.manifest_sha256,
                base_lineage_sha256=base_lineage,
            )
            output_objects.append(marker)
        base_marker = write_day_marker(
            store,
            root=root,
            product=product,
            day=day,
            tier=13,
            part_receipt=base_receipt,
            input_manifest_sha256=contract.manifest_sha256,
            base_lineage_sha256=base_lineage,
        )
        output_objects.append(base_marker)
        days.append(
            {
                "day": day.isoformat(),
                "base_lineage_sha256": base_lineage,
                "tiers": {tier: dict(receipt) for tier, receipt in tier_receipts.items()},
            }
        )
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "observation_month": month,
        "input_snapshot_id": snapshot_id,
        "input_manifest_sha256": contract.manifest_sha256,
        "base_checkpoint_key": month_checkpoint_key(root, month),
        "days": days,
        "output_objects": output_objects,
    }
    store.put_immutable(checkpoint_key, _json_bytes(checkpoint), content_type=JSON_CONTENT_TYPE)
    return checkpoint


def expected_input_parts(
    store: SnapshotStore,
    contract: InputContract,
    product: Product,
) -> dict[str, Mapping[str, Any]]:
    expected: dict[str, Mapping[str, Any]] = {}
    for month in product_months(contract):
        for batch_index in month_batch_indexes(contract, month):
            ledger = ledger_for_unit(store, contract, month, batch_index)
            for metadata in product_part_metadata(ledger, product.parameter):
                key = str(metadata["key"])
                if key in expected:
                    raise BreakdownError(f"canonical part appears in two bounded ledgers: {key!r}")
                expected[key] = metadata
    return expected


def finalize_lane(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: Product,
    base_checkpoints: Sequence[Mapping[str, Any]],
    tier_checkpoints: Sequence[Mapping[str, Any]],
    verify_workers: int,
    progress: Callable[[str], None],
) -> dict[str, Any]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    expected_raw = expected_input_parts(store, contract, product)
    consumed_raw: dict[str, Mapping[str, Any]] = {}
    expected_output_keys: set[str] = set()
    aggregate = Counter()
    all_days: list[str] = []
    input_month_digests: list[str] = []
    selected_month_digests: list[str] = []
    multiplicity_histogram: Counter[int] = Counter()
    verification_receipts: list[dict[str, Any]] = []
    for index, checkpoint in enumerate(base_checkpoints, start=1):
        verification_receipt = verify_checkpoint_once(
            store,
            checkpoint,
            root=root,
            phase="base",
            product=product,
            contract=contract,
            verify_workers=verify_workers,
        )
        verification_receipts.append(verification_receipt)
        expected_output_keys.add(str(verification_receipt["key"]))
        expected_output_keys.add(month_checkpoint_key(root, str(checkpoint["observation_month"])))
        for receipt in checkpoint["output_objects"]:
            expected_output_keys.add(str(receipt["key"]))
        for metadata in checkpoint["input_parts"]:
            key = str(metadata["key"])
            if key in consumed_raw:
                raise BreakdownError(f"lane consumed canonical part twice: {key!r}")
            consumed_raw[key] = metadata
        for name in (
            "input_physical_rows",
            "eligible_rows",
            "selected_rows",
            "superseded_rows",
            "rejected_rows",
            "duplicate_group_count",
        ):
            aggregate[name] += int(checkpoint[name])
        for multiplicity, count in checkpoint["multiplicity_histogram"].items():
            multiplicity_histogram[int(multiplicity)] += int(count)
        input_month_digests.append(str(checkpoint["input_row_digest"]))
        selected_month_digests.append(str(checkpoint["selected_lineage_digest"]))
        all_days.extend(str(value) for value in checkpoint["data_days"])
        progress(
            f"lane={product.lane} phase=verify-base checkpoint={index}/{len(base_checkpoints)} "
            f"month={checkpoint['observation_month']}"
        )
    if set(consumed_raw) != set(expected_raw):
        raise BreakdownError(
            f"{product.lane} raw part reconciliation failed; "
            f"missing={sorted(set(expected_raw) - set(consumed_raw))[:5]} "
            f"unexpected={sorted(set(consumed_raw) - set(expected_raw))[:5]}"
        )
    for key, expected in expected_raw.items():
        consumed = consumed_raw[key]
        if any(consumed[field] != expected[field] for field in ("row_count", "byte_count", "sha256", "row_digest")):
            raise BreakdownError(f"{product.lane} checkpoint changed canonical receipt {key!r}")
    if aggregate["input_physical_rows"] != aggregate["eligible_rows"] + aggregate["rejected_rows"]:
        raise BreakdownError(f"{product.lane} did not classify every physical input row exactly once")
    if aggregate["eligible_rows"] != aggregate["selected_rows"] + aggregate["superseded_rows"]:
        raise BreakdownError(f"{product.lane} precedence reconciliation failed")
    if aggregate["rejected_rows"]:
        raise BreakdownError(f"{product.lane} has rejected rows and cannot be published")
    if len(all_days) != len(set(all_days)):
        raise BreakdownError(f"{product.lane} base checkpoints overlap day partitions")

    tier_totals: dict[str, dict[str, int]] = {
        str(tier): {"row_count": 0, "part_count": 0, "byte_count": 0, "marker_count": 0} for tier in ZOOM_TIERS
    }
    tier_days: set[str] = set()
    for index, checkpoint in enumerate(tier_checkpoints, start=1):
        verification_receipt = verify_checkpoint_once(
            store,
            checkpoint,
            root=root,
            phase="tiers",
            product=product,
            contract=contract,
            verify_workers=verify_workers,
        )
        verification_receipts.append(verification_receipt)
        expected_output_keys.add(str(verification_receipt["key"]))
        expected_output_keys.add(tier_checkpoint_key(root, str(checkpoint["observation_month"])))
        for receipt in checkpoint["output_objects"]:
            expected_output_keys.add(str(receipt["key"]))
        for day_report in checkpoint["days"]:
            day_text = str(day_report["day"])
            if day_text in tier_days:
                raise BreakdownError(f"{product.lane} tier checkpoints overlap {day_text}")
            tier_days.add(day_text)
            for tier, receipt in day_report["tiers"].items():
                target = tier_totals[str(tier)]
                target["row_count"] += int(receipt["row_count"])
                target["part_count"] += 1
                target["byte_count"] += int(receipt["byte_count"])
            for tier in ZOOM_TIERS:
                tier_totals[str(tier)]["marker_count"] += 1
        progress(
            f"lane={product.lane} phase=verify-tiers checkpoint={index}/{len(tier_checkpoints)} "
            f"month={checkpoint['observation_month']}"
        )
    if tier_days != set(all_days):
        raise BreakdownError(f"{product.lane} tier days do not equal its z13 data days")
    if tier_totals["13"]["row_count"] != aggregate["selected_rows"]:
        raise BreakdownError(f"{product.lane} z13 row count does not equal selected winners")

    provenance_rows = 0
    provenance_bytes = 0
    for checkpoint in base_checkpoints:
        for receipt in checkpoint["output_objects"]:
            if "/_provenance/" in str(receipt["key"]):
                provenance_rows += int(receipt["row_count"])
                provenance_bytes += int(receipt["byte_count"])
    if provenance_rows != aggregate["input_physical_rows"]:
        raise BreakdownError(f"{product.lane} provenance does not preserve every physical row")

    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    actual_before = set(store.list_keys(f"{root}/")) - {manifest_key, completion_key}
    if actual_before != expected_output_keys:
        raise BreakdownError(
            f"{product.lane} output inventory mismatch before publication; "
            f"missing={sorted(expected_output_keys - actual_before)[:5]} "
            f"unexpected={sorted(actual_before - expected_output_keys)[:5]}"
        )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "signal_name": product.signal_name,
        "source_key": EXPECTED_SOURCE_KEY,
        "support_key": EXPECTED_SUPPORT_KEY,
        "normalized_unit": EXPECTED_UNIT,
        "lane_prefix": f"{root}/",
        "input_snapshot_id": snapshot_id,
        "input_snapshot_prefix": f"{contract.root}/",
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": aggregate["input_physical_rows"],
        "eligible_rows": aggregate["eligible_rows"],
        "selected_rows": aggregate["selected_rows"],
        "superseded_rows": aggregate["superseded_rows"],
        "rejected_rows": aggregate["rejected_rows"],
        "duplicate_group_count": aggregate["duplicate_group_count"],
        "max_multiplicity": max(multiplicity_histogram, default=0),
        "multiplicity_histogram": {str(key): value for key, value in sorted(multiplicity_histogram.items())},
        "input_part_count": len(expected_raw),
        "input_byte_count": sum(int(value["byte_count"]) for value in expected_raw.values()),
        "provenance_row_count": provenance_rows,
        "provenance_byte_count": provenance_bytes,
        "data_day_count": len(all_days),
        "observation_day_min": min(all_days) if all_days else None,
        "observation_day_max": max(all_days) if all_days else None,
        "input_month_digest": lineage_digest(input_month_digests),
        "selected_month_digest": lineage_digest(selected_month_digests),
        "provenance_schema": schema_manifest(PROVENANCE_SCHEMA),
        "lane_schema": schema_manifest(LANE_SCHEMA),
        "tiers": tier_totals,
        "checkpoint_count": len(base_checkpoints) + len(tier_checkpoints),
        "verification_marker_count": len(verification_receipts),
        "verification_marker_digest": lineage_digest(
            f"{receipt['key']}:{receipt['byte_count']}:{receipt['sha256']}" for receipt in verification_receipts
        ),
        "object_count_before_manifest": len(expected_output_keys),
        "verified_at": contract.manifest["verified_at"],
        "reconciliation": {
            "physical_equals_eligible_plus_rejected": True,
            "eligible_equals_selected_plus_superseded": True,
            "provenance_equals_physical": True,
            "z13_equals_selected": True,
            "all_data_days_have_tiers": list(ZOOM_TIERS),
        },
    }
    manifest_payload = _json_bytes(manifest)
    store.put_immutable(manifest_key, manifest_payload, content_type=JSON_CONTENT_TYPE)
    completion = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": manifest["physical_scope_rows"],
        "selected_rows": manifest["selected_rows"],
        "data_day_count": manifest["data_day_count"],
        "completed_at": manifest["verified_at"],
    }
    completion_payload = _json_bytes(completion)
    store.put_immutable(completion_key, completion_payload, content_type=JSON_CONTENT_TYPE)
    expected_final = expected_output_keys | {manifest_key, completion_key}
    actual_final = set(store.list_keys(f"{root}/"))
    if actual_final != expected_final:
        raise BreakdownError(f"{product.lane} final object inventory changed during publication")
    return {
        **manifest,
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "_complete_key": completion_key,
    }


def inventory_products(store: SnapshotStore, contract: InputContract) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    union_keys: set[str] = set()
    for product in PRODUCTS:
        parts: dict[str, Mapping[str, Any]] = {}
        months: set[str] = set()
        sources: set[str] = set()
        supports: set[str] = set()
        for month in product_months(contract):
            for batch_index in month_batch_indexes(contract, month):
                ledger = ledger_for_unit(store, contract, month, batch_index)
                for metadata in product_part_metadata(ledger, product.parameter):
                    key = str(metadata["key"])
                    if key in parts or key in union_keys:
                        raise BreakdownError(f"soil product part inventory overlaps at {key!r}")
                    parts[key] = metadata
                    union_keys.add(key)
                    months.add(month)
                    relative = str(metadata["relative_path"])
                    for segment in relative.split("/"):
                        if segment.startswith("source="):
                            sources.add(segment.removeprefix("source="))
                        elif segment.startswith("support="):
                            supports.add(segment.removeprefix("support="))
        reports[product.lane] = {
            "product_parameter": product.parameter,
            "signal_name": product.signal_name,
            "part_count": len(parts),
            "row_count": sum(int(value["row_count"]) for value in parts.values()),
            "byte_count": sum(int(value["byte_count"]) for value in parts.values()),
            "first_month": min(months) if months else None,
            "last_month": max(months) if months else None,
            "sources": sorted(sources),
            "supports": sorted(supports),
        }
    return {
        "input_snapshot_id": contract.manifest["snapshot_id"],
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "input_fact_rows": contract.manifest["row_count"],
        "input_fact_parts": contract.manifest["partition_count"],
        "bounded_ledger_count": len(contract.units),
        "products": reports,
        "soil_physical_rows": sum(int(report["row_count"]) for report in reports.values()),
        "soil_input_part_count": len(union_keys),
        "non_overlapping_product_filters": True,
    }


def finalize_bundle(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    lanes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if output_prefix.strip("/") != DEFAULT_OUTPUT_PREFIX:
        raise BreakdownError(f"soil-wetness output prefix must be {DEFAULT_OUTPUT_PREFIX!r}")
    root = _prefixed(
        object_store_prefix,
        f"{output_prefix.strip('/')}/_manifests/soil-wetness/snapshot={snapshot_id}",
    )
    if "layer=signal" in root.split("/"):
        raise BreakdownError("soil-wetness bundle must never enter layer=signal")
    if len(lanes) != len(PRODUCTS) or {str(lane["lane"]) for lane in lanes} != {product.lane for product in PRODUCTS}:
        raise BreakdownError("soil bundle requires exactly the three non-overlapping product lanes")
    physical_rows = sum(int(lane["physical_scope_rows"]) for lane in lanes)
    eligible_rows = sum(int(lane["eligible_rows"]) for lane in lanes)
    selected_rows = sum(int(lane["selected_rows"]) for lane in lanes)
    superseded_rows = sum(int(lane["superseded_rows"]) for lane in lanes)
    rejected_rows = sum(int(lane["rejected_rows"]) for lane in lanes)
    if physical_rows != eligible_rows + rejected_rows or eligible_rows != selected_rows + superseded_rows:
        raise BreakdownError("three-lane soil bundle does not reconcile to its canonical product population")
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "bundle": "nasa-soil-wetness",
        "bundle_prefix": f"{root}/",
        "input_snapshot_id": snapshot_id,
        "input_snapshot_prefix": f"{contract.root}/",
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "product_parameters": [product.parameter for product in PRODUCTS],
        "physical_scope_rows": physical_rows,
        "eligible_rows": eligible_rows,
        "selected_rows": selected_rows,
        "superseded_rows": superseded_rows,
        "rejected_rows": rejected_rows,
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
            "all_lanes_pin_same_input_manifest": all(
                lane["input_manifest_sha256"] == contract.manifest_sha256 for lane in lanes
            ),
        },
        "verified_at": contract.manifest["verified_at"],
    }
    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    manifest_payload = _json_bytes(manifest)
    store.put_immutable(manifest_key, manifest_payload, content_type=JSON_CONTENT_TYPE)
    completion = {
        "contract_version": CONTRACT_VERSION,
        "bundle": "nasa-soil-wetness",
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": physical_rows,
        "selected_rows": selected_rows,
        "lane_count": len(lanes),
        "completed_at": manifest["verified_at"],
    }
    completion_payload = _json_bytes(completion)
    store.put_immutable(completion_key, completion_payload, content_type=JSON_CONTENT_TYPE)
    actual = set(store.list_keys(f"{root}/"))
    if actual != {manifest_key, completion_key}:
        raise BreakdownError("soil bundle prefix contains an unexpected object")
    return {
        **manifest,
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "_complete_key": completion_key,
    }


def run_build(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    verify_workers: int,
    progress: Callable[[str], None],
) -> dict[str, Any]:
    lane_manifests: list[dict[str, Any]] = []
    months = product_months(contract)
    for product in PRODUCTS:
        progress(f"lane={product.lane} phase=base months={len(months)}")
        base_checkpoints: list[dict[str, Any]] = []
        for index, month in enumerate(months, start=1):
            checkpoint = build_base_month(
                store,
                contract,
                object_store_prefix=object_store_prefix,
                output_prefix=output_prefix,
                snapshot_id=snapshot_id,
                product=product,
                month=month,
            )
            base_checkpoints.append(checkpoint)
            progress(
                f"lane={product.lane} phase=base checkpoint={index}/{len(months)} month={month} "
                f"physical={checkpoint['input_physical_rows']} selected={checkpoint['selected_rows']} "
                f"superseded={checkpoint['superseded_rows']}"
            )
        progress(f"lane={product.lane} phase=tiers months={len(months)}")
        tier_checkpoints: list[dict[str, Any]] = []
        for index, (month, base_checkpoint) in enumerate(zip(months, base_checkpoints, strict=True), start=1):
            checkpoint = build_tier_month(
                store,
                contract,
                object_store_prefix=object_store_prefix,
                output_prefix=output_prefix,
                snapshot_id=snapshot_id,
                product=product,
                month=month,
                base_checkpoint=base_checkpoint,
            )
            tier_checkpoints.append(checkpoint)
            progress(
                f"lane={product.lane} phase=tiers checkpoint={index}/{len(months)} month={month} "
                f"days={len(checkpoint['days'])}"
            )
        progress(f"lane={product.lane} phase=verify")
        lane_manifest = finalize_lane(
            store,
            contract,
            object_store_prefix=object_store_prefix,
            output_prefix=output_prefix,
            snapshot_id=snapshot_id,
            product=product,
            base_checkpoints=base_checkpoints,
            tier_checkpoints=tier_checkpoints,
            verify_workers=verify_workers,
            progress=progress,
        )
        lane_manifests.append(lane_manifest)
        progress(
            f"lane={product.lane} phase=complete physical={lane_manifest['physical_scope_rows']} "
            f"selected={lane_manifest['selected_rows']} manifest_sha256={lane_manifest['manifest_sha256']}"
        )
    bundle = finalize_bundle(
        store,
        contract,
        object_store_prefix=object_store_prefix,
        output_prefix=output_prefix,
        snapshot_id=snapshot_id,
        lanes=lane_manifests,
    )
    progress(
        f"bundle=nasa-soil-wetness phase=complete physical={bundle['physical_scope_rows']} "
        f"selected={bundle['selected_rows']} manifest_sha256={bundle['manifest_sha256']}"
    )
    return bundle


def settings_from_file(path: Path) -> Settings:
    if not path.is_file():
        raise BreakdownError(f"settings file does not exist: {path}")
    return Settings(_env_file=path)  # type: ignore[call-arg]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("inventory", "build"))
    result.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    result.add_argument("--snapshot-id", default=DEFAULT_SNAPSHOT_ID)
    result.add_argument("--input-prefix", default=DEFAULT_INPUT_PREFIX)
    result.add_argument("--input-manifest-sha256", default=DEFAULT_INPUT_MANIFEST_SHA256)
    result.add_argument("--output-prefix", default=DEFAULT_OUTPUT_PREFIX)
    result.add_argument("--retry-attempts", type=int, default=8)
    result.add_argument("--retry-base-delay", type=float, default=0.5)
    result.add_argument("--verify-workers", type=int, default=DEFAULT_VERIFY_WORKERS)
    result.add_argument("--json", action="store_true")
    return result


def main() -> int:
    arguments = parser().parse_args()
    if arguments.retry_attempts < 1 or arguments.retry_base_delay < 0 or not 1 <= arguments.verify_workers <= 64:
        raise SystemExit("retry attempts must be positive, retry delay nonnegative, and verify workers in [1, 64]")
    configured = settings_from_file(arguments.env_file)
    credentials = configured.require_object_store()
    store = SnapshotStore.from_credentials(
        credentials,
        retry=RetryPolicy(arguments.retry_attempts, arguments.retry_base_delay),
    )
    contract = load_input_contract(
        store,
        object_store_prefix=configured.object_store_prefix,
        input_prefix=arguments.input_prefix,
        snapshot_id=arguments.snapshot_id,
        expected_manifest_sha256=arguments.input_manifest_sha256,
    )

    def progress(message: str) -> None:
        print(message, file=sys.stderr)

    report = (
        inventory_products(store, contract)
        if arguments.command == "inventory"
        else run_build(
            store,
            contract,
            object_store_prefix=configured.object_store_prefix,
            output_prefix=arguments.output_prefix,
            snapshot_id=arguments.snapshot_id,
            verify_workers=arguments.verify_workers,
            progress=progress,
        )
    )
    print(json.dumps(report, indent=2 if arguments.json else None, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
