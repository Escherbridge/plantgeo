"""Export every physical ``agri.signal_observation`` row to immutable canonical Parquet.

Run from ``services/agri-data-service``. Export creates and verifies the snapshot before publishing
``_COMPLETE``; verify is read-only unless ``--finalize`` is given.

    uv run python scripts/canonical_signal_snapshot.py export
    uv run python scripts/canonical_signal_snapshot.py export --snapshot-id 20260826-prod
    uv run python scripts/canonical_signal_snapshot.py verify --snapshot-id 20260826-prod

See ``scripts/AGENTS.md`` for the extraction and atomic-publication contract.
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
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from datetime import time as datetime_time
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import boto3  # type: ignore[import-untyped]
import psycopg2
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from psycopg2.extras import RealDictCursor

SERVICE_ROOT = Path(__file__).resolve().parent.parent
CHECKOUT_ENV_FILE: Final = Path.home() / "Programming" / "plantgeo" / "services" / "agri-data-service" / ".env"
DEFAULT_ENV_FILE: Final = SERVICE_ROOT / ".env" if (SERVICE_ROOT / ".env").is_file() else CHECKOUT_ENV_FILE
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import ObjectStoreCredentials, Settings  # noqa: E402

CONTRACT_VERSION: Final = "agri.signal_observation.raw-canonical.v1"
DEFAULT_RAW_PREFIX: Final = "raw-canonical/signal-observation"
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
DEFAULT_CELL_BATCH_SIZE: Final = 256
DEFAULT_TARGET_ROWS_PER_PART: Final = 250_000
DEFAULT_STATEMENT_TIMEOUT_MS: Final = 120_000
MAX_CELL_BATCH_SIZE: Final = 2_000
MAX_TARGET_ROWS_PER_PART: Final = 2_000_000
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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
        "ThrottlingException",
    }
)


def _utc_timestamp_type() -> pa.TimestampType:
    return pa.timestamp("us", tz="UTC")


CANONICAL_SCHEMA: Final = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("source_release_id", pa.string(), nullable=False),
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("signal_name", pa.string(), nullable=False),
        pa.field("source_parameter", pa.string(), nullable=False),
        pa.field("support_key", pa.string(), nullable=False),
        pa.field("observed_at", _utc_timestamp_type(), nullable=False),
        pa.field("valid_from", _utc_timestamp_type()),
        pa.field("valid_to", _utc_timestamp_type()),
        pa.field("data_available_at", _utc_timestamp_type(), nullable=False),
        pa.field("original_value", pa.float64()),
        pa.field("original_unit", pa.string()),
        pa.field("normalized_value", pa.float64()),
        pa.field("normalized_unit", pa.string()),
        pa.field("quality_flag", pa.string(), nullable=False),
        pa.field("coverage_fraction", pa.float64(), nullable=False),
        pa.field("is_observed", pa.bool_(), nullable=False),
        pa.field("metadata_json", pa.string(), nullable=False),
        pa.field("created_at", _utc_timestamp_type(), nullable=False),
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
    metadata={b"plantgeo_contract": CONTRACT_VERSION.encode("ascii")},
)

DIMENSION_SCHEMAS: Final = {
    "data_source": pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("key", pa.string(), nullable=False),
            pa.field("name", pa.string(), nullable=False),
            pa.field("owner", pa.string(), nullable=False),
            pa.field("purpose", pa.string(), nullable=False),
            pa.field("base_url", pa.string()),
            pa.field("license_name", pa.string(), nullable=False),
            pa.field("license_url", pa.string()),
            pa.field("citation", pa.string(), nullable=False),
            pa.field("refresh_policy", pa.string(), nullable=False),
            pa.field("retention_days", pa.int32()),
            pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
            pa.field("review_state", pa.string(), nullable=False),
            pa.field("review_due_at", _utc_timestamp_type()),
            pa.field("reviewed_at", _utc_timestamp_type()),
            pa.field("reviewed_by", pa.string()),
            pa.field("is_active", pa.bool_(), nullable=False),
            pa.field("configuration", pa.string(), nullable=False),
            pa.field("created_at", _utc_timestamp_type(), nullable=False),
            pa.field("updated_at", _utc_timestamp_type(), nullable=False),
        ],
        metadata={b"plantgeo_dimension": b"agri.data_source.v1"},
    ),
    "source_release": pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("data_source_id", pa.string(), nullable=False),
            pa.field("source_version", pa.string(), nullable=False),
            pa.field("retrieved_at", _utc_timestamp_type(), nullable=False),
            pa.field("data_available_at", _utc_timestamp_type(), nullable=False),
            pa.field("observed_from", _utc_timestamp_type()),
            pa.field("observed_to", _utc_timestamp_type()),
            pa.field("payload_checksum", pa.string(), nullable=False),
            pa.field("payload_bytes", pa.int64()),
            pa.field("schema_version", pa.string(), nullable=False),
            pa.field("license_snapshot", pa.string(), nullable=False),
            pa.field("query_parameters", pa.string(), nullable=False),
            pa.field("quality_summary", pa.string(), nullable=False),
            pa.field("validation_state", pa.string(), nullable=False),
            pa.field("validated_at", _utc_timestamp_type()),
            pa.field("supersedes_release_id", pa.string()),
            pa.field("retraction_reason", pa.string()),
            pa.field("created_at", _utc_timestamp_type(), nullable=False),
            pa.field("transform_version", pa.string(), nullable=False),
        ],
        metadata={b"plantgeo_dimension": b"agri.source_release.v1"},
    ),
    "spatial_cell": pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field("cell_key", pa.string(), nullable=False),
            pa.field("grid_name", pa.string(), nullable=False),
            pa.field("resolution_m", pa.int32(), nullable=False),
            pa.field("geometry", pa.binary(), nullable=False),
            pa.field("centroid", pa.binary(), nullable=False),
            pa.field("parent_cell_id", pa.string()),
            pa.field("coverage_fraction", pa.float64(), nullable=False),
            pa.field("created_at", _utc_timestamp_type(), nullable=False),
        ],
        metadata={b"plantgeo_dimension": b"agri.spatial_cell.v1"},
    ),
}

EXPECTED_SOURCE_FIELDS: Final = {
    "signal_observation": pa.schema(list(CANONICAL_SCHEMA)[:19]),
    **DIMENSION_SCHEMAS,
}

HASHED_COLUMNS: Final = tuple(field.name for field in CANONICAL_SCHEMA if field.name != "canonical_row_sha256")
PARTITION_COLUMNS: Final = (
    "data_source_key",
    "product_key",
    "support_key",
    "observation_year",
    "observation_month",
)
ROW_SORT_COLUMNS: Final = (
    "signal_name",
    "source_parameter",
    "observed_at",
    "cell_id",
    "source_release_id",
    "id",
)

SOURCE_COLUMNS_SQL: Final = """
SELECT table_name, ordinal_position, column_name, data_type, udt_schema, udt_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'agri'
  AND table_name = ANY(%s::text[])
