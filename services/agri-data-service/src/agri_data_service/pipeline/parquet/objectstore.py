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

A FULL RE-EXPORT MUST REMOVE THE PARTS IT NO LONGER WRITES, and that is why this module can delete.
A `static_lookup` export rewrites the whole population, so a re-export whose population SHRANK
writes `part-0` and `part-1` over a day that used to hold `part-0` .. `part-3` and leaves the last
two behind. Those orphans are read by anything scanning the day prefix, so the day serves the new
rows AND the superseded ones together -- on `evacuation-zones` that is a retracted evacuation level
published beside the current one. They also pin `oldest_export_instant` to the OLD export, which
holds the lane `stale` forever and re-exports 162 MB on every tick. `prune_surplus_parts` closes
both, and runs only AFTER every new part is written: a prune that ran first and then failed would
leave the day EMPTY, which reads as a present-but-thin version and is worse than the orphan.

EVERY OPERATION HERE NAMES ONE ZOOM TIER, AND NONE OF THEM MAY SPAN THE LADDER. `zoom` is a required
argument of every write, listing, existence check and prune -- there is deliberately no "all tiers"
mode and no default. One convenient tier-less listing is all it takes to hand a reader four
resolutions of the same day as though they were one population: nothing raises, the row counts merely
quadruple and the geometry silently disagrees with itself. A caller that genuinely wants the whole
ladder asks four times and knows it asked. The prune is scoped the same way and for the same reason
-- removing a day's surplus parts at z13 must not reach the z09 parts of that same day, which are a
different resolution of it rather than an older export of it.

A DAY IS FINISHED ONLY WHEN IT SAYS SO, AND THE COMPLETION CLAIM IS RETRACTED BY THE FIRST PART
WRITE, NOT BY THE ATTEMPT (owner, RUNBOOK 0.34.1). Writing `part-0` is the moment a day's previous
export stops being what the day holds, so that is where `write_partition` retracts the marker --
after the empty-row and governed-absence refusals, immediately before the upload.

The earlier design retracted it in the driver before the adapter ran, and that was wrong in one
direction that matters: EVERY failed attempt -- a statement timeout, a transient database error, a
source that now returns zero rows -- stripped the completion claim off a day whose parts were an
intact, previously-marked release. Nothing on disk had got worse, yet the day went from `data` to
`incomplete`, and once serving consults completion that is a good day disappearing from the API
because an unrelated export attempt failed. Retracting at the first write cannot do that: a day
nobody overwrote keeps its claim.

What the ordering still guarantees is the thing the marker exists for. Parts are never uploaded
under a marker left by an EARLIER export, because the marker is gone before the first of them
lands, so a run killed between two uploads leaves parts with no completion claim rather than a
mixture wearing one. A failed retraction fails the write -- there is no version of "the retraction
did not work, upload anyway" that is safe -- while a failed MARK merely leaves a complete day
looking unfinished, which costs one re-export and loses nothing.

CROSS-TIER AGREEMENT OF ONE DAY IS NOT THIS MODULE'S INVARIANT. `write_absence` still refuses to mark
a day that already holds data, but only at the tier being marked: the four tiers of one day live
under four disjoint prefixes, so policing them together would cost four listings per marker and still
race. "Every tier of a published day is present" is the DERIVATION step's obligation, because
derivation is the only thing that knows a coarse tier was computed from a base one.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import TYPE_CHECKING, Final, Protocol

import boto3  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from agri_data_service.config import ObjectStoreCredentials, Settings, settings
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    absence_marker_path,
    completion_marker_path,
    day_prefix,
    month_prefix,
    partition_path,
    stream_prefix,
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
    year_prefix,
    zoom_prefix,
)
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, get_stream_schema
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, base_non_null_columns

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
ABSENCE_CONTENT_TYPE: Final = "application/json"
COMPLETION_CONTENT_TYPE: Final = "application/json"
AVAILABILITY_RETRY_CONTENT_TYPE: Final = "application/json"
MAX_LISTED_KEYS: Final = 500_000
# One availability retry claim names every PHYSICAL receipt of one lane-day's whole ladder, because
# that is what a later turn rebuilds the day's evidence from without re-exporting it. The ceiling is
# therefore sized to the same population `availability_index.TYPED_RECEIPT_MAX_BYTES` allows per rung
# (1 MiB), times the four rungs, times a margin -- `soil-survey` streams ~3,016 parts in one day and
# a claim it could not fit would lose that day from the index for good. Anything past this is a
# caller trying to park a payload here rather than a pointer to one.
MAX_AVAILABILITY_RETRY_BYTES: Final = 8 * 1024 * 1024
_AVAILABILITY_RETRY_SEGMENT: Final = "availability/pending/"
_AVAILABILITY_RETRY_DAY_PREFIX: Final = "day="
_AVAILABILITY_RETRY_SUFFIX: Final = ".json"
_ABSENT_OBJECT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})


