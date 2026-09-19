"""Bucket I/O: the backend protocol, its boto3 and in-memory implementations, and the receipted writer.

Layer L3: may import `foundation`, `method` and `warehouse`; may NOT import `planes` or `interface`.
Why every write returns a receipt, why a listing is capped, and why nothing here ever falls back to
another store live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Final, Protocol

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs
import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.parquet_markers import (
    CompletedPart,
    GovernedAbsence,
    PartitionCompletion,
)
from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    PartitionKind,
    ZoomTier,
    absence_marker_path,
    availability_retry_path,
    completion_marker_path,
    day_prefix,
    derived_empty_completion_marker_path,
    partition_path,
    try_parse_partition_path,
    validate_layer_slug,
    validate_partition_kind,
    validate_zoom_tier,
)
from plantgeo_ml_service.warehouse.streams import stream_schema

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from datetime import date

    from plantgeo_ml_service.config import ObjectStoreCredentials
    from plantgeo_ml_service.warehouse.streams import ParquetStreamSchema

PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE: Final = "application/json"

#: A listing walks pages until it has this many keys and then refuses. A lane-day holds a handful of
#: objects; a run that asks for half a million has lost its prefix, and paging on forever would turn
#: that mistake into an unbounded download instead of an error.
MAX_LISTED_KEYS: Final = 500_000

#: Keys per `list_objects_v2` page. The store's own maximum; asking for fewer only multiplies calls.
LISTING_PAGE_SIZE: Final = 1_000

#: What the sibling writes and therefore what this service writes: the two must be byte-comparable.
PARQUET_FORMAT_VERSION: Final = "2.6"

#: A retry claim names one lane-day that still owes its availability step. It is a POINTER to work,
#: never a payload park, so it is bounded at the same ceiling the sibling's sweep enforces.
MAX_AVAILABILITY_RETRY_BYTES: Final = 8 * 1024 * 1024

#: The ceiling `read_object` applies when a caller names none. Every object of this layout that is
#: not a Parquet part is a marker, a pointer, a receipt or an artifact, and each of those is
#: kilobytes; a caller that genuinely needs more passes its own bound.
MAX_READ_OBJECT_BYTES: Final = 16 * 1024 * 1024

#: The ONLY prefix a dry run may re-root itself onto. Every lane that offers a `dry_run_prefix`
#: resolves it through `scratch_rooted_store` below, so "a dry run that writes anywhere else is a
#: production write wearing a flag" is one rule with one implementation rather than a per-lane habit.
SCRATCH_PREFIX_ROOT: Final = "ml/scratch/"

_ABSENT_OBJECT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})


class ObjectStoreError(RuntimeError):
    """Raised when a bucket operation cannot be honoured; never a signal to try another store."""


class ScratchPrefixError(ObjectStoreError):
    """Raised when a dry-run prefix is not a scratch root, so the run would write to the real lane."""


class ParquetWriteError(ObjectStoreError):
    """Raised when a table cannot be written under its stream's storage contract."""


class ParquetSchemaMismatchError(ParquetWriteError):
    """Raised when a table cannot be conformed to its stream's Arrow schema."""


class EmptyPartitionError(ParquetWriteError):
    """Raised when a write is asked to publish a table with no rows.

    Emptiness has its own vocabulary in this layout: a governed absence at the base rung, a
    derived-empty completion marker above it. An empty part file says neither and is a silent
    third claim, so it is refused rather than written.
    """


class NullBaseColumnError(ParquetWriteError):
    """Raised when a base-rung table nulls a column only the coarse rungs may null."""


class PartitionNotWrittenError(ObjectStoreError):
    """Raised when a read names a partition the bucket does not hold."""


class ObjectKeyError(ObjectStoreError):
    """Raised when a key would land outside the configured prefix."""


class ObjectTooLargeError(ObjectStoreError):
    """Raised when an object is larger than the ceiling the caller read it under.

    The size is settled by a `head` BEFORE any body is fetched: checking after a whole `Body.read()`
    bounds the answer and not the download, which is the resource the ceiling exists to protect.
    """


