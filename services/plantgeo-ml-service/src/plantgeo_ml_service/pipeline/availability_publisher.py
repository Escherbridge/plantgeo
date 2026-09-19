"""Publish a forecast lane's availability: build the generation, write it, compare-and-set the pointer.

Layer L3, spec FR-4a. Writing a partition does not publish it: no forecast day is selectable until
its generation and pointer exist. Why the pointer is a compare-and-set with exactly one retry, and
what a lost race must do instead of looping, live in `AGENTS.md` in this directory.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal, Protocol

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs
import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.canonical import canonical_json
from plantgeo_ml_service.foundation.parquet_paths import (
    PartitionKind,
    availability_pointer_path,
    validate_partition_kind,
)
from plantgeo_ml_service.pipeline.object_store import JSON_CONTENT_TYPE, PARQUET_CONTENT_TYPE, sha256_of
from plantgeo_ml_service.warehouse.availability import (
    AVAILABILITY_INDEX_SCHEMA,
    AVAILABILITY_REQUIRED_RUNGS,
    AVAILABILITY_SCHEMA_VERSION,
    GENERATION_MAX_BYTES,
    MAX_AVAILABILITY_ROWS,
    POINTER_MAX_BYTES,
    AvailabilityConfig,
    AvailabilityDocumentError,
    AvailabilityPointer,
    AvailabilityRow,
    generation_key_for,
    generation_metadata,
    generation_receipt_sha256,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from plantgeo_ml_service.pipeline.object_store import ObjectStore

#: A lost race is re-read and retried EXACTLY ONCE, then refused with a receipt. A publisher that
#: looped would livelock against a peer publishing the same lane, and the refusal is recoverable:
#: the generation object is immutable and content-addressed, so the next turn re-points at it
#: without rebuilding a byte.
POINTER_RETRY_BUDGET: Final = 1

PARQUET_FORMAT_VERSION: Final = "2.6"

PublicationOutcome = Literal["advanced", "lost_race", "conditional_put_unsupported"]


class AvailabilityPublishError(RuntimeError):
    """Raised when a generation cannot be built or the pointer cannot be advanced."""


@dataclass(slots=True)
class _ConditionalPutSupport:
    """Process-wide latch: once a store is caught ignoring `IfMatch`, no lane publishes again."""

    proven_unsupported: bool = False


#: A store that answers 200 to an `IfMatch` it did not honour turns compare-and-set into
#: last-write-wins, which silently drops another publisher's generation. One such answer disqualifies
#: the store for the rest of the process: a per-call refusal would let the next lane overwrite.
_CONDITIONAL_PUT_SUPPORT: Final = _ConditionalPutSupport()


def conditional_put_is_supported() -> bool:
    """Return whether this process has caught a pointer store ignoring its conditional header."""
    return not _CONDITIONAL_PUT_SUPPORT.proven_unsupported


def reset_conditional_put_support() -> None:
    """Clear the latch. For tests, which prove both sides of it in one process."""
    _CONDITIONAL_PUT_SUPPORT.proven_unsupported = False


@dataclass(frozen=True, slots=True)
class StoredPointer:
    """One pointer as the bucket currently holds it: its bytes and its comparison token."""

    payload: bytes
    etag: str


class PointerStore(Protocol):
    """Conditional reads and writes of one mutable pointer object; the rest of the lane is immutable.

    Every `key` crossing this protocol is ABSOLUTE: `publish_generation` resolves it through
    `ObjectStore.absolute_key`, so the pointer and the generation it names can never land under two
    different roots. An implementation that added a prefix of its own would undo that.
    """

    def read_pointer(self, key: str) -> StoredPointer | None: ...

    def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool: ...


@dataclass(slots=True)
class InMemoryPointerStore:
    """A dict-backed conditional store, so the lost-race path is provable without a network."""

    pointers: dict[str, StoredPointer] = field(default_factory=dict)
    revision: int = 0

    def read_pointer(self, key: str) -> StoredPointer | None:
        """Return the pointer this key holds, or `None` when it has never been written."""
        stored = self.pointers.get(key)
        return None if stored is None else StoredPointer(payload=_bounded_pointer(stored.payload), etag=stored.etag)

    def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:
        """Write only when the stored comparison token still matches what the caller last read."""
        current = self.pointers.get(key)
        current_etag = None if current is None else current.etag
        if current_etag != expected_etag:
            return False
        self.revision += 1
        self.pointers[key] = StoredPointer(payload=payload, etag=f'"{self.revision:032x}"')
        return True


class _ConditionalS3Api(Protocol):
    """The two conditional S3 calls the pointer needs, typed at the boundary rather than as `Any`."""

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class BotoPointerStore:
    """The real conditional store: `IfNoneMatch: *` to create, `IfMatch: <etag>` to advance.

    It holds NO prefix of its own: keys arrive already resolved by the `ObjectStore` that owns the
    generation, so a scratch dry run cannot advance the published `_LATEST.json`.
    """

    bucket: str
    client: _ConditionalS3Api

    def read_pointer(self, key: str) -> StoredPointer | None:
        """Read the pointer and the ETag that must still hold when it is advanced, bounded by its ceiling."""
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as error:
            if _names_an_absent_object(error):
                return None
            raise
        # Read one byte past the ceiling rather than the whole body: a pointer is a fixed-shape
        # document, so anything larger is not a pointer and must not be downloaded to find out.
        body = response["Body"].read(POINTER_MAX_BYTES + 1)  # type: ignore[attr-defined]  # botocore streams are untyped
        return StoredPointer(payload=_bounded_pointer(bytes(body)), etag=str(response["ETag"]))

    def compare_and_set(self, key: str, payload: bytes, *, expected_etag: str | None) -> bool:
        """Advance the pointer only when its comparison token still matches; `False` on a lost race."""
        request: dict[str, object] = {
            "Body": payload,
            "Bucket": self.bucket,
            "ContentType": JSON_CONTENT_TYPE,
            "Key": key,
        }
        if expected_etag is None:
            request["IfNoneMatch"] = "*"
        else:
            request["IfMatch"] = expected_etag
        try:
            self.client.put_object(**request)
        except Exception as error:
            if _names_a_conditional_conflict(error):
                return False
            raise
        return True


@dataclass(frozen=True, slots=True)
class Generation:
    """A built, not-yet-written generation: its rows, its serialized bytes and its content digest."""

    config: AvailabilityConfig
    rows: tuple[AvailabilityRow, ...]
    payload: bytes
    sha256: str
    created_at: datetime
    receipt_sha256: str
    prior_generation_key: str | None
    prior_generation_sha256: str | None


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    """What one publication attempt did: the generation it wrote, and whether the pointer moved."""

    outcome: PublicationOutcome
    lane_root: str
    generation_key: str
    generation_sha256: str
    pointer_key: str
    rows: int
    attempts: int
    earliest_terminal_day: date
    latest_terminal_day: date


def build_generation(
    config: AvailabilityConfig,
    rows: Sequence[AvailabilityRow],
    *,
    created_at: datetime | None = None,
    prior: tuple[str | None, str | None] = (None, None),
) -> Generation:
    """Validate one lane's terminal rows and serialize them as an immutable generation document."""
    moment = created_at if created_at is not None else datetime.now(tz=UTC)
    ordered = _validated_rows(config, rows, created_at=moment)
    prior_generation_key, prior_generation_sha256 = prior
    receipt = generation_receipt_sha256(
        config,
        ordered,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=moment,
    )
    earliest = min(row.day for row in ordered)
    latest = max(row.day for row in ordered)
    metadata = generation_metadata(
        config,
        receipt_sha256=receipt,
        row_span=(len(ordered), earliest, latest),
        prior_generation=(prior_generation_key, prior_generation_sha256),
        created_at=moment,
    )
    schema = AVAILABILITY_INDEX_SCHEMA.with_metadata(metadata)
    table = pa.Table.from_pylist([row.to_arrow() for row in ordered], schema=schema)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version=PARQUET_FORMAT_VERSION, write_statistics=True)
    payload = sink.getvalue()
    if len(payload) > GENERATION_MAX_BYTES:
        raise AvailabilityPublishError("the built generation exceeds its byte ceiling")
    return Generation(
        config=config,
        rows=ordered,
        payload=payload,
        sha256=sha256_of(payload),
        created_at=moment,
        receipt_sha256=receipt,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
    )


