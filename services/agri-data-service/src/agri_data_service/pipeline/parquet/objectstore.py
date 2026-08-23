"""S3-compatible object-store client and the Parquet partition writer every lane publishes through.

Layer L2: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `AGENTS.md` in this directory for the credential wiring, the mockable backend seam, and why
this path is synchronous.

A LISTING CARRIES THE EXPORT INSTANT, NOT JUST THE KEY. `list_objects_v2` already returns
`LastModified` on every entry, and discarding it cost a `static_lookup` lane the only signal that
separates two exports made on the same UTC day -- the day in the key is a VERSION STAMP, so once
day D holds a snapshot the key alone says nothing about WHICH state of the source that snapshot
read. One listing method therefore returns both, so no caller can reach a key-only path by accident.

ONE STREAM-DAY'S EXPORT INSTANT IS THE OLDEST OF ITS PART FILES, never the newest. A re-export that
rewrote `part-0` but left an older `part-1` beside it publishes a MIXTURE, and the oldest part is
the one that says so; the newest would report the whole day as freshly captured. An unknown instant
on any part collapses the answer to `None`, because a partly unattributed set is not a freshness
claim.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Final, Protocol

import boto3  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from agri_data_service.config import ObjectStoreCredentials, Settings, settings
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    day_prefix,
    month_prefix,
    partition_path,
    stream_prefix,
    try_parse_absence_marker_path,
    try_parse_partition_path,
    year_prefix,
)
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import date

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.paths import PartitionKind

PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
ABSENCE_CONTENT_TYPE: Final = "application/json"
MAX_LISTED_KEYS: Final = 500_000
_ABSENT_OBJECT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})


class ParquetWriteError(RuntimeError):
    """Base error for a refused Parquet partition write."""


class ParquetSchemaMismatchError(ParquetWriteError):
    """Raised when a table does not conform to its stream's registered storage contract."""


class EmptyPartitionError(ParquetWriteError):
    """Raised on a zero-row write: an empty file reads as a present day and hides a real gap."""


class GovernedAbsenceConflictError(ParquetWriteError):
    """Raised when data and a governed absence would coexist on one stream-day.

    Retracting either side is a manual admin action (RUNBOOK §0.21.5, §0.25.3), never something
    a lane's automatic write path does on its own.
    """


@dataclass(frozen=True, slots=True)
class ListedObject:
    """One object a listing returned: its bucket key, and when the store last wrote it."""

    key: str
    last_modified: datetime | None


@dataclass(frozen=True, slots=True)
class ListedPartition:
    """One part file or absence marker of the frozen layout, with the instant it was exported."""

    relative_path: str
    last_modified: datetime | None


class ObjectStoreBackend(Protocol):
    """The whole object-store surface the warehouse needs; implement it to test without a network."""

    def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def list_objects(self, prefix: str) -> Iterator[ListedObject]: ...

    def size_of(self, key: str) -> int | None: ...


