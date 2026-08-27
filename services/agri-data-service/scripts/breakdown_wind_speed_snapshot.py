"""Build the immutable Wind Speed lane from one pinned canonical signal snapshot.

The destination is intentionally outside the live reader layout. See `scripts/AGENTS.md` for the
snapshot, precedence, checkpoint, and promotion boundaries.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Protocol, TypeVar

import boto3  # type: ignore[import-untyped]
import polars as pl
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

from agri_data_service.config import ObjectStoreCredentials, Settings
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.parquet.tiers import derive_tier
from agri_data_service.warehouse.schemas.climate_field_wind_speed import (
    CLIMATE_FIELD_WIND_SPEED_SCHEMA,
    CLIMATE_FIELD_WIND_SPEED_STREAM,
)

SOURCE_SNAPSHOT_ID: Final = "prod-20260826-full-signal-v1"
SOURCE_PREFIX: Final = f"raw-canonical/signal-observation/snapshot={SOURCE_SNAPSHOT_ID}"
SOURCE_MANIFEST_SHA256: Final = "465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f"
DESTINATION_PREFIX: Final = f"layer={CLIMATE_FIELD_WIND_SPEED_STREAM}/snapshot={SOURCE_SNAPSHOT_ID}"

CONTRACT_VERSION: Final = "plantgeo.climate-field-wind-speed.snapshot.v1"
PRECEDENCE_VERSION: Final = "wind-speed.nasa-power-daily-ws2m.latest-release-then-id.v1"
RAW_CONTRACT_VERSION: Final = "agri.signal_observation.raw-canonical.v1"
SOURCE_PART_PREFIX: Final = "source=nasa-power-daily/product=WS2M/support=surface/"
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"
ZOOM_TIERS: Final = (13, 9, 5, 0)

EXPECTED_RAW_ROWS: Final = 1_166_676
EXPECTED_WINNER_ROWS: Final = 619_320
EXPECTED_DAY_COUNT: Final = 1_560
EXPECTED_CELLS_PER_DAY: Final = 397
EXPECTED_FIRST_DAY: Final = date(2022, 4, 30)
EXPECTED_LAST_DAY: Final = date(2026, 8, 6)
LEGACY_COARSE_DAY_COUNT: Final = 1_338
EXPECTED_REPAIRED_COARSE_DAYS: Final = 222
REPAIRED_COARSE_FIRST_DAY: Final = date(2025, 12, 28)

SERVING_SORT: Final = tuple(CLIMATE_FIELD_WIND_SPEED_SCHEMA.sort_columns)
SERVING_SCHEMA: Final = CLIMATE_FIELD_WIND_SPEED_SCHEMA.arrow_schema

PROVENANCE_SCHEMA: Final = pa.schema(
    [
        pa.field("source_snapshot_id", pa.string(), nullable=False),
        pa.field("source_manifest_sha256", pa.string(), nullable=False),
        pa.field("source_ledger_key", pa.string(), nullable=False),
        pa.field("source_ledger_sha256", pa.string(), nullable=False),
        pa.field("source_part_key", pa.string(), nullable=False),
        pa.field("source_part_sha256", pa.string(), nullable=False),
        pa.field("raw_observation_id", pa.int64(), nullable=False),
        pa.field("canonical_row_sha256", pa.string(), nullable=False),
        pa.field("source_release_id", pa.string(), nullable=False),
        pa.field("source_release_retrieved_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("data_source_key", pa.string(), nullable=False),
        pa.field("source_parameter", pa.string(), nullable=False),
        pa.field("support_key", pa.string(), nullable=False),
        pa.field("signal_name", pa.string(), nullable=False),
        pa.field("normalized_unit", pa.string(), nullable=False),
        pa.field("cell_id", pa.string(), nullable=False),
        pa.field("observation_day", pa.date32(), nullable=False),
        pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("normalized_value", pa.float64()),
        pa.field("coverage_fraction", pa.float64()),
        pa.field("allowed_client_exposure", pa.bool_(), nullable=False),
        pa.field("eligible_for_serving", pa.bool_(), nullable=False),
        pa.field("exclusion_reason", pa.string()),
        pa.field("precedence_rank", pa.int32()),
        pa.field("is_selected_winner", pa.bool_(), nullable=False),
        pa.field("winner_observation_id", pa.int64()),
        pa.field("duplicate_count", pa.int64(), nullable=False),
    ],
    metadata={b"plantgeo_contract": CONTRACT_VERSION.encode("ascii")},
)
PROVENANCE_SORT: Final = (
    "observation_day",
    "cell_id",
    "source_release_retrieved_at",
    "raw_observation_id",
)

_T = TypeVar("_T")
_RETRYABLE_CODES: Final = {
    "500",
    "502",
    "503",
    "504",
    "InternalError",
    "RequestTimeout",
    "SlowDown",
    "Throttling",
}
_PRECONDITION_CODES: Final = {"412", "PreconditionFailed"}
_MODULUS: Final = 1 << 256


class BreakdownError(RuntimeError):
    """A snapshot input or output failed a closed reconciliation gate."""


class ImmutableConflictError(BreakdownError):
    """An immutable destination key already holds different bytes."""


class S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def client_error_code(error: ClientError) -> str | None:
    detail = getattr(error, "response", {}).get("Error", {})
    code = detail.get("Code") if isinstance(detail, Mapping) else None
    return None if code is None else str(code)


def retryable(error: BaseException) -> bool:
    if isinstance(error, (ConnectionClosedError, EndpointConnectionError, ReadTimeoutError, TimeoutError)):
        return True
    return isinstance(error, ClientError) and client_error_code(error) in _RETRYABLE_CODES


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 8
    base_delay_seconds: float = 0.5

    def run(self, operation: Any) -> Any:
        for attempt in range(self.attempts):
            try:
                return operation()
            except BaseException as error:
                if not retryable(error) or attempt + 1 >= self.attempts:
                    raise
                time.sleep(self.base_delay_seconds * (2**attempt))
        raise AssertionError("unreachable retry exit")


@dataclass(slots=True)
class ImmutableStore:
    bucket: str
    client: S3Client
    prefix: str = ""
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    @classmethod
    def from_credentials(
        cls, credentials: ObjectStoreCredentials, *, prefix: str, retry: RetryPolicy
    ) -> ImmutableStore:
        client: S3Client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
            config=Config(retries={"max_attempts": retry.attempts, "mode": "adaptive"}),
        )
        return cls(bucket=credentials.bucket, client=client, prefix=prefix.strip("/"), retry=retry)

    def key_for(self, relative: str) -> str:
        inner = relative.strip("/")
        return f"{self.prefix}/{inner}" if self.prefix else inner

    def get(self, key: str) -> bytes | None:
        def load() -> bytes | None:
            try:
                response = self.client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as error:
                if client_error_code(error) in {"404", "NoSuchKey", "NotFound"}:
                    return None
                raise
            body = response.get("Body")
            if body is None:
                return None
            payload = body.read()  # type: ignore[attr-defined]
            return payload if isinstance(payload, bytes) else bytes(payload)

        return self.retry.run(load)

    def require(self, key: str) -> bytes:
        payload = self.get(key)
        if payload is None:
            raise BreakdownError(f"required object {key!r} is missing")
        return payload

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
        except ClientError as error:
            if client_error_code(error) not in _PRECONDITION_CODES:
                raise
            existing = self.get(key)
            if existing != payload:
                raise ImmutableConflictError(
                    f"immutable key {key!r} has sha256={sha256(existing or b'')}, attempted={sha256(payload)}"
                ) from error

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self.retry.run(lambda request=request: self.client.list_objects_v2(**request))
            keys.extend(
                str(item["Key"])
                for item in response.get("Contents", [])
                if isinstance(item, Mapping) and isinstance(item.get("Key"), str)
            )
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                return keys
            token = next_token


def parse_json(payload: bytes, *, key: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise BreakdownError(f"object {key!r} is not valid JSON") from error
    if not isinstance(value, dict):
        raise BreakdownError(f"object {key!r} is not a JSON object")
    return value


def schema_manifest(schema: pa.Schema) -> list[dict[str, Any]]:
    return [{"name": item.name, "type": str(item.type), "nullable": item.nullable} for item in schema]


def stable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BreakdownError("non-finite float cannot enter a durable row digest")
        return value
    return value


def row_sum256(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> str:
    total = 0
    for row in rows:
        payload = json_bytes([stable_value(row[column]) for column in columns])
        total = (total + int.from_bytes(hashlib.sha256(payload).digest(), "big")) % _MODULUS
    return f"{total:064x}"


def combine_sum256(values: Iterable[str]) -> str:
    return f"{sum(int(value, 16) for value in values) % _MODULUS:064x}"


def raw_identity_sum256(rows: Iterable[Mapping[str, Any]]) -> str:
    return row_sum256(rows, ("raw_observation_id", "canonical_row_sha256"))


def canonical_identity_sum256(rows: Iterable[Mapping[str, Any]]) -> str:
    return row_sum256(rows, ("id", "canonical_row_sha256"))


def canonical_row_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row_id, row_hash in sorted((int(row["id"]), str(row["canonical_row_sha256"])) for row in rows):
        digest.update(f"{row_id}:{row_hash}\n".encode("ascii"))
    return digest.hexdigest()


def serving_sum256(rows: Iterable[Mapping[str, Any]]) -> str:
    return row_sum256(rows, SERVING_SCHEMA.names)


def serialize_table(table: pa.Table, *, schema: pa.Schema, sort_columns: Sequence[str]) -> bytes:
    if not table.schema.equals(schema, check_metadata=False):
        try:
            table = table.cast(schema)
        except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
            raise BreakdownError(f"table cannot conform to expected schema: {error}") from error
    table = table.replace_schema_metadata(schema.metadata)
    table = table.sort_by([(column, "ascending") for column in sort_columns])
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd", write_statistics=True, row_group_size=64_000)
    return buffer.getvalue()


def serialize_serving(
    rows: Sequence[Mapping[str, Any]] | pl.DataFrame,
    *,
    require_cell_id: bool,
) -> tuple[bytes, pa.Table]:
    table = (
        rows.to_arrow() if isinstance(rows, pl.DataFrame) else pa.Table.from_pylist(list(rows), schema=SERVING_SCHEMA)
    )
    table = conform_to_stream_schema(table, CLIMATE_FIELD_WIND_SPEED_SCHEMA)
    if table.num_rows == 0:
        raise BreakdownError("refusing to write an empty serving part")
    if require_cell_id and table.column("cell_id").null_count:
        raise BreakdownError("base Wind Speed rows must carry cell_id")
    payload = serialize_table(table, schema=SERVING_SCHEMA, sort_columns=SERVING_SORT)
    return payload, pq.read_table(io.BytesIO(payload))


def receipt(
    key: str, payload: bytes, table: pa.Table, *, digest_columns: Sequence[str] | None = None
) -> dict[str, Any]:
    rows = table.to_pylist()
    result: dict[str, Any] = {
        "key": key,
        "row_count": table.num_rows,
        "byte_count": len(payload),
        "sha256": sha256(payload),
    }
    if digest_columns is not None:
        result["row_sum256"] = row_sum256(rows, digest_columns)
        result["digest_columns"] = list(digest_columns)
    return result


def put_or_adopt_parquet(
    store: ImmutableStore,
    key: str,
    payload: bytes,
    table: pa.Table,
    *,
    schema: pa.Schema,
    digest_columns: Sequence[str],
) -> tuple[bytes, pa.Table]:
    """Write immutable bytes, or adopt a crash orphan only when its durable rows are identical."""
    try:
        store.put_immutable(key, payload, content_type=PARQUET_CONTENT_TYPE)
        return payload, table
    except ImmutableConflictError as conflict:
        existing_payload = store.require(key)
        existing_table = pq.read_table(io.BytesIO(existing_payload))
        if (
            not existing_table.schema.equals(schema, check_metadata=True)
            or existing_table.num_rows != table.num_rows
            or row_sum256(existing_table.to_pylist(), digest_columns) != row_sum256(table.to_pylist(), digest_columns)
        ):
            raise conflict
        return existing_payload, existing_table


def verify_receipt(store: ImmutableStore, item: Mapping[str, Any], *, schema: pa.Schema) -> pa.Table:
    key = str(item["key"])
    payload = store.require(key)
    if len(payload) != int(item["byte_count"]) or sha256(payload) != item["sha256"]:
        raise BreakdownError(f"output object {key!r} failed byte reconciliation")
    table = pq.read_table(io.BytesIO(payload))
    if not table.schema.equals(schema, check_metadata=True) or table.num_rows != int(item["row_count"]):
        raise BreakdownError(f"output object {key!r} failed schema/row reconciliation")
    if "row_sum256" in item:
        recorded_columns = item.get("digest_columns")
        if recorded_columns is None:
            columns = (
                schema.names
                if schema.equals(SERVING_SCHEMA, check_metadata=False)
                else (
                    "raw_observation_id",
                    "canonical_row_sha256",
                )
            )
        elif not isinstance(recorded_columns, list) or not all(isinstance(name, str) for name in recorded_columns):
            raise BreakdownError(f"output receipt for {key!r} has invalid digest_columns")
        else:
            columns = tuple(recorded_columns)
        if row_sum256(table.to_pylist(), columns) != item["row_sum256"]:
            raise BreakdownError(f"output object {key!r} failed row digest reconciliation")
    return table


@dataclass(frozen=True, slots=True)
class Dimensions:
    releases: Mapping[str, Mapping[str, Any]]
    sources: Mapping[str, Mapping[str, Any]]


def load_source_contract(store: ImmutableStore) -> tuple[dict[str, Any], Dimensions]:
    manifest_key = store.key_for(f"{SOURCE_PREFIX}/manifest.json")
    manifest_payload = store.require(manifest_key)
    if sha256(manifest_payload) != SOURCE_MANIFEST_SHA256:
        raise BreakdownError("canonical source manifest does not match the pinned SHA-256")
    manifest = parse_json(manifest_payload, key=manifest_key)
    if (
        manifest.get("contract_version") != RAW_CONTRACT_VERSION
        or manifest.get("snapshot_id") != SOURCE_SNAPSHOT_ID
        or int(manifest.get("batch_count", 0)) != 424
    ):
        raise BreakdownError("canonical source manifest identity or extraction-unit count drifted")
    complete_key = store.key_for(f"{SOURCE_PREFIX}/_COMPLETE")
    complete = parse_json(store.require(complete_key), key=complete_key)
    if complete.get("manifest_sha256") != SOURCE_MANIFEST_SHA256:
        raise BreakdownError("canonical source completion marker does not bind the pinned manifest")

    dimension_tables: dict[str, pa.Table] = {}
    for name in ("data_source", "source_release"):
        metadata = manifest["dimension_objects"][name]
        payload = store.require(str(metadata["key"]))
        if len(payload) != int(metadata["byte_count"]) or sha256(payload) != metadata["sha256"]:
            raise BreakdownError(f"canonical {name} dimension failed byte reconciliation")
        table = pq.read_table(io.BytesIO(payload))
        if table.num_rows != int(metadata["row_count"]):
            raise BreakdownError(f"canonical {name} dimension failed row reconciliation")
        dimension_tables[name] = table
    sources = {str(row["id"]): row for row in dimension_tables["data_source"].to_pylist()}
    releases = {str(row["id"]): row for row in dimension_tables["source_release"].to_pylist()}
    return manifest, Dimensions(releases=releases, sources=sources)


def exclusion_reason(row: Mapping[str, Any]) -> str | None:
    reasons: list[str] = []
    if row["signal_name"] != "wind_speed":
        reasons.append("signal_name")
    if row["normalized_unit"] != "m/s":
        reasons.append("normalized_unit")
    if row["is_observed"] is not True:
        reasons.append("not_observed")
    if row["quality_flag"] != "accepted":
        reasons.append("quality_flag")
    if row["normalized_value"] is None:
        reasons.append("normalized_value_null")
    return ",".join(reasons) or None


def source_part_rows(
    store: ImmutableStore,
    part: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
) -> list[dict[str, Any]]:
    key = str(part["key"])
    payload = store.require(key)
    if len(payload) != int(part["byte_count"]) or sha256(payload) != part["sha256"]:
        raise BreakdownError(f"canonical Wind part {key!r} failed byte reconciliation")
    table = pq.read_table(io.BytesIO(payload))
    if table.num_rows != int(part["row_count"]) or schema_manifest(table.schema) != source_manifest["parquet_schema"]:
        raise BreakdownError(f"canonical Wind part {key!r} failed schema/row reconciliation")
    if (table.schema.metadata or {}).get(b"plantgeo_contract") != RAW_CONTRACT_VERSION.encode("ascii"):
        raise BreakdownError(f"canonical Wind part {key!r} lost its raw contract metadata")
    rows = table.to_pylist()
    if canonical_row_digest(rows) != part["row_digest"]:
        raise BreakdownError(f"canonical Wind part {key!r} failed row-digest reconciliation")
    return rows


def unit_keys(store: ImmutableStore, month: str, batch: int) -> tuple[str, str, str]:
    suffix = f"month={month}/cell-batch={batch:05d}"
    return (
        store.key_for(f"{DESTINATION_PREFIX}/_stage/{suffix}/winners.parquet"),
        store.key_for(f"{DESTINATION_PREFIX}/provenance/{suffix}/part-00000.parquet"),
        store.key_for(f"{DESTINATION_PREFIX}/_checkpoints/source-unit/{suffix}.json"),
    )


def validate_bound_unit_checkpoint(
    store: ImmutableStore,
    checkpoint: Mapping[str, Any],
    *,
    summary: Mapping[str, Any],
    ledger_key: str,
    ledger_sha256: str,
    wind_parts: Sequence[Mapping[str, Any]],
    stage_key: str,
    provenance_key: str,
) -> None:
    expected_parts = [
        {
            "key": part["key"],
            "row_count": part["row_count"],
            "byte_count": part["byte_count"],
            "sha256": part["sha256"],
            "row_digest": part["row_digest"],
        }
        for part in wind_parts
    ]
    if (
        checkpoint.get("contract_version") != CONTRACT_VERSION
        or checkpoint.get("precedence_version") != PRECEDENCE_VERSION
        or checkpoint.get("source_snapshot_id") != SOURCE_SNAPSHOT_ID
        or checkpoint.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
        or checkpoint.get("destination_prefix") != DESTINATION_PREFIX + "/"
        or checkpoint.get("observation_month") != summary["observation_month"]
        or checkpoint.get("cell_batch_index") != summary["cell_batch_index"]
        or checkpoint.get("source_ledger_key") != ledger_key
        or checkpoint.get("source_ledger_sha256") != ledger_sha256
        or checkpoint.get("source_parts") != expected_parts
        or checkpoint.get("stage_part", {}).get("key") != stage_key
        or checkpoint.get("provenance_part", {}).get("key") != provenance_key
    ):
        raise BreakdownError("existing Wind source-unit checkpoint is not bound to the current source unit")
    stage = verify_receipt(store, checkpoint["stage_part"], schema=SERVING_SCHEMA)
    provenance = verify_receipt(store, checkpoint["provenance_part"], schema=PROVENANCE_SCHEMA)
    eligible_count = sum(bool(value) for value in provenance.column("eligible_for_serving").to_pylist())
    selected_count = sum(bool(value) for value in provenance.column("is_selected_winner").to_pylist())
    observation_sum = sum(int(value) for value in stage.column("observation_count").to_pylist())
    provenance_identity = checkpoint["provenance_part"].get(
        "raw_identity_sum256",
        checkpoint["provenance_part"]["row_sum256"],
    )
    if (
        checkpoint["raw_identity_sum256"] != provenance_identity
        or int(checkpoint["raw_row_count"]) != provenance.num_rows
        or int(checkpoint["eligible_row_count"]) != eligible_count
        or int(checkpoint["ineligible_row_count"]) != provenance.num_rows - eligible_count
        or int(checkpoint["winner_row_count"]) != stage.num_rows
        or int(checkpoint["winner_row_count"]) != selected_count
        or int(checkpoint["duplicate_row_count"]) != eligible_count - selected_count
        or int(checkpoint["observation_count_sum"]) != observation_sum
        or eligible_count != observation_sum
    ):
        raise BreakdownError("existing Wind source-unit checkpoint failed internal population reconciliation")


def process_unit(
    store: ImmutableStore,
    source_manifest: Mapping[str, Any],
    dimensions: Dimensions,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    month = str(summary["observation_month"])
    batch = int(summary["cell_batch_index"])
    stage_key, provenance_key, checkpoint_key = unit_keys(store, month, batch)
    ledger_key = store.key_for(f"{SOURCE_PREFIX}/_ledger/month={month}/cell-batch={batch:05d}.json")
    ledger_payload = store.require(ledger_key)
    ledger = parse_json(ledger_payload, key=ledger_key)
    for name in ("observation_month", "cell_batch_index", "row_count", "part_count", "byte_count", "source_row_digest"):
        if ledger.get(name) != summary.get(name):
            raise BreakdownError(f"canonical ledger {ledger_key!r} disagrees with its pinned manifest summary")
    wind_parts = [part for part in ledger["parts"] if str(part["relative_path"]).startswith(SOURCE_PART_PREFIX)]
    if not wind_parts:
        raise BreakdownError(f"canonical ledger {ledger_key!r} has no WS2M/surface part")
    existing_payload = store.get(checkpoint_key)
    if existing_payload is not None:
        existing = parse_json(existing_payload, key=checkpoint_key)
        validate_bound_unit_checkpoint(
            store,
            existing,
            summary=summary,
            ledger_key=ledger_key,
            ledger_sha256=sha256(ledger_payload),
            wind_parts=wind_parts,
            stage_key=stage_key,
            provenance_key=provenance_key,
        )
        resumed_source_rows: list[dict[str, Any]] = []
        for part in wind_parts:
            resumed_source_rows.extend(source_part_rows(store, part, source_manifest))
        if (
            len(resumed_source_rows) != int(existing["raw_row_count"])
            or canonical_identity_sum256(resumed_source_rows) != existing["raw_identity_sum256"]
        ):
            raise BreakdownError("resumed Wind checkpoint does not reconcile to current canonical source rows")
        return existing

    raw_rows: list[dict[str, Any]] = []
    for part in wind_parts:
        rows = source_part_rows(store, part, source_manifest)
        for row in rows:
            if (
                row["data_source_key"] != "nasa-power-daily"
                or row["product_key"] != "WS2M"
                or row["source_parameter"] != "WS2M"
                or row["support_key"] != "surface"
                or row["observation_day"].strftime("%Y-%m") != month
            ):
                raise BreakdownError(f"Wind partition identity drift in {part['key']!r}")
            row["_source_part"] = part
            raw_rows.append(row)

    eligible_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    enriched: list[dict[str, Any]] = []
    for row in raw_rows:
        release = dimensions.releases.get(str(row["source_release_id"]))
        if release is None:
            raise BreakdownError(f"Wind fact references absent source release {row['source_release_id']!r}")
        source = dimensions.sources.get(str(release["data_source_id"]))
        if source is None or str(source["id"]) != str(row["data_source_id"]) or source["key"] != row["data_source_key"]:
            raise BreakdownError("Wind fact provenance disagrees with frozen source dimensions")
        item = dict(row)
        item["_release_retrieved_at"] = release["retrieved_at"]
        item["_allowed_client_exposure"] = bool(source["allowed_client_exposure"])
        item["_exclusion_reason"] = exclusion_reason(row)
        enriched.append(item)
        if item["_exclusion_reason"] is None:
            grain = (
                row["support_key"],
                row["signal_name"],
                row["normalized_unit"],
                row["cell_id"],
                row["observation_day"],
            )
            eligible_groups[grain].append(item)

    winner_rows: list[dict[str, Any]] = []
    provenance_rows: list[dict[str, Any]] = []
    group_details: dict[int, tuple[int, int, int]] = {}
    for group in eligible_groups.values():
        ranked = sorted(group, key=lambda row: (row["_release_retrieved_at"], int(row["id"])), reverse=True)
        winner = ranked[0]
        newest_observed_at = max(row["observed_at"] for row in ranked)
        winner_rows.append(
            {
                "support_key": winner["support_key"],
                "signal_name": winner["signal_name"],
                "normalized_unit": winner["normalized_unit"],
                "cell_id": winner["cell_id"],
                "observed_day": winner["observation_day"],
                "normalized_value": float(winner["normalized_value"]),
                "observation_count": len(ranked),
                "newest_observed_at": newest_observed_at,
                "coverage_fraction": winner["coverage_fraction"],
                "allowed_client_exposure": winner["_allowed_client_exposure"],
                "cell_longitude": float(winner["cell_centroid_longitude"]),
                "cell_latitude": float(winner["cell_centroid_latitude"]),
            }
        )
        for rank, row in enumerate(ranked, start=1):
            group_details[int(row["id"])] = (rank, int(winner["id"]), len(ranked))

    ledger_sha = sha256(ledger_payload)
    for row in enriched:
        details = group_details.get(int(row["id"]))
        part = row["_source_part"]
        provenance_rows.append(
            {
                "source_snapshot_id": SOURCE_SNAPSHOT_ID,
                "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                "source_ledger_key": ledger_key,
                "source_ledger_sha256": ledger_sha,
                "source_part_key": part["key"],
                "source_part_sha256": part["sha256"],
                "raw_observation_id": int(row["id"]),
                "canonical_row_sha256": row["canonical_row_sha256"],
                "source_release_id": row["source_release_id"],
                "source_release_retrieved_at": row["_release_retrieved_at"],
                "data_source_key": row["data_source_key"],
                "source_parameter": row["source_parameter"],
                "support_key": row["support_key"],
                "signal_name": row["signal_name"],
                "normalized_unit": row["normalized_unit"],
                "cell_id": row["cell_id"],
                "observation_day": row["observation_day"],
                "observed_at": row["observed_at"],
                "normalized_value": row["normalized_value"],
                "coverage_fraction": row["coverage_fraction"],
                "allowed_client_exposure": row["_allowed_client_exposure"],
                "eligible_for_serving": details is not None,
                "exclusion_reason": row["_exclusion_reason"],
                "precedence_rank": None if details is None else details[0],
                "is_selected_winner": details is not None and details[0] == 1,
                "winner_observation_id": None if details is None else details[1],
                "duplicate_count": 0 if details is None else details[2],
            }
        )

    stage_payload, stage_table = serialize_serving(winner_rows, require_cell_id=True)
    provenance_table = pa.Table.from_pylist(provenance_rows, schema=PROVENANCE_SCHEMA)
    provenance_payload = serialize_table(
        provenance_table,
        schema=PROVENANCE_SCHEMA,
        sort_columns=PROVENANCE_SORT,
    )
    provenance_table = pq.read_table(io.BytesIO(provenance_payload))
    stage_payload, stage_table = put_or_adopt_parquet(
        store,
        stage_key,
        stage_payload,
        stage_table,
        schema=SERVING_SCHEMA,
        digest_columns=SERVING_SCHEMA.names,
    )
    provenance_payload, provenance_table = put_or_adopt_parquet(
        store,
        provenance_key,
        provenance_payload,
        provenance_table,
        schema=PROVENANCE_SCHEMA,
        digest_columns=PROVENANCE_SCHEMA.names,
    )

    raw_digest = canonical_identity_sum256(raw_rows)
    stage_receipt = receipt(stage_key, stage_payload, stage_table, digest_columns=SERVING_SCHEMA.names)
    provenance_receipt = receipt(
        provenance_key,
        provenance_payload,
        provenance_table,
        digest_columns=PROVENANCE_SCHEMA.names,
    )
    provenance_receipt["raw_identity_sum256"] = raw_identity_sum256(provenance_table.to_pylist())
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "precedence_version": PRECEDENCE_VERSION,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "destination_prefix": DESTINATION_PREFIX + "/",
        "writer_versions": {"pyarrow": pa.__version__, "polars": pl.__version__},
        "observation_month": month,
        "cell_batch_index": batch,
        "source_ledger_key": ledger_key,
        "source_ledger_sha256": ledger_sha,
        "source_parts": [
            {
                "key": part["key"],
                "row_count": part["row_count"],
                "byte_count": part["byte_count"],
                "sha256": part["sha256"],
                "row_digest": part["row_digest"],
            }
            for part in wind_parts
        ],
        "raw_row_count": len(raw_rows),
        "eligible_row_count": sum(len(group) for group in eligible_groups.values()),
        "ineligible_row_count": sum(row["_exclusion_reason"] is not None for row in enriched),
        "winner_row_count": len(winner_rows),
        "duplicate_row_count": sum(len(group) - 1 for group in eligible_groups.values()),
        "observation_count_sum": sum(int(row["observation_count"]) for row in winner_rows),
        "raw_identity_sum256": raw_digest,
        "stage_part": stage_receipt,
        "provenance_part": provenance_receipt,
    }
    if (
        checkpoint["raw_row_count"] != checkpoint["provenance_part"]["row_count"]
        or checkpoint["raw_identity_sum256"] != checkpoint["provenance_part"]["raw_identity_sum256"]
        or checkpoint["eligible_row_count"] != checkpoint["observation_count_sum"]
        or checkpoint["winner_row_count"] != checkpoint["stage_part"]["row_count"]
    ):
        raise BreakdownError(f"Wind source unit {month}/{batch:05d} failed pre-write reconciliation")
    store.put_immutable(checkpoint_key, json_bytes(checkpoint), content_type=JSON_CONTENT_TYPE)
    return checkpoint


def day_part_key(store: ImmutableStore, day: date, tier: int) -> str:
    return store.key_for(
        f"{DESTINATION_PREFIX}/kind=observed/zoom={tier:02d}/year={day.year:04d}/month={day.month:02d}/"
        f"day={day.day:02d}/part-00000.parquet"
    )


def day_checkpoint_key(store: ImmutableStore, day: date) -> str:
    return store.key_for(f"{DESTINATION_PREFIX}/_checkpoints/day={day.isoformat()}.json")


def validate_day_checkpoint(
    store: ImmutableStore,
    checkpoint: Mapping[str, Any],
    *,
    day: date,
    base_frame: pl.DataFrame,
) -> None:
    if (
        checkpoint.get("contract_version") != CONTRACT_VERSION
        or checkpoint.get("precedence_version") != PRECEDENCE_VERSION
        or checkpoint.get("source_snapshot_id") != SOURCE_SNAPSHOT_ID
        or checkpoint.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
        or checkpoint.get("destination_prefix") != DESTINATION_PREFIX + "/"
        or checkpoint.get("observation_day") != day.isoformat()
    ):
        raise BreakdownError("existing Wind day checkpoint has a different contract or source")
    tiers = checkpoint.get("tiers")
    if not isinstance(tiers, Mapping) or {int(key) for key in tiers} != set(ZOOM_TIERS):
        raise BreakdownError("existing Wind day checkpoint does not contain all four tiers")
    tables: dict[int, pa.Table] = {}
    for tier in ZOOM_TIERS:
        item = tiers[str(tier)]
        if item.get("key") != day_part_key(store, day, tier):
            raise BreakdownError(f"existing Wind {day} z{tier} checkpoint names a different part key")
        tables[tier] = verify_receipt(store, item, schema=SERVING_SCHEMA)
        if set(tables[tier].column("observed_day").to_pylist()) != {day}:
            raise BreakdownError(f"existing Wind {day} z{tier} part contains a different day")
    base_rows = base_frame.to_dicts()
    expected_base_digest = serving_sum256(base_rows)
    base_observation_sum = sum(int(value) for value in base_frame["observation_count"].to_list())
    if (
        int(tiers["13"]["row_count"]) != base_frame.height
        or tiers["13"]["row_sum256"] != expected_base_digest
        or any(int(tiers[str(tier)]["observation_count_sum"]) != base_observation_sum for tier in ZOOM_TIERS)
    ):
        raise BreakdownError(f"existing Wind {day} checkpoint is not bound to the current staged winners")


def write_day(store: ImmutableStore, day: date, base_frame: pl.DataFrame) -> dict[str, Any]:
    checkpoint_key = day_checkpoint_key(store, day)
    existing_payload = store.get(checkpoint_key)
    if existing_payload is not None:
        existing = parse_json(existing_payload, key=checkpoint_key)
        validate_day_checkpoint(store, existing, day=day, base_frame=base_frame)
        return existing
    if base_frame.height != EXPECTED_CELLS_PER_DAY:
        raise BreakdownError(f"Wind {day} has {base_frame.height} base cells, expected {EXPECTED_CELLS_PER_DAY}")

    base_observation_sum = int(base_frame["observation_count"].sum())
    tier_receipts: dict[str, dict[str, Any]] = {}
    for tier in ZOOM_TIERS:
        frame = base_frame if tier == 13 else derive_tier(base_frame, stream=CLIMATE_FIELD_WIND_SPEED_STREAM, tier=tier)
        payload, table = serialize_serving(frame, require_cell_id=tier == 13)
        observation_sum = sum(int(value) for value in table.column("observation_count").to_pylist())
        if observation_sum != base_observation_sum:
            raise BreakdownError(f"Wind {day} z{tier} did not preserve observation_count")
        key = day_part_key(store, day, tier)
        payload, table = put_or_adopt_parquet(
            store,
            key,
            payload,
            table,
            schema=SERVING_SCHEMA,
            digest_columns=SERVING_SCHEMA.names,
        )
        item = receipt(key, payload, table, digest_columns=SERVING_SCHEMA.names)
        item["observation_count_sum"] = observation_sum
        tier_receipts[str(tier)] = item
    checkpoint = {
        "contract_version": CONTRACT_VERSION,
        "precedence_version": PRECEDENCE_VERSION,
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "destination_prefix": DESTINATION_PREFIX + "/",
        "writer_versions": {"pyarrow": pa.__version__, "polars": pl.__version__},
        "observation_day": day.isoformat(),
        "tiers": tier_receipts,
    }
    store.put_immutable(checkpoint_key, json_bytes(checkpoint), content_type=JSON_CONTENT_TYPE)
    return checkpoint


def load_month_stage(store: ImmutableStore, checkpoints: Sequence[Mapping[str, Any]]) -> pl.DataFrame:
    tables = [verify_receipt(store, checkpoint["stage_part"], schema=SERVING_SCHEMA) for checkpoint in checkpoints]
    if not tables:
        raise BreakdownError("a canonical source month produced no Wind stage parts")
    return pl.from_arrow(pa.concat_tables(tables)).sort(list(SERVING_SORT))  # type: ignore[union-attr]


def expected_days() -> list[date]:
    return [EXPECTED_FIRST_DAY + timedelta(days=index) for index in range(EXPECTED_DAY_COUNT)]


def verify_completed_snapshot(
    store: ImmutableStore,
    manifest: Mapping[str, Any],
    *,
    manifest_key: str,
    complete_key: str,
) -> None:
    serving_parts = manifest.get("serving_parts")
    provenance_parts = manifest.get("provenance_parts")
    stage_parts = manifest.get("stage_parts")
    source_units = manifest.get("source_units")
    if not all(isinstance(items, list) for items in (serving_parts, provenance_parts, stage_parts, source_units)):
        raise BreakdownError("completed Wind manifest omits its durable part or source-unit inventory")
    if (
        len(serving_parts) != int(manifest["serving_part_count"])
        or len(provenance_parts) != int(manifest["provenance_part_count"])
        or len(stage_parts) != int(manifest["stage_part_count"])
        or len(source_units) != int(manifest["source_unit_checkpoint_count"])
    ):
        raise BreakdownError("completed Wind manifest part/checkpoint counts disagree with its inventory")
    all_parts = [*serving_parts, *provenance_parts, *stage_parts]
    serving_by_key = {str(item["key"]): item for item in serving_parts}
    provenance_by_key = {str(item["key"]): item for item in provenance_parts}
    stage_by_key = {str(item["key"]): item for item in stage_parts}
    for index, item in enumerate(all_parts, start=1):
        schema = PROVENANCE_SCHEMA if "/provenance/" in str(item["key"]) else SERVING_SCHEMA
        verify_receipt(store, item, schema=schema)
        if index % 500 == 0:
            print(f"verify completed outputs [{index}/{len(all_parts)}]")
    source_checkpoints: list[dict[str, Any]] = []
    for unit in source_units:
        checkpoint_key = unit_keys(
            store,
            str(unit["observation_month"]),
            int(unit["cell_batch_index"]),
        )[2]
        checkpoint = parse_json(store.require(checkpoint_key), key=checkpoint_key)
        if (
            checkpoint.get("contract_version") != CONTRACT_VERSION
            or checkpoint.get("precedence_version") != PRECEDENCE_VERSION
            or checkpoint.get("source_snapshot_id") != SOURCE_SNAPSHOT_ID
            or checkpoint.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
            or checkpoint.get("destination_prefix") != DESTINATION_PREFIX + "/"
            or any(
                checkpoint.get(name) != unit.get(name)
                for name in (
                    "observation_month",
                    "cell_batch_index",
                    "source_ledger_key",
                    "source_ledger_sha256",
                    "source_parts",
                    "raw_row_count",
                    "eligible_row_count",
                    "winner_row_count",
                    "duplicate_row_count",
                    "raw_identity_sum256",
                )
            )
        ):
            raise BreakdownError(f"completed Wind source checkpoint {checkpoint_key!r} is misbound")
        stage_receipt = checkpoint.get("stage_part")
        provenance_receipt = checkpoint.get("provenance_part")
        if (
            not isinstance(stage_receipt, Mapping)
            or stage_by_key.get(str(stage_receipt.get("key"))) != stage_receipt
            or not isinstance(provenance_receipt, Mapping)
            or provenance_by_key.get(str(provenance_receipt.get("key"))) != provenance_receipt
        ):
            raise BreakdownError(f"completed Wind source checkpoint {checkpoint_key!r} disagrees with manifest parts")
        source_checkpoints.append(checkpoint)

    day_checkpoints: list[dict[str, Any]] = []
    for day in expected_days():
        checkpoint_key = day_checkpoint_key(store, day)
        checkpoint = parse_json(store.require(checkpoint_key), key=checkpoint_key)
        tiers = checkpoint.get("tiers")
        if (
            checkpoint.get("contract_version") != CONTRACT_VERSION
            or checkpoint.get("precedence_version") != PRECEDENCE_VERSION
            or checkpoint.get("source_snapshot_id") != SOURCE_SNAPSHOT_ID
            or checkpoint.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
            or checkpoint.get("destination_prefix") != DESTINATION_PREFIX + "/"
            or checkpoint.get("observation_day") != day.isoformat()
            or not isinstance(tiers, Mapping)
            or set(tiers) != {str(tier) for tier in ZOOM_TIERS}
        ):
            raise BreakdownError(f"completed Wind day checkpoint {checkpoint_key!r} is misbound")
        for tier in ZOOM_TIERS:
            tier_receipt = tiers[str(tier)]
            if (
                not isinstance(tier_receipt, Mapping)
                or serving_by_key.get(str(tier_receipt.get("key"))) != tier_receipt
                or tier_receipt.get("key") != day_part_key(store, day, tier)
            ):
                raise BreakdownError(
                    f"completed Wind day checkpoint {checkpoint_key!r} z{tier} disagrees with manifest parts"
                )
        day_checkpoints.append(checkpoint)

    source_totals = {
        "source_raw_row_count": sum(int(item["raw_row_count"]) for item in source_checkpoints),
        "eligible_row_count": sum(int(item["eligible_row_count"]) for item in source_checkpoints),
        "ineligible_row_count": sum(int(item["ineligible_row_count"]) for item in source_checkpoints),
        "precedence_winner_row_count": sum(int(item["winner_row_count"]) for item in source_checkpoints),
        "duplicate_version_row_count": sum(int(item["duplicate_row_count"]) for item in source_checkpoints),
        "provenance_row_count": sum(int(item["provenance_part"]["row_count"]) for item in source_checkpoints),
    }
    if any(int(manifest[name]) != value for name, value in source_totals.items()):
        raise BreakdownError("completed Wind source checkpoint totals disagree with the manifest")
    tier_totals = {
        str(tier): {
            "row_count": sum(int(item["tiers"][str(tier)]["row_count"]) for item in day_checkpoints),
            "byte_count": sum(int(item["tiers"][str(tier)]["byte_count"]) for item in day_checkpoints),
            "observation_count_sum": sum(
                int(item["tiers"][str(tier)]["observation_count_sum"]) for item in day_checkpoints
            ),
        }
        for tier in ZOOM_TIERS
    }
    if tier_totals != manifest.get("tier_totals") or len(day_checkpoints) != int(manifest["day_count"]):
        raise BreakdownError("completed Wind day checkpoint totals disagree with the manifest")
    raw_digest = combine_sum256(item["raw_identity_sum256"] for item in source_checkpoints)
    provenance_digest = combine_sum256(
        item["provenance_part"].get("raw_identity_sum256", item["provenance_part"]["row_sum256"])
        for item in source_checkpoints
    )
    stage_digest = combine_sum256(item["stage_part"]["row_sum256"] for item in source_checkpoints)
    z13_digest = combine_sum256(item["tiers"]["13"]["row_sum256"] for item in day_checkpoints)
    if (
        raw_digest != manifest.get("raw_identity_sum256")
        or provenance_digest != manifest.get("provenance_identity_sum256")
        or stage_digest != manifest.get("stage_winner_row_sum256")
        or z13_digest != manifest.get("z13_row_sum256")
    ):
        raise BreakdownError("completed Wind checkpoint digests disagree with the manifest")

    expected_keys = {
        manifest_key,
        complete_key,
        *(str(item["key"]) for item in all_parts),
        *(unit_keys(store, str(item["observation_month"]), int(item["cell_batch_index"]))[2] for item in source_units),
        *(day_checkpoint_key(store, day) for day in expected_days()),
    }
    actual_keys = set(store.list_keys(store.key_for(DESTINATION_PREFIX + "/")))
    if actual_keys != expected_keys:
        raise BreakdownError(
            f"completed Wind inventory mismatch: missing={sorted(expected_keys - actual_keys)[:5]}, "
            f"unexpected={sorted(actual_keys - expected_keys)[:5]}"
        )


def existing_complete(store: ImmutableStore) -> dict[str, Any] | None:
    complete_key = store.key_for(f"{DESTINATION_PREFIX}/_COMPLETE")
    complete_payload = store.get(complete_key)
    if complete_payload is None:
        return None
    complete = parse_json(complete_payload, key=complete_key)
    manifest_key = store.key_for(f"{DESTINATION_PREFIX}/manifest.json")
    manifest_payload = store.require(manifest_key)
    if (
        complete.get("manifest_sha256") != sha256(manifest_payload)
        or complete.get("contract_version") != CONTRACT_VERSION
        or complete.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
        or complete.get("destination_prefix") != DESTINATION_PREFIX + "/"
    ):
        raise BreakdownError("existing Wind completion marker does not bind its manifest")
    manifest = parse_json(manifest_payload, key=manifest_key)
    if (
        manifest.get("contract_version") != CONTRACT_VERSION
        or manifest.get("precedence_version") != PRECEDENCE_VERSION
        or manifest.get("source_snapshot_id") != SOURCE_SNAPSHOT_ID
        or manifest.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA256
        or manifest.get("destination_prefix") != DESTINATION_PREFIX + "/"
    ):
        raise BreakdownError("existing complete Wind snapshot has a different contract or source")
    for complete_name, manifest_name in (
        ("source_raw_row_count", "source_raw_row_count"),
        ("precedence_winner_row_count", "precedence_winner_row_count"),
        ("day_count", "day_count"),
        ("serving_part_count", "serving_part_count"),
        ("provenance_part_count", "provenance_part_count"),
    ):
        if complete.get(complete_name) != manifest.get(manifest_name):
            raise BreakdownError("existing Wind completion marker totals disagree with its manifest")
    verify_completed_snapshot(
        store,
        manifest,
        manifest_key=manifest_key,
        complete_key=complete_key,
    )
    return manifest


def finalize(
    store: ImmutableStore,
    source_manifest: Mapping[str, Any],
    unit_checkpoints: Sequence[Mapping[str, Any]],
    day_checkpoints: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    raw_rows = sum(int(item["raw_row_count"]) for item in unit_checkpoints)
    eligible_rows = sum(int(item["eligible_row_count"]) for item in unit_checkpoints)
    winner_rows = sum(int(item["winner_row_count"]) for item in unit_checkpoints)
    ineligible_rows = sum(int(item["ineligible_row_count"]) for item in unit_checkpoints)
    duplicate_rows = sum(int(item["duplicate_row_count"]) for item in unit_checkpoints)
    if (
        raw_rows != EXPECTED_RAW_ROWS
        or eligible_rows != EXPECTED_RAW_ROWS
        or winner_rows != EXPECTED_WINNER_ROWS
        or ineligible_rows != 0
        or duplicate_rows != EXPECTED_RAW_ROWS - EXPECTED_WINNER_ROWS
    ):
        raise BreakdownError(
            "canonical-to-Wind population gate failed: "
            f"raw={raw_rows}, eligible={eligible_rows}, winners={winner_rows}, ineligible={ineligible_rows}, "
            f"duplicates={duplicate_rows}"
        )
    if len(unit_checkpoints) != int(source_manifest["batch_count"]) or len(day_checkpoints) != EXPECTED_DAY_COUNT:
        raise BreakdownError("Wind checkpoint counts do not cover every source unit and day")

    day_set = {date.fromisoformat(str(item["observation_day"])) for item in day_checkpoints}
    if day_set != set(expected_days()):
        raise BreakdownError("Wind day checkpoints are not the exact expected consecutive horizon")
    tier_totals = {
        str(tier): {
            "row_count": sum(int(item["tiers"][str(tier)]["row_count"]) for item in day_checkpoints),
            "byte_count": sum(int(item["tiers"][str(tier)]["byte_count"]) for item in day_checkpoints),
            "observation_count_sum": sum(
                int(item["tiers"][str(tier)]["observation_count_sum"]) for item in day_checkpoints
            ),
        }
        for tier in ZOOM_TIERS
    }
    for tier in ZOOM_TIERS:
        if tier_totals[str(tier)]["observation_count_sum"] != eligible_rows:
            raise BreakdownError(f"Wind z{tier} aggregate does not reconcile to eligible canonical facts")
    if tier_totals["13"]["row_count"] != winner_rows:
        raise BreakdownError("Wind z13 rows do not reconcile to precedence winners")

    stage_digest = combine_sum256(item["stage_part"]["row_sum256"] for item in unit_checkpoints)
    z13_digest = combine_sum256(item["tiers"]["13"]["row_sum256"] for item in day_checkpoints)
    if stage_digest != z13_digest:
        raise BreakdownError("Wind staged winners do not reconcile byte-independently to final z13 rows")
    raw_digest = combine_sum256(item["raw_identity_sum256"] for item in unit_checkpoints)
    provenance_digest = combine_sum256(
        item["provenance_part"].get("raw_identity_sum256", item["provenance_part"]["row_sum256"])
        for item in unit_checkpoints
    )
    if raw_digest != provenance_digest:
        raise BreakdownError("Wind canonical raw identities do not reconcile to provenance rows")

    repaired_days = sorted(day for day in day_set if day >= REPAIRED_COARSE_FIRST_DAY)
    if (
        len(repaired_days) != EXPECTED_REPAIRED_COARSE_DAYS
        or EXPECTED_DAY_COUNT - len(repaired_days) != LEGACY_COARSE_DAY_COUNT
    ):
        raise BreakdownError("Wind coarse-tier repair window does not reconcile to the known 222-day deficit")

    serving_parts = [item["tiers"][str(tier)] for item in day_checkpoints for tier in ZOOM_TIERS]
    provenance_parts = [item["provenance_part"] for item in unit_checkpoints]
    stage_parts = [item["stage_part"] for item in unit_checkpoints]
    for index, item in enumerate([*serving_parts, *provenance_parts, *stage_parts], start=1):
        schema = PROVENANCE_SCHEMA if "/provenance/" in str(item["key"]) else SERVING_SCHEMA
        verify_receipt(store, item, schema=schema)
        if index % 500 == 0:
            print(f"verify outputs [{index}/{len(serving_parts) + len(provenance_parts) + len(stage_parts)}]")

    manifest_key = store.key_for(f"{DESTINATION_PREFIX}/manifest.json")
    complete_key = store.key_for(f"{DESTINATION_PREFIX}/_COMPLETE")
    existing_payload = store.get(manifest_key)
    verified_at = (
        str(parse_json(existing_payload, key=manifest_key)["verified_at"])
        if existing_payload is not None
        else datetime.now(UTC).isoformat(timespec="seconds")
    )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "precedence_version": PRECEDENCE_VERSION,
        "destination_prefix": DESTINATION_PREFIX + "/",
        "writer_versions": {"pyarrow": pa.__version__, "polars": pl.__version__},
        "source_snapshot_id": SOURCE_SNAPSHOT_ID,
        "source_snapshot_prefix": SOURCE_PREFIX + "/",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_contract_version": source_manifest["contract_version"],
        "source_high_watermark_id": source_manifest["high_watermark_id"],
        "source_batch_count": source_manifest["batch_count"],
        "source_wind_part_count": sum(len(item["source_parts"]) for item in unit_checkpoints),
        "source_raw_row_count": raw_rows,
        "eligible_row_count": eligible_rows,
        "ineligible_row_count": ineligible_rows,
        "precedence_winner_row_count": winner_rows,
        "duplicate_version_row_count": duplicate_rows,
        "provenance_row_count": sum(int(item["provenance_part"]["row_count"]) for item in unit_checkpoints),
        "observation_day_min": EXPECTED_FIRST_DAY.isoformat(),
        "observation_day_max": EXPECTED_LAST_DAY.isoformat(),
        "day_count": len(day_set),
        "cells_per_day": EXPECTED_CELLS_PER_DAY,
        "tier_totals": tier_totals,
        "all_tiers": list(ZOOM_TIERS),
        "serving_schema": schema_manifest(SERVING_SCHEMA),
        "serving_sort_columns": list(SERVING_SORT),
        "provenance_schema": schema_manifest(PROVENANCE_SCHEMA),
        "raw_identity_sum256": raw_digest,
        "provenance_identity_sum256": provenance_digest,
        "stage_winner_row_sum256": stage_digest,
        "z13_row_sum256": z13_digest,
        "known_legacy_coarse_day_count": LEGACY_COARSE_DAY_COUNT,
        "repaired_coarse_day_count": len(repaired_days),
        "repaired_coarse_day_min": repaired_days[0].isoformat(),
        "repaired_coarse_day_max": repaired_days[-1].isoformat(),
        "serving_part_count": len(serving_parts),
        "provenance_part_count": len(provenance_parts),
        "stage_part_count": len(stage_parts),
        "source_unit_checkpoint_count": len(unit_checkpoints),
        "day_checkpoint_count": len(day_checkpoints),
        "serving_parts": serving_parts,
        "provenance_parts": provenance_parts,
        "stage_parts": stage_parts,
        "source_units": [
            {
                "observation_month": item["observation_month"],
                "cell_batch_index": item["cell_batch_index"],
                "source_ledger_key": item["source_ledger_key"],
                "source_ledger_sha256": item["source_ledger_sha256"],
                "source_parts": item["source_parts"],
                "raw_row_count": item["raw_row_count"],
                "eligible_row_count": item["eligible_row_count"],
                "winner_row_count": item["winner_row_count"],
                "duplicate_row_count": item["duplicate_row_count"],
                "raw_identity_sum256": item["raw_identity_sum256"],
            }
            for item in unit_checkpoints
        ],
        "verified_at": verified_at,
    }
    working_keys = {
        *(str(item["key"]) for item in serving_parts),
        *(str(item["key"]) for item in provenance_parts),
        *(str(item["key"]) for item in stage_parts),
        *(
            unit_keys(store, str(item["observation_month"]), int(item["cell_batch_index"]))[2]
            for item in unit_checkpoints
        ),
        *(day_checkpoint_key(store, date.fromisoformat(str(item["observation_day"]))) for item in day_checkpoints),
    }
    expected_before_close = working_keys | ({manifest_key} if existing_payload is not None else set())
    actual_before_close = set(store.list_keys(store.key_for(DESTINATION_PREFIX + "/")))
    if actual_before_close != expected_before_close:
        raise BreakdownError(
            f"Wind destination inventory mismatch before close: "
            f"missing={sorted(expected_before_close - actual_before_close)[:5]}, "
            f"unexpected={sorted(actual_before_close - expected_before_close)[:5]}"
        )

    manifest_payload = json_bytes(manifest)
    store.put_immutable(manifest_key, manifest_payload, content_type=JSON_CONTENT_TYPE)
    complete = {
        "contract_version": CONTRACT_VERSION,
        "destination_prefix": DESTINATION_PREFIX + "/",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "manifest_key": manifest_key,
        "manifest_sha256": sha256(manifest_payload),
        "source_raw_row_count": raw_rows,
        "precedence_winner_row_count": winner_rows,
        "day_count": len(day_set),
        "serving_part_count": len(serving_parts),
        "provenance_part_count": len(provenance_parts),
    }
    store.put_immutable(complete_key, json_bytes(complete), content_type=JSON_CONTENT_TYPE)
    return manifest


def main() -> None:
    settings = Settings()
    store = ImmutableStore.from_credentials(
        settings.require_object_store(),
        prefix=settings.object_store_prefix,
        retry=RetryPolicy(),
    )
    completed = existing_complete(store)
    if completed is not None:
        print(
            f"already complete: prefix={completed['destination_prefix']} raw={completed['source_raw_row_count']} "
            f"winners={completed['precedence_winner_row_count']} days={completed['day_count']}"
        )
        return

    source_manifest, dimensions = load_source_contract(store)
    summaries = source_manifest.get("month_ledgers")
    if not isinstance(summaries, list) or len(summaries) != int(source_manifest["batch_count"]):
        raise BreakdownError("canonical manifest does not carry all extraction-unit summaries")
    summaries = sorted(summaries, key=lambda item: (item["observation_month"], item["cell_batch_index"]))
    unit_checkpoints: list[dict[str, Any]] = []
    for index, summary in enumerate(summaries, start=1):
        checkpoint = process_unit(store, source_manifest, dimensions, summary)
        unit_checkpoints.append(checkpoint)
        print(
            f"source units [{index}/{len(summaries)}] {checkpoint['observation_month']} "
            f"cb={checkpoint['cell_batch_index']:05d} raw={checkpoint['raw_row_count']} "
            f"winners={checkpoint['winner_row_count']}"
        )

    units_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for checkpoint in unit_checkpoints:
        units_by_month[str(checkpoint["observation_month"])].append(checkpoint)
    day_checkpoints: list[dict[str, Any]] = []
    day_index = 0
    for _month, checkpoints in sorted(units_by_month.items()):
        month_frame = load_month_stage(store, checkpoints)
        month_days = sorted(month_frame["observed_day"].unique().to_list())
        for day in month_days:
            day_index += 1
            frame = month_frame.filter(pl.col("observed_day") == day)
            checkpoint = write_day(store, day, frame)
            day_checkpoints.append(checkpoint)
            if day_index % 25 == 0 or day_index == EXPECTED_DAY_COUNT:
                print(f"day tiers [{day_index}/{EXPECTED_DAY_COUNT}] through {day}")

    manifest = finalize(store, source_manifest, unit_checkpoints, day_checkpoints)
    print(
        f"complete: prefix={manifest['destination_prefix']} raw={manifest['source_raw_row_count']} "
        f"winners={manifest['precedence_winner_row_count']} days={manifest['day_count']} "
        f"serving_parts={manifest['serving_part_count']} provenance_parts={manifest['provenance_part_count']} "
        f"repaired_coarse_days={manifest['repaired_coarse_day_count']}"
    )


if __name__ == "__main__":
    main()