def pointer_for(
    generation: Generation,
    *,
    layer: str,
    kind: PartitionKind,
    prior: tuple[str | None, str | None] | None = None,
) -> AvailabilityPointer:
    """Return the pointer document that would name `generation`, with every bound already checked.

    `prior` overrides the generation's own recorded predecessor, because a lost race re-reads the
    head and must bind to WHAT IS THERE NOW, not to what was there when the generation was built.
    """
    prior_key, prior_sha = generation.prior_generation_key, generation.prior_generation_sha256
    if prior is not None:
        prior_key, prior_sha = prior
    return AvailabilityPointer(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        identity=generation.config.identity,
        required_rungs=generation.config.identity.required_rungs,
        generation_key=generation_key_for(layer, kind, generation.sha256),
        generation_sha256=generation.sha256,
        generation_receipt_sha256=generation.receipt_sha256,
        generation_bytes=len(generation.payload),
        rows=len(generation.rows),
        earliest_terminal_day=min(row.day for row in generation.rows),
        latest_terminal_day=max(row.day for row in generation.rows),
        source_ceiling=generation.config.source_ceiling,
        prior_generation_key=prior_key,
        prior_generation_sha256=prior_sha,
        created_at=generation.created_at,
        bootstrap_receipt=generation.config.bootstrap_receipt,
    )


