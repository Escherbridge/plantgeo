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

CROSS-TIER AGREEMENT OF ONE DAY IS NOT THIS MODULE'S INVARIANT. `write_absence` still refuses to mark
a day that already holds data, but only at the tier being marked: the four tiers of one day live
under four disjoint prefixes, so policing them together would cost four listings per marker and still
race. "Every tier of a published day is present" is the DERIVATION step's obligation, because
derivation is the only thing that knows a coarse tier was computed from a base one.
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
    try_parse_absence_marker_path,
    try_parse_partition_path,
    year_prefix,
    zoom_prefix,
)
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import date

    from agri_data_service.foundation.parquet.absence import GovernedAbsence
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

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

    def delete(self, key: str) -> None: ...

    def list_objects(self, prefix: str) -> Iterator[ListedObject]: ...

    def size_of(self, key: str) -> int | None: ...


class _S3Api(Protocol):
    """The four boto3 S3 calls this module makes, typed at the boundary rather than as `Any`."""

    def put_object(self, **kwargs: object) -> object: ...

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
        if self.absence_exists(layer, kind, zoom, day):
            raise GovernedAbsenceConflictError(
                f"{layer!r} {kind} z{zoom} {day} carries a governed-absence marker; "
                "retracting it is a manual admin action, not something a write may do implicitly"
            )
        payload = _serialize_parquet(conformed, stream.compression)
        relative_path = partition_path(layer, kind, zoom, day, part_index)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=PARQUET_CONTENT_TYPE)
        return ParquetWriteReceipt(
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
        payload = absence.to_json_bytes()
        relative_path = absence_marker_path(layer, kind, zoom, day)
        key = self.key_for(relative_path)
        self._backend.put(key, payload, content_type=ABSENCE_CONTENT_TYPE)
        return AbsenceWriteReceipt(
            key=key,
            relative_path=relative_path,
            kind=kind,
            zoom=zoom,
            day=day,
            byte_count=len(payload),
            sha256=sha256_digest(payload),
        )

    def list_partition_objects(
        self,
        layer: str,
        kind: PartitionKind,
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[ListedPartition, ...]:
        """Return ONE TIER's part files and absence markers WITH their export instants, narrowed by year and month.

        `zoom` is required, and there is no mode that returns the whole ladder. See the module
        docstring: a tier-less listing blends four resolutions of the same day into one population
        and nothing about the result looks wrong.
        """
        scope = self._listing_scope(layer, kind, zoom, year, month)
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
        zoom: ZoomTier,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Return every part file and absence marker of ONE TIER as a relative path, narrowed by year and month."""
        return tuple(
            listed.relative_path for listed in self.list_partition_objects(layer, kind, zoom, year=year, month=month)
        )

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
            # absence marker parses as `None` here, and another lane, kind, TIER or day cannot match.
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