def availability_lane_root(layer: str, kind: PartitionKind) -> str:
    """Return the `layer=<slug>/kind=<kind>` root the availability contract keys everything beneath."""
    return stream_prefix(layer, kind).rstrip("/")


def availability_retry_path(layer: str, kind: PartitionKind, day: date) -> str:
    """Return the relative key of one lane-day's availability retry marker."""
    return (
        f"{availability_lane_root(layer, kind)}/{_AVAILABILITY_RETRY_SEGMENT}"
        f"{_AVAILABILITY_RETRY_DAY_PREFIX}{day.isoformat()}{_AVAILABILITY_RETRY_SUFFIX}"
    )


def availability_retry_prefix(layer: str, kind: PartitionKind) -> str:
    """Return the prefix holding every availability retry marker of one lane."""
    return f"{availability_lane_root(layer, kind)}/{_AVAILABILITY_RETRY_SEGMENT}"


def try_parse_availability_retry_path(path: str) -> date | None:
    """Return the day one availability retry marker owes, or `None` when the key is not one."""
    marker, separator, tail = path.partition(f"/{_AVAILABILITY_RETRY_SEGMENT}")
    if not separator or not marker or not tail.startswith(_AVAILABILITY_RETRY_DAY_PREFIX):
        return None
    if not tail.endswith(_AVAILABILITY_RETRY_SUFFIX):
        return None
    rendered = tail[len(_AVAILABILITY_RETRY_DAY_PREFIX) : -len(_AVAILABILITY_RETRY_SUFFIX)]
    try:
        parsed = date_type.fromisoformat(rendered)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == rendered else None


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
    """One part file, absence marker or completion marker of the layout, with the instant it was written."""

    relative_path: str
    last_modified: datetime | None


class ObjectStoreBackend(Protocol):
    """The whole object-store surface the warehouse needs; implement it to test without a network."""

    def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def list_objects(self, prefix: str) -> Iterator[ListedObject]: ...

    def size_of(self, key: str) -> int | None: ...

    def get(self, key: str) -> bytes | None: ...


class _S3Api(Protocol):
    """The five boto3 S3 calls this module makes, typed at the boundary rather than as `Any`."""

    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class ParquetWriteReceipt:
    """Provenance for one written partition: what landed, at which tier, where, and its integrity digest."""

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


@dataclass(frozen=True, slots=True)
class SurplusPruneResult:
    """What a post-export prune removed from one stream-day at one tier, and every removal it could not make.

    The rows this export wrote are correct, so a failed prune must never fail the export -- but an
    unreported one leaves the orphan that pins the lane `stale`, so `report` renders both halves.
    """

    removed: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def report(self) -> str | None:
        """Render what happened for a caller to surface, or `None` when there was nothing to say."""
        if not self.removed and not self.failures:
            return None
        lines = []
        if self.removed:
            lines.append(f"removed {len(self.removed)} surplus part file(s): {', '.join(self.removed)}")
        lines.extend(self.failures)
        return "; ".join(lines)