def publish_generation(
    store: ObjectStore,
    pointers: PointerStore,
    generation: Generation,
    *,
    layer: str,
    kind: PartitionKind,
) -> PublicationReceipt:
    """Write the immutable generation, then advance the pointer, retrying a lost race exactly once.

    The generation object goes up FIRST and is content-addressed, so a pointer that never advances
    leaves a complete, verifiable, unreferenced object rather than a dangling reference. Both keys
    are resolved through `store.absolute_key`, so a scratch prefix cannot reach the published lane.
    """
    validated_kind = validate_partition_kind(kind)
    if _CONDITIONAL_PUT_SUPPORT.proven_unsupported:
        raise AvailabilityPublishError(
            "a pointer store in this process answered a conditional put it did not honour; every "
            "further publish is refused because compare-and-set has degraded to last-write-wins"
        )
    generation_key = generation_key_for(layer, validated_kind, generation.sha256)
    store.put_immutable(generation_key, generation.payload, content_type=PARQUET_CONTENT_TYPE)
    pointer_key = store.absolute_key(availability_pointer_path(layer, validated_kind))
    current = pointers.read_pointer(pointer_key)
    pointer = pointer_for(generation, layer=layer, kind=validated_kind)
    for attempt in range(1, POINTER_RETRY_BUDGET + 2):
        prior = _prior_binding(current, generation)
        if prior is _REFUSE:
            return _receipt("lost_race", pointer=pointer, pointer_key=pointer_key, attempts=attempt)
        pointer = pointer_for(generation, layer=layer, kind=validated_kind, prior=prior)
        payload = pointer.to_json_bytes()
        expected_etag = None if current is None else current.etag
        if pointers.compare_and_set(pointer_key, payload, expected_etag=expected_etag):
            if not _pointer_landed_exactly(pointers, pointer_key, payload, expected_etag=expected_etag):
                _CONDITIONAL_PUT_SUPPORT.proven_unsupported = True
                return _receipt(
                    "conditional_put_unsupported", pointer=pointer, pointer_key=pointer_key, attempts=attempt
                )
            return _receipt("advanced", pointer=pointer, pointer_key=pointer_key, attempts=attempt)
        current = pointers.read_pointer(pointer_key)
    _record_pending_retry(store, generation, layer=layer, kind=validated_kind)
    return _receipt("lost_race", pointer=pointer, pointer_key=pointer_key, attempts=POINTER_RETRY_BUDGET + 1)


