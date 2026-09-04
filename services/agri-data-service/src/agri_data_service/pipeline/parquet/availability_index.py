"""Immutable availability generations, strict reads, bootstrap, and conditional publication."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from agri_data_service.config import ObjectStoreCredentials, Settings, settings
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.absence import (
    ABSENCE_SCHEMA_VERSION,
    GovernedAbsence,
    GovernedAbsenceError,
)
from agri_data_service.foundation.parquet.completion import (
    COMPLETION_SCHEMA_VERSIONS,
    DERIVED_EMPTY_FIELD,
    PartitionCompletion,
    PartitionCompletionError,
)
from agri_data_service.foundation.parquet.completion import (
    PARTS_FIELD as COMPLETION_PARTS_FIELD,
)
from agri_data_service.foundation.parquet.paths import (
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.availability_index import (
    AVAILABILITY_INDEX_SCHEMA,
    AVAILABILITY_METADATA_KEYS,
    AVAILABILITY_REQUIRED_RUNGS,
    AVAILABILITY_SCHEMA_VERSION,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    AvailabilityPublicationBarrier = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]

AvailabilityNature = Literal["daily_series", "release_series"]
TerminalState = Literal["published", "governed_absence"]

#: HOW WELL ONE ROW'S PARTS ARE PROVEN, and the only two answers there are.
#:
#: `digested` is the ordinary class: the row names every part it publishes and each name carries a
#: SHA-256 that was computed from that object's bytes, so `--apply` re-downloads and re-hashes them.
#:
#: `manifest_trusted` is the bootstrap-only class introduced by owner decision D3
#: (`environmental_postgres_retirement_20260904`): the row names NO parts and its proof is the
#: completion marker, which is itself fetched and digested. It exists because the contract as written
#: requires hashing every part of every lane-day -- for `fire-detections`, every day since
#: 2000-11-01 at every rung -- and that cost would push the time slider's startup fix behind the
#: whole cutover. A trusted row states a WEAKER claim; it never states a false one, because a digest
#: that was not computed from the object it describes is never emitted.
AvailabilityProvenance = Literal["digested", "manifest_trusted"]

DIGESTED_PROVENANCE: Final[AvailabilityProvenance] = "digested"
MANIFEST_TRUSTED_PROVENANCE: Final[AvailabilityProvenance] = "manifest_trusted"

#: The optional provenance key, SERIALIZED ONLY WHEN MANIFEST-TRUSTED -- the same rule
#: `foundation/parquet/completion.py::DERIVED_EMPTY_FIELD` follows, and for the same reason: a
#: `provenance: "digested"` key on every ordinary terminal document would change the content address
#: of evidence already written and re-verified byte-for-byte.
PROVENANCE_FIELD: Final = "provenance"

JSON_CONTENT_TYPE: Final = "application/json"
PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
BOOTSTRAP_INPUT_SCHEMA_VERSION: Final = "availability-bootstrap-input-v1"
PUBLICATION_INPUT_SCHEMA_VERSION: Final = "availability-publication-input-v1"
MAX_INPUT_BYTES: Final = 64 * 1024 * 1024
MAX_AVAILABILITY_ROWS: Final = 250_000
MAX_PUBLICATION_ATTEMPTS: Final = 4
MAX_IMMUTABLE_CREATE_ATTEMPTS: Final = 3
_SHA256_LENGTH: Final = 64
_MAX_RUNG: Final = 30
_LANE_ROOT_SEGMENT_COUNT: Final = 2
POINTER_MAX_BYTES: Final = 64 * 1024
TYPED_RECEIPT_MAX_BYTES: Final = 1024 * 1024
BOOTSTRAP_RECEIPT_MAX_BYTES: Final = 64 * 1024 * 1024
GENERATION_MAX_BYTES: Final = 256 * 1024 * 1024
EVIDENCE_OBJECT_MAX_BYTES: Final = 256 * 1024 * 1024
BOOTSTRAP_INVENTORY_SCHEMA_VERSION: Final = "availability-bootstrap-inventory-v1"
SOURCE_EVIDENCE_SCHEMA_VERSION: Final = "availability-source-evidence-v1"
TERMINAL_EVIDENCE_SCHEMA_VERSION: Final = "availability-terminal-evidence-v1"
SYSTEM_BOOTSTRAP_SCHEMA_VERSION: Final = "availability-system-bootstrap-v1"
BOOTSTRAP_MARKER_SCHEMA_VERSION: Final = "availability-bootstrap-marker-v1"

#: The bootstrap RECEIPT is content-addressed, so nothing can find it without already knowing its
#: digest. This marker sits at a deterministic key beside it and names it, which is what lets a
#: reader ask "was this lane ever bootstrapped?" with ONE GET instead of a prefix walk. See
#: `parquet_ops/availability_coverage.py`: a bootstrapped lane whose pointer is gone is withheld,
#: never quietly re-censused.
BOOTSTRAP_MARKER_MAX_BYTES: Final = 64 * 1024
_BOOTSTRAP_MARKER_FIELDS: Final = {
    "bootstrap_receipt_key",
    "bootstrap_receipt_sha256",
    "lane_root",
    "schema_version",
}
_IDENTITY_FIELDS: Final = {
    "lane_root",
    "lane",
    "product",
    "nature",
    "required_rungs",
    "verified_source_inventory_root",
}
_BOOTSTRAP_INVENTORY_FIELDS: Final = _IDENTITY_FIELDS | {
    "object_receipts",
    "schema_version",
    "source_ceiling",
}
_SOURCE_EVIDENCE_FIELDS: Final = _IDENTITY_FIELDS | {
    "day",
    "object_receipts",
    "schema_version",
    "source_ceiling",
}
_TERMINAL_EVIDENCE_FIELDS: Final = _IDENTITY_FIELDS | {
    "absence_reason",
    "absence_receipt",
    "completion_receipt",
    "data_receipts",
    "day",
    "row_count",
    "rung",
    "published_at",
    "schema_version",
    "source_ceiling",
    "source_receipt",
    "terminal_state",
}
_SYSTEM_BOOTSTRAP_FIELDS: Final = _IDENTITY_FIELDS | {
    "bootstrap_input_sha256",
    "created_at",
    "input_receipts",
    "outcome_sha256",
    "provenance",
    "row_count",
    "schema_version",
    "source_ceiling",
}
_ABSENT_CODES: Final = frozenset({"404", "NoSuchKey", "NotFound"})
_PRECONDITION_CODES: Final = frozenset({"412", "PreconditionFailed"})
_CREATE_RETRY_CODES: Final = frozenset({"409", "ConditionalRequestConflict"})
_CAS_CONFLICT_CODES: Final = _PRECONDITION_CODES | _CREATE_RETRY_CODES


class AvailabilityError(RuntimeError):
    """Base refusal for availability evidence or publication."""


class AvailabilityUnavailableError(AvailabilityError):
    """A required pointer or generation is missing or stale."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AvailabilityMalformedError(AvailabilityError):
    """Availability evidence is not the frozen schema."""


class AvailabilityChecksumError(AvailabilityError):
    """A checksum-bound object does not match its receipt."""


class AvailabilityConflictError(AvailabilityError):
    """An immutable key or conditional pointer update conflicts."""