ORDER BY table_name, ordinal_position
"""

DIMENSION_SQL: Final = {
    "data_source": """
        SELECT id::text, key, name, owner, purpose, base_url, license_name, license_url, citation,
               refresh_policy::text AS refresh_policy, retention_days, allowed_client_exposure,
               review_state, review_due_at, reviewed_at, reviewed_by, is_active,
               configuration::text AS configuration, created_at, updated_at
        FROM agri.data_source ORDER BY id
    """,
    "source_release": """
        SELECT id::text, data_source_id::text, source_version, retrieved_at, data_available_at,
               observed_from, observed_to, payload_checksum, payload_bytes, schema_version,
               license_snapshot, query_parameters::text AS query_parameters,
               quality_summary::text AS quality_summary, validation_state, validated_at,
               supersedes_release_id::text, retraction_reason, created_at, transform_version
        FROM agri.source_release ORDER BY id
    """,
    "spatial_cell": """
        SELECT id::text, cell_key, grid_name, resolution_m, ST_AsEWKB(geometry) AS geometry,
               ST_AsEWKB(centroid) AS centroid, parent_cell_id::text, coverage_fraction, created_at
        FROM agri.spatial_cell ORDER BY id
    """,
}

CELL_PAGE_SQL: Final = """
SELECT id::text AS id
FROM agri.spatial_cell
WHERE (%s::uuid IS NULL OR id > %s::uuid)
ORDER BY id
LIMIT %s
"""

CELL_EXTENT_SQL: Final = """
WITH selected(cell_id) AS (SELECT unnest(%s::uuid[]))
SELECT selected.cell_id::text AS cell_id,
       first_observation.observed_at AS first_observed_at,
       last_observation.observed_at AS last_observed_at
FROM selected
LEFT JOIN LATERAL (
    SELECT observed_at
    FROM agri.signal_observation
    WHERE cell_id = selected.cell_id AND id <= %s
    ORDER BY observed_at, signal_name, id
    LIMIT 1
) AS first_observation ON true
LEFT JOIN LATERAL (
    SELECT observed_at
    FROM agri.signal_observation
    WHERE cell_id = selected.cell_id AND id <= %s
    ORDER BY observed_at DESC, signal_name DESC, id DESC
    LIMIT 1
) AS last_observation ON true
ORDER BY selected.cell_id
"""

DAY_ROWS_SQL: Final = """
WITH selected(cell_id) AS (SELECT unnest(%s::uuid[]))
SELECT
    observation.id,
    observation.source_release_id::text,
    observation.cell_id::text,
    observation.signal_name,
    observation.source_parameter,
    observation.support_key,
    observation.observed_at,
    observation.valid_from,
    observation.valid_to,
    observation.data_available_at,
    observation.original_value,
    observation.original_unit,
    observation.normalized_value,
    observation.normalized_unit,
    observation.quality_flag,
    observation.coverage_fraction,
    observation.is_observed,
    observation.metadata_json::text AS metadata_json,
    observation.created_at,
    observation.source_parameter AS product_key,
    cell.cell_key,
    cell.grid_name AS cell_grid_name,
    cell.resolution_m AS cell_resolution_m,
    cell.parent_cell_id::text AS cell_parent_cell_id,
    ST_AsEWKB(cell.centroid) AS cell_centroid_wkb,
    ST_SRID(cell.centroid) AS cell_centroid_srid,
    ST_X(cell.centroid) AS cell_centroid_longitude,
    ST_Y(cell.centroid) AS cell_centroid_latitude,
    source.id::text AS data_source_id,
    source.key AS data_source_key
FROM selected
JOIN agri.signal_observation AS observation ON observation.cell_id = selected.cell_id
JOIN agri.spatial_cell AS cell ON cell.id = observation.cell_id
JOIN agri.source_release AS release ON release.id = observation.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE observation.id <= %s
  AND observation.observed_at >= %s
  AND observation.observed_at < %s
ORDER BY observation.cell_id, observation.observed_at, observation.signal_name, observation.id
"""


class SnapshotError(RuntimeError):
    """Base error for a refused or unverifiable snapshot operation."""


class ImmutableObjectConflictError(SnapshotError):
    """An immutable key already exists with different bytes."""


class SnapshotStore(Protocol):
    """Minimal immutable-object surface used by the exporter and its tests."""

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def list_keys(self, prefix: str) -> Iterator[str]: ...


class SourceReader(Protocol):
    """Bounded PostgreSQL reads needed to create and reconcile one snapshot."""

    def high_watermark(self) -> int | None: ...

    def source_schema(self) -> list[dict[str, Any]]: ...

    def dimension_snapshot(self) -> dict[str, list[dict[str, Any]]]: ...

    def enumerate_cell_ids(self, page_size: int) -> list[str]: ...

    def observation_extent(self, cell_ids: Sequence[str], high_watermark: int) -> tuple[date | None, date | None]: ...

    def rows_for_month(
        self,
        cell_ids: Sequence[str],
        high_watermark: int,
        month_start: date,
    ) -> list[dict[str, Any]]: ...


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _client_error_code(exc: ClientError) -> str | None:
    error = getattr(exc, "response", {}).get("Error", {})
    code = error.get("Code") if isinstance(error, Mapping) else None
    return str(code) if code is not None else None


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionClosedError, EndpointConnectionError, ReadTimeoutError, TimeoutError)):
        return True
    return isinstance(exc, ClientError) and _client_error_code(exc) in _RETRYABLE_CODES


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential retries for transient object-store operations."""

    attempts: int = 8
    base_delay_seconds: float = 0.5

    def run[T](self, operation: Callable[[], T]) -> T:
        last_error: BaseException | None = None
        for attempt in range(self.attempts):
            try:
                return operation()
            except BaseException as exc:
                if not _retryable(exc) or attempt + 1 >= self.attempts:
                    raise
                last_error = exc
                time.sleep(self.base_delay_seconds * (2**attempt))
        raise AssertionError(f"unreachable retry exit: {last_error}")


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(slots=True)
class BotoSnapshotStore:
    """Immutable object writer for the configured S3-compatible bucket."""

    bucket: str
    client: _S3Client
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_credentials(
        cls,
        credentials: ObjectStoreCredentials,
        *,
        retry: RetryPolicy,
    ) -> BotoSnapshotStore:
        client: _S3Client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(retries={"max_attempts": retry.attempts, "mode": "adaptive"}),
        )
        return cls(bucket=credentials.bucket, client=client, retry=retry)

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
            existing = self.get(key)
            if existing != payload:
                raise ImmutableObjectConflictError(
                    f"immutable object {key!r} already exists with sha256={_sha256(existing or b'')}; "
                    f"attempted sha256={_sha256(payload)}"
                ) from exc

    def get(self, key: str) -> bytes | None:
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

        return self.retry.run(load)

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