class GovernedAbsenceConflictError(ObjectStoreError):
    """Raised when a write and a governed absence would make opposite claims about one day.

    Retracting either claim is a manual admin action: a write that implicitly overrode the other
    would turn a deliberate, justified emptiness into a silent correction nobody reviewed.
    """


class ImmutableObjectConflictError(ObjectStoreError):
    """Raised when a content-addressed key already holds DIFFERENT bytes than the ones offered."""


@dataclass(frozen=True, slots=True)
class ListedObject:
    """One object a listing returned: its bucket key, and when the store last wrote it."""

    key: str
    last_modified: datetime | None


class ObjectStoreBackend(Protocol):
    """The whole bucket surface this service needs; implement it to test without a network."""

    def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def get(self, key: str) -> bytes | None: ...

    def head(self, key: str) -> int | None: ...

    def delete(self, key: str) -> None: ...

    def list_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> Iterator[ListedObject]: ...


class _S3Api(Protocol):
    """The five boto3 S3 calls this module makes, typed at the boundary rather than as `Any`."""

    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class ParquetWriteReceipt:
    """Provenance for one written partition: what landed, at which tier, where, and its digest."""

    key: str
    relative_path: str
    stream: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date
    row_count: int
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CompletionWriteReceipt:
    """Provenance for one written completion marker: the day it finishes, and what it claims landed."""

    key: str
    relative_path: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date
    part_count: int
    row_count: int
    byte_count: int
    sha256: str
    derived_empty: bool = False


@dataclass(frozen=True, slots=True)
class AbsenceWriteReceipt:
    """Provenance for one written governed-absence marker, including the tier it settles."""

    key: str
    relative_path: str
    kind: PartitionKind
    zoom: ZoomTier
    day: date
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PartitionRead:
    """One part file as it was read back, with the digest proving which bytes answered."""

    relative_path: str
    table: pa.Table
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BotoObjectStoreBackend:
    """The real bucket, reached with the credentials `config.require_object_store()` resolved."""

    bucket: str
    client: _S3Api

    @classmethod
    def from_credentials(cls, credentials: ObjectStoreCredentials) -> BotoObjectStoreBackend:
        """Build a client from validated credentials without performing any network call."""
        import boto3  # noqa: PLC0415 - imported lazily so tests never need the client at all

        client = boto3.client(
            "s3",
            endpoint_url=credentials.endpoint_url,
            region_name=credentials.region,
            aws_access_key_id=credentials.access_key_id.get_secret_value(),
            aws_secret_access_key=credentials.secret_access_key.get_secret_value(),
        )
        return cls(bucket=credentials.bucket, client=client)

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Upload one object, replacing whatever the key held."""
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType=content_type)

    def get(self, key: str) -> bytes | None:
        """Download one object whole, or return `None` when the key is absent."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if _names_an_absent_object(error):
                return None
            raise
        body = response["Body"]
        return bytes(body.read())  # type: ignore[attr-defined]  # botocore streams are not typed

    def head(self, key: str) -> int | None:
        """Return one object's byte count, or `None` when the key is absent."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if _names_an_absent_object(error):
                return None
            raise
        return int(str(response["ContentLength"]))

    def delete(self, key: str) -> None:
        """Remove one object; a key that was already absent is not an error."""
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def list_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> Iterator[ListedObject]:
        """Walk one prefix page by page, refusing once `max_keys` objects have been named."""
        seen = 0
        token: str | None = None
        while True:
            request: dict[str, object] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
                "MaxKeys": LISTING_PAGE_SIZE,
            }
            if token is not None:
                request["ContinuationToken"] = token
            response = self.client.list_objects_v2(**request)
            for entry in _listed_entries(response):
                seen += 1
                if seen > max_keys:
                    raise ObjectStoreError(
                        f"listing {prefix!r} passed the {max_keys}-key budget; narrow the prefix rather "
                        "than paging an unbounded population into memory"
                    )
                yield entry
            if not bool(response.get("IsTruncated")):
                return
            next_token = response.get("NextContinuationToken")
            if not isinstance(next_token, str):
                return
            token = next_token


@dataclass(slots=True)
class InMemoryObjectStoreBackend:
    """A dict-backed bucket, so every writer and reader test runs without a network."""

    objects: dict[str, bytes] = field(default_factory=dict)
    content_types: dict[str, str] = field(default_factory=dict)

    def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Store one object's bytes and the content type it was declared under."""
        self.objects[key] = payload
        self.content_types[key] = content_type

    def get(self, key: str) -> bytes | None:
        """Return one object's bytes, or `None` when the key is absent."""
        return self.objects.get(key)

    def head(self, key: str) -> int | None:
        """Return one object's byte count, or `None` when the key is absent."""
        payload = self.objects.get(key)
        return None if payload is None else len(payload)

    def delete(self, key: str) -> None:
        """Remove one object; a key that was already absent is not an error."""
        self.objects.pop(key, None)
        self.content_types.pop(key, None)

    def list_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> Iterator[ListedObject]:
        """Walk one prefix in key order, refusing once `max_keys` objects have been named."""
        seen = 0
        for key in sorted(self.objects):
            if not key.startswith(prefix):
                continue
            seen += 1
            if seen > max_keys:
                raise ObjectStoreError(f"listing {prefix!r} passed the {max_keys}-key budget")
            yield ListedObject(key=key, last_modified=None)