class AlreadyBootstrappedError(AvailabilityConflictError):
    """A different immutable bootstrap already owns the lane."""


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    """One object key and its required byte digest."""

    key: str
    sha256: str

    def __post_init__(self) -> None:
        _require_object_key(self.key, "receipt key")
        _require_sha256(self.sha256, "receipt sha256")

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {"key": self.key, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class AvailabilityIdentity:
    """Stable lane identity and authoritative rung contract."""

    lane_root: str
    lane: str
    product: str
    nature: AvailabilityNature
    required_rungs: tuple[int, ...]
    verified_source_inventory_root: str

    def __post_init__(self) -> None:
        _require_lane_root(self.lane_root)
        _require_name(self.lane, "lane")
        _require_name(self.product, "product")
        _require_nature(self.nature)
        _require_rungs(self.required_rungs)
        _require_sha256(self.verified_source_inventory_root, "verified_source_inventory_root")


@dataclass(frozen=True, slots=True)
class AvailabilityConfig:
    """Generation identity plus its immutable bootstrap binding and current ceiling."""

    identity: AvailabilityIdentity
    source_ceiling: date
    bootstrap_receipt: EvidenceReceipt


@dataclass(frozen=True, slots=True)
class TypedEvidenceArtifact:
    """Canonical typed evidence bytes and their content-addressed wrapper receipt."""

    receipt: EvidenceReceipt
    payload: bytes


@dataclass(frozen=True, slots=True)
class BootstrapInventoryEvidence:
    """Exact bootstrap manifest/checkpoint inventory for one lane."""

    identity: AvailabilityIdentity
    source_ceiling: date
    object_receipts: tuple[EvidenceReceipt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.object_receipts, tuple):
            raise ValueError("bootstrap object receipts must be an immutable tuple")
        _require_sorted_nonempty_receipts(self.object_receipts, "bootstrap object receipts")

    def to_wire(self) -> dict[str, object]:
        return {
            **_identity_wire(self.identity),
            "object_receipts": [receipt.to_wire() for receipt in self.object_receipts],
            "schema_version": BOOTSTRAP_INVENTORY_SCHEMA_VERSION,
            "source_ceiling": self.source_ceiling.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    """Exact source objects establishing one lane-day ceiling."""

    identity: AvailabilityIdentity
    day: date
    source_ceiling: date
    object_receipts: tuple[EvidenceReceipt, ...]

    def __post_init__(self) -> None:
        if self.day > self.source_ceiling:
            raise ValueError("source evidence day cannot exceed its source ceiling")
        if not isinstance(self.object_receipts, tuple):
            raise ValueError("source object receipts must be an immutable tuple")
        _require_sorted_nonempty_receipts(self.object_receipts, "source object receipts")

    def to_wire(self) -> dict[str, object]:
        return {
            **_identity_wire(self.identity),
            "day": self.day.isoformat(),
            "object_receipts": [receipt.to_wire() for receipt in self.object_receipts],
            "schema_version": SOURCE_EVIDENCE_SCHEMA_VERSION,
            "source_ceiling": self.source_ceiling.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TerminalEvidence:
    """Exact physical terminal evidence for one lane day and rung."""

    identity: AvailabilityIdentity
    day: date
    rung: int
    terminal_state: TerminalState
    row_count: int
    source_ceiling: date
    published_at: datetime
    source_receipt: EvidenceReceipt
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt | None
    absence_receipt: EvidenceReceipt | None
    absence_reason: str | None
    #: DECLARED here, derived on the row. Defaulted so every existing caller keeps building the
    #: ordinary class, and so a forward-path bug that dropped `data_receipts` is still refused
    #: instead of silently becoming a trusted row.
    provenance: AvailabilityProvenance = DIGESTED_PROVENANCE

    def __post_init__(self) -> None:
        _require_rung(self.rung)
        if self.terminal_state not in ("published", "governed_absence"):
            raise ValueError("terminal_state must be published or governed_absence")
        if isinstance(self.row_count, bool) or not isinstance(self.row_count, int):
            raise ValueError("row_count must be an integer")
        if self.rung not in self.identity.required_rungs:
            raise ValueError("terminal evidence rung is outside required_rungs")
        if self.day > self.source_ceiling:
            raise ValueError("terminal evidence day cannot exceed its source ceiling")
        _require_utc(self.published_at, "published_at")
        _validate_terminal_evidence_payload(self)

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            **_identity_wire(self.identity),
            "absence_reason": self.absence_reason,
            "absence_receipt": None if self.absence_receipt is None else self.absence_receipt.to_wire(),
            "completion_receipt": (None if self.completion_receipt is None else self.completion_receipt.to_wire()),
            "data_receipts": [receipt.to_wire() for receipt in self.data_receipts],
            "day": self.day.isoformat(),
            "row_count": self.row_count,
            "rung": self.rung,
            "published_at": _format_datetime(self.published_at),
            "schema_version": TERMINAL_EVIDENCE_SCHEMA_VERSION,
            "source_ceiling": self.source_ceiling.isoformat(),
            "source_receipt": self.source_receipt.to_wire(),
            "terminal_state": self.terminal_state,
        }
        if self.provenance == MANIFEST_TRUSTED_PROVENANCE:
            wire[PROVENANCE_FIELD] = self.provenance
        return wire


def build_bootstrap_inventory_evidence(value: BootstrapInventoryEvidence) -> TypedEvidenceArtifact:
    """Build one canonical content-addressed bootstrap-input wrapper."""
    return _build_typed_evidence(value.identity.lane_root, "bootstrap-input", value.to_wire())


def build_source_evidence(value: SourceEvidence) -> TypedEvidenceArtifact:
    """Build one canonical content-addressed per-day source wrapper."""
    return _build_typed_evidence(value.identity.lane_root, "source", value.to_wire())


def build_terminal_evidence(value: TerminalEvidence) -> TypedEvidenceArtifact:
    """Build one canonical content-addressed per-rung terminal wrapper."""
    return _build_typed_evidence(value.identity.lane_root, "terminal", value.to_wire())


def _identity_wire(identity: AvailabilityIdentity) -> dict[str, object]:
    return {
        "lane": identity.lane,
        "lane_root": identity.lane_root,
        "nature": identity.nature,
        "product": identity.product,
        "required_rungs": list(identity.required_rungs),
        "verified_source_inventory_root": identity.verified_source_inventory_root,
    }


def _build_typed_evidence(
    lane_root: str,
    purpose: Literal["bootstrap-input", "source", "terminal"],
    value: Mapping[str, object],
) -> TypedEvidenceArtifact:
    payload = canonical_json(value).encode("utf-8")
    ceiling = BOOTSTRAP_RECEIPT_MAX_BYTES if purpose == "bootstrap-input" else TYPED_RECEIPT_MAX_BYTES
    if len(payload) > ceiling:
        raise AvailabilityMalformedError(f"{purpose} evidence exceeds its byte ceiling")
    digest = sha256_digest(payload)
    return TypedEvidenceArtifact(
        receipt=EvidenceReceipt(
            key=f"{lane_root}/availability/evidence/{purpose}={digest}.json",
            sha256=digest,
        ),
        payload=payload,
    )


@dataclass(frozen=True, slots=True)
class AvailabilityRow:
    """One terminal `(day, rung)` outcome."""

    lane: str
    product: str
    nature: AvailabilityNature
    day: date
    rung: int
    terminal_state: TerminalState
    row_count: int
    source_receipt: EvidenceReceipt
    terminal_receipt: EvidenceReceipt
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt | None
    absence_reason: str | None
    source_ceiling: date
    published_at: datetime

    def __post_init__(self) -> None:
        _require_name(self.lane, "lane")
        _require_name(self.product, "product")
        _require_nature(self.nature)
        _require_rung(self.rung)
        if self.terminal_state not in ("published", "governed_absence"):
            raise ValueError("terminal_state must be published or governed_absence")
        if self.day > self.source_ceiling:
            raise ValueError("availability day cannot exceed its source ceiling")
        _require_utc(self.published_at, "published_at")
        _validate_data_receipt_collection(self.data_receipts)
        _validate_terminal_payload(self)

    @property
    def grain(self) -> tuple[date, int]:
        """Return the unique generation grain."""
        return self.day, self.rung

    @property
    def provenance(self) -> AvailabilityProvenance:
        """Return how well this row's parts are proven, DERIVED from the row's own shape."""
        return availability_row_provenance(
            terminal_state=self.terminal_state,
            row_count=self.row_count,
            data_receipts=self.data_receipts,
        )

    def evidence_receipts(self) -> tuple[EvidenceReceipt, ...]:
        """Return every object the row binds."""
        completion = () if self.completion_receipt is None else (self.completion_receipt,)
        return (self.source_receipt, self.terminal_receipt, *self.data_receipts, *completion)

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON and Arrow-compatible projection."""
        return {
            "absence_reason": self.absence_reason,
            "completion_receipt_key": None if self.completion_receipt is None else self.completion_receipt.key,
            "completion_receipt_sha256": (None if self.completion_receipt is None else self.completion_receipt.sha256),
            "data_receipts": [receipt.to_wire() for receipt in self.data_receipts],
            "day": self.day.isoformat(),
            "lane": self.lane,
            "nature": self.nature,
            "product": self.product,
            "published_at": _format_datetime(self.published_at),
            "row_count": self.row_count,
            "rung": self.rung,
            "source_ceiling": self.source_ceiling.isoformat(),
            "source_receipt_key": self.source_receipt.key,
            "source_receipt_sha256": self.source_receipt.sha256,
            "terminal_receipt_key": self.terminal_receipt.key,
            "terminal_receipt_sha256": self.terminal_receipt.sha256,
            "terminal_state": self.terminal_state,
        }

    def to_arrow(self) -> dict[str, object]:
        """Return the typed Arrow row projection."""
        payload = self.to_wire()
        payload["day"] = self.day
        payload["source_ceiling"] = self.source_ceiling
        payload["published_at"] = self.published_at
        return payload


def _validate_data_receipt_collection(receipts: tuple[EvidenceReceipt, ...]) -> None:
    if not isinstance(receipts, tuple):
        raise ValueError("data_receipts must be an immutable tuple")
    receipt_keys = tuple(receipt.key for receipt in receipts)
    if receipt_keys != tuple(sorted(set(receipt_keys))):
        raise ValueError("data_receipts must be sorted by unique object key")


def _validate_terminal_payload(row: AvailabilityRow) -> None:
    if row.terminal_state == "published":
        if row.absence_reason is not None:
            raise ValueError("a published availability row cannot carry an absence reason")
        if row.completion_receipt is None:
            raise ValueError("a published availability row requires a completion receipt")
        if _is_published_empty_rung(rung=row.rung, row_count=row.row_count, data_receipts=row.data_receipts):
            return
        if row.row_count <= 0:
            raise ValueError("a published availability row must carry a positive row_count")
        # NO PART RECEIPTS AND ROWS TO SHOW IS THE MANIFEST-TRUSTED SHAPE, and it is admitted HERE
        # rather than refused because the row asserts nothing about parts it did not hash. The
        # completion receipt proven above is the whole of its claim.
        #
        # WHAT KEEPS IT OFF THE FORWARD PATH IS NOT THIS FUNCTION. Two guards do, and neither is
        # here: `_refuse_trusted_publication_rows`, called from `_publish_availability_owned` -- the
        # chokepoint the CLI and `availability_extension` both pass through -- refuses the class on
        # any publication; and `_require_declared_provenance` refuses a `TerminalEvidence` whose
        # declared class disagrees with its shape, so a forward writer that dropped its
        # `data_receipts` dies at construction rather than becoming trusted by accident.
        return
    if row.row_count != 0:
        raise ValueError("a governed absence must carry row_count=0")
    if row.data_receipts or row.completion_receipt is not None:
        raise ValueError("a governed absence cannot carry data or completion receipts")
    if row.absence_reason is None or not row.absence_reason.strip():
        raise ValueError("a governed absence requires a non-blank reason")
    if row.absence_reason != row.absence_reason.strip():
        raise ValueError("absence_reason must use canonical trimmed spelling")


def _is_published_empty_rung(*, rung: int, row_count: int, data_receipts: tuple[EvidenceReceipt, ...]) -> bool:
    """True for the one published shape that carries no rows: a DERIVED rung that generalised to none.

    THE DAY IS PUBLISHED AT EVERY RUNG, and this rung of it is empty -- not absent. A governed
    absence claims the SOURCE had nothing, which is false here: the base rung demonstrably holds the
    rows this rung dropped, and a day whose ladder mixed the two terminal states would be refused by
    `_validate_generation_day` and unselectable at every rung rather than merely unindexed.

    THE BASE RUNG IS EXCLUDED, and that exclusion is what keeps the two vocabularies apart. A base
    rung holding no rows is a governed absence and has its own marker; only a rung DERIVED from a
    non-empty base can honestly say "the rows existed and none of them survived my resolution".
    `foundation/parquet/completion.py::PartitionCompletion` refuses the receipt this row binds unless
    it says the same thing, and `objectstore.write_completion_marker` refuses one at the base rung.
    """
    return rung != BASE_ZOOM_TIER and row_count == 0 and not data_receipts


def availability_row_provenance(
    *,
    terminal_state: TerminalState,
    row_count: int,
    data_receipts: tuple[EvidenceReceipt, ...],
) -> AvailabilityProvenance:
    """Classify how well one terminal outcome's parts are proven, FROM ITS SHAPE ALONE.

    THE SHAPE IS THE DECLARATION, and it has to be, because `AVAILABILITY_INDEX_SCHEMA` is frozen at
    version 1: a provenance COLUMN would not survive the generation round trip that `_write_generation`
    re-reads and compares. Published, holding rows, and naming no part is a claim only a
    manifest-trusted row can make -- an ordinary published row always names the parts it counted, and
    a rung that generalised to nothing carries `row_count == 0` and is classified `digested` because
    its emptiness is proven outright by its own marker.

    The bootstrap-input document and the terminal evidence may DECLARE the class in words; both are
    checked against this function, so the two can never disagree.
    """
    if terminal_state == "published" and row_count > 0 and not data_receipts:
        return MANIFEST_TRUSTED_PROVENANCE
    return DIGESTED_PROVENANCE


def availability_provenance_summary(rows: Sequence[AvailabilityRow]) -> dict[str, object]:
    """Return the per-class row count and day range a receipt must carry, in canonical spelling."""
    summary: dict[str, object] = {}
    for provenance in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        days = sorted({row.day for row in rows if row.provenance == provenance})
        summary[provenance] = {
            "earliest_day": days[0].isoformat() if days else None,
            "latest_day": days[-1].isoformat() if days else None,
            "row_count": sum(1 for row in rows if row.provenance == provenance),
        }
    return summary


def _parse_provenance_summary(value: object, label: str) -> dict[str, int]:
    """Validate a receipt's provenance summary and return its per-class row counts."""
    mapping = _require_mapping(value, label)
    _require_exact_keys(mapping, {DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE}, label)
    counts: dict[str, int] = {}
    for provenance in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        entry = _require_mapping(mapping[provenance], f"{label} {provenance}")
        _require_exact_keys(entry, {"earliest_day", "latest_day", "row_count"}, f"{label} {provenance}")
        row_count = _parse_nonnegative_int(entry["row_count"], f"{label} {provenance} row_count")
        earliest = entry["earliest_day"]
        latest = entry["latest_day"]
        if (earliest is None) != (latest is None) or (row_count == 0) != (earliest is None):
            raise ValueError(f"{label} {provenance} must carry a day range exactly when it counts rows")
        if earliest is not None and _parse_date(earliest, "earliest_day") > _parse_date(latest, "latest_day"):
            raise ValueError(f"{label} {provenance} day range is inverted")
        counts[provenance] = row_count
    return counts


@dataclass(frozen=True, slots=True)
class AvailabilityPointer:
    """Checksum-bound mutable pointer to one immutable generation."""

    schema_version: str
    identity: AvailabilityIdentity
    required_rungs: tuple[int, ...]
    generation_key: str
    generation_sha256: str
    generation_receipt_sha256: str
    generation_bytes: int
    rows: int
    earliest_terminal_day: date
    latest_terminal_day: date
    source_ceiling: date
    prior_generation_key: str | None
    prior_generation_sha256: str | None
    created_at: datetime
    bootstrap_receipt: EvidenceReceipt

    def __post_init__(self) -> None:
        if self.schema_version != AVAILABILITY_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {AVAILABILITY_SCHEMA_VERSION}")
        if self.required_rungs != self.identity.required_rungs:
            raise ValueError("pointer required_rungs do not match its identity")
        _require_sha256(self.generation_sha256, "generation_sha256")
        _require_sha256(self.generation_receipt_sha256, "generation_receipt_sha256")
        if _generation_sha_from_key(self.identity.lane_root, self.generation_key) != self.generation_sha256:
            raise ValueError("generation_key digest does not match generation_sha256")
        if not 0 < self.generation_bytes <= GENERATION_MAX_BYTES:
            raise ValueError("pointer generation_bytes exceeds the bounded generation contract")
        if not 0 < self.rows <= MAX_AVAILABILITY_ROWS:
            raise ValueError("pointer rows exceed the bounded generation contract")
        if self.earliest_terminal_day > self.latest_terminal_day:
            raise ValueError("pointer terminal-day range is inverted")
        if self.latest_terminal_day > self.source_ceiling:
            raise ValueError("pointer latest terminal day exceeds source ceiling")
        _require_prior_binding(
            self.prior_generation_key,
            self.prior_generation_sha256,
            self.identity.lane_root,
        )
        _require_utc(self.created_at, "created_at")

    def to_wire(self) -> dict[str, object]:
        """Return the exact pointer document."""
        return {
            "bootstrap_receipt_key": self.bootstrap_receipt.key,
            "bootstrap_receipt_sha256": self.bootstrap_receipt.sha256,
            "created_at": _format_datetime(self.created_at),
            "earliest_terminal_day": self.earliest_terminal_day.isoformat(),
            "generation_bytes": self.generation_bytes,
            "generation_key": self.generation_key,
            "generation_receipt_sha256": self.generation_receipt_sha256,
            "generation_sha256": self.generation_sha256,
            "lane": self.identity.lane,
            "lane_root": self.identity.lane_root,
            "latest_terminal_day": self.latest_terminal_day.isoformat(),
            "nature": self.identity.nature,
            "prior_generation_key": self.prior_generation_key,
            "prior_generation_sha256": self.prior_generation_sha256,
            "product": self.identity.product,
            "required_rungs": list(self.required_rungs),
            "rows": self.rows,
            "schema_version": self.schema_version,
            "source_ceiling": self.source_ceiling.isoformat(),
            "verified_source_inventory_root": self.identity.verified_source_inventory_root,
        }


@dataclass(frozen=True, slots=True)
class AvailabilityIndex:
    """One verified pointer and its immutable generation rows."""

    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]

    def selectable_days(self) -> tuple[date, ...]:
        """Return days whose exact authoritative rung set agrees on one terminal state."""
        by_day: dict[date, list[AvailabilityRow]] = {}
        for row in self.rows:
            by_day.setdefault(row.day, []).append(row)
        selectable: list[date] = []
        expected = self.pointer.required_rungs
        for day in sorted(by_day):
            rows = sorted(by_day[day], key=lambda item: expected.index(item.rung))
            if tuple(row.rung for row in rows) != expected:
                continue
            states = {row.terminal_state for row in rows}
            source_receipts = {row.source_receipt.sha256 for row in rows}
            absence_reasons = {row.absence_reason for row in rows}
            if len(states) == 1 and len(source_receipts) == 1 and len(absence_reasons) == 1:
                selectable.append(day)
        return tuple(selectable)


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


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    """Exact offline bootstrap input and its externally pinned digest."""

    identity: AvailabilityIdentity
    source_ceiling: date
    created_at: datetime
    input_receipts: tuple[EvidenceReceipt, ...]
    rows: tuple[AvailabilityRow, ...]
    input_sha256: str

    @property
    def provenance_summary(self) -> dict[str, object]:
        """Return the per-class row count and day range this input will record in its receipt."""
        return availability_provenance_summary(self.rows)


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    """Exact append or correction input."""

    config: AvailabilityConfig
    created_at: datetime
    rows: tuple[AvailabilityRow, ...]
    input_sha256: str


@dataclass(frozen=True, slots=True)
class PublicationResult:
    """The winning pointer and whether this call advanced it."""

    pointer: AvailabilityPointer
    advanced: bool
    attempts: int


@dataclass(frozen=True, slots=True)
class _LoadedLatest:
    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]
    etag: str