def _batched[T](values: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise SnapshotError(f"timezone-naive database timestamp cannot enter the canonical snapshot: {value!r}")
    return value.astimezone(UTC)


def _normalize_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    observed_at = raw.get("observed_at")
    if not isinstance(observed_at, datetime):
        raise SnapshotError("source row has no timestamp-valued observed_at")
    observation_day = _as_utc(observed_at).date()
    row: dict[str, Any] = {}
    for column in HASHED_COLUMNS:
        if column == "observation_day":
            value: Any = observation_day
        else:
            if column not in raw:
                raise SnapshotError(f"source query omitted canonical column {column!r}")
            value = raw[column]
        field_type = CANONICAL_SCHEMA.field(column).type
        if value is not None and pa.types.is_timestamp(field_type):
            value = _as_utc(value)
        elif value is not None and pa.types.is_string(field_type) and isinstance(value, UUID):
            value = str(value)
        elif value is not None and pa.types.is_binary(field_type):
            value = bytes(value)
        row[column] = value
    row["canonical_row_sha256"] = canonical_row_hash(row)
    return row


def _normalize_for_schema(raw: Mapping[str, Any], schema: pa.Schema) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for arrow_field in schema:
        if arrow_field.name not in raw:
            raise SnapshotError(f"dimension query omitted physical column {arrow_field.name!r}")
        value = raw[arrow_field.name]
        if value is not None and pa.types.is_timestamp(arrow_field.type):
            value = _as_utc(value)
        elif value is not None and pa.types.is_string(arrow_field.type) and isinstance(value, UUID):
            value = str(value)
        elif value is not None and pa.types.is_binary(arrow_field.type):
            value = bytes(value)
        row[arrow_field.name] = value
    return row


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
    """Hash every source, identity, provenance, and derived partition field with type framing."""
    digest = hashlib.sha256()
    for column in HASHED_COLUMNS:
        name = column.encode("utf-8")
        value = _hash_value(row[column])
        digest.update(struct.pack(">I", len(name)))
        digest.update(name)
        digest.update(struct.pack(">Q", len(value)))
        digest.update(value)
    return digest.hexdigest()


def row_set_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Digest a physical population in primary-key order; counts remain a separate assertion."""
    digest = hashlib.sha256()
    identities = sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows)
    for row_id, row_hash in identities:
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def dimension_row_set_digest(rows: Iterable[Mapping[str, Any]], schema: pa.Schema) -> str:
    digest = hashlib.sha256()
    columns = tuple(field.name for field in schema)
    encoded_rows: list[tuple[str, str]] = []
    for row in rows:
        row_digest = hashlib.sha256()
        for column in columns:
            value = _hash_value(row[column])
            row_digest.update(struct.pack(">Q", len(value)))
            row_digest.update(value)
        encoded_rows.append((str(row["id"]), row_digest.hexdigest()))
    for row_id, row_hash in sorted(encoded_rows):
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def _stats_value(value: Any) -> str | int | float | bool:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def census_for_schema(rows: Iterable[Mapping[str, Any]], schema: pa.Schema) -> dict[str, dict[str, Any]]:
    """Return a typed null/min/max census for every column in one storage contract."""
    result: dict[str, dict[str, Any]] = {
        arrow_field.name: {"null_count": 0, "nan_count": 0, "min": None, "max": None} for arrow_field in schema
    }
    for row in rows:
        for arrow_field in schema:
            value = row[arrow_field.name]
            stats = result[arrow_field.name]
            if value is None:
                stats["null_count"] += 1
                continue
            if isinstance(value, float) and math.isnan(value):
                stats["nan_count"] += 1
                continue
            if isinstance(value, float) and math.isinf(value):
                if value < 0:
                    stats["negative_infinity_count"] = int(stats.get("negative_infinity_count", 0)) + 1
                    stats["min"] = "-Infinity"
                else:
                    stats["positive_infinity_count"] = int(stats.get("positive_infinity_count", 0)) + 1
                    stats["max"] = "Infinity"
                continue
            candidate = _stats_value(value)
            if stats["min"] is None or (stats["min"] != "-Infinity" and candidate < stats["min"]):
                stats["min"] = candidate
            if stats["max"] is None or (stats["max"] != "Infinity" and candidate > stats["max"]):
                stats["max"] = candidate
    return result


def census(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return census_for_schema(rows, CANONICAL_SCHEMA)


def merge_censuses(censuses: Iterable[Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        field.name: {"null_count": 0, "nan_count": 0, "min": None, "max": None} for field in CANONICAL_SCHEMA
    }
    for item in censuses:
        for column, incoming in item.items():
            target = merged[column]
            target["null_count"] += int(incoming["null_count"])
            target["nan_count"] += int(incoming.get("nan_count", 0))
            if incoming.get("negative_infinity_count"):
                target["negative_infinity_count"] = int(target.get("negative_infinity_count", 0)) + int(
                    incoming["negative_infinity_count"]
                )
            if incoming.get("positive_infinity_count"):
                target["positive_infinity_count"] = int(target.get("positive_infinity_count", 0)) + int(
                    incoming["positive_infinity_count"]
                )
            minimum = incoming.get("min")
            maximum = incoming.get("max")
            if minimum == "-Infinity" or (
                minimum is not None
                and target["min"] != "-Infinity"
                and (target["min"] is None or minimum < target["min"])
            ):
                target["min"] = minimum
            if maximum == "Infinity" or (
                maximum is not None
                and target["max"] != "Infinity"
                and (target["max"] is None or maximum > target["max"])
            ):
                target["max"] = maximum
    return merged


def _arrow_schema_manifest(schema: pa.Schema = CANONICAL_SCHEMA) -> list[dict[str, Any]]:
    return [{"name": field.name, "type": str(field.type), "nullable": field.nullable} for field in schema]


def _validate_source_schema(records: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed on added, removed, reordered, or nullability-drifted physical columns."""
    if not records:  # In-memory test doubles may intentionally omit catalog metadata.
        return
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["table_name"]), []).append(record)
    if set(grouped) != set(EXPECTED_SOURCE_FIELDS):
        raise SnapshotError("production source schema contains an unexpected or missing canonical table")
    for table_name, expected_schema in EXPECTED_SOURCE_FIELDS.items():
        actual = grouped[table_name]
        actual_names = [str(record["column_name"]) for record in actual]
        expected_names = list(expected_schema.names)
        if actual_names != expected_names:
            raise SnapshotError(
                f"production source schema for agri.{table_name} does not match the canonical physical columns"
            )
        for record, arrow_field in zip(actual, expected_schema, strict=True):
            if (str(record["is_nullable"]) == "YES") != arrow_field.nullable:
                raise SnapshotError(f"production source nullability for agri.{table_name}.{arrow_field.name} drifted")