@dataclass(slots=True)
class WrittenObjectLedger:
    """Every layout object one recording scope wrote, keyed by relative path, the last write winning."""

    partitions: dict[str, ParquetWriteReceipt] = field(default_factory=dict)
    completions: dict[str, CompletionWriteReceipt] = field(default_factory=dict)
    absences: dict[str, AbsenceWriteReceipt] = field(default_factory=dict)

    def parts_for(self, *, kind: PartitionKind, zoom: ZoomTier, day: date) -> tuple[ParquetWriteReceipt, ...]:
        """Return one rung-day's part receipts in OBJECT-KEY order, which is the order evidence requires."""
        matched = (
            receipt
            for receipt in self.partitions.values()
            if receipt.kind == kind and receipt.zoom == zoom and receipt.day == day
        )
        return tuple(sorted(matched, key=lambda receipt: receipt.relative_path))

    def completion_for(self, *, kind: PartitionKind, zoom: ZoomTier, day: date) -> CompletionWriteReceipt | None:
        """Return the completion marker this scope wrote for one rung-day, if it wrote one."""
        return next(
            (
                receipt
                for receipt in self.completions.values()
                if receipt.kind == kind and receipt.zoom == zoom and receipt.day == day
            ),
            None,
        )

    def absence_for(self, *, kind: PartitionKind, zoom: ZoomTier, day: date) -> AbsenceWriteReceipt | None:
        """Return the governed-absence marker this scope wrote for one rung-day, if it wrote one."""
        return next(
            (
                receipt
                for receipt in self.absences.values()
                if receipt.kind == kind and receipt.zoom == zoom and receipt.day == day
            ),
            None,
        )


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

    def delete(self, key: str) -> None:
        """Remove one object; S3 treats deleting an absent key as success, and so does this."""
        self.client.delete_object(Bucket=self.bucket, Key=key)

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

    def get(self, key: str) -> bytes | None:
        """Return the object's bytes, or `None` when it does not exist.

        ABSENT IS `None`, NOT AN EXCEPTION, matching `size_of` directly above -- a part file that
        vanished between a listing and this read is a concurrent prune, which is a normal race in a
        store two writers share, not a fault worth ending a lane over.
        """
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _client_error_code(exc) in _ABSENT_OBJECT_CODES:
                return None
            raise
        body = response.get("Body")
        if body is None:
            return None
        payload = body.read()  # type: ignore[attr-defined]
        return payload if isinstance(payload, bytes) else bytes(payload)

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
        # Empty unless a caller opened `recording_written_objects`; see AGENTS.md, "the write ledger".
        self._ledgers: list[WrittenObjectLedger] = []

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

    @contextmanager
    def recording_written_objects(self) -> Iterator[WrittenObjectLedger]:
        """Capture the receipt of every part, completion and absence written inside this scope."""
        ledger = WrittenObjectLedger()
        self._ledgers.append(ledger)
        try:
            yield ledger
        finally:
            self._ledgers.remove(ledger)

    def _record_partition(self, receipt: ParquetWriteReceipt) -> ParquetWriteReceipt:
        for ledger in self._ledgers:
            ledger.partitions[receipt.relative_path] = receipt
        return receipt

    def _record_absence(self, receipt: AbsenceWriteReceipt) -> AbsenceWriteReceipt:
        for ledger in self._ledgers:
            ledger.absences[receipt.relative_path] = receipt
        return receipt

    def _record_completion(self, receipt: CompletionWriteReceipt) -> CompletionWriteReceipt:
        for ledger in self._ledgers:
            ledger.completions[receipt.relative_path] = receipt
        return receipt

    def write_partition(  # noqa: PLR0913 - one partition coordinate per arg, and none may be defaulted
        self,
        table: pa.Table,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
        part_index: int = 0,
    ) -> ParquetWriteReceipt:
        """Conform `table` to the layer's schema FOR THIS KIND, sort it to the grain, and upload one part file."""
        stream = get_stream_schema(layer, kind)
        conformed = conform_to_stream_schema(table, stream)
        if conformed.num_rows == 0:
            raise EmptyPartitionError(
                f"refusing to write a zero-row {layer!r} {kind} z{zoom} partition for {day}: "
                "an empty file reads as a present day and hides the gap"
            )
        _refuse_null_base_columns(conformed, layer=layer, zoom=zoom, day=day)
        if self.absence_exists(layer, kind, zoom, day):
            raise GovernedAbsenceConflictError(
                f"{layer!r} {kind} z{zoom} {day} carries a governed-absence marker; "
                "retracting it is a manual admin action, not something a write may do implicitly"
            )
        payload = _serialize_parquet(conformed, stream.compression)
        relative_path = partition_path(layer, kind, zoom, day, part_index)
        key = self.key_for(relative_path)
        if part_index == 0:
            # THE RETRACTION POINT. Every lane writes its parts contiguously from 0, so `part-0` is
            # the first byte of a new export and the moment the previous one stops describing this
            # day. Raising here abandons the day with its old export and old claim both intact,
            # which is the safe direction; the module docstring says why this may not move earlier.
            self.clear_completion_marker(layer, kind, zoom, day)
        self._backend.put(key, payload, content_type=PARQUET_CONTENT_TYPE)
        return self._record_partition(
            ParquetWriteReceipt(
                key=key,
                relative_path=relative_path,
                stream=stream.name,
                kind=kind,
                zoom=zoom,
                day=day,
                row_count=conformed.num_rows,
                byte_count=len(payload),
                sha256=sha256_digest(payload),
            )
        )

    def write_absence(
        self,
        absence: GovernedAbsence,
        *,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
    ) -> AbsenceWriteReceipt:
        """Mark one stream-day AT ONE TIER as deliberately empty, refusing when data already covers it."""
        day_scope = self.key_for(day_prefix(layer, kind, zoom, day))
        for existing in self._backend.list_objects(day_scope):
            if try_parse_partition_path(self.relative_key(existing.key)) is not None:
                raise GovernedAbsenceConflictError(
                    f"{layer!r} {kind} z{zoom} {day} already holds data ({self.relative_key(existing.key)}); "
                    "correcting a completed record is a manual admin action"
                )
        # A day cannot be both deliberately empty and a finished export. The refusal above already
        # rules out parts, so any completion marker still here is residue from a day whose parts were
        # removed -- retract it rather than leave two markers making opposite claims about one day.
        self.clear_completion_marker(layer, kind, zoom, day)
        payload = absence.to_json_bytes()
        relative_path = absence_marker_path(layer, kind, zoom, day)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=ABSENCE_CONTENT_TYPE)
        return self._record_absence(
            AbsenceWriteReceipt(
                key=key,
                relative_path=relative_path,
                kind=kind,
                zoom=zoom,
                day=day,
                byte_count=len(payload),
                sha256=sha256_digest(payload),
            )
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
        """Assert that one stream-day at one tier FINISHED exporting. Must be the export's LAST object.

        Nothing is re-listed to check the claim: the caller has the write receipts of every part it
        just uploaded, and a listing taken here would only re-ask the store a question the export
        already answered -- while adding a second failure mode to the one operation that must stay
        cheap enough to run after every single lane-day.
        """
        payload = completion.to_json_bytes()
        relative_path = completion_marker_path(layer, kind, zoom, day)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=COMPLETION_CONTENT_TYPE)
        return self._record_completion(
            CompletionWriteReceipt(
                key=key,
                relative_path=relative_path,
                kind=kind,
                zoom=zoom,
                day=day,
                part_count=completion.part_count,
                row_count=completion.row_count,
                byte_count=len(payload),
                sha256=sha256_digest(payload),
            )
        )

    def clear_completion_marker(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> None:
        """Retract one stream-day-tier's completion claim. Called by the first part write, and may raise.

        Deleting a key that is not there is a success in S3 and so it is here, which is what lets the
        write path call it unconditionally rather than behind an existence check that would cost a
        HEAD on every export to save a delete on almost none of them.

        IT RAISES ON FAILURE, DELIBERATELY. The caller must abandon the write: uploading a part while
        an older export's marker still stands is the exact state the marker exists to prevent, and a
        caller that swallowed this error would reintroduce it while believing it had been closed.
        """
        self._backend.delete(self.key_for(completion_marker_path(layer, kind, zoom, day)))

    def clear_absence_marker(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> None:
        """Explicitly authorize absence-to-data correction; see AGENTS.md."""
        self._backend.delete(self.key_for(absence_marker_path(layer, kind, zoom, day)))

    def availability_retry_marker_path(self, layer: str, kind: PartitionKind, day: date) -> str:
        """Return where one lane-day's availability retry claim lives, without touching the store."""
        return availability_retry_path(layer, kind, day)

    def write_availability_retry(self, payload: bytes, *, layer: str, kind: PartitionKind, day: date) -> str:
        """Record that one terminal lane-day still owes its availability step; returns the marker path."""
        if not payload or len(payload) > MAX_AVAILABILITY_RETRY_BYTES:
            raise ValueError(
                f"an availability retry marker must be 1..{MAX_AVAILABILITY_RETRY_BYTES} bytes, got {len(payload)}"
            )
        relative_path = availability_retry_path(layer, kind, day)
        self._backend.put(self.key_for(relative_path), payload, content_type=AVAILABILITY_RETRY_CONTENT_TYPE)
        return relative_path

    def read_availability_retry(self, layer: str, kind: PartitionKind, day: date) -> bytes | None:
        """Return one lane-day's availability retry marker, or `None` when nothing is owed."""
        payload = self._backend.get(self.key_for(availability_retry_path(layer, kind, day)))
        if payload is not None and len(payload) > MAX_AVAILABILITY_RETRY_BYTES:
            raise ValueError(f"availability retry marker for {layer!r} {kind} {day.isoformat()} exceeds its ceiling")
        return payload

    def list_availability_retry_days(self, layer: str, kind: PartitionKind) -> tuple[date, ...]:
        """Return every day of one lane whose availability step is owed, oldest first."""
        days: list[date] = []
        for listed in self._backend.list_objects(self.key_for(availability_retry_prefix(layer, kind))):
            day = try_parse_availability_retry_path(self.relative_key(listed.key))
            if day is not None:
                days.append(day)
            if len(days) > MAX_LISTED_KEYS:
                raise ValueError(f"listing {layer!r} {kind} availability retries exceeded the key budget")
        return tuple(sorted(days))

    def clear_availability_retry(self, layer: str, kind: PartitionKind, day: date) -> None:
        """Retract one lane-day's availability retry claim once the generation covers it."""
        self._backend.delete(self.key_for(availability_retry_path(layer, kind, day)))

    def list_partition_objects(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[ListedPartition, ...]:
        """Return ONE TIER's part files, absence markers and completion markers WITH their instants, by year/month.

        `zoom` is required, and there is no mode that returns the whole ladder. See the module
        docstring: a tier-less listing blends four resolutions of the same day into one population
        and nothing about the result looks wrong.
        """
        scope = self._listing_scope(layer, kind, zoom, year, month)
        found: list[ListedPartition] = []
        for listed in self._backend.list_objects(self.key_for(scope)):
            relative_path = self.relative_key(listed.key)
            # All THREE kinds pass, and the completion marker is the reason this is not two checks:
            # the census reads coverage out of exactly this listing, so a marker filtered out here
            # would leave every finished day looking half-written and be re-exported forever.
            if (
                try_parse_partition_path(relative_path) is None
                and try_parse_absence_marker_path(relative_path) is None
                and try_parse_completion_marker_path(relative_path) is None
            ):
                continue
            found.append(ListedPartition(relative_path=relative_path, last_modified=listed.last_modified))
            if len(found) > MAX_LISTED_KEYS:
                raise ValueError(f"listing {scope!r} exceeded the {MAX_LISTED_KEYS}-key budget")
        return tuple(found)

    def list_partition_keys(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return every part file, absence marker and completion marker of ONE TIER, narrowed by year and month.

        All three kinds, deliberately: the census decides coverage from exactly this list, so a
        completion marker filtered out here would leave every finished day reading as half-written.
        A caller that wants only the readable rows filters with `try_parse_partition_path` itself --
        several in `planes/` do -- and must then apply the completion rule alongside it.
        """
        return tuple(
            listed.relative_path for listed in self.list_partition_objects(layer, kind, zoom, year=year, month=month)
        )

    def read_partition(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> pa.Table:
        """Return ONE lane-day-tier's part files concatenated into a single table, in part order.

        THE WRITE PATH READS ONLY HERE, AND ONLY FOR DERIVATION. Every other reader in this repo
        scans the bucket through Polars with `polars_storage_options`, which is the right shape for
        serving: predicate pushdown, lazy, no bytes through this process. This method exists because
        the tier derivation runs INSIDE the writer, where there is a `store` and no credentials
        object -- and because it must see exactly the parts that were just written, not whatever a
        separately-configured scan resolves.

        Parts are read in INDEX ORDER, not listing order. S3 lists lexically, so `part-10` sorts
        before `part-2`, and a table assembled in that order would still hold every row but would no
        longer be in the grain order `conform_to_stream_schema` sorted it into -- which the
        derivation's own `sort` would then have to redo, and which any reader comparing two tiers
        byte-for-byte would see as a spurious difference.

        A part that vanishes between the listing and the read is SKIPPED rather than raising: the
        only thing that removes a part file is a concurrent prune, and RUNBOOK 0.33.3 B has the bulk
        drain running alongside the hourly cron by design. The lane-day advisory lock is what makes
        that race rare; this is what makes it survivable.
        """
        parsed = []
        for relative_path in self.list_partition_keys(layer, kind, zoom, year=day.year, month=day.month):
            partition = try_parse_partition_path(relative_path)
            if partition is not None and partition.day == day:
                parsed.append((partition.part_index, relative_path))
        if not parsed:
            raise ParquetWriteError(
                f"no part files to read for {layer!r} {kind} z{zoom} {day.isoformat()}; a tier cannot be derived "
                f"from a day that holds nothing"
            )
        tables = []
        for _, relative_path in sorted(parsed):
            payload = self._backend.get(self.key_for(relative_path))
            if payload is None:
                continue
            tables.append(pq.read_table(io.BytesIO(payload)))
        if not tables:
            raise ParquetWriteError(
                f"every part file of {layer!r} {kind} z{zoom} {day.isoformat()} disappeared between the listing and "
                f"the read; a concurrent prune emptied the day mid-derivation"
            )
        return pa.concat_tables(tables)

    def retract_partition_tier(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> SurplusPruneResult:
        """Empty ONE rung of one day: clear its completion claim, then delete every part it holds.

        DELIBERATELY NOT `prune_surplus_parts(written_part_count=0)`, which refuses that argument on
        purpose -- a prune may only ever TRAIL a completed write, and asking it to remove everything
        is how a bug empties a day it meant to shrink. Retraction is a different intent and gets a
        different name: a derived rung that now yields no rows must stop serving the rows it used to,
        and the day's base rung is untouched by it.

        THE MARKER GOES FIRST. While the parts are being removed the rung is briefly a partial read,
        and an UNMARKED partial read is what the census calls `incomplete` and redoes -- whereas a
        MARKED one is a rung asserting it finished while its rows vanish underneath it.

        Failures are RETURNED, not raised, matching `prune_surplus_parts`: the caller decides
        whether a rung it could not empty is fatal, and here it always is.
        """
        self.clear_completion_marker(layer, kind, zoom, day)
        removed: list[str] = []
        failures: list[str] = []
        for relative_path in self.list_partition_keys(layer, kind, zoom, year=day.year, month=day.month):
            partition = try_parse_partition_path(relative_path)
            if partition is None or partition.day != day:
                continue
            try:
                self._backend.delete(self.key_for(relative_path))
            except Exception as error:
                failures.append(f"{relative_path}: {type(error).__name__}: {error}")
            else:
                removed.append(relative_path)
        return SurplusPruneResult(removed=tuple(removed), failures=tuple(failures))

    def prune_surplus_parts(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        day: date,
        *,
        written_part_count: int,
    ) -> SurplusPruneResult:
        """Remove one stream-day's parts AT ONE TIER left behind by a SHRINKING full re-export. Never raises.

        Call this only AFTER every part of that re-export has landed: `written_part_count` is how
        many parts it wrote, so `part-<n>` for n >= it can only be surplus from an older, larger
        export of the SAME day AT THE SAME TIER. The module docstring says why the order may never be
        reversed, and why another tier's parts of this same day are never surplus.
        """
        if written_part_count <= 0:
            raise ValueError(
                f"refusing to prune {layer!r} {kind} z{zoom} {day.isoformat()} with written_part_count="
                f"{written_part_count}: that would delete every part of the day, and a prune may only "
                "ever trail a completed write"
            )
        try:
            listed = tuple(self._backend.list_objects(self.day_key_prefix(layer, kind, zoom, day)))
        except Exception as error:  # an unprunable day is a reportable orphan, never a failed export
            return SurplusPruneResult(
                removed=(),
                failures=(
                    f"listing {layer!r} {kind} z{zoom} {day.isoformat()} for surplus parts failed, so parts "
                    f"from a larger earlier export may still be published beside this one: "
                    f"{type(error).__name__}: {error}",
                ),
            )
        removed: list[str] = []
        failures: list[str] = []
        for entry in listed:
            relative_path = self.relative_key(entry.key)
            # Every coordinate is re-checked from the PARSED path, never inferred from the prefix: an
            # absence marker and a completion marker both parse as `None` here -- neither is ever
            # surplus -- and another lane, kind, TIER or day cannot match.
            parsed = try_parse_partition_path(relative_path)
            if (
                parsed is None
                or parsed.layer != layer
                or parsed.kind != kind
                or parsed.zoom != zoom
                or parsed.day != day
            ):
                continue
            if parsed.part_index < written_part_count:
                continue
            try:
                self._backend.delete(entry.key)
            except Exception as error:  # same: report the survivor, never lose the written rows
                failures.append(
                    f"removing surplus part {relative_path} failed, so it is still published beside this "
                    f"export: {type(error).__name__}: {error}"
                )
                continue
            removed.append(relative_path)
        return SurplusPruneResult(removed=tuple(removed), failures=tuple(failures))

    def partition_exists(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date, part_index: int = 0) -> bool:
        """Report whether one part file of one tier has been written, without downloading it."""
        return self._backend.size_of(self.key_for(partition_path(layer, kind, zoom, day, part_index))) is not None

    def absence_exists(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> bool:
        """Report whether one stream-day carries a governed-absence marker AT THIS TIER, without downloading it."""
        return self._backend.size_of(self.key_for(absence_marker_path(layer, kind, zoom, day))) is not None

    def part_blocking_absence(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str | None:
        """Return one part key that would make a governed absence at this rung a lie, or `None`.

        The SAME listing `write_absence` performs before it refuses, exposed so a caller writing a
        whole LADDER of markers can ask about every rung BEFORE it writes the first one -- a ladder
        that refuses half-way leaves coarse markers standing over a base rung that still serves rows.
        """
        day_scope = self.key_for(day_prefix(layer, kind, zoom, day))
        for existing in self._backend.list_objects(day_scope):
            relative_path = self.relative_key(existing.key)
            if try_parse_partition_path(relative_path) is not None:
                return relative_path
        return None

    def read_absence(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> GovernedAbsence | None:
        """Return one tier's governed-absence evidence, or ``None`` when no marker exists."""
        payload = self._backend.get(self.key_for(absence_marker_path(layer, kind, zoom, day)))
        return None if payload is None else GovernedAbsence.from_json_bytes(payload)

    def read_completion_marker(
        self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date
    ) -> PartitionCompletion | None:
        """Return one tier's completion receipt, or ``None`` when no marker exists."""
        payload = self._backend.get(self.key_for(completion_marker_path(layer, kind, zoom, day)))
        return None if payload is None else PartitionCompletion.from_json_bytes(payload)

    def day_key_prefix(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> str:
        """Return the absolute bucket prefix holding every part file for one stream-day at one tier."""
        return self.key_for(day_prefix(layer, kind, zoom, day))

    @staticmethod
    def _listing_scope(layer: str, kind: PartitionKind, zoom: ZoomTier, year: int | None, month: int | None) -> str:
        """Narrow a listing to one tier, then optionally to a year and a month inside it."""
        if year is None:
            if month is not None:
                raise ValueError("narrowing a listing to a month requires the year as well")
            return zoom_prefix(layer, kind, zoom)
        if month is None:
            return year_prefix(layer, kind, zoom, year)
        return month_prefix(layer, kind, zoom, year, month)


def _refuse_null_base_columns(table: pa.Table, *, layer: str, zoom: ZoomTier, day: date) -> None:
    """At the BASE rung only, refuse a null in a column the lane declared must never be null there.

    Eight fields across six lanes are `nullable=True` in their arrow schema for one reason: their
    COARSE rungs null them, and pyarrow will not cast a null into a non-nullable field. That
    relaxation also removed the check that used to make a NULL `sensor_id` fail this write loudly.
    `TierDerivation.base_non_null_columns` names them back, and this is where the naming bites.

    Derived rungs are skipped deliberately -- nulling those columns is exactly what they are for.
    """
    if zoom != BASE_ZOOM_TIER:
        return
    offenders = [
        column
        for column in base_non_null_columns(layer)
        if column in table.column_names and table.column(column).null_count > 0
    ]
    if offenders:
        raise ParquetWriteError(
            f"refusing to write the BASE rung of {layer!r} for {day.isoformat()}: column(s) {offenders} hold nulls, "
            f"and this lane declares them non-null at z{BASE_ZOOM_TIER}. They are nullable in the arrow schema only "
            f"so the coarse rungs may null them; a null here means the producer regressed, not that the tier axis "
            f"permits it"
        )


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
    zoom: ZoomTier,
    day: date,
) -> datetime | None:
    """Return the OLDEST instant among one stream-day's part files AT ONE TIER, from a listing already made.

    `None` means the question cannot be answered: either no part file covers that day at that tier,
    or one of them carries no instant. This module's docstring says why the oldest is the honest
    answer. Another tier's parts are ignored rather than folded in: a coarse tier derived hours after
    the base one would otherwise drag the base day's freshness back to the derivation's clock.
    """
    oldest: datetime | None = None
    for entry in listed:
        parsed = try_parse_partition_path(entry.relative_path)
        if parsed is None or parsed.layer != layer or parsed.kind != kind or parsed.zoom != zoom or parsed.day != day:
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