@dataclass(frozen=True, slots=True)
class _VerifiedGeneration:
    pointer: AvailabilityPointer
    rows: tuple[AvailabilityRow, ...]


@dataclass(frozen=True, slots=True)
class _RequestRowClassification:
    replayed_grains: tuple[tuple[date, int], ...]
    added_grains: tuple[tuple[date, int], ...]
    conflicting_grains: tuple[tuple[date, int], ...]
    stale_conflicting_grains: tuple[tuple[date, int], ...]

    @property
    def is_exact_replay(self) -> bool:
        return bool(self.replayed_grains) and not self.added_grains and not self.conflicting_grains


def availability_pointer_key(lane_root: str) -> str:
    """Return the mutable pointer key beneath one physical lane root."""
    _require_lane_root(lane_root)
    return f"{lane_root}/availability/_LATEST.json"


def availability_generation_key(lane_root: str, generation_sha256: str) -> str:
    """Return the immutable content-addressed Parquet key."""
    _require_lane_root(lane_root)
    _require_sha256(generation_sha256, "generation sha256")
    return f"{lane_root}/availability/generation={generation_sha256}/availability.parquet"


def availability_lane_identity(lane_root: str) -> tuple[str, str]:
    """Return one lane root's `(layer, kind)`, refusing anything outside the frozen layout."""
    return _physical_lane_identity(lane_root)


def availability_bootstrap_marker_key(lane_root: str) -> str:
    """Return the DETERMINISTIC key naming this lane's immutable bootstrap receipt."""
    _require_lane_root(lane_root)
    return f"{lane_root}/availability/bootstrap/_BOOTSTRAPPED.json"


def read_bootstrap_marker(store: AvailabilityStorage, *, lane_root: str) -> EvidenceReceipt | None:
    """Return the bootstrap receipt this lane's marker names, or `None` when it was never bootstrapped.

    ONE GET, and never a listing. A lane that answers `None` here has no availability history at all,
    which is the only state a transitional census may fall back on; a lane that answers a receipt and
    has no pointer has LOST its head, which is a fault to withhold rather than to re-prove by scan.
    """
    stored = store.read(availability_bootstrap_marker_key(lane_root), max_bytes=BOOTSTRAP_MARKER_MAX_BYTES)
    if stored is None:
        return None
    value = _decode_canonical_json_object(stored.payload, "availability bootstrap marker")
    # EVERY shape fault becomes `AvailabilityMalformedError`, because the caller is the coverage
    # reader: a bare `ValueError` out of here is not one of the four refusals it classifies and
    # would fail the whole census instead of withholding one lane.
    try:
        _require_exact_keys(value, _BOOTSTRAP_MARKER_FIELDS, "availability bootstrap marker")
        if value["schema_version"] != BOOTSTRAP_MARKER_SCHEMA_VERSION:
            raise ValueError(f"bootstrap marker schema must be {BOOTSTRAP_MARKER_SCHEMA_VERSION}")
        if value["lane_root"] != lane_root:
            raise ValueError("bootstrap marker does not describe the lane it is filed under")
        return EvidenceReceipt(
            key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
            sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
        )
    except ValueError as exc:
        raise AvailabilityMalformedError(f"malformed availability bootstrap marker for {lane_root!r}") from exc


def _bootstrap_marker_payload(lane_root: str, receipt: EvidenceReceipt) -> bytes:
    """Render the marker. Receipt-derived ONLY, so a re-attempt writes byte-identical immutable content."""
    return canonical_json(
        {
            "bootstrap_receipt_key": receipt.key,
            "bootstrap_receipt_sha256": receipt.sha256,
            "lane_root": lane_root,
            "schema_version": BOOTSTRAP_MARKER_SCHEMA_VERSION,
        }
    ).encode("utf-8")


def read_terminal_evidence(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    identity: AvailabilityIdentity,
) -> TerminalEvidence:
    """Read and fully verify one rung's terminal evidence wrapper and the physical objects it binds."""
    evidence, _snapshots = _verify_terminal_evidence_receipt(store, receipt, expected_identity=identity)
    return evidence


def availability_row_from_terminal_evidence(
    evidence: TerminalEvidence,
    *,
    terminal_receipt: EvidenceReceipt,
) -> AvailabilityRow:
    """Rebuild the one row a terminal evidence document can bind, so a retry cannot invent a different one."""
    return AvailabilityRow(
        lane=evidence.identity.lane,
        product=evidence.identity.product,
        nature=evidence.identity.nature,
        day=evidence.day,
        rung=evidence.rung,
        terminal_state=evidence.terminal_state,
        row_count=evidence.row_count,
        source_receipt=evidence.source_receipt,
        terminal_receipt=terminal_receipt,
        data_receipts=evidence.data_receipts,
        completion_receipt=evidence.completion_receipt,
        absence_reason=evidence.absence_reason,
        source_ceiling=evidence.source_ceiling,
        published_at=evidence.published_at,
    )


def compute_verified_source_inventory_root(receipts: Sequence[EvidenceReceipt]) -> str:
    """Digest the exact sorted bootstrap manifest/checkpoint inventory."""
    ordered = tuple(sorted(receipts, key=lambda receipt: receipt.key))
    if not ordered or len({receipt.key for receipt in ordered}) != len(ordered):
        raise ValueError("source inventory receipts must be non-empty with unique keys")
    payload = {
        "domain": "plantgeo.availability.source-inventory.v1",
        "receipts": [receipt.to_wire() for receipt in ordered],
    }
    return sha256_digest(canonical_json(payload))


