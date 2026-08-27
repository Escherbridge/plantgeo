"""Build four immutable soil-temperature lanes from a pinned canonical signal snapshot.

This operator never opens PostgreSQL and never writes the shared signal layer. Run from
services/agri-data-service:

    uv run python scripts/soil_temperature_snapshot_breakdown.py inventory
    uv run python scripts/soil_temperature_snapshot_breakdown.py build

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
EXPECTED_LEDGER_COUNT: Final = 424
EXPECTED_FACT_PART_COUNT: Final = 8_364
EXPECTED_SOURCE_PARTS_PER_PRODUCT: Final = 424
EXPECTED_MONTH_COUNT: Final = 53
EXPECTED_SOURCE_KEY: Final = "open-meteo-era5-land-archive"
EXPECTED_SUPPORT_KEY: Final = "era5-land-0.1deg"
EXPECTED_UNIT: Final = "C"
EXPECTED_CELL_GRID: Final = "sentinel2-ndvi-0p25deg"
ZOOM_RESOLUTIONS: Final[dict[int, float]] = {9: 0.01, 5: 0.2, 0: 5.0}
ZOOM_TIERS: Final = (13, 9, 5, 0)
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
        pa.field("source_part_key", pa.string(), nullable=False),
        pa.field("source_part_sha256", pa.string(), nullable=False),
        pa.field("source_row_ordinal", pa.int64(), nullable=False),
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
        pa.field("data_source_key", pa.string(), nullable=False),
        pa.field("source_parameter", pa.string(), nullable=False),
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

LANE_SORT_COLUMNS: Final = (
    "data_source_key",
    "source_parameter",
    "support_key",
    "signal_name",
    "normalized_unit",
    "cell_id",
    "observed_day",
)


@dataclass(frozen=True, slots=True)
class Product:
    parameter: str
    product_key: str
    lane: str
    signal_name: str


PRODUCTS: Final = (
    Product(
        "soil_temperature_0_to_7cm_mean",
        "soil_temperature_0_to_7cm_mean",
        "soil-temperature-0-to-7cm",
        "soil_temperature_level_1",
    ),
    Product(
        "soil_temperature_7_to_28cm_mean",
        "soil_temperature_7_to_28cm_mean",
        "soil-temperature-7-to-28cm",
        "soil_temperature_level_2",
    ),
    Product(
        "soil_temperature_28_to_100cm_mean",
        "soil_temperature_28_to_100cm_mean",
        "soil-temperature-28-to-100cm",
        "soil_temperature_level_3",
    ),
    Product(
        "soil_temperature_100_to_255cm_mean",
        "soil_temperature_100_to_255cm_mean",
        "soil-temperature-100-to-255cm",
        "soil_temperature_level_4",
    ),
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


def provenance_source_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    entries = sorted(
        (
            str(row["source_part_key"]),
            int(row["source_row_ordinal"]),
            str(row["source_part_sha256"]),
            str(row["canonical_row_sha256"]),
        )
        for row in rows
    )
    for key, ordinal, part_sha256, row_sha256 in entries:
        digest.update(f"{key}\0{ordinal}\0{part_sha256}\0{row_sha256}\n".encode())
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
            config=Config(retries={"max_attempts": retry.attempts, "mode": "adaptive"}),
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


def dedicated_output_prefix(output_prefix: str) -> str:
    cleaned = output_prefix.strip("/")
    if cleaned != DEFAULT_OUTPUT_PREFIX or "layer=signal" in cleaned.lower():
        raise BreakdownError(f"output prefix must be the dedicated {DEFAULT_OUTPUT_PREFIX!r} root")
    return cleaned


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
        raise BreakdownError("canonical fact Arrow schema differs from the soil-temperature breakdown contract")
    raw_units = manifest.get("month_ledgers")
    if (
        not isinstance(raw_units, list)
        or len(raw_units) != EXPECTED_LEDGER_COUNT
        or len(raw_units) != int(manifest.get("batch_count", -1))
    ):
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
    for dimension_name, metadata in dimensions.items():
        if not isinstance(dimension_name, str) or not isinstance(metadata, Mapping):
            raise BreakdownError("canonical manifest has an invalid dimension descriptor")
        expected_key = f"{root}/_dimensions/{dimension_name}.parquet"
        if metadata.get("key") != expected_key:
            raise BreakdownError(f"canonical dimension {dimension_name!r} escaped its pinned snapshot path")
        if (
            not isinstance(metadata.get("row_count"), int)
            or int(metadata["row_count"]) < 0
            or not isinstance(metadata.get("byte_count"), int)
            or int(metadata["byte_count"]) <= 0
            or not isinstance(metadata.get("sha256"), str)
            or not _SHA256.fullmatch(str(metadata["sha256"]))
        ):
            raise BreakdownError(f"canonical dimension {dimension_name!r} has an invalid receipt")

    def dimension_rows(name: str) -> list[dict[str, Any]]:
        metadata = dimensions.get(name)
        if not isinstance(metadata, Mapping):
            raise BreakdownError(f"canonical manifest omits dimension {name!r}")
        key = str(metadata["key"])
        size = int(metadata["byte_count"])
        expected_dimension_prefix = f"{root}/_dimensions/"
        if not key.startswith(expected_dimension_prefix) or key != f"{expected_dimension_prefix}{name}.parquet":
            raise BreakdownError(f"canonical dimension {name!r} escaped its pinned snapshot path")
        if size < 0 or int(metadata["row_count"]) < 0 or not _SHA256.fullmatch(str(metadata["sha256"])):
            raise BreakdownError(f"canonical dimension {name!r} has an invalid receipt")
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


def validate_part_descriptor(contract: InputContract, part: Mapping[str, Any]) -> None:
    relative = part.get("relative_path")
    key = part.get("key")
    sha256 = part.get("sha256")
    row_digest = part.get("row_digest")
    row_count = part.get("row_count")
    byte_count = part.get("byte_count")
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")):
        raise BreakdownError("canonical ledger part has an invalid relative_path")
    segments = relative.split("/")
    if "\\" in relative or any(segment in {"", ".", ".."} for segment in segments):
        raise BreakdownError(f"canonical ledger part has a non-canonical relative path: {relative!r}")
    expected_key = f"{contract.root}/{relative}"
    if key != expected_key or not str(key).startswith(f"{contract.root}/"):
        raise BreakdownError(f"canonical ledger part escaped its pinned snapshot root: {key!r}")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise BreakdownError(f"canonical ledger part has an invalid SHA-256: {key!r}")
    if not isinstance(row_digest, str) or not _SHA256.fullmatch(row_digest):
        raise BreakdownError(f"canonical ledger part has an invalid row digest: {key!r}")
    if not isinstance(row_count, int) or row_count <= 0 or not isinstance(byte_count, int) or byte_count <= 0:
        raise BreakdownError(f"canonical ledger part has invalid row or byte counts: {key!r}")


def part_dimensions(part: Mapping[str, Any]) -> dict[str, str]:
    segments = str(part["relative_path"]).split("/")
    if len(segments) != 6 or not segments[-1].endswith(".parquet"):
        raise BreakdownError(f"canonical fact part has an invalid partition layout: {part['relative_path']!r}")
    expected_names = ("source", "product", "support", "year", "month")
    result: dict[str, str] = {}
    for segment, name in zip(segments[:5], expected_names, strict=True):
        prefix = f"{name}="
        if not segment.startswith(prefix) or not segment.removeprefix(prefix):
            raise BreakdownError(f"canonical fact part has an invalid {name} partition: {part['relative_path']!r}")
        result[name] = segment.removeprefix(prefix)
    return result


def ledger_for_unit(store: SnapshotStore, contract: InputContract, month: str, batch_index: int) -> dict[str, Any]:
    cached = contract.ledger_cache.get((month, batch_index))
    if cached is not None:
        return cached
    summary = contract.units[(month, batch_index)]
    ledger_relative = f"_ledger/month={month}/cell-batch={batch_index:05d}.json"
    key = f"{contract.root}/{ledger_relative}"
    if not key.startswith(f"{contract.root}/_ledger/"):
        raise BreakdownError("canonical ledger key escaped its pinned snapshot root")
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
    seen_keys: set[str] = set()
    part_rows = 0
    part_bytes = 0
    for part in ledger["parts"]:
        if not isinstance(part, Mapping):
            raise BreakdownError(f"canonical checkpoint {key!r} has a non-object part descriptor")
        validate_part_descriptor(contract, part)
        dimensions = part_dimensions(part)
        if f"{dimensions['year']}-{dimensions['month']}" != month:
            raise BreakdownError(f"canonical checkpoint {key!r} contains a part from another month")
        part_key = str(part["key"])
        if part_key in seen_keys:
            raise BreakdownError(f"canonical checkpoint {key!r} repeats part {part_key!r}")
        seen_keys.add(part_key)
        part_rows += int(part["row_count"])
        part_bytes += int(part["byte_count"])
    if part_rows != checks["row_count"] or part_bytes != checks["byte_count"]:
        raise BreakdownError(f"canonical checkpoint {key!r} part receipts do not sum to ledger totals")
    contract.ledger_cache[(month, batch_index)] = ledger
    return ledger


def product_part_metadata(
    ledger: Mapping[str, Any],
    contract: InputContract,
    product: Product,
) -> list[Mapping[str, Any]]:
    prefix = f"source={EXPECTED_SOURCE_KEY}/product={product.product_key}/support={EXPECTED_SUPPORT_KEY}/"
    selected: list[Mapping[str, Any]] = []
    for part in ledger["parts"]:
        if not isinstance(part, Mapping):
            raise BreakdownError("validated canonical ledger changed during product selection")
        relative = str(part["relative_path"])
        if relative.startswith(prefix):
            if part["key"] != f"{contract.root}/{relative}":
                raise BreakdownError(f"selected product descriptor drifted from its canonical key: {part['key']!r}")
            selected.append(part)
    return selected


def descriptor_totals(parts: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    values = list(parts)
    return {
        "part_count": len(values),
        "row_count": sum(int(part["row_count"]) for part in values),
        "byte_count": sum(int(part["byte_count"]) for part in values),
    }


def ledger_scope_census(store: SnapshotStore, contract: InputContract) -> dict[str, Any]:
    product_by_key = {product.product_key: product for product in PRODUCTS}
    included: dict[str, dict[str, Mapping[str, Any]]] = {product.lane: {} for product in PRODUCTS}
    excluded: dict[str, dict[str, Mapping[str, Any]]] = {
        "other_source": {},
        "same_source_other_product": {},
        "matching_product_wrong_support": {},
    }
    all_parts: dict[str, Mapping[str, Any]] = {}
    for month in product_months(contract):
        for batch_index in month_batch_indexes(contract, month):
            ledger = ledger_for_unit(store, contract, month, batch_index)
            for part in ledger["parts"]:
                if not isinstance(part, Mapping):
                    raise BreakdownError("validated canonical ledger changed during scope census")
                key = str(part["key"])
                if key in all_parts:
                    raise BreakdownError(f"canonical fact part appears in two ledgers: {key!r}")
                all_parts[key] = part
                dimensions = part_dimensions(part)
                product = product_by_key.get(dimensions["product"])
                if dimensions["source"] != EXPECTED_SOURCE_KEY:
                    destination = excluded["other_source"]
                elif product is None:
                    destination = excluded["same_source_other_product"]
                elif dimensions["support"] != EXPECTED_SUPPORT_KEY:
                    destination = excluded["matching_product_wrong_support"]
                else:
                    destination = included[product.lane]
                if key in destination:
                    raise BreakdownError(f"canonical scope census classified part twice: {key!r}")
                destination[key] = part
    manifest_totals = {
        "part_count": int(contract.manifest["partition_count"]),
        "row_count": int(contract.manifest["row_count"]),
        "byte_count": int(contract.manifest["fact_byte_count"]),
    }
    if (
        manifest_totals["part_count"] != EXPECTED_FACT_PART_COUNT
        or descriptor_totals(all_parts.values()) != manifest_totals
    ):
        raise BreakdownError("all canonical ledger descriptors do not reconcile to pinned manifest fact totals")
    classified_keys = set().union(
        *(set(parts) for parts in included.values()),
        *(set(parts) for parts in excluded.values()),
    )
    if classified_keys != set(all_parts):
        raise BreakdownError("canonical ledger scope census did not classify every fact part exactly once")
    for product in PRODUCTS:
        if len(included[product.lane]) != EXPECTED_SOURCE_PARTS_PER_PRODUCT:
            raise BreakdownError(
                f"{product.lane} scope has {len(included[product.lane])} parts; "
                f"expected {EXPECTED_SOURCE_PARTS_PER_PRODUCT}"
            )
    return {
        "manifest_totals": manifest_totals,
        "included_parts": included,
        "included_totals": descriptor_totals(part for parts in included.values() for part in parts.values()),
        "scope_exclusions": {reason: descriptor_totals(parts.values()) for reason, parts in excluded.items()},
    }


def load_canonical_part(store: SnapshotStore, metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    key = str(metadata["key"])
    size = int(metadata["byte_count"])
    payload = store.get(key, max_bytes=size)
    if payload is None or len(payload) != size or _sha256(payload) != metadata["sha256"]:
        raise BreakdownError(f"canonical soil-temperature part {key!r} failed byte reconciliation")
    table = pq.read_table(io.BytesIO(payload))
    if not table.schema.equals(RAW_SCHEMA, check_metadata=False):
        raise BreakdownError(f"canonical soil-temperature part {key!r} has the wrong Arrow schema")
    rows = table.to_pylist()
    if len(rows) != int(metadata["row_count"]) or row_set_digest(rows) != metadata["row_digest"]:
        raise BreakdownError(f"canonical soil-temperature part {key!r} failed physical-row reconciliation")
    for ordinal, row in enumerate(rows):
        if row["canonical_row_sha256"] != canonical_row_hash(row):
            raise BreakdownError(f"canonical soil-temperature part {key!r} contains a row with a bad canonical hash")
        row["source_part_key"] = key
        row["source_part_sha256"] = str(metadata["sha256"])
        row["source_row_ordinal"] = ordinal
    return rows


def verify_all_ledger_rows(
    store: SnapshotStore,
    contract: InputContract,
    *,
    progress: Callable[[str], None],
) -> dict[str, Any]:
    units = sorted(contract.units)
    verified_rows = 0
    verified_parts = 0
    verified_bytes = 0
    ledger_digests: list[str] = []
    for index, (month, batch_index) in enumerate(units, start=1):
        ledger = ledger_for_unit(store, contract, month, batch_index)
        parts = [part for part in ledger["parts"] if isinstance(part, Mapping)]
        with ThreadPoolExecutor(max_workers=8) as executor:
            part_rows = list(executor.map(lambda part: load_canonical_part(store, part), parts))
        rows = [row for rows_for_part in part_rows for row in rows_for_part]
        actual_digest = row_set_digest(rows)
        if len(rows) != int(ledger["row_count"]) or actual_digest != ledger["source_row_digest"]:
            raise BreakdownError(
                f"canonical ledger {month}/{batch_index:05d} failed full physical source_row_digest verification"
            )
        verified_rows += len(rows)
        verified_parts += len(parts)
        verified_bytes += sum(int(part["byte_count"]) for part in parts)
        ledger_digests.append(f"{month}:{batch_index:05d}:{actual_digest}")
        progress(
            f"phase=ledger-preflight checkpoint={index}/{len(units)} month={month} "
            f"cell_batch={batch_index:05d} parts={len(parts)} rows={len(rows)}"
        )
    expected = {
        "row_count": int(contract.manifest["row_count"]),
        "part_count": int(contract.manifest["partition_count"]),
        "byte_count": int(contract.manifest["fact_byte_count"]),
    }
    actual = {"row_count": verified_rows, "part_count": verified_parts, "byte_count": verified_bytes}
    if actual != expected:
        raise BreakdownError("full canonical ledger verification does not reconcile to manifest fact totals")
    return {
        **actual,
        "ledger_count": len(units),
        "ledger_source_row_digest_root": lineage_digest(ledger_digests),
    }


def lane_root(object_store_prefix: str, output_prefix: str, snapshot_id: str, product: Product) -> str:
    dedicated = dedicated_output_prefix(output_prefix)
    return _prefixed(
        object_store_prefix,
        f"{dedicated}/lane={product.lane}/snapshot={snapshot_id}",
    )


def month_directory(root: str, tier: int, month: str) -> str:
    return f"{root}/kind=observed/zoom={tier:02d}/year={month[:4]}/month={month[5:]}"


def verify_receipt(store: SnapshotStore, receipt: Mapping[str, Any], *, schema: pa.Schema | None = None) -> bytes:
    key = str(receipt["key"])
    size = int(receipt["byte_count"])
    payload = store.get(key, max_bytes=size)
    if payload is None or len(payload) != size or _sha256(payload) != receipt["sha256"]:
        raise BreakdownError(f"output receipt failed byte reconciliation: {key!r}")
    if schema is not None:
        table = pq.read_table(io.BytesIO(payload))
        if not table.schema.equals(schema, check_metadata=False) or table.num_rows != int(receipt["row_count"]):
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
    months = tuple(sorted({month for month, _batch in contract.units}))
    if len(months) != EXPECTED_MONTH_COUNT:
        raise BreakdownError(f"pinned snapshot has {len(months)} months; expected {EXPECTED_MONTH_COUNT}")
    return months


def month_batch_indexes(contract: InputContract, month: str) -> tuple[int, ...]:
    return tuple(sorted(batch for unit_month, batch in contract.units if unit_month == month))


def _eligibility_reason(
    row: Mapping[str, Any],
    product: Product,
    release: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str | None:
    if row["source_parameter"] != product.parameter:
        return "wrong_source_parameter"
    if row["product_key"] != product.product_key:
        return "wrong_product_key"
    if row["data_source_key"] != EXPECTED_SOURCE_KEY or source["key"] != EXPECTED_SOURCE_KEY:
        return "wrong_data_source"
    if row["support_key"] != EXPECTED_SUPPORT_KEY:
        return "wrong_support"
    if row["signal_name"] != product.signal_name:
        return "wrong_signal"
    if row["normalized_unit"] != EXPECTED_UNIT:
        return "wrong_normalized_unit"
    if row["original_unit"] != EXPECTED_UNIT:
        return "wrong_original_unit"
    if row["cell_grid_name"] != EXPECTED_CELL_GRID:
        return "wrong_cell_grid"
    if not row["is_observed"]:
        return "not_observed"
    if row["quality_flag"] != "accepted":
        return "quality_not_accepted"
    value = row["normalized_value"]
    if value is None:
        return "normalized_value_null"
    if not math.isfinite(float(value)):
        return "normalized_value_non_finite"
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
            raise BreakdownError(f"soil-temperature row {row_id} references a release absent from the frozen dimension")
        source = contract.sources.get(str(release["data_source_id"]))
        if source is None:
            raise BreakdownError(f"soil-temperature row {row_id} references a source absent from the frozen dimension")
        release_by_row[row_id] = release
        source_by_row[row_id] = source
        reason = _eligibility_reason(row, product, release, source)
        rejection_by_row[row_id] = reason
        if reason is None:
            grain = (
                row["data_source_key"],
                row["source_parameter"],
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
            "data_source_key": winner["data_source_key"],
            "source_parameter": winner["source_parameter"],
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
        raise AssertionError("soil-temperature month classification did not close")
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
        "provenance_source_part_count": len({str(row["source_part_key"]) for row in provenance}),
        "provenance_source_digest": provenance_source_digest(provenance),
    }
    return provenance, dict(by_day), stats


def month_checkpoint_key(root: str, month: str) -> str:
    return f"{root}/_checkpoints/base/year={month[:4]}/month={month[5:]}.json"


def tier_checkpoint_key(root: str, month: str) -> str:
    return f"{root}/_checkpoints/tiers/year={month[:4]}/month={month[5:]}.json"


def validate_checkpoint_objects(store: SnapshotStore, checkpoint: Mapping[str, Any]) -> None:
    objects = checkpoint.get("output_objects")
    if not isinstance(objects, list):
        raise BreakdownError("breakdown checkpoint has no output_objects list")
    for receipt in objects:
        if not isinstance(receipt, Mapping):
            raise BreakdownError("breakdown checkpoint contains an invalid output receipt")
        key = str(receipt["key"])
        schema = PROVENANCE_SCHEMA if "/_provenance/" in key else LANE_SCHEMA if key.endswith(".parquet") else None
        verify_receipt(store, receipt, schema=schema)


def verify_provenance_lineage(
    store: SnapshotStore,
    receipt: Mapping[str, Any],
    input_parts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    payload = verify_receipt(store, receipt, schema=PROVENANCE_SCHEMA)
    rows = pq.read_table(io.BytesIO(payload)).to_pylist()
    expected: dict[str, Mapping[str, Any]] = {}
    for part in input_parts:
        key = str(part["key"])
        if key in expected:
            raise BreakdownError(f"checkpoint repeats canonical source part {key!r}")
        expected[key] = part
    counts: Counter[str] = Counter()
    ordinals: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        key = str(row["source_part_key"])
        part = expected.get(key)
        if part is None:
            raise BreakdownError(f"provenance row references unconsumed source part {key!r}")
        if row["source_part_sha256"] != part["sha256"]:
            raise BreakdownError(f"provenance row changed source-part SHA-256 for {key!r}")
        if row["canonical_row_sha256"] != canonical_row_hash(row):
            raise BreakdownError(f"provenance row changed its canonical physical fact for {key!r}")
        ordinal = int(row["source_row_ordinal"])
        if ordinal < 0 or ordinal in ordinals[key]:
            raise BreakdownError(f"provenance source-row ordinal is invalid or repeated for {key!r}")
        ordinals[key].add(ordinal)
        counts[key] += 1
    if set(counts) != set(expected):
        raise BreakdownError(
            "provenance source-part inventory differs from its checkpoint; "
            f"missing={sorted(set(expected) - set(counts))[:5]} unexpected={sorted(set(counts) - set(expected))[:5]}"
        )
    for key, part in expected.items():
        row_count = int(part["row_count"])
        if counts[key] != row_count or ordinals[key] != set(range(row_count)):
            raise BreakdownError(f"provenance rows do not form an exact ordinal census for {key!r}")
    return {
        "row_count": len(rows),
        "source_part_count": len(expected),
        "source_digest": provenance_source_digest(rows),
    }


def input_part_receipt(part: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "key": part["key"],
        "row_count": int(part["row_count"]),
        "byte_count": int(part["byte_count"]),
        "sha256": part["sha256"],
        "row_digest": part["row_digest"],
    }


def month_input_parts(
    store: SnapshotStore,
    contract: InputContract,
    product: Product,
    month: str,
) -> list[Mapping[str, Any]]:
    parts: list[Mapping[str, Any]] = []
    for batch_index in month_batch_indexes(contract, month):
        ledger = ledger_for_unit(store, contract, month, batch_index)
        parts.extend(product_part_metadata(ledger, contract, product))
    keys = [str(part["key"]) for part in parts]
    if len(keys) != len(set(keys)):
        raise BreakdownError(f"{product.lane} {month} repeats a canonical input part")
    return parts


def validate_base_checkpoint_semantics(
    checkpoint: Mapping[str, Any],
    *,
    root: str,
    product: Product,
    month: str,
) -> None:
    base_part = checkpoint.get("base_part")
    output_objects = checkpoint.get("output_objects")
    if not isinstance(base_part, Mapping) or not isinstance(output_objects, list):
        raise BreakdownError(f"{product.lane} {month} base checkpoint has invalid output receipts")
    if base_part.get("key") != f"{month_directory(root, 13, month)}/part-0.parquet":
        raise BreakdownError(f"{product.lane} {month} z13 receipt escaped its monthly path")
    if int(base_part["row_count"]) != int(checkpoint["selected_rows"]):
        raise BreakdownError(f"{product.lane} {month} z13 rows do not equal selected winners")
    expected_keys = {str(base_part["key"])}
    if int(checkpoint["input_physical_rows"]):
        expected_keys.add(f"{root}/_provenance/year={month[:4]}/month={month[5:]}/part-0.parquet")
    if {str(receipt["key"]) for receipt in output_objects} != expected_keys:
        raise BreakdownError(f"{product.lane} {month} base checkpoint output inventory changed")
    days = checkpoint.get("data_days")
    if (
        not isinstance(days, list)
        or len(days) != len(set(days))
        or any(not isinstance(day, str) or day[:7] != month for day in days)
    ):
        raise BreakdownError(f"{product.lane} {month} base checkpoint has invalid observed-day census")


def compute_base_month(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: Product,
    month: str,
) -> tuple[dict[str, Any], bytes, dict[str, bytes]]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    input_parts = month_input_parts(store, contract, product, month)
    input_receipts = [input_part_receipt(part) for part in input_parts]
    raw_rows: list[dict[str, Any]] = []
    for metadata in input_parts:
        raw_rows.extend(load_canonical_part(store, metadata))
    provenance, base_by_day, stats = classify_month(raw_rows, product, contract)
    base_rows = [row for day_rows in base_by_day.values() for row in day_rows]
    if not base_rows:
        raise BreakdownError(f"{product.lane} {month} has no downstream winners")
    output_objects: list[dict[str, Any]] = []
    output_payloads: dict[str, bytes] = {}
    if provenance:
        provenance_key = f"{root}/_provenance/year={month[:4]}/month={month[5:]}/part-0.parquet"
        provenance_payload = serialize_table(
            provenance,
            PROVENANCE_SCHEMA,
            ("source_part_key", "source_row_ordinal", "id"),
        )
        output_payloads[provenance_key] = provenance_payload
        output_objects.append(table_receipt(provenance_key, provenance_payload, len(provenance)))
    base_key = f"{month_directory(root, 13, month)}/part-0.parquet"
    base_payload = serialize_table(base_rows, LANE_SCHEMA, LANE_SORT_COLUMNS)
    output_payloads[base_key] = base_payload
    base_receipt = table_receipt(base_key, base_payload, len(base_rows))
    output_objects.append(base_receipt)
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "product_key": product.product_key,
        "signal_name": product.signal_name,
        "data_source_key": EXPECTED_SOURCE_KEY,
        "support_key": EXPECTED_SUPPORT_KEY,
        "normalized_unit": EXPECTED_UNIT,
        "observation_month": month,
        "input_snapshot_id": snapshot_id,
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "input_parts": input_receipts,
        **stats,
        "data_days": sorted(day.isoformat() for day in base_by_day),
        "base_part": base_receipt,
        "output_objects": output_objects,
    }
    return checkpoint, _json_bytes(checkpoint), output_payloads


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
    checkpoint, checkpoint_payload, output_payloads = compute_base_month(
        store,
        contract,
        object_store_prefix=object_store_prefix,
        output_prefix=output_prefix,
        snapshot_id=snapshot_id,
        product=product,
        month=month,
    )
    existing_payload = store.get(checkpoint_key, max_bytes=2_000_000)
    if existing_payload is not None:
        if existing_payload != checkpoint_payload:
            raise BreakdownError(f"base checkpoint {checkpoint_key!r} differs from fresh deterministic computation")
        for key, expected_payload in output_payloads.items():
            actual_payload = store.get(key, max_bytes=len(expected_payload) + 1)
            if actual_payload != expected_payload:
                raise BreakdownError(f"resumed base object differs from fresh deterministic computation: {key!r}")
        validate_base_checkpoint_semantics(checkpoint, root=root, product=product, month=month)
        return checkpoint
    for key, payload in output_payloads.items():
        store.put_immutable(key, payload, content_type=PARQUET_CONTENT_TYPE)
    store.put_immutable(checkpoint_key, checkpoint_payload, content_type=JSON_CONTENT_TYPE)
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
            row["data_source_key"],
            row["source_parameter"],
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
        (
            data_source_key,
            source_parameter,
            support_key,
            signal_name,
            normalized_unit,
            observed_day,
            longitude,
            latitude,
        ) = key
        coverages = [float(row["coverage_fraction"]) for row in rows if row["coverage_fraction"] is not None]
        exposure_values = [
            bool(row["allowed_client_exposure"]) for row in rows if row["allowed_client_exposure"] is not None
        ]
        result.append(
            {
                "data_source_key": data_source_key,
                "source_parameter": source_parameter,
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


def checkpoint_object_receipt(
    store: SnapshotStore,
    key: str,
    checkpoint: Mapping[str, Any],
    *,
    kind: str,
) -> dict[str, Any]:
    expected_payload = _json_bytes(checkpoint)
    actual_payload = store.get(key, max_bytes=len(expected_payload) + 1)
    if actual_payload != expected_payload:
        raise BreakdownError(f"checkpoint bytes differ from their reconciled object: {key!r}")
    return {
        "key": key,
        "kind": kind,
        "row_count": None,
        "byte_count": len(expected_payload),
        "sha256": _sha256(expected_payload),
    }


def inventory_object_receipt(receipt: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        "key": str(receipt["key"]),
        "kind": kind,
        "row_count": int(receipt["row_count"]),
        "byte_count": int(receipt["byte_count"]),
        "sha256": str(receipt["sha256"]),
    }


def receipt_inventory_root(receipts: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted((dict(receipt) for receipt in receipts), key=lambda receipt: str(receipt["key"]))
    if len(ordered) != len({str(receipt["key"]) for receipt in ordered}):
        raise BreakdownError("object receipt inventory contains duplicate keys")
    return _sha256(_json_bytes(ordered))


def verify_byte_receipt(store: SnapshotStore, receipt: Mapping[str, Any]) -> None:
    key = str(receipt["key"])
    size = int(receipt["byte_count"])
    payload = store.get(key, max_bytes=size)
    if payload is None or len(payload) != size or _sha256(payload) != receipt["sha256"]:
        raise BreakdownError(f"publication receipt failed byte reconciliation: {key!r}")


def compute_month_marker(
    *,
    root: str,
    product: Product,
    month: str,
    tier: int,
    part_receipt: Mapping[str, Any],
    input_manifest_sha256: str,
    base_lineage_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    key = f"{month_directory(root, tier, month)}/_complete.json"
    marker = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "product_key": product.product_key,
        "data_source_key": EXPECTED_SOURCE_KEY,
        "tier": tier,
        "observation_month": month,
        "part_count": 1,
        "row_count": int(part_receipt["row_count"]),
        "part_key": part_receipt["key"],
        "part_sha256": part_receipt["sha256"],
        "base_lineage_sha256": base_lineage_sha256,
        "input_manifest_sha256": input_manifest_sha256,
    }
    payload = _json_bytes(marker)
    return marker_receipt(key, payload, int(part_receipt["row_count"])), payload


def verify_month_marker(
    store: SnapshotStore,
    receipt: Mapping[str, Any],
    *,
    product: Product,
    month: str,
    tier: int,
    part_receipt: Mapping[str, Any],
    input_manifest_sha256: str,
    base_lineage_sha256: str,
) -> None:
    payload = verify_receipt(store, receipt)
    if int(receipt["row_count"]) != int(part_receipt["row_count"]):
        raise BreakdownError(f"monthly tier marker receipt changed its row binding: {receipt['key']!r}")
    marker = _json_object(payload, key=str(receipt["key"]))
    expected = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "product_key": product.product_key,
        "data_source_key": EXPECTED_SOURCE_KEY,
        "tier": tier,
        "observation_month": month,
        "part_count": 1,
        "row_count": int(part_receipt["row_count"]),
        "part_key": part_receipt["key"],
        "part_sha256": part_receipt["sha256"],
        "base_lineage_sha256": base_lineage_sha256,
        "input_manifest_sha256": input_manifest_sha256,
    }
    if marker != expected:
        raise BreakdownError(f"monthly tier marker changed semantics: {receipt['key']!r}")


def base_checkpoint_sha256(
    store: SnapshotStore,
    root: str,
    month: str,
    expected_checkpoint: Mapping[str, Any],
) -> str:
    key = month_checkpoint_key(root, month)
    payload = store.get(key, max_bytes=2_000_000)
    if payload is None or _json_object(payload, key=key) != expected_checkpoint:
        raise BreakdownError(f"tier build is not bound to the exact base checkpoint {key!r}")
    return _sha256(payload)


def load_lane_table(store: SnapshotStore, receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = verify_receipt(store, receipt, schema=LANE_SCHEMA)
    return pq.read_table(io.BytesIO(payload)).to_pylist()


def validate_tier_checkpoint_semantics(
    store: SnapshotStore,
    checkpoint: Mapping[str, Any],
    *,
    root: str,
    product: Product,
    month: str,
    base_receipt: Mapping[str, Any],
    base_checkpoint_digest: str,
    input_manifest_sha256: str,
) -> None:
    tiers = checkpoint.get("tiers")
    markers = checkpoint.get("markers")
    if not isinstance(tiers, Mapping) or set(tiers) != {str(tier) for tier in ZOOM_TIERS}:
        raise BreakdownError(f"{product.lane} {month} tier checkpoint has an invalid part map")
    if not isinstance(markers, Mapping) or set(markers) != {str(tier) for tier in ZOOM_TIERS}:
        raise BreakdownError(f"{product.lane} {month} tier checkpoint has an invalid marker map")
    if tiers["13"] != base_receipt or checkpoint.get("base_checkpoint_sha256") != base_checkpoint_digest:
        raise BreakdownError(f"{product.lane} {month} tier checkpoint changed its exact base binding")
    expected_output_keys: set[str] = set()
    base_lineage = str(checkpoint.get("base_lineage_sha256"))
    base_rows = load_lane_table(store, base_receipt)
    expected_base_lineage = lineage_digest(str(row["lineage_sha256"]) for row in base_rows)
    if not _SHA256.fullmatch(base_lineage) or base_lineage != expected_base_lineage:
        raise BreakdownError(f"{product.lane} {month} tier checkpoint has an invalid base lineage digest")
    for tier in ZOOM_TIERS:
        part = tiers[str(tier)]
        marker = markers[str(tier)]
        if not isinstance(part, Mapping) or not isinstance(marker, Mapping):
            raise BreakdownError(f"{product.lane} {month} tier checkpoint has a non-object receipt")
        expected_part_key = f"{month_directory(root, tier, month)}/part-0.parquet"
        expected_marker_key = f"{month_directory(root, tier, month)}/_complete.json"
        if part.get("key") != expected_part_key or marker.get("key") != expected_marker_key:
            raise BreakdownError(f"{product.lane} {month} tier receipt escaped its monthly path")
        verify_receipt(store, part, schema=LANE_SCHEMA)
        verify_month_marker(
            store,
            marker,
            product=product,
            month=month,
            tier=tier,
            part_receipt=part,
            input_manifest_sha256=input_manifest_sha256,
            base_lineage_sha256=base_lineage,
        )
        if tier != 13:
            expected_output_keys.add(str(part["key"]))
        expected_output_keys.add(str(marker["key"]))
    output_objects = checkpoint.get("output_objects")
    if not isinstance(output_objects, list) or {str(item["key"]) for item in output_objects} != expected_output_keys:
        raise BreakdownError(f"{product.lane} {month} tier checkpoint output inventory changed")


def compute_tier_month(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    product: Product,
    month: str,
    base_checkpoint: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes, dict[str, tuple[bytes, str]]]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    base_digest = base_checkpoint_sha256(store, root, month, base_checkpoint)
    base_receipt = base_checkpoint.get("base_part")
    if not isinstance(base_receipt, Mapping):
        raise BreakdownError(f"base checkpoint for {product.lane} {month} has no monthly z13 part")
    output_objects: list[dict[str, Any]] = []
    output_payloads: dict[str, tuple[bytes, str]] = {}
    base_rows = load_lane_table(store, base_receipt)
    if not base_rows:
        raise BreakdownError(f"base part for {product.lane} {month} is empty")
    base_grain = {
        (
            row["data_source_key"],
            row["source_parameter"],
            row["support_key"],
            row["signal_name"],
            row["normalized_unit"],
            row["cell_id"],
            row["observed_day"],
        )
        for row in base_rows
    }
    if len(base_grain) != len(base_rows):
        raise BreakdownError(f"base part for {product.lane} {month} violates its unique grain")
    if any(
        row["data_source_key"] != EXPECTED_SOURCE_KEY
        or row["source_parameter"] != product.parameter
        or row["signal_name"] != product.signal_name
        or row["normalized_unit"] != EXPECTED_UNIT
        or row["support_key"] != EXPECTED_SUPPORT_KEY
        or str(row["observed_day"])[:7] != month
        or row["input_manifest_sha256"] != contract.manifest_sha256
        for row in base_rows
    ):
        raise BreakdownError(f"base part for {product.lane} {month} contains another product, month, or input")
    base_lineage = lineage_digest(str(row["lineage_sha256"]) for row in base_rows)
    tier_receipts: dict[str, Mapping[str, Any]] = {"13": base_receipt}
    for tier in (9, 5, 0):
        derived_rows = derive_tier_rows(base_rows, tier)
        key = f"{month_directory(root, tier, month)}/part-0.parquet"
        payload = serialize_table(derived_rows, LANE_SCHEMA, LANE_SORT_COLUMNS)
        receipt = table_receipt(key, payload, len(derived_rows))
        tier_receipts[str(tier)] = receipt
        output_objects.append(receipt)
        output_payloads[key] = (payload, PARQUET_CONTENT_TYPE)
    marker_receipts: dict[str, Mapping[str, Any]] = {}
    for tier in ZOOM_TIERS:
        marker, marker_payload = compute_month_marker(
            root=root,
            product=product,
            month=month,
            tier=tier,
            part_receipt=tier_receipts[str(tier)],
            input_manifest_sha256=contract.manifest_sha256,
            base_lineage_sha256=base_lineage,
        )
        marker_receipts[str(tier)] = marker
        output_objects.append(marker)
        output_payloads[str(marker["key"])] = (marker_payload, JSON_CONTENT_TYPE)
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "product_key": product.product_key,
        "data_source_key": EXPECTED_SOURCE_KEY,
        "support_key": EXPECTED_SUPPORT_KEY,
        "signal_name": product.signal_name,
        "normalized_unit": EXPECTED_UNIT,
        "observation_month": month,
        "input_snapshot_id": snapshot_id,
        "input_manifest_sha256": contract.manifest_sha256,
        "base_checkpoint_key": month_checkpoint_key(root, month),
        "base_checkpoint_sha256": base_digest,
        "base_lineage_sha256": base_lineage,
        "tiers": {tier: dict(receipt) for tier, receipt in tier_receipts.items()},
        "markers": {tier: dict(receipt) for tier, receipt in marker_receipts.items()},
        "output_objects": output_objects,
    }
    return checkpoint, _json_bytes(checkpoint), output_payloads


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
    checkpoint, checkpoint_payload, output_payloads = compute_tier_month(
        store,
        contract,
        object_store_prefix=object_store_prefix,
        output_prefix=output_prefix,
        snapshot_id=snapshot_id,
        product=product,
        month=month,
        base_checkpoint=base_checkpoint,
    )
    existing_payload = store.get(checkpoint_key, max_bytes=4_000_000)
    if existing_payload is not None:
        if existing_payload != checkpoint_payload:
            raise BreakdownError(f"tier checkpoint {checkpoint_key!r} differs from fresh deterministic computation")
        for key, (expected_payload, _content_type) in output_payloads.items():
            actual_payload = store.get(key, max_bytes=len(expected_payload) + 1)
            if actual_payload != expected_payload:
                raise BreakdownError(f"resumed tier object differs from fresh deterministic computation: {key!r}")
        validate_tier_checkpoint_semantics(
            store,
            checkpoint,
            root=root,
            product=product,
            month=month,
            base_receipt=base_checkpoint["base_part"],
            base_checkpoint_digest=str(checkpoint["base_checkpoint_sha256"]),
            input_manifest_sha256=contract.manifest_sha256,
        )
        return checkpoint
    for key, (payload, content_type) in output_payloads.items():
        store.put_immutable(key, payload, content_type=content_type)
    store.put_immutable(checkpoint_key, checkpoint_payload, content_type=JSON_CONTENT_TYPE)
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
            for metadata in product_part_metadata(ledger, contract, product):
                key = str(metadata["key"])
                if key in expected:
                    raise BreakdownError(f"canonical part appears in two bounded ledgers: {key!r}")
                expected[key] = metadata
    if len(expected) != EXPECTED_SOURCE_PARTS_PER_PRODUCT:
        raise BreakdownError(
            f"{product.lane} has {len(expected)} pinned source parts; expected {EXPECTED_SOURCE_PARTS_PER_PRODUCT}"
        )
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
) -> dict[str, Any]:
    root = lane_root(object_store_prefix, output_prefix, snapshot_id, product)
    expected_raw = expected_input_parts(store, contract, product)
    consumed_raw: dict[str, Mapping[str, Any]] = {}
    expected_output_keys: set[str] = set()
    object_receipts: dict[str, dict[str, Any]] = {}
    aggregate = Counter()
    all_days: list[str] = []
    input_month_digests: list[str] = []
    selected_month_digests: list[str] = []
    provenance_month_digests: list[str] = []
    multiplicity_histogram: Counter[int] = Counter()
    rejection_counts: Counter[str] = Counter()
    for checkpoint in base_checkpoints:
        checkpoint_month = str(checkpoint["observation_month"])
        current_input_receipts = [
            input_part_receipt(part) for part in month_input_parts(store, contract, product, checkpoint_month)
        ]
        if checkpoint.get("input_parts") != current_input_receipts:
            raise BreakdownError(f"{product.lane} {checkpoint_month} checkpoint changed its ledger input receipts")
        validate_checkpoint_objects(store, checkpoint)
        validate_base_checkpoint_semantics(
            checkpoint,
            root=root,
            product=product,
            month=checkpoint_month,
        )
        base_checkpoint_key = month_checkpoint_key(root, checkpoint_month)
        expected_output_keys.add(base_checkpoint_key)
        object_receipts[base_checkpoint_key] = checkpoint_object_receipt(
            store,
            base_checkpoint_key,
            checkpoint,
            kind="base_checkpoint",
        )
        for receipt in checkpoint["output_objects"]:
            receipt_key = str(receipt["key"])
            expected_output_keys.add(receipt_key)
            receipt_kind = "provenance" if "/_provenance/" in receipt_key else "z13_data"
            object_receipts[receipt_key] = inventory_object_receipt(receipt, kind=receipt_kind)
        provenance_receipts = [
            receipt for receipt in checkpoint["output_objects"] if "/_provenance/" in str(receipt["key"])
        ]
        expected_provenance_receipts = 1 if int(checkpoint["input_physical_rows"]) else 0
        if len(provenance_receipts) != expected_provenance_receipts:
            raise BreakdownError(f"{product.lane} checkpoint has an invalid provenance object count")
        if provenance_receipts:
            verified_lineage = verify_provenance_lineage(
                store,
                provenance_receipts[0],
                checkpoint["input_parts"],
            )
            if (
                verified_lineage["row_count"] != int(checkpoint["input_physical_rows"])
                or verified_lineage["source_part_count"] != int(checkpoint["provenance_source_part_count"])
                or verified_lineage["source_digest"] != checkpoint["provenance_source_digest"]
            ):
                raise BreakdownError(f"{product.lane} checkpoint provenance lineage digest changed")
            provenance_month_digests.append(str(verified_lineage["source_digest"]))
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
        for reason, count in checkpoint["rejection_counts"].items():
            rejection_counts[str(reason)] += int(count)
        input_month_digests.append(str(checkpoint["input_row_digest"]))
        selected_month_digests.append(str(checkpoint["selected_lineage_digest"]))
        all_days.extend(str(value) for value in checkpoint["data_days"])
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
        raise BreakdownError(
            f"{product.lane} has explicitly classified exclusions and cannot be published: {dict(rejection_counts)}"
        )
    if len(all_days) != len(set(all_days)):
        raise BreakdownError(f"{product.lane} base checkpoints overlap day partitions")

    tier_totals: dict[str, dict[str, int]] = {
        str(tier): {"row_count": 0, "part_count": 0, "byte_count": 0, "marker_count": 0} for tier in ZOOM_TIERS
    }
    base_by_month = {str(checkpoint["observation_month"]): checkpoint for checkpoint in base_checkpoints}
    if len(base_by_month) != EXPECTED_MONTH_COUNT:
        raise BreakdownError(f"{product.lane} does not have exactly {EXPECTED_MONTH_COUNT} base checkpoints")
    tier_months: set[str] = set()
    for checkpoint in tier_checkpoints:
        month = str(checkpoint["observation_month"])
        if month in tier_months or month not in base_by_month:
            raise BreakdownError(f"{product.lane} tier checkpoints overlap or add an unknown month: {month}")
        tier_months.add(month)
        base_checkpoint = base_by_month[month]
        base_receipt = base_checkpoint.get("base_part")
        if not isinstance(base_receipt, Mapping):
            raise BreakdownError(f"{product.lane} {month} base checkpoint has no monthly z13 receipt")
        base_digest = base_checkpoint_sha256(store, root, month, base_checkpoint)
        validate_checkpoint_objects(store, checkpoint)
        validate_tier_checkpoint_semantics(
            store,
            checkpoint,
            root=root,
            product=product,
            month=month,
            base_receipt=base_receipt,
            base_checkpoint_digest=base_digest,
            input_manifest_sha256=contract.manifest_sha256,
        )
        tier_checkpoint_object_key = tier_checkpoint_key(root, month)
        expected_output_keys.add(tier_checkpoint_object_key)
        object_receipts[tier_checkpoint_object_key] = checkpoint_object_receipt(
            store,
            tier_checkpoint_object_key,
            checkpoint,
            kind="tier_checkpoint",
        )
        for receipt in checkpoint["output_objects"]:
            receipt_key = str(receipt["key"])
            expected_output_keys.add(receipt_key)
            receipt_kind = "tier_marker" if receipt_key.endswith("/_complete.json") else "coarse_data"
            object_receipts[receipt_key] = inventory_object_receipt(receipt, kind=receipt_kind)
        for tier, receipt in checkpoint["tiers"].items():
            target = tier_totals[str(tier)]
            target["row_count"] += int(receipt["row_count"])
            target["part_count"] += 1
            target["byte_count"] += int(receipt["byte_count"])
        for tier in ZOOM_TIERS:
            tier_totals[str(tier)]["marker_count"] += 1
    if tier_months != set(base_by_month):
        raise BreakdownError(f"{product.lane} tier months do not equal its base months")
    if any(
        totals["part_count"] != EXPECTED_MONTH_COUNT or totals["marker_count"] != EXPECTED_MONTH_COUNT
        for totals in tier_totals.values()
    ):
        raise BreakdownError(f"{product.lane} does not have one part and marker per month and tier")
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
    if sum(int(checkpoint["provenance_source_part_count"]) for checkpoint in base_checkpoints) != len(expected_raw):
        raise BreakdownError(f"{product.lane} provenance does not reconcile every canonical source part")

    manifest_key = f"{root}/manifest.json"
    completion_key = f"{root}/_COMPLETE"
    actual_objects = set(store.list_keys(f"{root}/"))
    if completion_key in actual_objects and manifest_key not in actual_objects:
        raise BreakdownError(f"{product.lane} has a completion marker without its manifest")
    actual_before = actual_objects - {manifest_key, completion_key}
    if actual_before != expected_output_keys:
        raise BreakdownError(
            f"{product.lane} output inventory mismatch before publication; "
            f"missing={sorted(expected_output_keys - actual_before)[:5]} "
            f"unexpected={sorted(actual_before - expected_output_keys)[:5]}"
        )
    if set(object_receipts) != expected_output_keys:
        raise BreakdownError(f"{product.lane} cryptographic receipt inventory does not cover every premanifest object")
    ordered_object_receipts = [object_receipts[key] for key in sorted(object_receipts)]
    object_inventory_sha256 = receipt_inventory_root(ordered_object_receipts)
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "lane": product.lane,
        "product_parameter": product.parameter,
        "product_key": product.product_key,
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
        "rejection_counts": dict(sorted(rejection_counts.items())),
        "duplicate_group_count": aggregate["duplicate_group_count"],
        "max_multiplicity": max(multiplicity_histogram, default=0),
        "multiplicity_histogram": {str(key): value for key, value in sorted(multiplicity_histogram.items())},
        "input_part_count": len(expected_raw),
        "input_byte_count": sum(int(value["byte_count"]) for value in expected_raw.values()),
        "provenance_row_count": provenance_rows,
        "provenance_byte_count": provenance_bytes,
        "provenance_source_part_count": len(expected_raw),
        "provenance_source_digest": lineage_digest(provenance_month_digests),
        "data_day_count": len(all_days),
        "observation_day_min": min(all_days) if all_days else None,
        "observation_day_max": max(all_days) if all_days else None,
        "input_month_digest": lineage_digest(input_month_digests),
        "selected_month_digest": lineage_digest(selected_month_digests),
        "provenance_schema": schema_manifest(PROVENANCE_SCHEMA),
        "lane_schema": schema_manifest(LANE_SCHEMA),
        "tiers": tier_totals,
        "checkpoint_count": len(base_checkpoints) + len(tier_checkpoints),
        "object_count_before_manifest": len(expected_output_keys),
        "object_receipts": ordered_object_receipts,
        "object_inventory_sha256": object_inventory_sha256,
        "verified_at": contract.manifest["verified_at"],
        "reconciliation": {
            "physical_equals_eligible_plus_rejected": True,
            "eligible_equals_selected_plus_superseded": True,
            "provenance_equals_physical": True,
            "provenance_source_parts_equal_input_parts": True,
            "provenance_rows_bind_source_part_sha256_and_ordinal": True,
            "z13_equals_selected": True,
            "all_months_have_one_part_and_marker_per_tier": list(ZOOM_TIERS),
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
        "object_inventory_sha256": manifest["object_inventory_sha256"],
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
        "manifest_receipt": {
            "key": manifest_key,
            "byte_count": len(manifest_payload),
            "sha256": _sha256(manifest_payload),
        },
        "completion_receipt": {
            "key": completion_key,
            "byte_count": len(completion_payload),
            "sha256": _sha256(completion_payload),
        },
        "_complete_key": completion_key,
    }


def inventory_products(store: SnapshotStore, contract: InputContract) -> dict[str, Any]:
    scope = ledger_scope_census(store, contract)
    reports: dict[str, Any] = {}
    union_keys: set[str] = set()
    for product in PRODUCTS:
        parts = scope["included_parts"][product.lane]
        if set(parts) & union_keys:
            raise BreakdownError(f"soil-temperature scope overlaps at {sorted(set(parts) & union_keys)[:1]}")
        union_keys.update(parts)
        dimensions = [part_dimensions(metadata) for metadata in parts.values()]
        months = {f"{value['year']}-{value['month']}" for value in dimensions}
        reports[product.lane] = {
            "product_parameter": product.parameter,
            "product_key": product.product_key,
            "signal_name": product.signal_name,
            **descriptor_totals(parts.values()),
            "first_month": min(months) if months else None,
            "last_month": max(months) if months else None,
            "sources": sorted({value["source"] for value in dimensions}),
            "supports": sorted({value["support"] for value in dimensions}),
        }
        if len(parts) != EXPECTED_SOURCE_PARTS_PER_PRODUCT:
            raise BreakdownError(
                f"{product.lane} has {len(parts)} pinned source parts; expected {EXPECTED_SOURCE_PARTS_PER_PRODUCT}"
            )
    return {
        "input_snapshot_id": contract.manifest["snapshot_id"],
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "input_fact_rows": contract.manifest["row_count"],
        "input_fact_parts": contract.manifest["partition_count"],
        "input_fact_bytes": contract.manifest["fact_byte_count"],
        "bounded_ledger_count": len(contract.units),
        "products": reports,
        "included_scope": scope["included_totals"],
        "scope_exclusions": scope["scope_exclusions"],
        "non_overlapping_product_filters": True,
        "all_fact_descriptors_classified_once": True,
        "snapshot_equals_included_plus_scope_exclusions": all(
            int(scope["manifest_totals"][field])
            == int(scope["included_totals"][field])
            + sum(int(values[field]) for values in scope["scope_exclusions"].values())
            for field in ("part_count", "row_count", "byte_count")
        ),
    }


def finalize_bundle(
    store: SnapshotStore,
    contract: InputContract,
    *,
    object_store_prefix: str,
    output_prefix: str,
    snapshot_id: str,
    lanes: Sequence[Mapping[str, Any]],
    ledger_verification: Mapping[str, Any],
) -> dict[str, Any]:
    dedicated = dedicated_output_prefix(output_prefix)
    root = _prefixed(
        object_store_prefix,
        f"{dedicated}/_manifests/soil-temperature/snapshot={snapshot_id}",
    )
    if len(lanes) != len(PRODUCTS) or {str(lane["lane"]) for lane in lanes} != {product.lane for product in PRODUCTS}:
        raise BreakdownError("soil-temperature bundle requires exactly the four non-overlapping product lanes")
    lane_publication_receipts: list[dict[str, Any]] = []
    for lane in lanes:
        manifest_receipt = lane.get("manifest_receipt")
        completion_receipt = lane.get("completion_receipt")
        if not isinstance(manifest_receipt, Mapping) or not isinstance(completion_receipt, Mapping):
            raise BreakdownError(f"lane {lane.get('lane')!r} has no publication receipts")
        verify_byte_receipt(store, manifest_receipt)
        verify_byte_receipt(store, completion_receipt)
        lane_publication_receipts.append(
            {
                "lane": str(lane["lane"]),
                "manifest_receipt": dict(manifest_receipt),
                "completion_receipt": dict(completion_receipt),
                "object_inventory_sha256": str(lane["object_inventory_sha256"]),
            }
        )
    lane_publication_receipts.sort(key=lambda receipt: receipt["lane"])
    lane_publication_inventory_sha256 = _sha256(_json_bytes(lane_publication_receipts))
    scope = ledger_scope_census(store, contract)
    expected_parts: dict[str, Mapping[str, Any]] = {}
    for product in PRODUCTS:
        for key, metadata in scope["included_parts"][product.lane].items():
            if key in expected_parts:
                raise BreakdownError(f"soil-temperature bundle source products overlap at {key!r}")
            expected_parts[key] = metadata
    physical_rows = sum(int(lane["physical_scope_rows"]) for lane in lanes)
    eligible_rows = sum(int(lane["eligible_rows"]) for lane in lanes)
    selected_rows = sum(int(lane["selected_rows"]) for lane in lanes)
    superseded_rows = sum(int(lane["superseded_rows"]) for lane in lanes)
    rejected_rows = sum(int(lane["rejected_rows"]) for lane in lanes)
    if physical_rows != eligible_rows + rejected_rows or eligible_rows != selected_rows + superseded_rows:
        raise BreakdownError("four-lane soil-temperature bundle does not reconcile to its canonical product population")
    if (
        physical_rows != int(scope["included_totals"]["row_count"])
        or sum(int(lane["input_part_count"]) for lane in lanes) != len(expected_parts)
        or sum(int(lane["input_byte_count"]) for lane in lanes) != int(scope["included_totals"]["byte_count"])
    ):
        raise BreakdownError("soil-temperature bundle does not reconcile to the pinned-ledger source-part census")
    source_part_receipt_digest = lineage_digest(
        f"{key}:{part['sha256']}:{part['row_count']}:{part['byte_count']}:{part['row_digest']}"
        for key, part in expected_parts.items()
    )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "bundle": "era5-land-soil-temperature",
        "bundle_prefix": f"{root}/",
        "input_snapshot_id": snapshot_id,
        "input_snapshot_prefix": f"{contract.root}/",
        "input_manifest_key": contract.manifest_key,
        "input_manifest_sha256": contract.manifest_sha256,
        "product_parameters": [product.parameter for product in PRODUCTS],
        "product_keys": [product.product_key for product in PRODUCTS],
        "source_key": EXPECTED_SOURCE_KEY,
        "support_key": EXPECTED_SUPPORT_KEY,
        "normalized_unit": EXPECTED_UNIT,
        "physical_scope_rows": physical_rows,
        "eligible_rows": eligible_rows,
        "selected_rows": selected_rows,
        "superseded_rows": superseded_rows,
        "rejected_rows": rejected_rows,
        "source_part_count": len(expected_parts),
        "source_part_byte_count": sum(int(part["byte_count"]) for part in expected_parts.values()),
        "source_part_receipt_digest": source_part_receipt_digest,
        "snapshot_fact_scope": scope["manifest_totals"],
        "included_descriptor_scope": scope["included_totals"],
        "descriptor_scope_exclusions": scope["scope_exclusions"],
        "full_ledger_verification": dict(ledger_verification),
        "lane_publication_receipts": lane_publication_receipts,
        "lane_publication_inventory_sha256": lane_publication_inventory_sha256,
        "lane_count": len(lanes),
        "lanes": [
            {
                "lane": lane["lane"],
                "product_parameter": lane["product_parameter"],
                "product_key": lane["product_key"],
                "lane_prefix": lane["lane_prefix"],
                "manifest_key": lane["manifest_key"],
                "manifest_sha256": lane["manifest_sha256"],
                "manifest_receipt": lane["manifest_receipt"],
                "completion_receipt": lane["completion_receipt"],
                "object_inventory_sha256": lane["object_inventory_sha256"],
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
            "product_partitions_are_pairwise_disjoint": True,
            "lane_physical_rows_equal_pinned_ledger_rows": True,
            "lane_input_parts_equal_pinned_ledger_parts": True,
            "lane_input_bytes_equal_pinned_ledger_bytes": True,
            "all_snapshot_fact_descriptors_classified_once": True,
            "snapshot_facts_equal_included_plus_scope_exclusions": all(
                int(scope["manifest_totals"][field])
                == int(scope["included_totals"][field])
                + sum(int(values[field]) for values in scope["scope_exclusions"].values())
                for field in ("part_count", "row_count", "byte_count")
            ),
            "provenance_preserves_source_part_sha256_and_row_ordinal": all(
                lane["reconciliation"]["provenance_rows_bind_source_part_sha256_and_ordinal"] for lane in lanes
            ),
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
    existing_objects = set(store.list_keys(f"{root}/"))
    unexpected = existing_objects - {manifest_key, completion_key}
    if unexpected:
        raise BreakdownError(f"soil-temperature bundle prefix contains unexpected objects: {sorted(unexpected)[:5]}")
    if completion_key in existing_objects and manifest_key not in existing_objects:
        raise BreakdownError("soil-temperature bundle has a completion marker without its manifest")
    manifest_payload = _json_bytes(manifest)
    store.put_immutable(manifest_key, manifest_payload, content_type=JSON_CONTENT_TYPE)
    completion = {
        "contract_version": CONTRACT_VERSION,
        "bundle": "era5-land-soil-temperature",
        "manifest_key": manifest_key,
        "manifest_sha256": _sha256(manifest_payload),
        "input_manifest_sha256": contract.manifest_sha256,
        "physical_scope_rows": physical_rows,
        "selected_rows": selected_rows,
        "lane_count": len(lanes),
        "lane_publication_inventory_sha256": lane_publication_inventory_sha256,
        "completed_at": manifest["verified_at"],
    }
    completion_payload = _json_bytes(completion)
    store.put_immutable(completion_key, completion_payload, content_type=JSON_CONTENT_TYPE)
    actual = set(store.list_keys(f"{root}/"))
    if actual != {manifest_key, completion_key}:
        raise BreakdownError("soil-temperature bundle prefix contains an unexpected object")
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
    progress: Callable[[str], None],
) -> dict[str, Any]:
    ledger_scope_census(store, contract)
    ledger_verification = verify_all_ledger_rows(store, contract, progress=progress)
    months = product_months(contract)
    checkpoints_by_lane: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
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
                f"monthly_parts={len(checkpoint['tiers'])} monthly_markers={len(checkpoint['markers'])}"
            )
        checkpoints_by_lane[product.lane] = (base_checkpoints, tier_checkpoints)

    exclusion_counts: Counter[str] = Counter()
    for base_checkpoints, _tier_checkpoints in checkpoints_by_lane.values():
        for checkpoint in base_checkpoints:
            for reason, count in checkpoint["rejection_counts"].items():
                exclusion_counts[str(reason)] += int(count)
    if exclusion_counts:
        raise BreakdownError(
            "soil-temperature bundle has explicitly classified exclusions; no lane manifests were published: "
            f"{dict(sorted(exclusion_counts.items()))}"
        )

    lane_manifests: list[dict[str, Any]] = []
    for product in PRODUCTS:
        base_checkpoints, tier_checkpoints = checkpoints_by_lane[product.lane]
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
        ledger_verification=ledger_verification,
    )
    progress(
        f"bundle=era5-land-soil-temperature phase=complete physical={bundle['physical_scope_rows']} "
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
    result.add_argument("--retry-attempts", type=int, default=8)
    result.add_argument("--retry-base-delay", type=float, default=0.5)
    result.add_argument("--json", action="store_true")
    return result


def main() -> int:
    arguments = parser().parse_args()
    if arguments.retry_attempts < 1 or arguments.retry_base_delay < 0:
        raise SystemExit("retry attempts must be positive and retry base delay cannot be negative")
    configured = settings_from_file(arguments.env_file)
    credentials = configured.require_object_store()
    store = SnapshotStore.from_credentials(
        credentials,
        retry=RetryPolicy(arguments.retry_attempts, arguments.retry_base_delay),
    )
    contract = load_input_contract(
        store,
        object_store_prefix=configured.object_store_prefix,
        input_prefix=DEFAULT_INPUT_PREFIX,
        snapshot_id=DEFAULT_SNAPSHOT_ID,
        expected_manifest_sha256=DEFAULT_INPUT_MANIFEST_SHA256,
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
            output_prefix=DEFAULT_OUTPUT_PREFIX,
            snapshot_id=DEFAULT_SNAPSHOT_ID,
            progress=progress,
        )
    )
    print(json.dumps(report, indent=2 if arguments.json else None, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