class _S3Api(Protocol):
    """The three boto3 S3 calls this module makes, typed at the boundary rather than as `Any`."""

    def put_object(self, **kwargs: object) -> object: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class ParquetWriteReceipt:
    """Provenance for one written partition: what landed, where, and its integrity digest."""

    key: str
    relative_path: str
    stream: str
    kind: PartitionKind
    day: date
    row_count: int
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class AbsenceWriteReceipt:
    """Provenance for one written governed-absence marker."""

    key: str
    relative_path: str
    kind: PartitionKind
    day: date
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BotoObjectStoreBackend:
    """`ObjectStoreBackend` over one boto3 S3 client and one bucket."""

    bucket: str
    client: _S3Api

    @classmethod
    def from_credentials(cls, credentials: ObjectStoreCredentials) -> BotoObjectStoreBackend:
        """Build a client from validated coordinates; constructing it performs no network call."""
        client: _S3Api = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
        )
        return cls(bucket=credentials.bucket, client=client)

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Upload one object, overwriting any object already at `key`."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType=content_type)

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        """Yield every object under `prefix`, following continuation tokens to the end of the listing."""
        token: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
            if token is not None:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            yield from _listed_objects(response)
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str) or not next_token:
                return
            token = next_token

    def size_of(self, key: str) -> int | None:
        """Return the object's byte count, or `None` when it does not exist."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _client_error_code(exc) in _ABSENT_OBJECT_CODES:
                return None
            raise
        length = response.get("ContentLength")
        return length if isinstance(length, int) else None


class ObjectStore:
    """Layout-aware facade: maps the frozen partition layout onto one bucket, through one backend."""

    def __init__(self, backend: ObjectStoreBackend, *, prefix: str = "") -> None:
        self._backend = backend
        self._prefix = f"{prefix.strip('/')}/" if prefix.strip("/") else ""

    @classmethod
    def from_settings(cls, source: Settings | None = None) -> ObjectStore:
        """Build a bucket-backed store, raising when object storage is not configured."""
        resolved = settings if source is None else source
        credentials = resolved.require_object_store()
        return cls(BotoObjectStoreBackend.from_credentials(credentials), prefix=resolved.object_store_prefix)

    @property
    def prefix(self) -> str:
        """Return the bucket-root prefix every key is written beneath; `""` when unprefixed."""
        return self._prefix

    def key_for(self, relative_path: str) -> str:
        """Return the absolute bucket key for a path expressed in the frozen layout."""
        return f"{self._prefix}{relative_path}"

    def relative_key(self, key: str) -> str:
        """Strip the store prefix from an absolute bucket key, or raise if it is outside the store."""
        if not key.startswith(self._prefix):
            raise ValueError(f"key {key!r} does not live under this store's prefix {self._prefix!r}")
        return key[len(self._prefix) :]

    def write_partition(
        self,
        table: pa.Table,
        *,
        layer: str,
        kind: PartitionKind,
        day: date,
        part_index: int = 0,
    ) -> ParquetWriteReceipt:
        """Conform `table` to the layer's schema FOR THIS KIND, sort it to the grain, and upload one part file."""
        stream = get_stream_schema(layer, kind)
        conformed = conform_to_stream_schema(table, stream)
        if conformed.num_rows == 0:
            raise EmptyPartitionError(
                f"refusing to write a zero-row {layer!r} {kind} partition for {day}: "
                "an empty file reads as a present day and hides the gap"
            )
        if self.absence_exists(layer, kind, day):
            raise GovernedAbsenceConflictError(
                f"{layer!r} {kind} {day} carries a governed-absence marker; "
                "retracting it is a manual admin action, not something a write may do implicitly"
            )
        payload = _serialize_parquet(conformed, stream.compression)
        relative_path = partition_path(layer, kind, day, part_index)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=PARQUET_CONTENT_TYPE)
        return ParquetWriteReceipt(
            key=key,
            relative_path=relative_path,
            stream=stream.name,
            kind=kind,
            day=day,
            row_count=conformed.num_rows,
            byte_count=len(payload),
            sha256=sha256_digest(payload),
        )

    def write_absence(
        self,
        absence: GovernedAbsence,
        *,
        layer: str,
        kind: PartitionKind,
        day: date,
    ) -> AbsenceWriteReceipt:
        """Mark one stream-day as deliberately empty, refusing when data already covers it."""
        day_scope = self.key_for(day_prefix(layer, kind, day))
        for existing in self._backend.list_objects(day_scope):
            if try_parse_partition_path(self.relative_key(existing.key)) is not None:
                raise GovernedAbsenceConflictError(
                    f"{layer!r} {kind} {day} already holds data ({self.relative_key(existing.key)}); "
                    "correcting a completed record is a manual admin action"
                )
        payload = absence.to_json_bytes()
        relative_path = absence_marker_path(layer, kind, day)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=ABSENCE_CONTENT_TYPE)
        return AbsenceWriteReceipt(
            key=key,
            relative_path=relative_path,
            kind=kind,
            day=day,
            byte_count=len(payload),
            sha256=sha256_digest(payload),
        )

    def list_partition_objects(
        self,
        layer: str,
        kind: PartitionKind,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[ListedPartition, ...]:
        """Return one stream's part files and absence markers WITH their export instants, narrowed by year and month."""
        scope = self._listing_scope(layer, kind, year, month)
        found: list[ListedPartition] = []
        for listed in self._backend.list_objects(self.key_for(scope)):
            relative_path = self.relative_key(listed.key)
            if try_parse_partition_path(relative_path) is None and try_parse_absence_marker_path(relative_path) is None:
                continue
            found.append(ListedPartition(relative_path=relative_path, last_modified=listed.last_modified))
            if len(found) > MAX_LISTED_KEYS:
                raise ValueError(f"listing {scope!r} exceeded the {MAX_LISTED_KEYS}-key budget")
        return tuple(found)

    def list_partition_keys(
        self,
        layer: str,
        kind: PartitionKind,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return every part file and absence marker of one stream as a relative path, narrowed by year and month."""
        return tuple(
            listed.relative_path for listed in self.list_partition_objects(layer, kind, year=year, month=month)
        )

    def day_export_instant(self, layer: str, kind: PartitionKind, day: date) -> datetime | None:
        """Return when one stream-day's part files were exported, from a listing scoped to that day alone."""
        listed = tuple(
            ListedPartition(relative_path=self.relative_key(entry.key), last_modified=entry.last_modified)
            for entry in self._backend.list_objects(self.day_key_prefix(layer, kind, day))
        )
        return oldest_export_instant(listed, layer=layer, kind=kind, day=day)

    def partition_exists(self, layer: str, kind: PartitionKind, day: date, part_index: int = 0) -> bool:
        """Report whether one part file has been written, without downloading it."""
        return self._backend.size_of(self.key_for(partition_path(layer, kind, day, part_index))) is not None

    def absence_exists(self, layer: str, kind: PartitionKind, day: date) -> bool:
        """Report whether one stream-day carries a governed-absence marker, without downloading it."""
        return self._backend.size_of(self.key_for(absence_marker_path(layer, kind, day))) is not None

    def day_key_prefix(self, layer: str, kind: PartitionKind, day: date) -> str:
        """Return the absolute bucket prefix holding every part file for one stream-day."""
        return self.key_for(day_prefix(layer, kind, day))

    @staticmethod
    def _listing_scope(layer: str, kind: PartitionKind, year: int | None, month: int | None) -> str:
        if year is None:
            if month is not None:
                raise ValueError("narrowing a listing to a month requires the year as well")
            return stream_prefix(layer, kind)
        if month is None:
            return year_prefix(layer, kind, year)
        return month_prefix(layer, kind, year, month)


def conform_to_stream_schema(table: pa.Table, stream: ParquetStreamSchema) -> pa.Table:
    """Select, cast, and sort `table` into its stream's storage contract, or raise."""
    try:
        cast = table.select(stream.column_names).cast(stream.arrow_schema)
    except (KeyError, TypeError, ValueError) as exc:
        raise ParquetSchemaMismatchError(f"table does not conform to the {stream.name!r} stream schema: {exc}") from exc
    return cast.sort_by([(column, "ascending") for column in stream.sort_columns])


def polars_storage_options(credentials: ObjectStoreCredentials) -> dict[str, str]:
    """Return Polars/object_store connection options for the bucket. Never log the result."""
    return {
        "aws_endpoint_url": credentials.endpoint_url,
        "aws_region": credentials.region,
        "aws_access_key_id": credentials.access_key_id.get_secret_value(),
        "aws_secret_access_key": credentials.secret_access_key.get_secret_value(),
    }


def _serialize_parquet(table: pa.Table, compression: str) -> bytes:
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression=compression, write_statistics=True)
    return buffer.getvalue()


def oldest_export_instant(
    listed: Iterable[ListedPartition],
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
) -> datetime | None:
    """Return the OLDEST instant among one stream-day's part files, from a listing already made.

    `None` means the question cannot be answered: either no part file covers that day, or one of
    them carries no instant. This module's docstring says why the oldest is the honest answer.
    """
    oldest: datetime | None = None
    for entry in listed:
        parsed = try_parse_partition_path(entry.relative_path)
        if parsed is None or parsed.layer != layer or parsed.kind != kind or parsed.day != day:
            continue
        if entry.last_modified is None:
            return None
        oldest = entry.last_modified if oldest is None else min(oldest, entry.last_modified)
    return oldest


def _listed_objects(response: Mapping[str, object]) -> Iterator[ListedObject]:
    """Pull object keys and their last-modified instants out of an untrusted `list_objects_v2` response.

    A `LastModified` that is absent, of the wrong type, or timezone-naive is reported as `None`: an
    unknown instant degrades a currency answer to day resolution, where a wrong one would shift it.
    """
    contents = response.get("Contents")
    if not isinstance(contents, list):
        return
    for item in contents:
        if not isinstance(item, Mapping):
            continue
        key = item.get("Key")
        if not isinstance(key, str):
            continue
        last_modified = item.get("LastModified")
        reported = last_modified if isinstance(last_modified, datetime) and last_modified.tzinfo is not None else None
        yield ListedObject(key=key, last_modified=reported)


def _client_error_code(exc: ClientError) -> str | None:
    response = getattr(exc, "response", None)
    if not isinstance(response, Mapping):
        return None
    error = response.get("Error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("Code")
    return code if isinstance(code, str) else None