def load_bootstrap_request(
    path: Path,
    *,
    expected_sha256: str,
    expected_row_count: int,
) -> BootstrapRequest:
    """Load and validate one externally pinned bootstrap document."""
    value, actual_sha256 = _load_exact_document(path, expected_sha256=expected_sha256)
    _require_exact_keys(
        value,
        {
            "created_at",
            "input_receipts",
            "lane",
            "lane_root",
            "nature",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "bootstrap input",
    )
    if value["schema_version"] != BOOTSTRAP_INPUT_SCHEMA_VERSION:
        raise ValueError(f"bootstrap input schema_version must be {BOOTSTRAP_INPUT_SCHEMA_VERSION}")
    identity = _parse_identity(value)
    rows = _parse_rows(value["rows"], identity=identity)
    _require_expected_rows(rows, expected_row_count)
    source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    created_at = _parse_datetime(value["created_at"], "created_at")
    _validate_generation_rows(rows, identity=identity, source_ceiling=source_ceiling)
    _require_rows_published_by(rows, created_at)
    input_receipts = _parse_receipts(value["input_receipts"], "input_receipts")
    if not input_receipts:
        raise ValueError("bootstrap input requires at least one verified manifest/checkpoint receipt")
    if input_receipts != tuple(sorted(input_receipts, key=lambda receipt: receipt.key)):
        raise ValueError("bootstrap input_receipts must use canonical key order")
    for receipt in input_receipts:
        _require_typed_receipt_key(receipt, identity.lane_root, "bootstrap-input")
    return BootstrapRequest(
        identity=identity,
        source_ceiling=source_ceiling,
        created_at=created_at,
        input_receipts=input_receipts,
        rows=rows,
        input_sha256=actual_sha256,
    )


def load_publication_request(
    path: Path,
    *,
    expected_sha256: str,
    expected_row_count: int,
) -> PublicationRequest:
    """Load and validate one externally pinned append/correction document."""
    value, actual_sha256 = _load_exact_document(path, expected_sha256=expected_sha256)
    _require_exact_keys(
        value,
        {
            "bootstrap_receipt_key",
            "bootstrap_receipt_sha256",
            "created_at",
            "lane",
            "lane_root",
            "nature",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "publication input",
    )
    if value["schema_version"] != PUBLICATION_INPUT_SCHEMA_VERSION:
        raise ValueError(f"publication input schema_version must be {PUBLICATION_INPUT_SCHEMA_VERSION}")
    identity = _parse_identity(value)
    rows = _parse_rows(value["rows"], identity=identity)
    _require_expected_rows(rows, expected_row_count)
    source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    created_at = _parse_datetime(value["created_at"], "created_at")
    _validate_generation_rows(rows, identity=identity, source_ceiling=source_ceiling)
    _refuse_trusted_publication_rows(rows)
    _require_rows_published_by(rows, created_at)
    return PublicationRequest(
        config=AvailabilityConfig(
            identity=identity,
            source_ceiling=source_ceiling,
            bootstrap_receipt=EvidenceReceipt(
                key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
                sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
            ),
        ),
        created_at=created_at,
        rows=rows,
        input_sha256=actual_sha256,
    )


def _refuse_trusted_publication_rows(rows: Sequence[AvailabilityRow]) -> None:
    """Keep the manifest-trusted class to the BOOTSTRAP, which is the only thing that made it necessary.

    A forward publication writes the day it is publishing, so it holds every part's digest already --
    `objectstore.WrittenObjectLedger` recorded them as it uploaded. A trusted row arriving here is
    therefore never a saved download; it is a lost one, and admitting it would let the region owner
    decision D3 bounded to history grow forward one tick at a time.

    CALLED FROM THE CHOKEPOINT, `_publish_availability_owned`, and not only from the document loader.
    The loader serves ONE caller (`interface/cli/data.py`); the PRIMARY forward writer is
    `availability_extension._publish_rows`, which builds its `PublicationRequest` in memory and never
    loads a document at all. A guard on the loader alone would be bypassed by every direct-to-Parquet
    writer owner decision D4 mandates. `load_publication_request` keeps its own call because refusing
    at load time names the offending document before a single object is fetched.
    """
    trusted = [row.grain for row in rows if row.provenance == MANIFEST_TRUSTED_PROVENANCE]
    if trusted:
        rendered = ", ".join(f"{day.isoformat()}/z{rung}" for day, rung in trusted[:5])
        raise ValueError(
            f"publication input carries {len(trusted)} manifest-trusted row(s) ({rendered}); only a bootstrap "
            "may bind a day it did not hash, and a forward publication holds every part digest it wrote"
        )


async def bootstrap_availability(
    session: AsyncSession,
    store: AvailabilityStorage,
    request: BootstrapRequest,
    *,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Create generation zero while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, request.identity.lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _bootstrap_availability_owned(store, request)


def _bootstrap_availability_owned(store: AvailabilityStorage, request: BootstrapRequest) -> PublicationResult:
    """Create one lane's immutable bootstrap receipt and generation zero; caller owns its barrier."""
    _require_sha256(request.input_sha256, "bootstrap input sha256")
    _require_utc(request.created_at, "created_at")
    _validate_generation_rows(
        request.rows,
        identity=request.identity,
        source_ceiling=request.source_ceiling,
    )
    _require_rows_published_by(request.rows, request.created_at)
    _require_sorted_nonempty_receipts(request.input_receipts, "bootstrap input receipts")
    receipt_payload = _bootstrap_receipt_payload(request)
    receipt_sha256 = sha256_digest(receipt_payload)
    receipt = EvidenceReceipt(
        key=f"{request.identity.lane_root}/availability/bootstrap/receipt={receipt_sha256}.json",
        sha256=receipt_sha256,
    )
    config = AvailabilityConfig(
        identity=request.identity,
        source_ceiling=request.source_ceiling,
        bootstrap_receipt=receipt,
    )
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_optional(store, request.identity.lane_root)
        if latest is not None:
            if _is_same_bootstrap(latest, config=config):
                return PublicationResult(pointer=latest.pointer, advanced=False, attempts=attempt)
            raise AlreadyBootstrappedError(
                f"availability lane {request.identity.lane_root!r} already has a different bootstrap"
            )
        if snapshots is None:
            inventories, verified_inventory = _verify_bootstrap_inventory_receipts(
                store,
                request.input_receipts,
                expected_identity=request.identity,
                expected_source_ceiling=request.source_ceiling,
            )
            underlying = _dedupe_receipts(
                tuple(receipt for inventory in inventories for receipt in inventory.object_receipts)
            )
            if compute_verified_source_inventory_root(underlying) != request.identity.verified_source_inventory_root:
                raise AvailabilityChecksumError(
                    "bootstrap inventory wrappers do not establish verified_source_inventory_root"
                )
            verified = list(verified_inventory)
            verified.extend(_verify_rows_evidence(store, request.rows, identity=request.identity))
            store.put_immutable(receipt.key, receipt_payload, content_type=JSON_CONTENT_TYPE)
            # The deterministic marker, written BEFORE the pointer exists: it is what proves this
            # lane was bootstrapped at all once its mutable head is gone. Immutable and
            # receipt-derived, so a retried bootstrap re-writes identical bytes and a DIFFERENT
            # bootstrap is refused here rather than quietly beginning a second history.
            store.put_immutable(
                availability_bootstrap_marker_key(request.identity.lane_root),
                _bootstrap_marker_payload(request.identity.lane_root, receipt),
                content_type=JSON_CONTENT_TYPE,
            )
            verified.append(
                _verify_system_bootstrap_receipt(
                    store,
                    receipt,
                    expected_identity=request.identity,
                    maximum_row_count=len(request.rows),
                    maximum_source_ceiling=request.source_ceiling,
                )
            )
            snapshots = _dedupe_snapshots(verified)
        pointer = _write_generation(
            store,
            config=config,
            rows=request.rows,
            prior_generation_key=None,
            prior_generation_sha256=None,
            created_at=request.created_at,
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(request.identity.lane_root),
            pointer_payload,
            expected_etag=None,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt)
    raise AvailabilityConflictError("availability bootstrap pointer remained contended after bounded retries")


async def publish_availability(
    session: AsyncSession,
    store: AvailabilityStorage,
    request: PublicationRequest,
    *,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Publish terminal outcomes while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, request.config.identity.lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _publish_availability_owned(store, request)


def _publish_availability_owned(store: AvailabilityStorage, request: PublicationRequest) -> PublicationResult:
    """Append or correct terminal outcomes and conditionally advance the pointer; caller owns its barrier."""
    _require_sha256(request.input_sha256, "publication input sha256")
    _require_utc(request.created_at, "created_at")
    _validate_generation_rows(
        request.rows,
        identity=request.config.identity,
        source_ceiling=request.config.source_ceiling,
    )
    _refuse_trusted_publication_rows(request.rows)
    _require_rows_published_by(request.rows, request.created_at)
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_required(store, request.config.identity.lane_root)
        _require_config_compatible(request.config, latest.pointer)
        classification = _classify_request_rows(latest.rows, request.rows)
        if classification.is_exact_replay and request.config.source_ceiling <= latest.pointer.source_ceiling:
            return PublicationResult(pointer=latest.pointer, advanced=False, attempts=attempt)
        if classification.stale_conflicting_grains:
            rendered = ", ".join(f"{day.isoformat()}/z{rung}" for day, rung in classification.stale_conflicting_grains)
            raise AvailabilityConflictError(f"stale publication conflicts with existing grains: {rendered}")
        if snapshots is None:
            snapshots = _dedupe_snapshots(
                (
                    _verify_system_bootstrap_receipt(
                        store,
                        request.config.bootstrap_receipt,
                        expected_identity=request.config.identity,
                        maximum_row_count=latest.pointer.rows,
                        maximum_source_ceiling=latest.pointer.source_ceiling,
                    ),
                    *_verify_rows_evidence(store, request.rows, identity=request.config.identity),
                )
            )
        merged = _merge_rows(latest.rows, request.rows)
        effective_config = AvailabilityConfig(
            identity=request.config.identity,
            source_ceiling=max(request.config.source_ceiling, latest.pointer.source_ceiling),
            bootstrap_receipt=request.config.bootstrap_receipt,
        )
        effective_created_at = _logical_created_at(request.created_at, latest.pointer.created_at)
        pointer = _write_generation(
            store,
            config=effective_config,
            rows=merged,
            prior_generation_key=latest.pointer.generation_key,
            prior_generation_sha256=latest.pointer.generation_sha256,
            created_at=effective_created_at,
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(request.config.identity.lane_root),
            pointer_payload,
            expected_etag=latest.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt)
    raise AvailabilityConflictError("availability pointer remained contended after bounded retries")


async def rollback_availability(  # noqa: PLR0913 - public ownership seam plus retained-generation coordinates
    session: AsyncSession,
    store: AvailabilityStorage,
    *,
    lane_root: str,
    target_generation_key: str,
    created_at: datetime,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> PublicationResult:
    """Restore a retained generation while exclusively owning the lane publication boundary."""
    async with publication_barrier(session, lane_root) as granted:
        if not granted:
            raise AvailabilityConflictError("availability publication barrier is contended")
        return _rollback_availability_owned(
            store,
            lane_root=lane_root,
            target_generation_key=target_generation_key,
            created_at=created_at,
        )


def _rollback_availability_owned(
    store: AvailabilityStorage,
    *,
    lane_root: str,
    target_generation_key: str,
    created_at: datetime,
) -> PublicationResult:
    """Republish a retained generation after the current head; caller owns its lane barrier."""
    _require_utc(created_at, "created_at")
    target_sha256 = _generation_sha_from_key(lane_root, target_generation_key)
    snapshots: tuple[EvidenceSnapshot, ...] | None = None
    for attempt in range(1, MAX_PUBLICATION_ATTEMPTS + 1):
        latest = _load_latest_required(store, lane_root)
        target = _read_generation_for_pointer(
            store,
            pointer=latest.pointer,
            generation_key=target_generation_key,
            generation_sha256=target_sha256,
            require_pointer_metadata=False,
        )
        if snapshots is None:
            snapshots = _dedupe_snapshots(
                (
                    _verify_system_bootstrap_receipt(
                        store,
                        target.pointer.bootstrap_receipt,
                        expected_identity=target.pointer.identity,
                        maximum_row_count=target.pointer.rows,
                        maximum_source_ceiling=target.pointer.source_ceiling,
                    ),
                    *_verify_rows_evidence(store, target.rows, identity=target.pointer.identity),
                )
            )
        config = AvailabilityConfig(
            identity=target.pointer.identity,
            source_ceiling=target.pointer.source_ceiling,
            bootstrap_receipt=target.pointer.bootstrap_receipt,
        )
        pointer = _write_generation(
            store,
            config=config,
            rows=target.rows,
            prior_generation_key=latest.pointer.generation_key,
            prior_generation_sha256=latest.pointer.generation_sha256,
            created_at=_logical_created_at(created_at, latest.pointer.created_at),
        )
        pointer_payload = _pointer_payload(pointer)
        _revalidate_snapshots(store, snapshots)
        if store.compare_and_swap(
            availability_pointer_key(lane_root),
            pointer_payload,
            expected_etag=latest.etag,
            content_type=JSON_CONTENT_TYPE,
        ):
            return PublicationResult(pointer=pointer, advanced=True, attempts=attempt)
    raise AvailabilityConflictError("availability rollback pointer remained contended after bounded retries")


def read_latest_availability(  # noqa: PLR0913
    store: AvailabilityStorage,
    *,
    lane_root: str,
    expected_lane: str | None = None,
    expected_product: str | None = None,
    expected_nature: AvailabilityNature | None = None,
    expected_required_rungs: tuple[int, ...] | None = None,
    required_source_ceiling: date | None = None,
) -> AvailabilityIndex:
    """Read exactly one pointer and its checksum-bound generation, failing closed."""
    latest = _load_latest_required(store, lane_root)
    expectations: tuple[tuple[str, object | None, object], ...] = (
        ("lane", expected_lane, latest.pointer.identity.lane),
        ("product", expected_product, latest.pointer.identity.product),
        ("nature", expected_nature, latest.pointer.identity.nature),
        ("required_rungs", expected_required_rungs, latest.pointer.required_rungs),
    )
    for label, expected, actual in expectations:
        if expected is not None and expected != actual:
            raise AvailabilityUnavailableError(
                "availability_stale",
                f"availability {label} {actual!r} does not match required {expected!r}",
            )
    if required_source_ceiling is not None and latest.pointer.source_ceiling < required_source_ceiling:
        raise AvailabilityUnavailableError(
            "availability_stale",
            f"availability source ceiling {latest.pointer.source_ceiling} precedes required {required_source_ceiling}",
        )
    return AvailabilityIndex(pointer=latest.pointer, rows=latest.rows)


def _write_generation(  # noqa: PLR0913
    store: AvailabilityStorage,
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> AvailabilityPointer:
    _require_utc(created_at, "created_at")
    _validate_generation_rows(rows, identity=config.identity, source_ceiling=config.source_ceiling)
    _require_rows_published_by(rows, created_at)
    generation_receipt = _generation_receipt_sha256(
        config=config,
        rows=rows,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    earliest = min(row.day for row in rows)
    latest = max(row.day for row in rows)
    payload = _serialize_generation(
        config=config,
        rows=rows,
        generation_receipt_sha256=generation_receipt,
        earliest_terminal_day=earliest,
        latest_terminal_day=latest,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    generation_sha256 = sha256_digest(payload)
    generation_key = availability_generation_key(config.identity.lane_root, generation_sha256)
    pointer = AvailabilityPointer(
        schema_version=AVAILABILITY_SCHEMA_VERSION,
        identity=config.identity,
        required_rungs=config.identity.required_rungs,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_receipt_sha256=generation_receipt,
        generation_bytes=len(payload),
        rows=len(rows),
        earliest_terminal_day=earliest,
        latest_terminal_day=latest,
        source_ceiling=config.source_ceiling,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
        bootstrap_receipt=config.bootstrap_receipt,
    )
    store.put_immutable(generation_key, payload, content_type=PARQUET_CONTENT_TYPE)
    reread = _read_generation_for_pointer(
        store,
        pointer=pointer,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        require_pointer_metadata=True,
    )
    if reread.rows != rows:
        raise AvailabilityChecksumError("availability generation reread changed its canonical rows")
    return pointer


def _load_latest_optional(store: AvailabilityStorage, lane_root: str) -> _LoadedLatest | None:
    pointer_key = availability_pointer_key(lane_root)
    stored = store.read(pointer_key, max_bytes=POINTER_MAX_BYTES)
    if stored is None:
        return None
    try:
        pointer = _parse_pointer(stored.payload, expected_lane_root=lane_root)
        generation = _read_generation_for_pointer(
            store,
            pointer=pointer,
            generation_key=pointer.generation_key,
            generation_sha256=pointer.generation_sha256,
            require_pointer_metadata=True,
        )
    except AvailabilityChecksumError:
        raise
    except AvailabilityUnavailableError:
        raise
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
        raise AvailabilityMalformedError(f"malformed availability evidence for {lane_root!r}") from exc
    return _LoadedLatest(pointer=pointer, rows=generation.rows, etag=stored.etag)


def _load_latest_required(store: AvailabilityStorage, lane_root: str) -> _LoadedLatest:
    latest = _load_latest_optional(store, lane_root)
    if latest is None:
        raise AvailabilityUnavailableError("availability_missing", f"availability pointer is missing for {lane_root!r}")
    return latest


def _read_generation_for_pointer(
    store: AvailabilityStorage,
    *,
    pointer: AvailabilityPointer,
    generation_key: str,
    generation_sha256: str,
    require_pointer_metadata: bool,
) -> _VerifiedGeneration:
    read_ceiling = (
        pointer.generation_bytes
        if generation_key == pointer.generation_key and generation_sha256 == pointer.generation_sha256
        else GENERATION_MAX_BYTES
    )
    stored = store.read(generation_key, max_bytes=read_ceiling)
    if stored is None:
        raise AvailabilityUnavailableError(
            "availability_stale", f"availability generation {generation_key!r} is missing"
        )
    actual_sha256 = sha256_digest(stored.payload)
    if actual_sha256 != generation_sha256:
        raise AvailabilityChecksumError(
            f"availability generation checksum mismatch: expected {generation_sha256}, got {actual_sha256}"
        )
    expected_key = availability_generation_key(pointer.identity.lane_root, actual_sha256)
    if expected_key != generation_key:
        raise AvailabilityChecksumError("availability generation key is not bound to its byte digest")
    parquet_file = pq.ParquetFile(io.BytesIO(stored.payload))
    if parquet_file.metadata.num_rows > MAX_AVAILABILITY_ROWS:
        raise AvailabilityMalformedError("availability generation declares too many physical rows")
    if (
        generation_key == pointer.generation_key
        and generation_sha256 == pointer.generation_sha256
        and parquet_file.metadata.num_rows != pointer.rows
    ):
        raise AvailabilityMalformedError("availability pointer row count disagrees before materialization")
    if not parquet_file.schema_arrow.remove_metadata().equals(AVAILABILITY_INDEX_SCHEMA):
        raise AvailabilityMalformedError("availability generation Arrow schema does not match version 1")
    table = _materialize_generation(parquet_file)
    metadata = table.schema.metadata
    metadata_keys = set() if metadata is None else set(metadata) - {b"ARROW:schema"}
    if metadata is None or metadata_keys != AVAILABILITY_METADATA_KEYS:
        raise AvailabilityMalformedError("availability generation metadata is missing, extra, or malformed")
    rows = tuple(_row_from_mapping(item) for item in cast("list[dict[str, object]]", table.to_pylist()))
    metadata_pointer = _pointer_from_metadata(
        metadata,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_bytes=len(stored.payload),
    )
    _validate_generation_rows(
        rows,
        identity=metadata_pointer.identity,
        source_ceiling=metadata_pointer.source_ceiling,
    )
    _require_rows_published_by(rows, metadata_pointer.created_at)
    if (
        metadata_pointer.rows != len(rows)
        or metadata_pointer.earliest_terminal_day != min(row.day for row in rows)
        or metadata_pointer.latest_terminal_day != max(row.day for row in rows)
    ):
        raise AvailabilityMalformedError("availability metadata does not match its physical row population")
    expected_receipt = _generation_receipt_sha256(
        config=AvailabilityConfig(
            identity=metadata_pointer.identity,
            source_ceiling=metadata_pointer.source_ceiling,
            bootstrap_receipt=metadata_pointer.bootstrap_receipt,
        ),
        rows=rows,
        prior_generation_key=metadata_pointer.prior_generation_key,
        prior_generation_sha256=metadata_pointer.prior_generation_sha256,
        created_at=metadata_pointer.created_at,
    )
    if expected_receipt != metadata_pointer.generation_receipt_sha256:
        raise AvailabilityChecksumError("availability semantic generation receipt is invalid")
    if require_pointer_metadata and metadata_pointer != pointer:
        raise AvailabilityUnavailableError("availability_stale", "availability pointer and Parquet metadata disagree")
    if not require_pointer_metadata and (
        metadata_pointer.identity != pointer.identity or metadata_pointer.bootstrap_receipt != pointer.bootstrap_receipt
    ):
        raise AvailabilityUnavailableError("availability_stale", "retained generation belongs to another contract")
    return _VerifiedGeneration(pointer=metadata_pointer, rows=rows)


def _materialize_generation(parquet_file: pq.ParquetFile) -> pa.Table:
    return parquet_file.read()


def _serialize_generation(  # noqa: PLR0913
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    generation_receipt_sha256: str,
    earliest_terminal_day: date,
    latest_terminal_day: date,
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> bytes:
    metadata = _metadata(
        config=config,
        generation_receipt_sha256=generation_receipt_sha256,
        row_count=len(rows),
        earliest_terminal_day=earliest_terminal_day,
        latest_terminal_day=latest_terminal_day,
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=created_at,
    )
    schema = AVAILABILITY_INDEX_SCHEMA.with_metadata(metadata)
    table = pa.Table.from_pylist([row.to_arrow() for row in rows], schema=schema)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", version="2.6", write_statistics=True)
    return sink.getvalue()


def _metadata(  # noqa: PLR0913
    *,
    config: AvailabilityConfig,
    generation_receipt_sha256: str,
    row_count: int,
    earliest_terminal_day: date,
    latest_terminal_day: date,
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> dict[bytes, bytes]:
    values = {
        "bootstrap_receipt_key": config.bootstrap_receipt.key,
        "bootstrap_receipt_sha256": config.bootstrap_receipt.sha256,
        "created_at": _format_datetime(created_at),
        "earliest_terminal_day": earliest_terminal_day.isoformat(),
        "generation_receipt_sha256": generation_receipt_sha256,
        "lane": config.identity.lane,
        "lane_root": config.identity.lane_root,
        "latest_terminal_day": latest_terminal_day.isoformat(),
        "nature": config.identity.nature,
        "prior_generation_key": canonical_json(prior_generation_key),
        "prior_generation_sha256": canonical_json(prior_generation_sha256),
        "product": config.identity.product,
        "required_rungs": canonical_json(config.identity.required_rungs),
        "row_count": str(row_count),
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "source_ceiling": config.source_ceiling.isoformat(),
        "verified_source_inventory_root": config.identity.verified_source_inventory_root,
    }
    return {f"availability.{key}".encode(): value.encode() for key, value in values.items()}


def _pointer_from_metadata(
    metadata: Mapping[bytes, bytes],
    *,
    generation_key: str,
    generation_sha256: str,
    generation_bytes: int,
) -> AvailabilityPointer:
    def text(name: str) -> str:
        return metadata[f"availability.{name}".encode()].decode("utf-8")

    required_rungs_value: object = json.loads(text("required_rungs"))
    prior_value: object = json.loads(text("prior_generation_key"))
    prior_sha_value: object = json.loads(text("prior_generation_sha256"))
    required_rungs = _parse_rungs(required_rungs_value)
    prior_generation_key = _optional_string(prior_value, "prior_generation_key")
    prior_generation_sha256 = _optional_string(prior_sha_value, "prior_generation_sha256")
    identity = AvailabilityIdentity(
        lane_root=text("lane_root"),
        lane=text("lane"),
        product=text("product"),
        nature=_parse_nature(text("nature")),
        required_rungs=required_rungs,
        verified_source_inventory_root=text("verified_source_inventory_root"),
    )
    return AvailabilityPointer(
        schema_version=text("schema_version"),
        identity=identity,
        required_rungs=required_rungs,
        generation_key=generation_key,
        generation_sha256=generation_sha256,
        generation_receipt_sha256=text("generation_receipt_sha256"),
        generation_bytes=generation_bytes,
        rows=_parse_positive_int(text("row_count"), "row_count"),
        earliest_terminal_day=_parse_date(text("earliest_terminal_day"), "earliest_terminal_day"),
        latest_terminal_day=_parse_date(text("latest_terminal_day"), "latest_terminal_day"),
        source_ceiling=_parse_date(text("source_ceiling"), "source_ceiling"),
        prior_generation_key=prior_generation_key,
        prior_generation_sha256=prior_generation_sha256,
        created_at=_parse_datetime(text("created_at"), "created_at"),
        bootstrap_receipt=EvidenceReceipt(
            key=text("bootstrap_receipt_key"),
            sha256=text("bootstrap_receipt_sha256"),
        ),
    )


def _parse_pointer(payload: bytes, *, expected_lane_root: str) -> AvailabilityPointer:
    value = _decode_json_object(payload, "availability pointer")
    _require_exact_keys(
        value,
        {
            "bootstrap_receipt_key",
            "bootstrap_receipt_sha256",
            "created_at",
            "earliest_terminal_day",
            "generation_bytes",
            "generation_key",
            "generation_receipt_sha256",
            "generation_sha256",
            "lane",
            "lane_root",
            "latest_terminal_day",
            "nature",
            "prior_generation_key",
            "prior_generation_sha256",
            "product",
            "required_rungs",
            "rows",
            "schema_version",
            "source_ceiling",
            "verified_source_inventory_root",
        },
        "availability pointer",
    )
    required_rungs = _parse_rungs(value["required_rungs"])
    identity = AvailabilityIdentity(
        lane_root=_require_string(value["lane_root"], "lane_root"),
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        required_rungs=required_rungs,
        verified_source_inventory_root=_require_string(
            value["verified_source_inventory_root"], "verified_source_inventory_root"
        ),
    )
    if identity.lane_root != expected_lane_root:
        raise ValueError("availability pointer lane_root does not match its object path")
    pointer = AvailabilityPointer(
        schema_version=_require_string(value["schema_version"], "schema_version"),
        identity=identity,
        required_rungs=required_rungs,
        generation_key=_require_string(value["generation_key"], "generation_key"),
        generation_sha256=_require_string(value["generation_sha256"], "generation_sha256"),
        generation_receipt_sha256=_require_string(value["generation_receipt_sha256"], "generation_receipt_sha256"),
        generation_bytes=_parse_positive_int(value["generation_bytes"], "generation_bytes"),
        rows=_parse_positive_int(value["rows"], "rows"),
        earliest_terminal_day=_parse_date(value["earliest_terminal_day"], "earliest_terminal_day"),
        latest_terminal_day=_parse_date(value["latest_terminal_day"], "latest_terminal_day"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        prior_generation_key=_optional_string(value["prior_generation_key"], "prior_generation_key"),
        prior_generation_sha256=_optional_string(value["prior_generation_sha256"], "prior_generation_sha256"),
        created_at=_parse_datetime(value["created_at"], "created_at"),
        bootstrap_receipt=EvidenceReceipt(
            key=_require_string(value["bootstrap_receipt_key"], "bootstrap_receipt_key"),
            sha256=_require_string(value["bootstrap_receipt_sha256"], "bootstrap_receipt_sha256"),
        ),
    )
    if pointer.schema_version != AVAILABILITY_SCHEMA_VERSION:
        raise ValueError(f"availability pointer schema_version must be {AVAILABILITY_SCHEMA_VERSION}")
    _require_sha256(pointer.generation_sha256, "generation_sha256")
    _require_sha256(pointer.generation_receipt_sha256, "generation_receipt_sha256")
    if _generation_sha_from_key(identity.lane_root, pointer.generation_key) != pointer.generation_sha256:
        raise ValueError("generation_key digest does not match generation_sha256")
    _require_prior_binding(pointer.prior_generation_key, pointer.prior_generation_sha256, identity.lane_root)
    if pointer.earliest_terminal_day > pointer.latest_terminal_day:
        raise ValueError("availability pointer terminal-day range is inverted")
    if pointer.latest_terminal_day > pointer.source_ceiling:
        raise ValueError("availability pointer latest terminal day exceeds source ceiling")
    return pointer


def _pointer_payload(pointer: AvailabilityPointer) -> bytes:
    payload = canonical_json(pointer.to_wire()).encode("utf-8")
    if len(payload) > POINTER_MAX_BYTES:
        raise AvailabilityMalformedError("availability pointer exceeds its byte ceiling")
    return payload


def _generation_receipt_sha256(
    *,
    config: AvailabilityConfig,
    rows: tuple[AvailabilityRow, ...],
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> str:
    payload = {
        "bootstrap_receipt": config.bootstrap_receipt.to_wire(),
        "created_at": _format_datetime(created_at),
        "lane": config.identity.lane,
        "lane_root": config.identity.lane_root,
        "nature": config.identity.nature,
        "prior_generation_key": prior_generation_key,
        "prior_generation_sha256": prior_generation_sha256,
        "product": config.identity.product,
        "required_rungs": list(config.identity.required_rungs),
        "rows": [row.to_wire() for row in rows],
        "schema_version": AVAILABILITY_SCHEMA_VERSION,
        "source_ceiling": config.source_ceiling.isoformat(),
        "verified_source_inventory_root": config.identity.verified_source_inventory_root,
    }
    return sha256_digest(canonical_json(payload))


def _bootstrap_receipt_payload(request: BootstrapRequest) -> bytes:
    outcome_sha256 = sha256_digest(canonical_json([row.to_wire() for row in request.rows]))
    payload = {
        "bootstrap_input_sha256": request.input_sha256,
        "created_at": _format_datetime(request.created_at),
        "input_receipts": [receipt.to_wire() for receipt in request.input_receipts],
        "lane": request.identity.lane,
        "lane_root": request.identity.lane_root,
        "nature": request.identity.nature,
        "outcome_sha256": outcome_sha256,
        "product": request.identity.product,
        # THE WEAKER PROVENANCE, RECORDED WHERE IT CANNOT BE MISSED (spec tripwire, D3): how many
        # rows of this lane were bound by digest, how many by manifest trust, and the day range of
        # each. Derived from the rows themselves, so a replayed bootstrap re-writes identical bytes.
        "provenance": availability_provenance_summary(request.rows),
        "required_rungs": list(request.identity.required_rungs),
        "row_count": len(request.rows),
        "schema_version": SYSTEM_BOOTSTRAP_SCHEMA_VERSION,
        "source_ceiling": request.source_ceiling.isoformat(),
        "verified_source_inventory_root": request.identity.verified_source_inventory_root,
    }
    encoded = canonical_json(payload).encode("utf-8")
    if len(encoded) > BOOTSTRAP_RECEIPT_MAX_BYTES:
        raise AvailabilityMalformedError("system bootstrap receipt exceeds its byte ceiling")
    return encoded


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


def _verify_raw_receipts(
    store: AvailabilityStorage,
    receipts: Sequence[EvidenceReceipt],
) -> tuple[EvidenceSnapshot, ...]:
    return tuple(
        _read_receipt_snapshot(store, receipt, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)[1]
        for receipt in _dedupe_receipts(receipts)
    )


def _dedupe_receipts(receipts: Sequence[EvidenceReceipt]) -> tuple[EvidenceReceipt, ...]:
    by_key: dict[str, EvidenceReceipt] = {}
    for receipt in receipts:
        held = by_key.setdefault(receipt.key, receipt)
        if held.sha256 != receipt.sha256:
            raise AvailabilityChecksumError(f"receipt key {receipt.key!r} is bound to two different digests")
    return tuple(by_key[key] for key in sorted(by_key))


def _dedupe_snapshots(snapshots: Sequence[EvidenceSnapshot]) -> tuple[EvidenceSnapshot, ...]:
    by_key: dict[str, EvidenceSnapshot] = {}
    for snapshot in snapshots:
        held = by_key.setdefault(snapshot.key, snapshot)
        if held != snapshot:
            raise AvailabilityConflictError(f"object {snapshot.key!r} was observed with two identities")
    return tuple(by_key[key] for key in sorted(by_key))


def _revalidate_snapshots(store: AvailabilityStorage, snapshots: Sequence[EvidenceSnapshot]) -> None:
    for snapshot in snapshots:
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


def _verify_bootstrap_inventory_receipts(
    store: AvailabilityStorage,
    receipts: Sequence[EvidenceReceipt],
    *,
    expected_identity: AvailabilityIdentity,
    expected_source_ceiling: date,
) -> tuple[tuple[BootstrapInventoryEvidence, ...], tuple[EvidenceSnapshot, ...]]:
    inventories: list[BootstrapInventoryEvidence] = []
    snapshots: list[EvidenceSnapshot] = []
    for receipt in _dedupe_receipts(receipts):
        value, wrapper_snapshot = _read_canonical_typed_document(
            store,
            receipt,
            lane_root=expected_identity.lane_root,
            purpose="bootstrap-input",
            max_bytes=BOOTSTRAP_RECEIPT_MAX_BYTES,
        )
        _require_exact_keys(value, _BOOTSTRAP_INVENTORY_FIELDS, "bootstrap inventory evidence")
        if value["schema_version"] != BOOTSTRAP_INVENTORY_SCHEMA_VERSION:
            raise AvailabilityMalformedError("unknown bootstrap inventory evidence schema")
        inventory = BootstrapInventoryEvidence(
            identity=_parse_identity(value),
            source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
            object_receipts=_parse_receipts(value["object_receipts"], "object_receipts"),
        )
        if inventory.identity != expected_identity or inventory.source_ceiling != expected_source_ceiling:
            raise AvailabilityConflictError("bootstrap inventory evidence does not match its request contract")
        inventories.append(inventory)
        snapshots.append(wrapper_snapshot)
        snapshots.extend(_verify_raw_receipts(store, inventory.object_receipts))
    return tuple(inventories), _dedupe_snapshots(snapshots)


def _verify_rows_evidence(
    store: AvailabilityStorage,
    rows: Sequence[AvailabilityRow],
    *,
    identity: AvailabilityIdentity,
) -> tuple[EvidenceSnapshot, ...]:
    source_cache: dict[str, SourceEvidence] = {}
    snapshots: list[EvidenceSnapshot] = []
    for row in rows:
        source = source_cache.get(row.source_receipt.key)
        if source is None:
            source, source_snapshots = _verify_source_evidence_receipt(
                store,
                row.source_receipt,
                expected_identity=identity,
                expected_day=row.day,
                expected_source_ceiling=row.source_ceiling,
            )
            source_cache[row.source_receipt.key] = source
            snapshots.extend(source_snapshots)
        elif (source.day, source.source_ceiling) != (row.day, row.source_ceiling):
            raise AvailabilityConflictError("one source evidence receipt was reused across incompatible rows")
        terminal, terminal_snapshots = _verify_terminal_evidence_receipt(
            store,
            row.terminal_receipt,
            expected_identity=identity,
        )
        _cross_bind_terminal_row(row, terminal)
        snapshots.extend(terminal_snapshots)
    return _dedupe_snapshots(snapshots)


def _verify_source_evidence_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
    expected_day: date,
    expected_source_ceiling: date,
) -> tuple[SourceEvidence, tuple[EvidenceSnapshot, ...]]:
    value, wrapper_snapshot = _read_canonical_typed_document(
        store,
        receipt,
        lane_root=expected_identity.lane_root,
        purpose="source",
        max_bytes=TYPED_RECEIPT_MAX_BYTES,
    )
    _require_exact_keys(value, _SOURCE_EVIDENCE_FIELDS, "source evidence")
    if value["schema_version"] != SOURCE_EVIDENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown source evidence schema")
    evidence = SourceEvidence(
        identity=_parse_identity(value),
        day=_parse_date(value["day"], "day"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        object_receipts=_parse_receipts(value["object_receipts"], "object_receipts"),
    )
    if (
        evidence.identity != expected_identity
        or evidence.day != expected_day
        or evidence.source_ceiling != expected_source_ceiling
    ):
        raise AvailabilityConflictError("source evidence does not match its availability row")
    nested = _verify_raw_receipts(store, evidence.object_receipts)
    return evidence, _dedupe_snapshots((wrapper_snapshot, *nested))


def _verify_terminal_evidence_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
) -> tuple[TerminalEvidence, tuple[EvidenceSnapshot, ...]]:
    value, wrapper_snapshot = _read_canonical_typed_document(
        store,
        receipt,
        lane_root=expected_identity.lane_root,
        purpose="terminal",
        max_bytes=TYPED_RECEIPT_MAX_BYTES,
    )
    expected_fields = _TERMINAL_EVIDENCE_FIELDS
    if PROVENANCE_FIELD in value:
        # Admitted as a key, never as a value: `_require_declared_provenance` refuses any spelling
        # but `manifest_trusted`, and the canonical re-serialization already performed on this
        # payload refuses a document that says it and does not mean it.
        expected_fields = expected_fields | {PROVENANCE_FIELD}
    _require_exact_keys(value, expected_fields, "terminal evidence")
    if value["schema_version"] != TERMINAL_EVIDENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown terminal evidence schema")
    evidence = TerminalEvidence(
        identity=_parse_identity(value),
        day=_parse_date(value["day"], "day"),
        rung=_parse_int(value["rung"], "rung"),
        terminal_state=_parse_terminal_state(value["terminal_state"]),
        row_count=_parse_nonnegative_int(value["row_count"], "row_count"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        published_at=_parse_datetime(value["published_at"], "published_at"),
        source_receipt=_parse_receipt(value["source_receipt"], "source_receipt"),
        data_receipts=_parse_receipts(value["data_receipts"], "data_receipts"),
        completion_receipt=_parse_optional_receipt_value(value["completion_receipt"], "completion_receipt"),
        absence_receipt=_parse_optional_receipt_value(value["absence_receipt"], "absence_receipt"),
        absence_reason=_optional_string(value["absence_reason"], "absence_reason"),
        provenance=_parse_provenance(value.get(PROVENANCE_FIELD)),
    )
    if evidence.identity != expected_identity:
        raise AvailabilityConflictError("terminal evidence identity does not match its lane")
    physical = _verify_terminal_physical_objects(store, evidence)
    return evidence, _dedupe_snapshots((wrapper_snapshot, *physical))


def _cross_bind_terminal_row(row: AvailabilityRow, evidence: TerminalEvidence) -> None:
    expected = (
        row.day,
        row.rung,
        row.terminal_state,
        row.row_count,
        row.source_ceiling,
        row.published_at,
        row.source_receipt,
        row.data_receipts,
        row.completion_receipt,
        row.absence_reason,
        row.provenance,
    )
    actual = (
        evidence.day,
        evidence.rung,
        evidence.terminal_state,
        evidence.row_count,
        evidence.source_ceiling,
        evidence.published_at,
        evidence.source_receipt,
        evidence.data_receipts,
        evidence.completion_receipt,
        evidence.absence_reason,
        evidence.provenance,
    )
    if actual != expected:
        raise AvailabilityConflictError("terminal evidence does not exactly bind its availability row")


def _verify_terminal_physical_objects(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
) -> tuple[EvidenceSnapshot, ...]:
    if evidence.terminal_state == "governed_absence":
        return _verify_absence_object(store, evidence)
    layer, kind = _physical_lane_identity(evidence.identity.lane_root)
    # NO DATA RECEIPTS IS A REACHABLE, VERIFIED STATE HERE -- an emptied derived rung. The loop runs
    # zero times, the contiguity check passes over an empty range, and the row count matches at zero;
    # `_verify_completion_object` then proves the marker says the same thing. Nothing is waved past.
    snapshots: list[EvidenceSnapshot] = []
    part_indexes: list[int] = []
    physical_rows = 0
    for receipt in evidence.data_receipts:
        parsed = try_parse_partition_path(receipt.key)
        if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
            layer,
            kind,
            evidence.rung,
            evidence.day,
        ):
            raise AvailabilityConflictError("data receipt path does not match terminal evidence")
        stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=EVIDENCE_OBJECT_MAX_BYTES)
        try:
            metadata = pq.ParquetFile(io.BytesIO(stored.payload)).metadata
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
            raise AvailabilityMalformedError(f"data receipt {receipt.key!r} is not valid Parquet") from exc
        physical_rows += metadata.num_rows
        part_indexes.append(parsed.part_index)
        snapshots.append(snapshot)
    if sorted(part_indexes) != list(range(len(part_indexes))):
        raise AvailabilityConflictError("data receipt part indexes must be contiguous and ordered")
    if evidence.provenance != MANIFEST_TRUSTED_PROVENANCE and physical_rows != evidence.row_count:
        # A MANIFEST-TRUSTED ROW HAS NO PARTS TO COUNT, so this comparison would read 0 against its
        # row_count and refuse the one class it was built to admit. What that row owes instead is
        # proven in `_verify_completion_object`: the marker is fetched, digested against the receipt
        # the row binds, and made to say the same number.
        raise AvailabilityConflictError("data Parquet row counts do not match terminal row_count")
    snapshots.extend(_verify_completion_object(store, evidence, layer=layer, kind=kind))
    return tuple(snapshots)


def _verify_completion_object(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
    *,
    layer: str,
    kind: str,
) -> tuple[EvidenceSnapshot, ...]:
    receipt = evidence.completion_receipt
    if receipt is None:
        raise AvailabilityMalformedError("published terminal evidence has no completion receipt")
    parsed = try_parse_completion_marker_path(receipt.key)
    if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
        layer,
        kind,
        evidence.rung,
        evidence.day,
    ):
        raise AvailabilityConflictError("completion receipt path does not match terminal evidence")
    if parsed.derived_empty != _is_published_empty_rung(
        rung=evidence.rung, row_count=evidence.row_count, data_receipts=evidence.data_receipts
    ):
        # THE KEY NAME IS ALSO A CLAIM, and it is checked before the body is opened. A row citing
        # `_complete.json` for an emptied rung binds the receipt of a rung whose parts were deleted
        # out from under it, and a row citing `_complete.empty.json` for a rung holding parts binds
        # a receipt that says the opposite of what it points at.
        raise AvailabilityConflictError("completion receipt key and terminal evidence disagree about an emptied rung")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=TYPED_RECEIPT_MAX_BYTES)
    value = _decode_json_object(stored.payload, "completion marker")
    expected_keys = {"schema_version", "part_count", "row_count", "completed_at", "run_id"}
    if DERIVED_EMPTY_FIELD in value:
        # Admitted as a key, never as a value: `PartitionCompletion.from_json_bytes` refuses any
        # spelling but `true`, and the byte-for-byte re-serialization below refuses anything else the
        # payload could be hiding. Widening the key set costs nothing that those two do not re-check.
        expected_keys.add(DERIVED_EMPTY_FIELD)
    if COMPLETION_PARTS_FIELD in value:
        # Same admission, same reason: the marker's own decoder binds this field to schema version 2
        # and refuses a partial list, and `_require_recorded_parts_agree` below makes it answer to the
        # row that cites it.
        expected_keys.add(COMPLETION_PARTS_FIELD)
    _require_exact_keys(value, expected_keys, "completion marker")
    if value["schema_version"] not in COMPLETION_SCHEMA_VERSIONS:
        raise AvailabilityMalformedError("unknown completion marker schema")
    try:
        completion = PartitionCompletion.from_json_bytes(stored.payload)
    except PartitionCompletionError as exc:
        raise AvailabilityMalformedError("invalid completion marker") from exc
    if completion.to_json_bytes() != stored.payload:
        raise AvailabilityMalformedError("completion marker does not use its authoritative serialization")
    if completion.row_count != evidence.row_count:
        raise AvailabilityConflictError("completion counts do not match terminal evidence")
    _require_part_count_agrees(completion, evidence)
    _require_recorded_parts_agree(completion, evidence, layer=layer, kind=kind)
    if completion.derived_empty != _is_published_empty_rung(
        rung=evidence.rung, row_count=evidence.row_count, data_receipts=evidence.data_receipts
    ):
        # The two statements must be the same statement. A row claiming an empty rung over an
        # ordinary receipt would bind evidence that never said the rung generalised to nothing, and
        # an ordinary row over a derived-empty receipt would serve a rung the receipt calls empty.
        raise AvailabilityConflictError("completion marker and terminal evidence disagree about an emptied rung")
    if completion.completed_at > evidence.published_at:
        raise AvailabilityConflictError("completion marker postdates terminal publication")
    return (snapshot,)


def _require_part_count_agrees(completion: PartitionCompletion, evidence: TerminalEvidence) -> None:
    """Make the marker's part count answer to the row, in the two different ways the classes allow."""
    if evidence.provenance != MANIFEST_TRUSTED_PROVENANCE:
        if completion.part_count != len(evidence.data_receipts):
            raise AvailabilityConflictError("completion counts do not match terminal evidence")
        return
    # THE WHOLE OF WHAT A TRUSTED ROW CLAIMS ABOUT PARTS: that the export said it finished holding
    # some. Nothing here re-counts the objects, because that is the download this class exists to
    # avoid; a marker claiming zero parts over a row claiming rows is still a contradiction and dies.
    if completion.part_count <= 0:
        raise AvailabilityConflictError("a manifest-trusted row binds a completion marker claiming at least one part")


def _require_recorded_parts_agree(
    completion: PartitionCompletion,
    evidence: TerminalEvidence,
    *,
    layer: str,
    kind: str,
) -> None:
    """Bind a marker that RECORDED its parts to the row citing it, so the newer receipt is not decorative.

    A marker written with per-part digests is the artifact that lets a later compile bind a day
    without downloading it, so the moment it is cited it must describe this exact rung-day: real
    partition paths, contiguous indexes, and -- for a digested row -- the same (key, sha256) set the
    row published. A marker without recorded parts is the legacy shape and states nothing to check.
    """
    if not completion.parts:
        return
    recorded: dict[str, str] = {}
    part_indexes: list[int] = []
    for part in completion.parts:
        parsed = try_parse_partition_path(part.relative_path)
        if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
            layer,
            kind,
            evidence.rung,
            evidence.day,
        ):
            raise AvailabilityConflictError("completion marker records a part outside the rung-day it closes")
        recorded[part.relative_path] = part.sha256
        part_indexes.append(parsed.part_index)
    if sorted(part_indexes) != list(range(len(part_indexes))):
        raise AvailabilityConflictError("completion marker part indexes must be contiguous and ordered")
    if evidence.provenance == MANIFEST_TRUSTED_PROVENANCE:
        return
    published = {receipt.key: receipt.sha256 for receipt in evidence.data_receipts}
    if recorded != published:
        raise AvailabilityConflictError("completion marker parts and terminal data receipts disagree")


def _verify_absence_object(
    store: AvailabilityStorage,
    evidence: TerminalEvidence,
) -> tuple[EvidenceSnapshot, ...]:
    receipt = evidence.absence_receipt
    if receipt is None:
        raise AvailabilityMalformedError("governed absence terminal evidence has no absence receipt")
    layer, kind = _physical_lane_identity(evidence.identity.lane_root)
    parsed = try_parse_absence_marker_path(receipt.key)
    if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
        layer,
        kind,
        evidence.rung,
        evidence.day,
    ):
        raise AvailabilityConflictError("absence receipt path does not match terminal evidence")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=TYPED_RECEIPT_MAX_BYTES)
    value = _decode_json_object(stored.payload, "governed absence marker")
    _require_exact_keys(
        value,
        {"schema_version", "reason", "upstream_response", "recorded_at", "run_id"},
        "governed absence marker",
    )
    if value["schema_version"] != ABSENCE_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown governed absence marker schema")
    try:
        absence = GovernedAbsence.from_json_bytes(stored.payload)
    except GovernedAbsenceError as exc:
        raise AvailabilityMalformedError("invalid governed absence marker") from exc
    if absence.to_json_bytes() != stored.payload:
        raise AvailabilityMalformedError("governed absence marker does not use its authoritative serialization")
    if absence.reason != evidence.absence_reason:
        raise AvailabilityConflictError("absence marker reason does not match terminal evidence")
    if absence.recorded_at > evidence.published_at:
        raise AvailabilityConflictError("absence marker postdates terminal publication")
    return (snapshot,)


def _read_canonical_typed_document(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    lane_root: str,
    purpose: Literal["bootstrap-input", "source", "terminal"],
    max_bytes: int,
) -> tuple[dict[str, object], EvidenceSnapshot]:
    _require_typed_receipt_key(receipt, lane_root, purpose)
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=max_bytes)
    return _decode_canonical_json_object(stored.payload, f"{purpose} evidence"), snapshot


def _require_typed_receipt_key(
    receipt: EvidenceReceipt,
    lane_root: str,
    purpose: Literal["bootstrap-input", "source", "terminal"],
) -> None:
    expected_key = f"{lane_root}/availability/evidence/{purpose}={receipt.sha256}.json"
    if receipt.key != expected_key:
        raise AvailabilityConflictError(f"{purpose} receipt key is not content-addressed for its lane")


def _verify_system_bootstrap_receipt(
    store: AvailabilityStorage,
    receipt: EvidenceReceipt,
    *,
    expected_identity: AvailabilityIdentity,
    maximum_row_count: int,
    maximum_source_ceiling: date,
) -> EvidenceSnapshot:
    expected_key = f"{expected_identity.lane_root}/availability/bootstrap/receipt={receipt.sha256}.json"
    if receipt.key != expected_key:
        raise AvailabilityConflictError("system bootstrap receipt key is outside its content-addressed lane path")
    stored, snapshot = _read_receipt_snapshot(store, receipt, max_bytes=BOOTSTRAP_RECEIPT_MAX_BYTES)
    value = _decode_canonical_json_object(stored.payload, "system bootstrap receipt")
    _require_exact_keys(value, _SYSTEM_BOOTSTRAP_FIELDS, "system bootstrap receipt")
    if value["schema_version"] != SYSTEM_BOOTSTRAP_SCHEMA_VERSION:
        raise AvailabilityMalformedError("unknown system bootstrap receipt schema")
    identity = _parse_identity(value)
    if identity != expected_identity:
        raise AvailabilityConflictError("system bootstrap receipt identity does not match its pointer")
    input_receipts = _parse_receipts(value["input_receipts"], "input_receipts")
    _require_sorted_nonempty_receipts(input_receipts, "bootstrap input receipts")
    for input_receipt in input_receipts:
        expected_input_key = f"{identity.lane_root}/availability/evidence/bootstrap-input={input_receipt.sha256}.json"
        if input_receipt.key != expected_input_key:
            raise AvailabilityConflictError("system bootstrap receipt references an invalid bootstrap input key")
    _require_sha256(
        _require_string(value["bootstrap_input_sha256"], "bootstrap_input_sha256"),
        "bootstrap_input_sha256",
    )
    _require_sha256(_require_string(value["outcome_sha256"], "outcome_sha256"), "outcome_sha256")
    bootstrap_source_ceiling = _parse_date(value["source_ceiling"], "source_ceiling")
    if bootstrap_source_ceiling > maximum_source_ceiling:
        raise AvailabilityConflictError("system bootstrap source ceiling exceeds its generation contract")
    _parse_datetime(value["created_at"], "created_at")
    row_count = _parse_positive_int(value["row_count"], "row_count")
    if row_count > min(MAX_AVAILABILITY_ROWS, maximum_row_count):
        raise AvailabilityMalformedError("system bootstrap receipt row_count exceeds the generation bound")
    provenance = _parse_provenance_summary(value["provenance"], "system bootstrap provenance")
    if sum(provenance.values()) != row_count:
        raise AvailabilityMalformedError("system bootstrap provenance classes do not account for every row")
    return snapshot


def _decode_canonical_json_object(payload: bytes, label: str) -> dict[str, object]:
    value = _decode_json_object(payload, label)
    if canonical_json(value).encode("utf-8") != payload:
        raise AvailabilityMalformedError(f"{label} is not canonical JSON")
    return value


def _parse_receipt(value: object, label: str) -> EvidenceReceipt:
    mapping = _require_mapping(value, label)
    _require_exact_keys(mapping, {"key", "sha256"}, label)
    return EvidenceReceipt(
        key=_require_string(mapping["key"], f"{label} key"),
        sha256=_require_string(mapping["sha256"], f"{label} sha256"),
    )


def _parse_optional_receipt_value(value: object, label: str) -> EvidenceReceipt | None:
    if value is None:
        return None
    return _parse_receipt(value, label)


def _require_sorted_nonempty_receipts(receipts: Sequence[EvidenceReceipt], label: str) -> None:
    if not receipts:
        raise ValueError(f"{label} must be non-empty")
    keys = tuple(receipt.key for receipt in receipts)
    if keys != tuple(sorted(set(keys))):
        raise ValueError(f"{label} must use sorted unique object keys")


def _validate_terminal_evidence_payload(evidence: TerminalEvidence) -> None:
    _validate_data_receipt_collection(evidence.data_receipts)
    _require_declared_provenance(evidence)
    if evidence.terminal_state == "published":
        if evidence.absence_receipt is not None or evidence.absence_reason is not None:
            raise ValueError("published terminal evidence cannot carry absence evidence")
        if evidence.completion_receipt is None:
            raise ValueError("published terminal evidence requires a completion receipt")
        if _is_published_empty_rung(
            rung=evidence.rung, row_count=evidence.row_count, data_receipts=evidence.data_receipts
        ):
            return
        if evidence.row_count <= 0:
            raise ValueError("published terminal evidence requires a positive row_count")
        if not evidence.data_receipts and evidence.provenance != MANIFEST_TRUSTED_PROVENANCE:
            raise ValueError("published terminal evidence requires data and completion receipts")
        return
    if evidence.row_count != 0 or evidence.data_receipts or evidence.completion_receipt is not None:
        raise ValueError("governed absence terminal evidence cannot carry published data")
    if evidence.absence_receipt is None or evidence.absence_reason is None or not evidence.absence_reason.strip():
        raise ValueError("governed absence terminal evidence requires an absence receipt and reason")
    if evidence.absence_reason != evidence.absence_reason.strip():
        raise ValueError("terminal absence reason must use canonical trimmed spelling")


def _require_declared_provenance(evidence: TerminalEvidence) -> None:
    """Bind the DECLARED class to the shape, so a document can never claim one and carry the other."""
    if evidence.provenance not in (DIGESTED_PROVENANCE, MANIFEST_TRUSTED_PROVENANCE):
        raise ValueError(f"{PROVENANCE_FIELD} must be {DIGESTED_PROVENANCE} or {MANIFEST_TRUSTED_PROVENANCE}")
    derived = availability_row_provenance(
        terminal_state=evidence.terminal_state,
        row_count=evidence.row_count,
        data_receipts=evidence.data_receipts,
    )
    if derived != evidence.provenance:
        raise ValueError(
            f"terminal evidence declares {evidence.provenance} and has the shape of {derived}; a manifest-trusted "
            "outcome publishes rows and names no part digest, and nothing else may claim that class"
        )


def _physical_lane_identity(lane_root: str) -> tuple[str, str]:
    segments = lane_root.split("/")
    if (
        len(segments) != _LANE_ROOT_SEGMENT_COUNT
        or not segments[0].startswith("layer=")
        or not segments[1].startswith("kind=")
    ):
        raise ValueError("lane_root must be exactly layer=<slug>/kind=<observed|forecast>")
    layer = segments[0].removeprefix("layer=")
    kind = segments[1].removeprefix("kind=")
    if not layer or kind not in {"observed", "forecast"}:
        raise ValueError("lane_root must be exactly layer=<slug>/kind=<observed|forecast>")
    return layer, kind


def _merge_rows(
    existing: tuple[AvailabilityRow, ...],
    changes: tuple[AvailabilityRow, ...],
) -> tuple[AvailabilityRow, ...]:
    merged = {row.grain: row for row in existing}
    merged.update((row.grain, row) for row in changes)
    return tuple(sorted(merged.values(), key=lambda row: (row.day, row.rung)))


def _classify_request_rows(
    existing: tuple[AvailabilityRow, ...],
    requested: tuple[AvailabilityRow, ...],
) -> _RequestRowClassification:
    existing_by_grain = {row.grain: row for row in existing}
    replayed: list[tuple[date, int]] = []
    added: list[tuple[date, int]] = []
    conflicting: list[tuple[date, int]] = []
    stale_conflicting: list[tuple[date, int]] = []
    for row in requested:
        held = existing_by_grain.get(row.grain)
        if held is None:
            added.append(row.grain)
        elif held == row:
            replayed.append(row.grain)
        else:
            conflicting.append(row.grain)
            if row.published_at <= held.published_at:
                stale_conflicting.append(row.grain)
    return _RequestRowClassification(
        replayed_grains=tuple(replayed),
        added_grains=tuple(added),
        conflicting_grains=tuple(conflicting),
        stale_conflicting_grains=tuple(stale_conflicting),
    )


def _logical_created_at(requested: datetime, winning: datetime) -> datetime:
    return max(requested, winning + timedelta(microseconds=1))


def _is_same_bootstrap(
    latest: _LoadedLatest,
    *,
    config: AvailabilityConfig,
) -> bool:
    return latest.pointer.identity == config.identity and latest.pointer.bootstrap_receipt == config.bootstrap_receipt


def _require_config_compatible(config: AvailabilityConfig, pointer: AvailabilityPointer) -> None:
    if config.identity != pointer.identity or config.bootstrap_receipt != pointer.bootstrap_receipt:
        raise AvailabilityConflictError("publication input does not match the lane's immutable bootstrap contract")


def _validate_generation_rows(
    rows: tuple[AvailabilityRow, ...],
    *,
    identity: AvailabilityIdentity,
    source_ceiling: date,
) -> None:
    if not rows or len(rows) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"availability generation must contain 1..{MAX_AVAILABILITY_ROWS} rows")
    grains: set[tuple[date, int]] = set()
    rows_by_day: dict[date, list[AvailabilityRow]] = {}
    for row in rows:
        _validate_generation_row(row, identity=identity, source_ceiling=source_ceiling)
        if row.grain in grains:
            raise ValueError(f"duplicate availability grain {row.day}/z{row.rung}")
        grains.add(row.grain)
        rows_by_day.setdefault(row.day, []).append(row)
    expected_order = tuple(sorted(rows, key=lambda row: (row.day, row.rung)))
    if rows != expected_order:
        raise ValueError("availability rows must be sorted by day then rung")
    for day, day_rows in rows_by_day.items():
        _validate_generation_day(day, day_rows, required_rungs=identity.required_rungs)
    if max(row.source_ceiling for row in rows) != source_ceiling:
        raise ValueError("generation source ceiling must equal the maximum receipt-bound row source ceiling")


def _validate_generation_row(
    row: AvailabilityRow,
    *,
    identity: AvailabilityIdentity,
    source_ceiling: date,
) -> None:
    if (row.lane, row.product, row.nature) != (identity.lane, identity.product, identity.nature):
        raise ValueError("availability row identity does not match its generation")
    if row.rung not in identity.required_rungs:
        raise ValueError(f"availability row rung {row.rung} is outside required_rungs")
    if row.source_ceiling > source_ceiling:
        raise ValueError("availability row source ceiling exceeds its generation source ceiling")
    _require_typed_receipt_key(row.source_receipt, identity.lane_root, "source")
    _require_typed_receipt_key(row.terminal_receipt, identity.lane_root, "terminal")
    if row.terminal_state == "published":
        layer, kind = _physical_lane_identity(identity.lane_root)
        for receipt in row.data_receipts:
            parsed = try_parse_partition_path(receipt.key)
            if parsed is None or (parsed.layer, parsed.kind, parsed.zoom, parsed.day) != (
                layer,
                kind,
                row.rung,
                row.day,
            ):
                raise ValueError("availability data receipt path does not match its row")
        if row.completion_receipt is None:
            raise ValueError("published availability row requires a completion receipt")
        completion = try_parse_completion_marker_path(row.completion_receipt.key)
        if completion is None or (completion.layer, completion.kind, completion.zoom, completion.day) != (
            layer,
            kind,
            row.rung,
            row.day,
        ):
            raise ValueError("availability completion receipt path does not match its row")


def _validate_generation_day(
    day: date,
    rows: list[AvailabilityRow],
    *,
    required_rungs: tuple[int, ...],
) -> None:
    if tuple(row.rung for row in rows) != required_rungs:
        raise ValueError(f"availability day {day} does not contain the exact required_rungs ladder")
    if len({row.terminal_state for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes terminal states across its ladder")
    if len({row.source_receipt for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes source receipts across its ladder")
    if len({row.source_ceiling for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes source ceilings across its ladder")
    if len({row.absence_reason for row in rows}) != 1:
        raise ValueError(f"availability day {day} mixes absence reasons across its ladder")


def _require_rows_published_by(rows: Sequence[AvailabilityRow], created_at: datetime) -> None:
    for row in rows:
        if row.published_at > created_at:
            raise ValueError("availability row published_at cannot exceed generation created_at")


def _parse_identity(value: Mapping[str, object]) -> AvailabilityIdentity:
    return AvailabilityIdentity(
        lane_root=_require_string(value["lane_root"], "lane_root"),
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        required_rungs=_parse_rungs(value["required_rungs"]),
        verified_source_inventory_root=_require_string(
            value["verified_source_inventory_root"], "verified_source_inventory_root"
        ),
    )


def _parse_rows(value: object, *, identity: AvailabilityIdentity) -> tuple[AvailabilityRow, ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"rows must contain 1..{MAX_AVAILABILITY_ROWS} entries")
    rows = tuple(_row_from_mapping(_require_mapping(item, "row")) for item in value)
    for row in rows:
        if (row.lane, row.product, row.nature) != (identity.lane, identity.product, identity.nature):
            raise ValueError("row identity does not match input identity")
    return rows


def _row_from_mapping(value: Mapping[str, object]) -> AvailabilityRow:
    # ONE PARSER, TWO CALLERS: the offline input document, where a row MAY declare its provenance
    # class in words, and the Arrow generation, where it never can -- `AVAILABILITY_INDEX_SCHEMA` is
    # frozen at version 1 and holds no such column. The declaration is therefore optional, and it is
    # checked against the shape rather than believed; the shape is what survives the round trip.
    declared = value.get(PROVENANCE_FIELD)
    expected_keys = set(_AVAILABILITY_ROW_FIELDS)
    if PROVENANCE_FIELD in value:
        expected_keys.add(PROVENANCE_FIELD)
    _require_exact_keys(value, expected_keys, "availability row")
    row = _row_fields(value)
    if declared is not None and _parse_declared_row_provenance(declared) != row.provenance:
        raise ValueError(
            f"availability row declares {declared!r} and has the shape of {row.provenance}; a manifest-trusted "
            "row publishes rows and names no part digest, and nothing else may claim that class"
        )
    return row


def _parse_declared_row_provenance(value: object) -> AvailabilityProvenance:
    """Read a row's DECLARED class. Both spellings are admitted: the input document is compiled, not derived."""
    if value == DIGESTED_PROVENANCE:
        return DIGESTED_PROVENANCE
    if value == MANIFEST_TRUSTED_PROVENANCE:
        return MANIFEST_TRUSTED_PROVENANCE
    raise ValueError(f"{PROVENANCE_FIELD} must be {DIGESTED_PROVENANCE} or {MANIFEST_TRUSTED_PROVENANCE}")


_AVAILABILITY_ROW_FIELDS: Final = {
    "absence_reason",
    "completion_receipt_key",
    "completion_receipt_sha256",
    "data_receipts",
    "day",
    "lane",
    "nature",
    "product",
    "published_at",
    "row_count",
    "rung",
    "source_ceiling",
    "source_receipt_key",
    "source_receipt_sha256",
    "terminal_receipt_key",
    "terminal_receipt_sha256",
    "terminal_state",
}


def _row_fields(value: Mapping[str, object]) -> AvailabilityRow:
    terminal_state = _parse_terminal_state(value["terminal_state"])
    return AvailabilityRow(
        lane=_require_string(value["lane"], "lane"),
        product=_require_string(value["product"], "product"),
        nature=_parse_nature(value["nature"]),
        day=_parse_date(value["day"], "day"),
        rung=_parse_int(value["rung"], "rung"),
        terminal_state=terminal_state,
        row_count=_parse_nonnegative_int(value["row_count"], "row_count"),
        source_receipt=EvidenceReceipt(
            key=_require_string(value["source_receipt_key"], "source_receipt_key"),
            sha256=_require_string(value["source_receipt_sha256"], "source_receipt_sha256"),
        ),
        terminal_receipt=EvidenceReceipt(
            key=_require_string(value["terminal_receipt_key"], "terminal_receipt_key"),
            sha256=_require_string(value["terminal_receipt_sha256"], "terminal_receipt_sha256"),
        ),
        data_receipts=_parse_receipts(value["data_receipts"], "data_receipts"),
        completion_receipt=_optional_receipt(value, "completion_receipt_key", "completion_receipt_sha256"),
        absence_reason=_optional_string(value["absence_reason"], "absence_reason"),
        source_ceiling=_parse_date(value["source_ceiling"], "source_ceiling"),
        published_at=_parse_datetime(value["published_at"], "published_at"),
    )


def _optional_receipt(value: Mapping[str, object], key_name: str, sha_name: str) -> EvidenceReceipt | None:
    key = value[key_name]
    sha256 = value[sha_name]
    if key is None and sha256 is None:
        return None
    if key is None or sha256 is None:
        raise ValueError(f"{key_name} and {sha_name} must both be null or both be strings")
    return EvidenceReceipt(key=_require_string(key, key_name), sha256=_require_string(sha256, sha_name))


def _parse_receipts(value: object, label: str) -> tuple[EvidenceReceipt, ...]:
    if not isinstance(value, list) or len(value) > MAX_AVAILABILITY_ROWS:
        raise ValueError(f"{label} must be a bounded list")
    receipts: list[EvidenceReceipt] = []
    for item in value:
        mapping = _require_mapping(item, label)
        _require_exact_keys(mapping, {"key", "sha256"}, label)
        receipts.append(
            EvidenceReceipt(
                key=_require_string(mapping["key"], "receipt key"),
                sha256=_require_string(mapping["sha256"], "receipt sha256"),
            )
        )
    return tuple(receipts)


def _load_exact_document(path: Path, *, expected_sha256: str) -> tuple[dict[str, object], str]:
    _require_sha256(expected_sha256, "input sha256")
    with path.open("rb") as source:
        payload = source.read(MAX_INPUT_BYTES + 1)
    if len(payload) > MAX_INPUT_BYTES:
        raise ValueError(f"availability input exceeds {MAX_INPUT_BYTES} bytes")
    actual_sha256 = sha256_digest(payload)
    if actual_sha256 != expected_sha256:
        raise AvailabilityChecksumError(
            f"availability input checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    return _decode_json_object(payload, "availability input"), actual_sha256


def _decode_json_object(payload: bytes, label: str) -> dict[str, object]:
    value: object = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_pairs)
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object with string keys")
    return cast("dict[str, object]", value)


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON object contains duplicate key {key!r}")
        value[key] = item
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object")
    return cast("dict[str, object]", value)


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields must be exactly: {', '.join(sorted(expected))}")


def _require_expected_rows(rows: tuple[AvailabilityRow, ...], expected_row_count: int) -> None:
    if not 1 <= expected_row_count <= MAX_AVAILABILITY_ROWS:
        raise ValueError(f"expected-row-count must be between 1 and {MAX_AVAILABILITY_ROWS}")
    if len(rows) != expected_row_count:
        raise ValueError(f"availability input holds {len(rows)} rows, not expected-row-count={expected_row_count}")


def _parse_rungs(value: object) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError("required_rungs must be a non-empty ordered integer list")
    rungs = tuple(cast("list[int]", value))
    _require_rungs(rungs)
    return rungs


def _require_rungs(rungs: tuple[int, ...]) -> None:
    if rungs != AVAILABILITY_REQUIRED_RUNGS:
        raise ValueError(f"required_rungs must be the canonical ordered set {AVAILABILITY_REQUIRED_RUNGS}")
    for rung in rungs:
        _require_rung(rung)


def _require_rung(rung: int) -> None:
    if isinstance(rung, bool) or not 0 <= rung <= _MAX_RUNG:
        raise ValueError(f"rung must be an integer between 0 and {_MAX_RUNG}")


def _parse_nature(value: object) -> AvailabilityNature:
    if value == "daily_series":
        return "daily_series"
    if value == "release_series":
        return "release_series"
    raise ValueError("nature must be daily_series or release_series")


def _require_nature(value: str) -> None:
    _parse_nature(value)


def _parse_provenance(value: object) -> AvailabilityProvenance:
    """Read a declared provenance class; absent means the ordinary one, and `digested` is never written."""
    if value is None:
        return DIGESTED_PROVENANCE
    if value == MANIFEST_TRUSTED_PROVENANCE:
        return MANIFEST_TRUSTED_PROVENANCE
    raise ValueError(f"{PROVENANCE_FIELD} is written only as {MANIFEST_TRUSTED_PROVENANCE}, got {value!r}")


def _parse_terminal_state(value: object) -> TerminalState:
    if value == "published":
        return "published"
    if value == "governed_absence":
        return "governed_absence"
    raise ValueError("terminal_state must be published or governed_absence")


def _parse_date(value: object, label: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = _require_string(value, label)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical YYYY-MM-DD date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{label} must be a canonical YYYY-MM-DD date")
    return parsed


def _parse_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        _require_utc(value, label)
        return value.astimezone(UTC)
    text = _require_string(value, label)
    if not text.endswith("Z"):
        raise ValueError(f"{label} must be canonical UTC with a Z suffix")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical UTC timestamp") from exc
    _require_utc(parsed, label)
    if _format_datetime(parsed) != text:
        raise ValueError(f"{label} must use canonical UTC spelling")
    return parsed


def _format_datetime(value: datetime) -> str:
    _require_utc(value, "datetime")
    rendered = value.isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"


def _require_utc(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None or value.astimezone(UTC).utcoffset() != value.utcoffset():
        raise ValueError(f"{label} must be timezone-aware UTC")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty trimmed string")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, label)


def _parse_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _parse_nonnegative_int(value: object, label: str) -> int:
    parsed = _parse_int(value, label)
    if parsed < 0:
        raise ValueError(f"{label} must be non-negative")
    return parsed


def _parse_positive_int(value: object, label: str) -> int:
    if isinstance(value, str):
        if not value.isdecimal():
            raise ValueError(f"{label} must be a positive integer")
        parsed = int(value)
    else:
        parsed = _parse_int(value, label)
    if parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _require_sha256(value: str, label: str) -> None:
    if len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _require_name(value: str, label: str) -> None:
    if not value or value != value.strip() or "/" in value or "\\" in value or ".." in value:
        raise ValueError(f"{label} must be a safe non-empty lane identifier")


def _require_lane_root(value: str) -> None:
    if (
        not value
        or value != value.strip("/")
        or value.startswith("/")
        or "\\" in value
        or ".." in value
        or not value.startswith("layer=")
    ):
        raise ValueError("lane_root must be a relative layer=... object prefix")
    _physical_lane_identity(value)


def _require_object_key(value: str, label: str) -> None:
    if not value or value != value.strip("/") or value.startswith("/") or "\\" in value or ".." in value:
        raise ValueError(f"{label} must be a relative safe object key")


def _normalize_prefix(prefix: str) -> str:
    normalized = prefix.strip("/")
    if not normalized:
        return ""
    _require_object_key(normalized, "object store prefix")
    return f"{normalized}/"


def _generation_sha_from_key(lane_root: str, key: str) -> str:
    prefix = f"{lane_root}/availability/generation="
    suffix = "/availability.parquet"
    if not key.startswith(prefix) or not key.endswith(suffix):
        raise ValueError("generation key is outside the lane's immutable availability layout")
    sha256 = key[len(prefix) : -len(suffix)]
    _require_sha256(sha256, "generation key digest")
    return sha256


def _require_prior_binding(key: str | None, sha256: str | None, lane_root: str) -> None:
    if key is None and sha256 is None:
        return
    if key is None or sha256 is None:
        raise ValueError("prior generation key and SHA-256 must both be null or both be present")
    _require_sha256(sha256, "prior_generation_sha256")
    if _generation_sha_from_key(lane_root, key) != sha256:
        raise ValueError("prior generation key digest does not match prior_generation_sha256")


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