#: The sentinel `_prior_binding` returns when the head is not a predecessor this generation may
#: succeed: a different lane, our own digest already published, or a body that is not a pointer.
_REFUSE: Final[tuple[str | None, str | None]] = ("", "")


def _prior_binding(current: StoredPointer | None, generation: Generation) -> tuple[str | None, str | None]:
    """Return the prior-generation binding a pointer written NOW must carry, or `_REFUSE`.

    The head is re-read on every attempt, so the payload is rebuilt around it: re-putting the
    payload built against a stale head is the lost update this retry exists to prevent.
    """
    if current is None:
        return (None, None)
    head = _decoded_head(current.payload)
    key, sha256 = head.get("generation_key"), head.get("generation_sha256")
    if not isinstance(key, str) or not isinstance(sha256, str):
        return _REFUSE
    # A head for another lane is not a predecessor at all; a head that IS this generation would be
    # named its own predecessor, and republishing identical content is a no-op, not an advance.
    if head.get("lane_root") != generation.config.identity.lane_root or sha256 == generation.sha256:
        return _REFUSE
    return (key, sha256)


def _decoded_head(payload: bytes) -> dict[str, object]:
    """Decode the stored pointer, answering with an empty document for anything that is not one."""
    try:
        head = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return head if isinstance(head, dict) else {}


def _pointer_landed_exactly(
    pointers: PointerStore, pointer_key: str, payload: bytes, *, expected_etag: str | None
) -> bool:
    """Read the pointer back and prove the conditional put was honoured rather than merely answered.

    A store that ignores `IfMatch` answers 200 to a request it did not gate, so `True` from
    `compare_and_set` proves nothing on its own. Fresh bytes that are not the bytes we wrote, or a
    head that never moved, mean compare-and-set has degraded to last-write-wins.
    """
    landed = pointers.read_pointer(pointer_key)
    if landed is None or landed.payload != payload:
        return False
    return landed.etag != expected_etag


def _record_pending_retry(store: ObjectStore, generation: Generation, *, layer: str, kind: PartitionKind) -> None:
    """Leave the claim the sibling's retry sweep looks for, one per terminal lane-day the race lost."""
    for day in sorted({row.day for row in generation.rows}):
        claim = canonical_json(
            {
                "day": day.isoformat(),
                "generation_key": generation_key_for(layer, kind, generation.sha256),
                "generation_sha256": generation.sha256,
                "lane_root": generation.config.identity.lane_root,
                "recorded_at": _format_instant(generation.created_at),
            }
        ).encode("utf-8")
        store.write_availability_retry(claim, layer=layer, kind=kind, day=day)


def _format_instant(moment: datetime) -> str:
    """Render one UTC instant the way every other document in this lane renders it."""
    return f"{moment.astimezone(UTC).isoformat(timespec='microseconds')[:-6]}Z"


def _bounded_pointer(payload: bytes) -> bytes:
    """Return the pointer bytes, refusing a body larger than the pointer contract can hold."""
    if len(payload) > POINTER_MAX_BYTES:
        raise AvailabilityPublishError(
            f"a pointer holds at most {POINTER_MAX_BYTES} bytes; a larger body at the pointer key is "
            "not a pointer and is refused rather than parsed"
        )
    return payload


def _receipt(
    outcome: PublicationOutcome,
    *,
    pointer: AvailabilityPointer,
    pointer_key: str,
    attempts: int,
) -> PublicationReceipt:
    """Build the receipt both the advanced and the refused path return; neither path returns None."""
    return PublicationReceipt(
        outcome=outcome,
        lane_root=pointer.identity.lane_root,
        generation_key=pointer.generation_key,
        generation_sha256=pointer.generation_sha256,
        pointer_key=pointer_key,
        rows=pointer.rows,
        attempts=attempts,
        earliest_terminal_day=pointer.earliest_terminal_day,
        latest_terminal_day=pointer.latest_terminal_day,
    )


