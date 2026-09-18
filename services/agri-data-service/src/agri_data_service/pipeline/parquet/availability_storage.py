"""Conditional object storage for availability, and the snapshot bookkeeping publication rides on.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from agri_data_service.config import ObjectStoreCredentials, Settings, settings
from agri_data_service.foundation.canonical import sha256_digest
from agri_data_service.pipeline.parquet.availability_primitives import (
    _ABSENT_CODES,
    _CAS_CONFLICT_CODES,
    _CREATE_RETRY_CODES,
    _PRECONDITION_CODES,
    EVIDENCE_OBJECT_MAX_BYTES,
    MAX_IMMUTABLE_CREATE_ATTEMPTS,
    AvailabilityChecksumError,
    AvailabilityConflictError,
    AvailabilityMalformedError,
    AvailabilityUnavailableError,
    _normalize_prefix,
    _require_object_key,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend

if TYPE_CHECKING:
    from collections.abc import Callable

    from agri_data_service.pipeline.parquet.availability_documents import EvidenceReceipt


@dataclass(frozen=True, slots=True)
class StoredAvailabilityObject:
    """One object read with the ETag needed for pointer compare-and-swap."""

    payload: bytes
    etag: str
    version_id: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """One immutable observed object identity retained through pointer publication."""

    key: str
    expected_sha256: str
    observed_sha256: str
    byte_count: int
    etag: str
    version_id: str | None
    max_bytes: int


class AvailabilityStorage(Protocol):
    """The conditional object operations required by availability publication."""

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None: ...

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool: ...


class _ConditionalS3Client(Protocol):
    """The conditional S3 calls availability needs."""

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class BotoAvailabilityStorage:
    """Availability storage using real S3 conditional requests."""

    bucket: str
    client: _ConditionalS3Client
    prefix: str = ""

    @classmethod
    def from_credentials(
        cls,
        credentials: ObjectStoreCredentials,
        *,
        prefix: str = "",
    ) -> BotoAvailabilityStorage:
        """Build the conditional adapter without performing network I/O."""
        backend = BotoObjectStoreBackend.from_credentials(credentials)
        return cls(bucket=backend.bucket, client=backend.client, prefix=_normalize_prefix(prefix))

    @classmethod
    def from_settings(cls, source: Settings | None = None) -> BotoAvailabilityStorage:
        """Build from validated application settings."""
        resolved = settings if source is None else source
        return cls.from_credentials(
            resolved.require_object_store(),
            prefix=resolved.object_store_prefix,
        )

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Read bytes and their strong comparison token."""
        _require_max_bytes(max_bytes)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except ClientError as exc:
            if _client_error_code(exc) in _ABSENT_CODES:
                return None
            raise
        body = response.get("Body")
        content_length = response.get("ContentLength")
        etag = response.get("ETag")
        version_id = response.get("VersionId")
        if (
            body is None
            or isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length < 0
            or not isinstance(etag, str)
            or not etag
            or etag != etag.strip()
            or (
                version_id is not None
                and (not isinstance(version_id, str) or not version_id or version_id != version_id.strip())
            )
        ):
            if body is not None:
                _close_body(body)
            raise AvailabilityMalformedError(f"object {key!r} did not return a valid length, ETag, and VersionId")
        if content_length > max_bytes:
            _close_body(body)
            raise AvailabilityUnavailableError(
                "availability_oversized",
                f"object {key!r} declares {content_length} bytes above its {max_bytes}-byte ceiling",
            )
        try:
            payload_value = body.read(max_bytes + 1)  # type: ignore[attr-defined]
        finally:
            _close_body(body)
        if not isinstance(payload_value, (bytes, bytearray, memoryview)):
            raise AvailabilityMalformedError(f"object {key!r} returned a non-bytes body")
        payload = bytes(payload_value)
        if len(payload) > max_bytes or len(payload) != content_length:
            raise AvailabilityMalformedError(
                f"object {key!r} body length {len(payload)} disagrees with declared {content_length}"
            )
        return StoredAvailabilityObject(
            payload=payload,
            etag=etag,
            version_id=version_id,
        )

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Create one immutable object, accepting only an exact idempotent replay."""
        for attempt in range(1, MAX_IMMUTABLE_CREATE_ATTEMPTS + 1):
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=self._key(key),
                    Body=payload,
                    ContentType=content_type,
                    IfNoneMatch="*",
                )
                return
            except ClientError as exc:
                code = _client_error_code(exc)
                if code in _PRECONDITION_CODES:
                    self._adopt_exact_immutable(key, payload)
                    return
                if code not in _CREATE_RETRY_CODES:
                    raise
            existing = self.read(key, max_bytes=len(payload))
            if existing is not None and existing.payload != payload:
                raise AvailabilityConflictError(
                    f"immutable availability object {key!r} became visible with different bytes"
                )
            if attempt == MAX_IMMUTABLE_CREATE_ATTEMPTS:
                raise AvailabilityConflictError(
                    f"immutable availability object {key!r} remained contended after bounded retries"
                )

    def compare_and_swap(
        self,
        key: str,
        payload: bytes,
        *,
        expected_etag: str | None,
        content_type: str,
    ) -> bool:
        """Advance the pointer only when its comparison token still matches."""
        request: dict[str, object] = {
            "Body": payload,
            "Bucket": self.bucket,
            "ContentType": content_type,
            "Key": self._key(key),
        }
        if expected_etag is None:
            request["IfNoneMatch"] = "*"
        else:
            request["IfMatch"] = expected_etag
        try:
            self.client.put_object(**request)
        except ClientError as exc:
            if _client_error_code(exc) in _CAS_CONFLICT_CODES:
                return False
            raise
        return True

    def _adopt_exact_immutable(self, key: str, payload: bytes) -> None:
        existing = self.read(key, max_bytes=len(payload))
        if existing is None or existing.payload != payload:
            raise AvailabilityConflictError(f"immutable availability object {key!r} already holds different bytes")

    def _key(self, key: str) -> str:
        _require_object_key(key, "availability key")
        return f"{self.prefix}{key}"


def _read_receipt_snapshot(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    max_bytes: int,
) -> tuple[StoredAvailabilityObject, EvidenceSnapshot]:
    stored = store.read(receipt.key, max_bytes=max_bytes)
    if stored is None:
        raise AvailabilityUnavailableError(
            "availability_evidence_missing",
            f"receipt object {receipt.key!r} is missing",
        )
    actual = sha256_digest(stored.payload)
    if actual != receipt.sha256:
        raise AvailabilityChecksumError(
            f"receipt object {receipt.key!r} checksum mismatch: expected {receipt.sha256}, got {actual}"
        )
    return stored, EvidenceSnapshot(
        key=receipt.key,
        expected_sha256=receipt.sha256,
        observed_sha256=actual,
        byte_count=len(stored.payload),
        etag=stored.etag,
        version_id=stored.version_id,
        max_bytes=max_bytes,
    )


_VERIFICATION_WORKERS: Final = 8


def _verify_bounded[VerificationInput, VerificationOutput](
    operation: Callable[[VerificationInput], VerificationOutput],
    values: Sequence[VerificationInput],
) -> tuple[VerificationOutput, ...]:
    """Join bounded batches in input order; see AGENTS.md verification concurrency."""
    if len(values) <= 1:
        return tuple(operation(value) for value in values)
    results: list[VerificationOutput] = []
    with ThreadPoolExecutor(max_workers=_VERIFICATION_WORKERS) as executor:
        for start in range(0, len(values), _VERIFICATION_WORKERS):
            futures = [executor.submit(operation, value) for value in values[start : start + _VERIFICATION_WORKERS]]
            results.extend(future.result() for future in futures)
    return tuple(results)


def _verify_raw_receipts(
    store: AvailabilityStorage,
    receipts: Sequence[EvidenceReceipt],
    *,
    parallel: bool = False,
) -> tuple[EvidenceSnapshot, ...]:
    def verify(receipt: EvidenceReceipt) -> EvidenceSnapshot:
        return _read_receipt_snapshot(store, receipt, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)[1]

    unique = _dedupe_receipts(receipts)
    return _verify_bounded(verify, unique) if parallel else tuple(verify(receipt) for receipt in unique)


def _dedupe_receipts(receipts: Sequence[EvidenceReceipt]) -> tuple[EvidenceReceipt, ...]:
    by_key: dict[str, EvidenceReceipt] = {}
    for receipt in receipts:
        held = by_key.setdefault(receipt.key, receipt)
        if held.sha256 != receipt.sha256:
            raise AvailabilityChecksumError(f"receipt key {receipt.key!r} is bound to two different digests")
    return tuple(by_key[key] for key in sorted(by_key))


def _snapshot_identity(snapshot: EvidenceSnapshot) -> tuple[str, str, str, int, str, str | None]:
    """What makes two observations of one object THE SAME OBJECT, which `max_bytes` is not.

    MEASURED 2026-09-07, sensors: comparing whole snapshots refused a bootstrap over an object whose
    bytes were byte-identical in both readings. `layer=sensors/kind=observed/zoom=00/.../absent.json`
    is cited twice by design -- once in a SOURCE document's `object_receipts`, read through
    `_verify_raw_receipts` under `EVIDENCE_OBJECT_MAX_BYTES` (256 MiB), and once as a TERMINAL row's
    `absence_receipt`, read through `_verify_absence_object` under `TYPED_RECEIPT_MAX_BYTES` (1 MiB).
    Same key, same `expected_sha256`, same `observed_sha256`, same etag -- and unequal dataclasses,
    because the read ceiling rides along as a field.

    `max_bytes` is an argument to the read, not a property of the object, so it cannot participate in
    identity. Every claim the caps enforce has already been enforced where the cap was applied: the
    1 MiB ceiling refuses an oversized typed receipt inside `_read_receipt_snapshot`, long before
    this function sees the result. Excluding it here weakens no check; including it invented a
    conflict that no corruption could have caused.

    Nothing about the REAL conflict is relaxed: two readings whose bytes actually differ still differ
    in `observed_sha256` and are still refused below.
    """
    return (
        snapshot.key,
        snapshot.expected_sha256,
        snapshot.observed_sha256,
        snapshot.byte_count,
        snapshot.etag,
        snapshot.version_id,
    )


def _dedupe_snapshots(snapshots: Sequence[EvidenceSnapshot]) -> tuple[EvidenceSnapshot, ...]:
    by_key: dict[str, EvidenceSnapshot] = {}
    for snapshot in snapshots:
        held = by_key.setdefault(snapshot.key, snapshot)
        if _snapshot_identity(held) != _snapshot_identity(snapshot):
            raise AvailabilityConflictError(f"object {snapshot.key!r} was observed with two identities")
        if snapshot.max_bytes > held.max_bytes:
            # Keep the LARGER ceiling. `_revalidate_snapshots` re-reads at `snapshot.max_bytes` right
            # before the pointer swap, and a re-read capped below the object's size fails the
            # publication outright -- so of two equally valid ceilings, only the larger is safe here.
            by_key[snapshot.key] = snapshot
    return tuple(by_key[key] for key in sorted(by_key))


def _revalidate_snapshots(store: AvailabilityStorage, snapshots: Sequence[EvidenceSnapshot]) -> None:
    def verify(snapshot: EvidenceSnapshot) -> None:
        stored = store.read(snapshot.key, max_bytes=snapshot.max_bytes)
        if stored is None:
            raise AvailabilityUnavailableError(
                "availability_evidence_changed",
                f"snapshotted object {snapshot.key!r} disappeared before pointer publication",
            )
        observed = sha256_digest(stored.payload)
        identity = (observed, len(stored.payload), stored.etag, stored.version_id)
        expected = (
            snapshot.observed_sha256,
            snapshot.byte_count,
            snapshot.etag,
            snapshot.version_id,
        )
        if identity != expected:
            raise AvailabilityConflictError(f"snapshotted object {snapshot.key!r} changed before pointer publication")

    _verify_bounded(verify, snapshots)


def _client_error_code(exc: ClientError) -> str:
    error = exc.response.get("Error")
    if not isinstance(error, Mapping):
        return ""
    code = error.get("Code")
    return str(code) if code is not None else ""


def _require_max_bytes(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("max_bytes must be a non-negative integer")


def _close_body(body: object) -> None:
    close = getattr(body, "close", None)
    if callable(close):
        close()