class ReadOnlyObjectStore(Protocol):
    """The read half of the warehouse: list, size, fetch. No `put`, no `delete`, by construction.

    A serving plane is handed one of these rather than an `ObjectStore`, so "a read cannot write"
    is a property of the object it holds instead of a rule every reader has to keep.
    """

    def read_object(self, relative_path: str, *, max_bytes: int = MAX_READ_OBJECT_BYTES) -> bytes | None: ...

    def object_size(self, relative_path: str) -> int | None: ...

    def list_relative_paths(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[str, ...]: ...

    def list_recent_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[ListedObject, ...]: ...


@dataclass(frozen=True, slots=True)
class ReadOnlyObjectStoreView:
    """The read-only facade a serving plane receives, built from a backend and a prefix.

    Deliberately NOT a subclass of `ObjectStore` and deliberately not holding one: inheritance or
    delegation would keep `put` and `delete` reachable through the object a route handed out.
    """

    backend: ObjectStoreBackend
    prefix: str = ""

    def read_object(self, relative_path: str, *, max_bytes: int = MAX_READ_OBJECT_BYTES) -> bytes | None:
        """Read one object under its byte ceiling, settling the size with a `head` first."""
        return _read_bounded(self.backend, _absolute_key(self.prefix, relative_path), max_bytes=max_bytes)

    def object_size(self, relative_path: str) -> int | None:
        """Return one object's byte count without fetching it, or `None` when the key is absent."""
        return self.backend.head(_absolute_key(self.prefix, relative_path))

    def list_relative_paths(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[str, ...]:
        """Return every key under one RELATIVE prefix, with the store prefix stripped back off."""
        return tuple(entry.key for entry in self.list_recent_objects(prefix, max_keys=max_keys))

    def list_recent_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[ListedObject, ...]:
        """Return every listed object under one RELATIVE prefix, keys relative and times intact."""
        return _relative_listing(self.backend, self.prefix, prefix, max_keys=max_keys)


@dataclass(frozen=True, slots=True)
class ObjectStore:
    """The receipted warehouse writer and reader, bound to one backend and one prefix."""

    backend: ObjectStoreBackend
    #: An optional root INSIDE the bucket, outside the frozen `layer=/kind=` layout, so a dry run
    #: can be proven against a scratch root without touching the published warehouse.
    prefix: str = ""

    def absolute_key(self, relative_path: str) -> str:
        """Return the bucket key for one relative path, refusing anything that escapes the prefix."""
        return _absolute_key(self.prefix, relative_path)

    def read_only(self) -> ReadOnlyObjectStoreView:
        """Return the read-only facade over this store's backend and prefix."""
        return ReadOnlyObjectStoreView(backend=self.backend, prefix=self.prefix)

    def write_partition(  # noqa: PLR0913 - one keyword per partition identity field is the contract
        self,
        table: pa.Table,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
        part_index: int = 0,
        stream: ParquetStreamSchema | None = None,
    ) -> ParquetWriteReceipt:
        """Conform to the REGISTERED schema for `(layer, kind)`, serialize, and upload one partition."""
        validated_kind = validate_partition_kind(kind)
        tier = validate_zoom_tier(zoom)
        registered = stream_schema(validate_layer_slug(layer), validated_kind)
        if stream is not None and stream != registered:
            raise ParquetSchemaMismatchError(
                f"the schema passed for {layer!r} {validated_kind} is not the one the warehouse registry "
                "pins for that stream; a partition written under a caller's own schema is unreadable "
                "by every reader that trusts the registry"
            )
        if table.num_rows == 0:
            raise EmptyPartitionError(
                f"stream {registered.name!r} day {day.isoformat()} rung {tier} has no rows; emptiness is "
                "recorded as a governed absence or a derived-empty completion marker, never as an empty part"
            )
        conformed = conform_to_stream_schema(table, registered)
        if tier == BASE_PARTITION_ZOOM:
            refuse_null_base_columns(conformed, registered, day=day)
        if self.absence_exists(layer, validated_kind, tier, day):
            raise GovernedAbsenceConflictError(
                f"{layer!r} {validated_kind} z{tier} {day.isoformat()} carries a governed-absence marker; "
                "retracting it is a manual admin action, not something a write may do implicitly"
            )
        payload = serialize_parquet(conformed, registered.compression)
        relative_path = partition_path(layer, validated_kind, tier, day, part_index)
        key = self.absolute_key(relative_path)
        if part_index == 0:
            # THE RETRACTION POINT. Every export writes its parts contiguously from 0, so `part-0` is
            # the first byte of a new export and the moment the previous one stops describing this
            # day. Clearing here abandons the day with its old export and old claim both intact when
            # the write then fails, which is the safe direction. See `AGENTS.md` in this directory.
            self.clear_completion_marker(layer, validated_kind, tier, day)
        self.backend.put(key, payload, content_type=PARQUET_CONTENT_TYPE)
        return ParquetWriteReceipt(
            key=key,
            relative_path=relative_path,
            stream=registered.name,
            kind=validated_kind,
            zoom=tier,
            day=day,
            row_count=conformed.num_rows,
            byte_count=len(payload),
            sha256=sha256_of(payload),
        )

    def write_completion_marker(
        self,
        completion: PartitionCompletion,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
    ) -> CompletionWriteReceipt:
        """Upload the object that ASSERTS one stream-day at one tier finished exporting."""
        tier = validate_zoom_tier(zoom)
        if completion.derived_empty and tier == BASE_PARTITION_ZOOM:
            raise ParquetWriteError(
                "the base rung cannot be derived-empty: it generalises nothing, so its emptiness is a "
                "governed absence and has its own marker"
            )
        relative_path = (
            derived_empty_completion_marker_path(layer, kind, tier, day)
            if completion.derived_empty
            else completion_marker_path(layer, kind, tier, day)
        )
        payload = completion.to_json_bytes()
        key = self.absolute_key(relative_path)
        self.backend.put(key, payload, content_type=JSON_CONTENT_TYPE)
        return CompletionWriteReceipt(
            key=key,
            relative_path=relative_path,
            kind=validate_partition_kind(kind),
            zoom=tier,
            day=day,
            part_count=completion.part_count,
            row_count=completion.row_count,
            byte_count=len(payload),
            sha256=sha256_of(payload),
            derived_empty=completion.derived_empty,
        )

    def write_absence_marker(
        self,
        absence: GovernedAbsence,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
    ) -> AbsenceWriteReceipt:
        """Upload the object recording one stream-day is deliberately empty, refusing over real data."""
        tier = validate_zoom_tier(zoom)
        validated_kind = validate_partition_kind(kind)
        blocking = self.part_blocking_absence(layer, validated_kind, tier, day)
        if blocking is not None:
            raise GovernedAbsenceConflictError(
                f"{layer!r} {validated_kind} z{tier} {day.isoformat()} already holds data ({blocking}); "
                "correcting a completed record is a manual admin action"
            )
        # A day cannot be both deliberately empty and a finished export. The refusal above already
        # rules out parts, so any completion marker still here is residue from a day whose parts were
        # removed: retract it rather than leave two markers making opposite claims about one day.
        self.clear_completion_marker(layer, validated_kind, tier, day)
        relative_path = absence_marker_path(layer, validated_kind, tier, day)
        payload = absence.to_json_bytes()
        key = self.absolute_key(relative_path)
        self.backend.put(key, payload, content_type=JSON_CONTENT_TYPE)
        return AbsenceWriteReceipt(
            key=key,
            relative_path=relative_path,
            kind=validated_kind,
            zoom=tier,
            day=day,
            byte_count=len(payload),
            sha256=sha256_of(payload),
        )

    def absence_exists(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> bool:
        """Report whether one stream-day carries a governed-absence marker AT THIS TIER, without reading it."""
        return self.backend.head(self.absolute_key(absence_marker_path(layer, kind, zoom, day))) is not None

    def part_blocking_absence(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str | None:
        """Return one part key that would make a governed absence at this rung a lie, or `None`."""
        for relative_path in self.list_relative_paths(day_prefix(layer, kind, zoom, day)):
            if try_parse_partition_path(relative_path) is not None:
                return relative_path
        return None

    def clear_completion_marker(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> None:
        """Retract BOTH completion claims of one stream-day-tier; raises on failure, deliberately.

        Both names, because a rung that derived to nothing last time and to rows this time would
        otherwise keep its `_complete.empty.json` beside the new parts and claim, at one rung, both
        that it holds rows and that it holds none. Deleting an absent key is a success.
        """
        self.backend.delete(self.absolute_key(completion_marker_path(layer, kind, zoom, day)))
        if zoom != BASE_PARTITION_ZOOM:
            self.backend.delete(self.absolute_key(derived_empty_completion_marker_path(layer, kind, zoom, day)))

    def put_immutable(self, relative_path: str, payload: bytes, *, content_type: str) -> bool:
        """Create one content-addressed object, ADOPTING an exact replay and refusing a different body.

        Returns whether these bytes were newly written. The key names its own content digest, so a
        key already holding identical bytes is the same object and a retry is a success; a key
        holding different bytes under a content address means the address is a lie and is refused.
        """
        key = self.absolute_key(relative_path)
        existing = self.backend.get(key)
        if existing is not None:
            if existing != payload:
                raise ImmutableObjectConflictError(
                    f"{relative_path!r} already holds different bytes than the ones offered; a "
                    "content-addressed object that disagrees with its own key is never overwritten"
                )
            return False
        self.backend.put(key, payload, content_type=content_type)
        return True

    def write_availability_retry(self, payload: bytes, *, layer: str, kind: PartitionKind, day: date) -> str:
        """Record that one terminal lane-day still owes its availability step; returns the marker path."""
        if not payload or len(payload) > MAX_AVAILABILITY_RETRY_BYTES:
            raise ObjectStoreError(
                f"an availability retry claim carries 1..{MAX_AVAILABILITY_RETRY_BYTES} bytes of POINTERS "
                f"to the day's receipts, got {len(payload)}; it is not somewhere to park a payload"
            )
        relative_path = availability_retry_path(layer, validate_partition_kind(kind), day)
        self.backend.put(self.absolute_key(relative_path), payload, content_type=JSON_CONTENT_TYPE)
        return relative_path

    def read_partition(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
        part_index: int = 0,
    ) -> PartitionRead:
        """Read one part file back, refusing rather than answering from anywhere else when it is absent."""
        relative_path = partition_path(layer, kind, validate_zoom_tier(zoom), day, part_index)
        payload = self.backend.get(self.absolute_key(relative_path))
        if payload is None:
            raise PartitionNotWrittenError(f"no partition object at {relative_path!r}")
        table = pq.read_table(io.BytesIO(payload))
        return PartitionRead(
            relative_path=relative_path,
            table=table,
            byte_count=len(payload),
            sha256=sha256_of(payload),
        )

    def read_object(self, relative_path: str, *, max_bytes: int = MAX_READ_OBJECT_BYTES) -> bytes | None:
        """Read one arbitrary object under its byte ceiling, or `None` when the key is absent."""
        return _read_bounded(self.backend, self.absolute_key(relative_path), max_bytes=max_bytes)

    def object_size(self, relative_path: str) -> int | None:
        """Return one object's byte count without fetching it, or `None` when the key is absent."""
        return self.backend.head(self.absolute_key(relative_path))

    def list_relative_paths(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[str, ...]:
        """Return every key under one RELATIVE prefix, with the store prefix stripped back off."""
        return tuple(entry.key for entry in self.list_recent_objects(prefix, max_keys=max_keys))

    def list_recent_objects(self, prefix: str, *, max_keys: int = MAX_LISTED_KEYS) -> tuple[ListedObject, ...]:
        """Return every listed object under one RELATIVE prefix, keys relative and times intact."""
        return _relative_listing(self.backend, self.prefix, prefix, max_keys=max_keys)


def _absolute_key(prefix: str, relative_path: str) -> str:
    """Return the bucket key for one relative path, refusing anything that escapes the prefix."""
    if not relative_path or relative_path.startswith("/"):
        raise ObjectKeyError(f"{relative_path!r} is not a relative object path")
    if "\\" in relative_path or ".." in relative_path.split("/"):
        raise ObjectKeyError(f"{relative_path!r} would traverse outside the configured prefix")
    return f"{prefix}{relative_path}"


def _read_bounded(backend: ObjectStoreBackend, key: str, *, max_bytes: int) -> bytes | None:
    """Fetch one object only after a `head` proves it fits, refusing an oversize one by type.

    The pre-check is the bound: a ceiling applied to bytes already in memory has paid the whole
    download before it says no, and the download is what an unbounded object costs.
    """
    if max_bytes < 1:
        raise ObjectStoreError(f"a read ceiling is at least one byte, got {max_bytes}")
    size = backend.head(key)
    if size is None:
        return None
    if size > max_bytes:
        raise ObjectTooLargeError(f"the object is {size} bytes, over this read's {max_bytes}-byte ceiling")
    payload = backend.get(key)
    if payload is not None and len(payload) > max_bytes:
        # The `head` and the `get` disagreed, which means the object was replaced mid-read. The
        # bytes in hand are the ones that matter, so they are refused rather than trusted.
        raise ObjectTooLargeError(
            f"the object grew to {len(payload)} bytes between its head and its read, over the {max_bytes}-byte ceiling"
        )
    return payload


def _relative_listing(
    backend: ObjectStoreBackend, store_prefix: str, prefix: str, *, max_keys: int
) -> tuple[ListedObject, ...]:
    """List one RELATIVE prefix, returning entries whose keys have the store prefix stripped off."""
    listed = backend.list_objects(f"{store_prefix}{prefix}", max_keys=max_keys)
    return tuple(
        ListedObject(key=entry.key[len(store_prefix) :], last_modified=entry.last_modified) for entry in listed
    )


def conform_to_stream_schema(table: pa.Table, stream: ParquetStreamSchema) -> pa.Table:
    """Select, cast AND SORT `table` into its stream's storage contract, so its bytes are its content."""
    missing = tuple(name for name in stream.column_names if name not in table.column_names)
    if missing:
        raise ParquetSchemaMismatchError(f"table for stream {stream.name!r} is missing column(s) {missing}")
    try:
        conformed = table.select(list(stream.column_names)).cast(stream.arrow_schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as error:
        raise ParquetSchemaMismatchError(
            f"table for stream {stream.name!r} does not cast to its pinned schema: {error}"
        ) from error
    # The sort is part of the contract, not a nicety: without it a partition's bytes depend on the
    # order the caller happened to build its rows in, and the determinism the receipts checksum
    # (NFR 1: same artifact, same inputs, same seed -> byte-identical partition) is not provable.
    return conformed.sort_by([(column, "ascending") for column in stream.sort_columns])


def refuse_null_base_columns(table: pa.Table, stream: ParquetStreamSchema, *, day: date) -> None:
    """Refuse a base-rung table that nulls a column only the coarse rungs are allowed to null."""
    offending = tuple(name for name in stream.base_non_null_columns if table.column(name).null_count > 0)
    if offending:
        raise NullBaseColumnError(
            f"stream {stream.name!r} day {day.isoformat()} nulls base-rung column(s) {offending}; those "
            "columns are nullable only so the coarse rungs may null them"
        )


def serialize_parquet(table: pa.Table, compression: str) -> bytes:
    """Serialize one table to Parquet bytes under the codec the sibling service writes."""
    sink = io.BytesIO()
    pq.write_table(table, sink, compression=compression, version=PARQUET_FORMAT_VERSION, write_statistics=True)
    return sink.getvalue()


def completed_parts_from(receipts: tuple[ParquetWriteReceipt, ...]) -> tuple[CompletedPart, ...]:
    """Fold part receipts into the per-part digests a version-2 completion marker carries."""
    return tuple(
        CompletedPart(
            relative_path=receipt.relative_path,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
            sha256=receipt.sha256,
        )
        for receipt in receipts
    )


def sha256_of(payload: bytes) -> str:
    """Return the lowercase hex SHA-256 of exactly these bytes."""
    return hashlib.sha256(payload).hexdigest()


def scratch_rooted_store(store: ObjectStore, dry_run_prefix: str | None) -> ObjectStore:
    """Return the store a run writes through, refusing a dry-run prefix that is not a scratch root.

    `None` means the published lane, which is the caller's own gate to defend. Anything else must
    sit under `SCRATCH_PREFIX_ROOT` and end in `/`, or the "dry run" would land in the warehouse
    under a slightly different key and look exactly like a real publication.
    """
    if dry_run_prefix is None:
        return store
    if not dry_run_prefix.startswith(SCRATCH_PREFIX_ROOT) or not dry_run_prefix.endswith("/"):
        raise ScratchPrefixError(
            f"dry-run prefix {dry_run_prefix!r} is not a scratch root under {SCRATCH_PREFIX_ROOT!r}; a dry run "
            "that writes anywhere else is a production write wearing a flag"
        )
    return dataclasses.replace(store, prefix=dry_run_prefix)


def _listed_entries(response: Mapping[str, object]) -> Iterator[ListedObject]:
    """Read one `list_objects_v2` page into typed entries, skipping anything malformed."""
    contents = response.get("Contents")
    if not isinstance(contents, list):
        return
    for entry in contents:
        if not isinstance(entry, dict):
            continue
        key = entry.get("Key")
        if not isinstance(key, str):
            continue
        modified = entry.get("LastModified")
        yield ListedObject(key=key, last_modified=modified if isinstance(modified, datetime) else None)


def _names_an_absent_object(error: Exception) -> bool:
    """Return whether a botocore error says the key is absent rather than that the call failed."""
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return False
    error_body = response.get("Error")
    code = error_body.get("Code") if isinstance(error_body, dict) else None
    status = response.get("ResponseMetadata")
    http_code = status.get("HTTPStatusCode") if isinstance(status, dict) else None
    return str(code) in _ABSENT_OBJECT_CODES or str(http_code) in _ABSENT_OBJECT_CODES