def _validated_rows(
    config: AvailabilityConfig, rows: Sequence[AvailabilityRow], *, created_at: datetime
) -> tuple[AvailabilityRow, ...]:
    """Order the rows by (day, rung) and refuse any ladder the availability contract would not admit."""
    if not rows:
        raise AvailabilityPublishError("a generation with no terminal rows publishes nothing")
    if len(rows) > MAX_AVAILABILITY_ROWS:
        raise AvailabilityPublishError(f"a generation holds at most {MAX_AVAILABILITY_ROWS} rows, got {len(rows)}")
    ordered = tuple(sorted(rows, key=lambda row: row.grain))
    if len({row.grain for row in ordered}) != len(ordered):
        raise AvailabilityPublishError("a generation cannot carry two rows for one (day, rung)")
    expected = config.identity.required_rungs
    by_day: dict[date, list[AvailabilityRow]] = {}
    for row in ordered:
        if row.lane != config.identity.lane or row.product != config.identity.product:
            raise AvailabilityPublishError(f"row for {row.day} names a lane other than {config.identity.lane_root!r}")
        if row.nature != config.identity.nature:
            raise AvailabilityPublishError(
                f"row for {row.day} declares nature {row.nature!r}, not the lane's {config.identity.nature!r}; "
                "the nature is how a reader knows what a day MEANS, so one row may not redefine it"
            )
        if row.source_ceiling != config.source_ceiling:
            raise AvailabilityPublishError("every row must carry the generation's own source ceiling")
        # A row published AFTER the generation that indexes it would be evidence from the future:
        # the generation would claim a terminal state it could not have observed when it was built.
        if row.published_at > created_at:
            raise AvailabilityPublishError(
                f"row for {row.day} rung {row.rung} was published after the generation was created; "
                "an availability row's published_at cannot exceed the generation's created_at"
            )
        by_day.setdefault(row.day, []).append(row)
    for day, day_rows in by_day.items():
        if tuple(row.rung for row in day_rows) != expected:
            raise AvailabilityPublishError(
                f"day {day} names rungs {tuple(row.rung for row in day_rows)}, not the authoritative "
                f"ladder {expected}; a partially indexed day would be selectable at a resolution nobody wrote"
            )
        if len({row.terminal_state for row in day_rows}) != 1:
            raise AvailabilityPublishError(f"day {day} mixes terminal states across its ladder")
    return ordered


def required_rungs_are_the_full_ladder(config: AvailabilityConfig) -> bool:
    """Return whether this config's rung contract is the whole published ladder, as FR-4a requires."""
    return config.identity.required_rungs == AVAILABILITY_REQUIRED_RUNGS


def _names_an_absent_object(error: Exception) -> bool:
    """Return whether a botocore error says the pointer is absent rather than that the call failed."""
    return _error_code(error) in {"404", "NoSuchKey", "NotFound"}


def _names_a_conditional_conflict(error: Exception) -> bool:
    """Return whether a botocore error is a lost compare-and-set rather than a transport failure."""
    return _error_code(error) in {"412", "PreconditionFailed", "409", "ConditionalRequestConflict"}


def _error_code(error: Exception) -> str:
    """Read the error code out of a botocore exception, or `''` for anything that is not one."""
    response = getattr(error, "response", None)
    if not isinstance(response, dict):
        return ""
    body = response.get("Error")
    if isinstance(body, dict) and isinstance(body.get("Code"), str):
        return str(body["Code"])
    metadata = response.get("ResponseMetadata")
    if isinstance(metadata, dict) and metadata.get("HTTPStatusCode") is not None:
        return str(metadata["HTTPStatusCode"])
    return ""


# Re-exported so a caller that only publishes never has to import the document module too.
__all__ = [
    "POINTER_RETRY_BUDGET",
    "AvailabilityDocumentError",
    "AvailabilityPublishError",
    "BotoPointerStore",
    "Generation",
    "InMemoryPointerStore",
    "PointerStore",
    "PublicationReceipt",
    "StoredPointer",
    "build_generation",
    "conditional_put_is_supported",
    "pointer_for",
    "publish_generation",
    "required_rungs_are_the_full_ladder",
    "reset_conditional_put_support",
]