def _verify_fact_dimension_closure(
    rows: Iterable[Mapping[str, Any]],
    dimensions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    sources = {str(row["id"]): row for row in dimensions["data_source"]}
    releases = {str(row["id"]): row for row in dimensions["source_release"]}
    cells = {str(row["id"]): row for row in dimensions["spatial_cell"]}
    for release in releases.values():
        if str(release["data_source_id"]) not in sources:
            raise SnapshotError("frozen source_release dimension is not data_source-FK closed")
    for cell in cells.values():
        parent = cell["parent_cell_id"]
        if parent is not None and str(parent) not in cells:
            raise SnapshotError("frozen spatial_cell dimension is not parent-FK closed")
    for row in rows:
        release = releases.get(str(row["source_release_id"]))
        cell = cells.get(str(row["cell_id"]))
        if release is None or cell is None:
            raise SnapshotError("fact row references an identity absent from the frozen companion dimensions")
        source = sources.get(str(release["data_source_id"]))
        if source is None:
            raise SnapshotError("fact release references a source absent from the frozen companion dimensions")
        if (
            str(row["data_source_id"]) != str(source["id"])
            or row["data_source_key"] != source["key"]
            or row["cell_key"] != cell["cell_key"]
            or row["cell_grid_name"] != cell["grid_name"]
            or row["cell_resolution_m"] != cell["resolution_m"]
            or row["cell_parent_cell_id"] != cell["parent_cell_id"]
            or row["cell_centroid_wkb"] != cell["centroid"]
        ):
            raise SnapshotError("fact denormalized identity differs from the frozen companion dimensions")


def _partition_value(value: object) -> str:
    rendered = str(value)
    return quote(rendered, safe="-_.~") if rendered else "__EMPTY__"


def partition_directory(row: Mapping[str, Any]) -> str:
    observation_day = row["observation_day"]
    if not isinstance(observation_day, date):
        raise SnapshotError("observation_day is not a date")
    return "/".join(
        (
            f"source={_partition_value(row['data_source_key'])}",
            f"product={_partition_value(row['product_key'])}",
            f"support={_partition_value(row['support_key'])}",
            f"year={observation_day.year:04d}",
            f"month={observation_day.month:02d}",
        )
    )


def _serialize_rows(rows: Sequence[Mapping[str, Any]]) -> bytes:
    table = pa.Table.from_pylist(list(rows), schema=CANONICAL_SCHEMA)
    table = table.sort_by([(column, "ascending") for column in ROW_SORT_COLUMNS])
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", write_statistics=True, row_group_size=64_000)
    return buffer.getvalue()


def _serialize_dimension(rows: Sequence[Mapping[str, Any]], schema: pa.Schema) -> bytes:
    table = pa.Table.from_pylist(list(rows), schema=schema).sort_by([("id", "ascending")])
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", write_statistics=True, row_group_size=64_000)
    return buffer.getvalue()


def _parse_json_object(payload: bytes, *, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"object {key!r} is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SnapshotError(f"object {key!r} is not a JSON object")
    return parsed


@dataclass(slots=True)
class PostgresSignalSource:
    """Read-only, bounded PostgreSQL access to the raw observation plane."""

    dsn: str
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS

    def _connection(self) -> Any:
        connection = psycopg2.connect(self.dsn, connect_timeout=30)
        connection.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = %s", (self.statement_timeout_ms,))
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        return connection

    def high_watermark(self) -> int | None:
        with self._connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id FROM agri.signal_observation ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            return int(row[0]) if row is not None else None

    def source_schema(self) -> list[dict[str, Any]]:
        tables = ["data_source", "signal_observation", "source_release", "spatial_cell"]
        with self._connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(SOURCE_COLUMNS_SQL, (tables,))
            return [dict(row) for row in cursor.fetchall()]

    def dimension_snapshot(self) -> dict[str, list[dict[str, Any]]]:
        dimensions: dict[str, list[dict[str, Any]]] = {}
        with self._connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            for table, sql in DIMENSION_SQL.items():
                cursor.execute(sql)
                dimensions[table] = [
                    _normalize_for_schema(dict(row), DIMENSION_SCHEMAS[table]) for row in cursor.fetchall()
                ]
        return dimensions

    def enumerate_cell_ids(self, page_size: int) -> list[str]:
        cells: list[str] = []
        last: str | None = None
        while True:
            with self._connection() as connection, connection.cursor() as cursor:
                cursor.execute(CELL_PAGE_SQL, (last, last, page_size))
                page = [str(row[0]) for row in cursor.fetchall()]
            if not page:
                return cells
            cells.extend(page)
            last = page[-1]

    def observation_extent(
        self,
        cell_ids: Sequence[str],
        high_watermark: int,
    ) -> tuple[date | None, date | None]:
        first_day: date | None = None
        last_day: date | None = None
        for cell_batch in _batched(cell_ids, DEFAULT_CELL_BATCH_SIZE):
            with self._connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(CELL_EXTENT_SQL, (list(cell_batch), high_watermark, high_watermark))
                for record in cursor.fetchall():
                    first = record["first_observed_at"]
                    last = record["last_observed_at"]
                    if first is not None:
                        candidate = _as_utc(first).date()
                        first_day = candidate if first_day is None else min(first_day, candidate)
                    if last is not None:
                        candidate = _as_utc(last).date()
                        last_day = candidate if last_day is None else max(last_day, candidate)
        return first_day, last_day

    def rows_for_month(
        self,
        cell_ids: Sequence[str],
        high_watermark: int,
        month_start: date,
    ) -> list[dict[str, Any]]:
        start = datetime.combine(month_start, datetime_time.min, tzinfo=UTC)
        next_month = date(month_start.year + (month_start.month == 12), (month_start.month % 12) + 1, 1)
        end = datetime.combine(next_month, datetime_time.min, tzinfo=UTC)
        result: list[dict[str, Any]] = []
        with self._connection() as connection, connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SET LOCAL enable_seqscan = off")
            cursor.execute(DAY_ROWS_SQL, (list(cell_ids), high_watermark, start, end))
            result.extend(_normalize_row(row) for row in cursor.fetchall())
        return result


def validate_raw_prefix(raw_prefix: str) -> str:
    normalized = raw_prefix.strip().strip("/")
    if not normalized.startswith("raw-canonical/"):
        raise ValueError("raw prefix must start with 'raw-canonical/'")
    if "layer=" in normalized or "\\" in normalized or ".." in normalized:
        raise ValueError("raw prefix must be isolated from serving layouts and contain no traversal")
    return normalized


def validate_snapshot_id(snapshot_id: str) -> str:
    if not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("snapshot id must contain only letters, digits, '.', '_', or '-' (max 128 characters)")
    return snapshot_id


def _new_snapshot_id(high_watermark: int | None) -> str:
    instant = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    watermark = "empty" if high_watermark is None else str(high_watermark)
    return f"{instant}-hwm{watermark}-{uuid4().hex[:8]}"


def _months(first: date | None, last: date | None) -> Iterator[date]:
    if first is None or last is None:
        return
    current = date(first.year, first.month, 1)
    ceiling = date(last.year, last.month, 1)
    while current <= ceiling:
        yield current
        current = date(current.year + (current.month == 12), (current.month % 12) + 1, 1)


@dataclass(slots=True)
class CanonicalSignalExporter:
    """Resumable snapshot coordinator over a source reader and immutable object store."""

    source: SourceReader
    store: SnapshotStore
    object_store_prefix: str = ""
    raw_prefix: str = DEFAULT_RAW_PREFIX
    cell_batch_size: int = DEFAULT_CELL_BATCH_SIZE
    target_rows_per_part: int = DEFAULT_TARGET_ROWS_PER_PART

    def __post_init__(self) -> None:
        self.raw_prefix = validate_raw_prefix(self.raw_prefix)
        self.object_store_prefix = self.object_store_prefix.strip().strip("/")
        if not 1 <= self.cell_batch_size <= MAX_CELL_BATCH_SIZE:
            raise ValueError(f"cell_batch_size must be between 1 and {MAX_CELL_BATCH_SIZE}")
        if not 1 <= self.target_rows_per_part <= MAX_TARGET_ROWS_PER_PART:
            raise ValueError(f"target_rows_per_part must be between 1 and {MAX_TARGET_ROWS_PER_PART}")

    def _root(self, snapshot_id: str) -> str:
        relative = f"{self.raw_prefix}/snapshot={validate_snapshot_id(snapshot_id)}"
        return f"{self.object_store_prefix}/{relative}" if self.object_store_prefix else relative

    def _key(self, snapshot_id: str, relative: str) -> str:
        return f"{self._root(snapshot_id)}/{relative.lstrip('/')}"

    def _load_json(self, key: str) -> dict[str, Any] | None:
        payload = self.store.get(key)
        return None if payload is None else _parse_json_object(payload, key=key)

    def prepare_snapshot(self, snapshot_id: str | None = None) -> dict[str, Any]:
        if snapshot_id is not None:
            existing = self._load_json(self._key(snapshot_id, "_SNAPSHOT.json"))
            if existing is not None:
                self._validate_snapshot(existing, snapshot_id)
                return existing
        high_watermark = self.source.high_watermark()
        resolved_id = validate_snapshot_id(snapshot_id or _new_snapshot_id(high_watermark))
        source_schema = self.source.source_schema()
        _validate_source_schema(source_schema)
        dimensions = self.source.dimension_snapshot()
        _verify_fact_dimension_closure((), dimensions)
        dimension_objects: dict[str, dict[str, Any]] = {}
        for table_name, rows in dimensions.items():
            schema = DIMENSION_SCHEMAS[table_name]
            payload = _serialize_dimension(rows, schema)
            key = self._key(resolved_id, f"_dimensions/{table_name}.parquet")
            self.store.put_immutable(key, payload, content_type=PARQUET_CONTENT_TYPE)
            dimension_objects[table_name] = {
                "key": key,
                "row_count": len(rows),
                "byte_count": len(payload),
                "sha256": _sha256(payload),
                "row_digest": dimension_row_set_digest(rows, schema),
                "schema": _arrow_schema_manifest(schema),
                "census": census_for_schema(rows, schema),
            }
        dimensions_payload = _json_bytes({"objects": dimension_objects})
        cells = self.source.enumerate_cell_ids(self.cell_batch_size)
        first_day, last_day = (
            self.source.observation_extent(cells, high_watermark) if high_watermark is not None else (None, None)
        )
        cells_payload = _json_bytes({"cell_ids": cells})
        snapshot = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": resolved_id,
            "snapshot_prefix": self._root(resolved_id) + "/",
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source_relation": "agri.signal_observation",
            "high_watermark_id": high_watermark,
            "observation_day_min": first_day.isoformat() if first_day else None,
            "observation_day_max": last_day.isoformat() if last_day else None,
            "cell_count": len(cells),
            "cells_sha256": _sha256(cells_payload),
            "dimensions_sha256": _sha256(dimensions_payload),
            "dimension_row_counts": {table: len(rows) for table, rows in dimensions.items()},
            "cell_batch_size": self.cell_batch_size,
            "target_rows_per_part": self.target_rows_per_part,
            "source_schema": source_schema,
            "parquet_schema": _arrow_schema_manifest(),
            "partition_columns": list(PARTITION_COLUMNS),
            "sort_columns": list(ROW_SORT_COLUMNS),
            "rejected_rows": 0,
        }
        self.store.put_immutable(
            self._key(resolved_id, "_DIMENSIONS.json"), dimensions_payload, content_type=JSON_CONTENT_TYPE
        )
        self.store.put_immutable(self._key(resolved_id, "_CELLS.json"), cells_payload, content_type=JSON_CONTENT_TYPE)
        self.store.put_immutable(
            self._key(resolved_id, "_SNAPSHOT.json"), _json_bytes(snapshot), content_type=JSON_CONTENT_TYPE
        )
        return snapshot

    def _validate_snapshot(self, snapshot: Mapping[str, Any], snapshot_id: str) -> None:
        if snapshot.get("contract_version") != CONTRACT_VERSION or snapshot.get("snapshot_id") != snapshot_id:
            raise SnapshotError(f"snapshot descriptor for {snapshot_id!r} has a different identity or contract")
        if int(snapshot.get("cell_batch_size", 0)) != self.cell_batch_size:
            raise SnapshotError("resume must use the snapshot's original --cell-batch-size")
        if int(snapshot.get("target_rows_per_part", 0)) != self.target_rows_per_part:
            raise SnapshotError("resume must use the snapshot's original --target-rows-per-part")

    def _load_cells(self, snapshot: Mapping[str, Any]) -> list[str]:
        snapshot_id = str(snapshot["snapshot_id"])
        key = self._key(snapshot_id, "_CELLS.json")
        payload = self.store.get(key)
        if payload is None or _sha256(payload) != snapshot["cells_sha256"]:
            raise SnapshotError(f"cell identity object {key!r} is absent or does not match its snapshot checksum")
        parsed = _parse_json_object(payload, key=key)
        cells = parsed.get("cell_ids")
        if not isinstance(cells, list) or not all(isinstance(cell, str) for cell in cells):
            raise SnapshotError(f"cell identity object {key!r} has an invalid cell_ids payload")
        if len(cells) != snapshot["cell_count"]:
            raise SnapshotError(f"cell identity object {key!r} has the wrong cell count")
        return cells

    def _verify_dimensions(self, snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        snapshot_id = str(snapshot["snapshot_id"])
        key = self._key(snapshot_id, "_DIMENSIONS.json")
        payload = self.store.get(key)
        if payload is None or _sha256(payload) != snapshot["dimensions_sha256"]:
            raise SnapshotError(f"dimension descriptor {key!r} is absent or does not match its checksum")
        descriptor = _parse_json_object(payload, key=key)
        objects = descriptor.get("objects")
        if not isinstance(objects, Mapping):
            raise SnapshotError(f"dimension descriptor {key!r} has no objects map")
        dimension_rows: dict[str, list[dict[str, Any]]] = {}
        for table_name, schema in DIMENSION_SCHEMAS.items():
            metadata = objects.get(table_name)
            if not isinstance(metadata, Mapping):
                raise SnapshotError(f"dimension descriptor omits {table_name!r}")
            object_key = str(metadata["key"])
            object_payload = self.store.get(object_key)
            if object_payload is None:
                raise SnapshotError(f"dimension Parquet object {object_key!r} is missing")
            if len(object_payload) != metadata["byte_count"] or _sha256(object_payload) != metadata["sha256"]:
                raise SnapshotError(f"dimension Parquet object {object_key!r} failed byte reconciliation")
            table = pq.read_table(io.BytesIO(object_payload))
            if not table.schema.equals(schema, check_metadata=False):
                raise SnapshotError(f"dimension Parquet object {object_key!r} has the wrong schema")
            parquet_rows = table.to_pylist()
            if (
                len(parquet_rows) != metadata["row_count"]
                or dimension_row_set_digest(parquet_rows, schema) != metadata["row_digest"]
                or census_for_schema(parquet_rows, schema) != metadata["census"]
            ):
                raise SnapshotError(f"dimension Parquet object {object_key!r} failed row/census reconciliation")
            dimension_rows[table_name] = parquet_rows
        _verify_fact_dimension_closure((), dimension_rows)
        return (
            {str(table_name): dict(metadata) for table_name, metadata in objects.items()},
            dimension_rows,
        )

    def export(self, snapshot_id: str | None = None, *, progress: Callable[[str], None] = print) -> dict[str, Any]:
        snapshot = self.prepare_snapshot(snapshot_id)
        resolved_id = str(snapshot["snapshot_id"])
        cells = self._load_cells(snapshot)
        high_watermark = snapshot["high_watermark_id"]
        first = date.fromisoformat(snapshot["observation_day_min"]) if snapshot["observation_day_min"] else None
        last = date.fromisoformat(snapshot["observation_day_max"]) if snapshot["observation_day_max"] else None
        months = list(_months(first, last))
        progress(
            f"snapshot={resolved_id} prefix={snapshot['snapshot_prefix']} high_watermark={high_watermark} "
            f"months={len(months)} cells={len(cells)}"
        )
        cell_batches = list(_batched(cells, self.cell_batch_size))
        unit_count = len(months) * len(cell_batches)
        unit_index = 0
        for month_start in months:
            for cell_batch_index, cell_batch in enumerate(cell_batches):
                unit_index += 1
                ledger_key = self._key(
                    resolved_id,
                    f"_ledger/month={month_start.strftime('%Y-%m')}/cell-batch={cell_batch_index:05d}.json",
                )
                if self.store.get(ledger_key) is not None:
                    progress(
                        f"[{unit_index}/{unit_count}] {month_start:%Y-%m} cell-batch={cell_batch_index:05d}: "
                        "checkpoint present"
                    )
                    continue
                if high_watermark is None:
                    raise AssertionError("non-empty month range requires a high watermark")
                rows = self.source.rows_for_month(cell_batch, int(high_watermark), month_start)
                ledger = self._write_month_batch(
                    resolved_id,
                    month_start,
                    cell_batch_index,
                    cell_batch,
                    rows,
                )
                self.store.put_immutable(ledger_key, _json_bytes(ledger), content_type=JSON_CONTENT_TYPE)
                progress(
                    f"[{unit_index}/{unit_count}] {month_start:%Y-%m} cell-batch={cell_batch_index:05d}: "
                    f"{ledger['row_count']} rows, {ledger['part_count']} parts, {ledger['byte_count']} bytes"
                )
        manifest = self.finalize(resolved_id, progress=progress)
        progress(
            f"complete: prefix={manifest['snapshot_prefix']} rows={manifest['row_count']} "
            f"parts={manifest['partition_count']} bytes={manifest['byte_count']}"
        )
        return manifest

    def _write_month_batch(
        self,
        snapshot_id: str,
        month_start: date,
        cell_batch_index: int,
        cell_ids: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            row_day = row["observation_day"]
            if not isinstance(row_day, date) or (row_day.year, row_day.month) != (month_start.year, month_start.month):
                raise SnapshotError(f"source returned {row_day} while exporting {month_start:%Y-%m}")
            grouped.setdefault(partition_directory(row), []).append(row)
        parts: list[dict[str, Any]] = []
        for directory, group_rows in sorted(grouped.items()):
            for part_index, part_rows in enumerate(_batched(group_rows, self.target_rows_per_part)):
                relative_path = f"{directory}/part-cb{cell_batch_index:05d}-{part_index:05d}.parquet"
                key = self._key(snapshot_id, relative_path)
                payload = _serialize_rows(part_rows)
                self.store.put_immutable(key, payload, content_type=PARQUET_CONTENT_TYPE)
                parts.append(
                    {
                        "key": key,
                        "relative_path": relative_path,
                        "row_count": len(part_rows),
                        "byte_count": len(payload),
                        "sha256": _sha256(payload),
                        "row_digest": row_set_digest(part_rows),
                        "census": census(part_rows),
                    }
                )
        return {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": snapshot_id,
            "observation_month": month_start.strftime("%Y-%m"),
            "cell_batch_index": cell_batch_index,
            "cell_count": len(cell_ids),
            "cell_ids_sha256": _sha256(_json_bytes(list(cell_ids))),
            "row_count": len(rows),
            "part_count": len(parts),
            "byte_count": sum(int(part["byte_count"]) for part in parts),
            "source_row_digest": row_set_digest(rows),
            "source_census": census(rows),
            "parts": parts,
            "rejected_rows": 0,
        }

    def _load_ledgers(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        snapshot_id = str(snapshot["snapshot_id"])
        first = date.fromisoformat(snapshot["observation_day_min"]) if snapshot["observation_day_min"] else None
        last = date.fromisoformat(snapshot["observation_day_max"]) if snapshot["observation_day_max"] else None
        cells = self._load_cells(snapshot)
        cell_batches = list(_batched(cells, self.cell_batch_size))
        ledgers: list[dict[str, Any]] = []
        for month_start in _months(first, last):
            for cell_batch_index, cell_batch in enumerate(cell_batches):
                key = self._key(
                    snapshot_id,
                    f"_ledger/month={month_start.strftime('%Y-%m')}/cell-batch={cell_batch_index:05d}.json",
                )
                payload = self.store.get(key)
                if payload is None:
                    raise SnapshotError(f"missing month/cell-batch checkpoint {key!r}")
                ledger = _parse_json_object(payload, key=key)
                if (
                    ledger.get("observation_month") != month_start.strftime("%Y-%m")
                    or ledger.get("cell_batch_index") != cell_batch_index
                    or ledger.get("cell_ids_sha256") != _sha256(_json_bytes(list(cell_batch)))
                ):
                    raise SnapshotError(f"checkpoint {key!r} names a different durable extraction unit")
                ledgers.append(ledger)
        return ledgers

    def verify(
        self,
        snapshot_id: str,
        *,
        progress: Callable[[str], None] = print,
    ) -> dict[str, Any]:
        snapshot = self._load_json(self._key(snapshot_id, "_SNAPSHOT.json"))
        if snapshot is None:
            raise SnapshotError(f"snapshot {snapshot_id!r} does not exist")
        self._validate_snapshot(snapshot, snapshot_id)
        current_source_schema = self.source.source_schema()
        _validate_source_schema(current_source_schema)
        if current_source_schema != snapshot["source_schema"]:
            raise SnapshotError("production source schema changed after the snapshot descriptor was written")
        cells = self._load_cells(snapshot)
        dimension_objects, dimension_rows = self._verify_dimensions(snapshot)
        cell_batches = list(_batched(cells, self.cell_batch_size))
        ledgers = self._load_ledgers(snapshot)
        high_watermark = snapshot["high_watermark_id"]
        parquet_censuses: list[Mapping[str, Mapping[str, Any]]] = []
        verified_rows = 0
        verified_bytes = 0
        expected_part_keys: set[str] = set()
        for index, ledger in enumerate(ledgers, start=1):
            month_start = date.fromisoformat(f"{ledger['observation_month']}-01")
            cell_batch_index = int(ledger["cell_batch_index"])
            cell_batch = cell_batches[cell_batch_index]
            source_rows = (
                self.source.rows_for_month(cell_batch, int(high_watermark), month_start)
                if high_watermark is not None
                else []
            )
            _verify_fact_dimension_closure(source_rows, dimension_rows)
            if len(source_rows) != ledger["row_count"] or row_set_digest(source_rows) != ledger["source_row_digest"]:
                raise SnapshotError(
                    f"PostgreSQL changed or checkpoint row parity failed for {month_start:%Y-%m} "
                    f"cell-batch={cell_batch_index:05d}"
                )
            parquet_rows: list[dict[str, Any]] = []
            for part in ledger["parts"]:
                key = str(part["key"])
                expected_part_keys.add(key)
                payload = self.store.get(key)
                if payload is None:
                    raise SnapshotError(f"manifest part {key!r} is missing")
                if len(payload) != part["byte_count"] or _sha256(payload) != part["sha256"]:
                    raise SnapshotError(f"manifest part {key!r} failed byte checksum reconciliation")
                table = pq.read_table(io.BytesIO(payload))
                if not table.schema.equals(CANONICAL_SCHEMA, check_metadata=False):
                    raise SnapshotError(f"manifest part {key!r} has the wrong Parquet schema")
                part_rows = table.to_pylist()
                for row in part_rows:
                    stored_hash = row["canonical_row_sha256"]
                    computed_hash = canonical_row_hash(row)
                    if stored_hash != computed_hash:
                        raise SnapshotError(f"manifest part {key!r} contains a row whose canonical hash is wrong")
                if len(part_rows) != part["row_count"] or row_set_digest(part_rows) != part["row_digest"]:
                    raise SnapshotError(f"manifest part {key!r} failed row reconciliation")
                part_census = census(part_rows)
                if part_census != part["census"]:
                    raise SnapshotError(f"manifest part {key!r} failed null/min/max reconciliation")
                parquet_rows.extend(part_rows)
                parquet_censuses.append(part_census)
                verified_bytes += len(payload)
            if len(parquet_rows) != len(source_rows) or row_set_digest(parquet_rows) != row_set_digest(source_rows):
                raise SnapshotError(
                    f"PostgreSQL-to-Parquet row parity failed for {month_start:%Y-%m} cell-batch={cell_batch_index:05d}"
                )
            verified_rows += len(parquet_rows)
            progress(
                f"verify [{index}/{len(ledgers)}] {month_start:%Y-%m} cell-batch={cell_batch_index:05d}: "
                f"{len(parquet_rows)} rows exact"
            )
        dimension_keys = {
            self._key(snapshot_id, f"_dimensions/{table_name}.parquet") for table_name in DIMENSION_SCHEMAS
        }
        expected_inventory = expected_part_keys | dimension_keys
        actual_part_keys = {
            key for key in self.store.list_keys(self._root(snapshot_id) + "/") if key.endswith(".parquet")
        }
        if actual_part_keys != expected_inventory:
            missing = sorted(expected_inventory - actual_part_keys)
            unexpected = sorted(actual_part_keys - expected_inventory)
            raise SnapshotError(f"snapshot part inventory mismatch; missing={missing[:5]}, unexpected={unexpected[:5]}")
        dimension_byte_count = sum(int(metadata["byte_count"]) for metadata in dimension_objects.values())
        return {
            "row_count": verified_rows,
            "byte_count": verified_bytes + dimension_byte_count,
            "fact_byte_count": verified_bytes,
            "dimension_byte_count": dimension_byte_count,
            "partition_count": len(expected_part_keys),
            "object_count": len(expected_inventory),
            "dimension_objects": dimension_objects,
            "source_census": merge_censuses(ledger["source_census"] for ledger in ledgers),
            "parquet_census": merge_censuses(parquet_censuses),
            "verified_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }

    def finalize(self, snapshot_id: str, *, progress: Callable[[str], None] = print) -> dict[str, Any]:
        snapshot = self._load_json(self._key(snapshot_id, "_SNAPSHOT.json"))
        if snapshot is None:
            raise SnapshotError(f"snapshot {snapshot_id!r} does not exist")
        ledgers = self._load_ledgers(snapshot)
        verification = self.verify(snapshot_id, progress=progress)
        if verification["source_census"] != verification["parquet_census"]:
            raise SnapshotError("aggregate source and Parquet null/min/max censuses differ")
        manifest_key = self._key(snapshot_id, "manifest.json")
        existing_manifest = self._load_json(manifest_key)
        verified_at = (
            str(existing_manifest["verified_at"])
            if existing_manifest is not None and "verified_at" in existing_manifest
            else verification["verified_at"]
        )
        manifest = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": snapshot_id,
            "snapshot_prefix": snapshot["snapshot_prefix"],
            "source_relation": snapshot["source_relation"],
            "high_watermark_id": snapshot["high_watermark_id"],
            "observation_day_min": snapshot["observation_day_min"],
            "observation_day_max": snapshot["observation_day_max"],
            "row_count": verification["row_count"],
            "partition_count": verification["partition_count"],
            "object_count": verification["object_count"],
            "byte_count": verification["byte_count"],
            "fact_byte_count": verification["fact_byte_count"],
            "dimension_byte_count": verification["dimension_byte_count"],
            "batch_count": len(ledgers),
            "rejected_rows": sum(int(ledger["rejected_rows"]) for ledger in ledgers),
            "source_schema": snapshot["source_schema"],
            "dimension_row_counts": snapshot["dimension_row_counts"],
            "dimensions_sha256": snapshot["dimensions_sha256"],
            "dimension_objects": verification["dimension_objects"],
            "parquet_schema": snapshot["parquet_schema"],
            "partition_columns": snapshot["partition_columns"],
            "sort_columns": snapshot["sort_columns"],
            "source_census": verification["source_census"],
            "parquet_census": verification["parquet_census"],
            "verified_at": verified_at,
            "month_ledgers": [
                {
                    "observation_month": ledger["observation_month"],
                    "cell_batch_index": ledger["cell_batch_index"],
                    "row_count": ledger["row_count"],
                    "part_count": ledger["part_count"],
                    "byte_count": ledger["byte_count"],
                    "source_row_digest": ledger["source_row_digest"],
                }
                for ledger in ledgers
            ],
        }
        manifest_payload = _json_bytes(manifest)
        self.store.put_immutable(manifest_key, manifest_payload, content_type=JSON_CONTENT_TYPE)
        completion = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": snapshot_id,
            "manifest_key": manifest_key,
            "manifest_sha256": _sha256(manifest_payload),
            "row_count": manifest["row_count"],
            "partition_count": manifest["partition_count"],
            "byte_count": manifest["byte_count"],
            "completed_at": verified_at,
        }
        self.store.put_immutable(
            self._key(snapshot_id, "_COMPLETE"), _json_bytes(completion), content_type=JSON_CONTENT_TYPE
        )
        return manifest


def _settings(env_file: Path) -> Settings:
    if not env_file.is_file():
        raise SnapshotError(f"settings file does not exist: {env_file}")
    return Settings(_env_file=env_file)  # type: ignore[call-arg]


def _sync_dsn(configured: Settings, explicit: str | None) -> str:
    if explicit:
        return explicit.replace("postgresql+asyncpg://", "postgresql://", 1)
    if "database_url_sync" in configured.model_fields_set:
        return configured.database_url_sync.replace("postgresql+psycopg2://", "postgresql://", 1)
    candidate = configured.local_source_loader_database_url or configured.database_url
    if candidate:
        return candidate.replace("postgresql+asyncpg://", "postgresql://", 1)
    return configured.database_url_sync.replace("postgresql+psycopg2://", "postgresql://", 1)


def _build_exporter(arguments: argparse.Namespace) -> CanonicalSignalExporter:
    configured = _settings(arguments.env_file)
    credentials = configured.require_object_store()
    retry = RetryPolicy(attempts=arguments.retry_attempts, base_delay_seconds=arguments.retry_base_delay)
    store = BotoSnapshotStore.from_credentials(credentials, retry=retry)
    source = PostgresSignalSource(_sync_dsn(configured, arguments.dsn), arguments.statement_timeout_ms)
    return CanonicalSignalExporter(
        source=source,
        store=store,
        object_store_prefix=configured.object_store_prefix,
        raw_prefix=arguments.raw_prefix,
        cell_batch_size=arguments.cell_batch_size,
        target_rows_per_part=arguments.target_rows_per_part,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("export", "verify"))
    parser.add_argument("--snapshot-id", help="stable resume identity; required by verify")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help="settings/credentials .env file")
    parser.add_argument(
        "--dsn",
        help="explicit libpq DSN; otherwise LOCAL_SOURCE_LOADER_DATABASE_URL/DATABASE_URL is converted to sync",
    )
    parser.add_argument("--raw-prefix", default=DEFAULT_RAW_PREFIX)
    parser.add_argument("--cell-batch-size", type=int, default=DEFAULT_CELL_BATCH_SIZE)
    parser.add_argument("--target-rows-per-part", type=int, default=DEFAULT_TARGET_ROWS_PER_PART)
    parser.add_argument("--statement-timeout-ms", type=int, default=DEFAULT_STATEMENT_TIMEOUT_MS)
    parser.add_argument("--retry-attempts", type=int, default=8)
    parser.add_argument("--retry-base-delay", type=float, default=0.5)
    parser.add_argument("--finalize", action="store_true", help="after verify, publish manifest and _COMPLETE")
    parser.add_argument("--json", action="store_true", help="print the final report as JSON")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.retry_attempts < 1 or arguments.retry_base_delay < 0:
        raise SystemExit("retry attempts must be positive and retry base delay cannot be negative")
    if not 1 <= arguments.statement_timeout_ms <= 3_600_000:
        raise SystemExit("statement timeout must be between 1 and 3600000 milliseconds")
    exporter = _build_exporter(arguments)
    progress: Callable[[str], None] = (lambda _message: None) if arguments.json else print
    if arguments.command == "export":
        report = exporter.export(arguments.snapshot_id, progress=progress)
    else:
        if not arguments.snapshot_id:
            raise SystemExit("verify requires --snapshot-id")
        report = (
            exporter.finalize(arguments.snapshot_id, progress=progress)
            if arguments.finalize
            else exporter.verify(arguments.snapshot_id, progress=progress)
        )
    if arguments.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
