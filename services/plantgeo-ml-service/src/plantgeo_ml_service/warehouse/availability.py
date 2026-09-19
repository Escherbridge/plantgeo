"""The availability generation and pointer documents: lane identity, terminal rows, and the ladder.

Layer L2. A COPY of the document half of agri-data-service's `pipeline/parquet/availability_*.py`,
held honest by `tests/test_availability_parity.py`. The publisher that WRITES these lives at
`pipeline/availability_publisher.py`. Why writing a partition does not publish it, and why the
pointer is compare-and-set rather than last-write-wins, live in `AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.canonical import canonical_json, sha256_digest
from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    ZOOM_TIERS,
    PartitionKind,
    availability_generation_path,
    availability_lane_root,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

AVAILABILITY_SCHEMA_VERSION: Final = "1"

#: The AUTHORITATIVE ladder: a day is selectable only when EVERY rung of it reached one terminal
#: state. A publisher that indexed three of four rungs would make a day selectable at a resolution
#: it never wrote.
AVAILABILITY_REQUIRED_RUNGS: Final[tuple[int, ...]] = ZOOM_TIERS

AvailabilityNature = Literal["daily_series", "release_series", "static_lookup"]
TerminalState = Literal["published", "governed_absence"]
AvailabilityProvenance = Literal["digested", "manifest_trusted"]

DIGESTED_PROVENANCE: Final[AvailabilityProvenance] = "digested"
MANIFEST_TRUSTED_PROVENANCE: Final[AvailabilityProvenance] = "manifest_trusted"

#: Bounded because an unbounded index is an unbounded download on a read path.
MAX_AVAILABILITY_ROWS: Final = 250_000
GENERATION_MAX_BYTES: Final = 256 * 1024 * 1024
POINTER_MAX_BYTES: Final = 64 * 1024

_SHA256_LENGTH: Final = 64
_HEX_ALPHABET: Final = frozenset("0123456789abcdef")

_DATA_RECEIPT_TYPE: Final = pa.struct(
    [
        pa.field("key", pa.string(), nullable=False),
        pa.field("sha256", pa.string(), nullable=False),
    ]
)

AVAILABILITY_INDEX_SCHEMA: Final = pa.schema(
    [
        pa.field("lane", pa.string(), nullable=False),
        pa.field("product", pa.string(), nullable=False),
        pa.field("nature", pa.string(), nullable=False),
        pa.field("day", pa.date32(), nullable=False),
        pa.field("rung", pa.int16(), nullable=False),
        pa.field("terminal_state", pa.string(), nullable=False),
        pa.field("row_count", pa.int64(), nullable=False),
        pa.field("source_receipt_key", pa.string(), nullable=False),
        pa.field("source_receipt_sha256", pa.string(), nullable=False),
        pa.field("terminal_receipt_key", pa.string(), nullable=False),
        pa.field("terminal_receipt_sha256", pa.string(), nullable=False),
        pa.field("data_receipts", pa.list_(pa.field("item", _DATA_RECEIPT_TYPE, nullable=False)), nullable=False),
        pa.field("completion_receipt_key", pa.string(), nullable=True),
        pa.field("completion_receipt_sha256", pa.string(), nullable=True),
        pa.field("absence_reason", pa.string(), nullable=True),
        pa.field("source_ceiling", pa.date32(), nullable=False),
        pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

AVAILABILITY_METADATA_KEYS: Final[frozenset[bytes]] = frozenset(
    {
        b"availability.bootstrap_receipt_key",
        b"availability.bootstrap_receipt_sha256",
        b"availability.created_at",
        b"availability.earliest_terminal_day",
        b"availability.generation_receipt_sha256",
        b"availability.lane",
        b"availability.lane_root",
        b"availability.latest_terminal_day",
        b"availability.nature",
        b"availability.prior_generation_key",
        b"availability.prior_generation_sha256",
        b"availability.product",
        b"availability.required_rungs",
        b"availability.row_count",
        b"availability.schema_version",
        b"availability.source_ceiling",
        b"availability.verified_source_inventory_root",
    }
)


class AvailabilityDocumentError(ValueError):
    """Raised when an availability document cannot be admitted as written."""


def require_sha256(value: str, label: str) -> str:
    """Return `value` if it is a lowercase SHA-256 hex digest, else raise."""
    if len(value) != _SHA256_LENGTH or not set(value) <= _HEX_ALPHABET:
        raise AvailabilityDocumentError(f"{label} must be a lowercase SHA-256 digest")
    return value


def require_object_key(value: str, label: str) -> str:
    """Return `value` if it is a relative, traversal-free object key, else raise."""
    if not value or value != value.strip("/") or "\\" in value or ".." in value.split("/"):
        raise AvailabilityDocumentError(f"{label} must be a relative safe object key")
    return value


def format_datetime(value: datetime) -> str:
    """Render one UTC instant exactly as the sibling's pointer and receipt documents do."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise AvailabilityDocumentError("an availability timestamp must be timezone-aware UTC")
    return f"{value.isoformat(timespec='microseconds')[:-6]}Z"


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    """One object key and its required byte digest."""

    key: str
    sha256: str

    def __post_init__(self) -> None:
        require_object_key(self.key, "receipt key")
        require_sha256(self.sha256, "receipt sha256")

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
        if not self.lane_root.startswith("layer=") or self.lane_root != self.lane_root.strip("/"):
            raise AvailabilityDocumentError("lane_root must be a relative layer=... object prefix")
        for name in ("lane", "product"):
            value = str(getattr(self, name))
            if not value or value != value.strip() or "/" in value:
                raise AvailabilityDocumentError(f"{name} must be a safe non-empty lane identifier")
        if not self.required_rungs or tuple(sorted(set(self.required_rungs))) != tuple(self.required_rungs):
            raise AvailabilityDocumentError("required_rungs must be a sorted, unique, non-empty ladder")
        require_sha256(self.verified_source_inventory_root, "verified_source_inventory_root")


@dataclass(frozen=True, slots=True)
class AvailabilityConfig:
    """Generation identity plus its immutable bootstrap binding and current ceiling."""

    identity: AvailabilityIdentity
    source_ceiling: date
    bootstrap_receipt: EvidenceReceipt


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
        if self.terminal_state not in ("published", "governed_absence"):
            raise AvailabilityDocumentError("terminal_state must be published or governed_absence")
        if self.day > self.source_ceiling:
            raise AvailabilityDocumentError("availability day cannot exceed its source ceiling")
        format_datetime(self.published_at)
        receipt_keys = tuple(receipt.key for receipt in self.data_receipts)
        if receipt_keys != tuple(sorted(set(receipt_keys))):
            raise AvailabilityDocumentError("data_receipts must be sorted by unique object key")
        self._validate_terminal_payload()

    def _validate_terminal_payload(self) -> None:
        """Refuse any published/absent shape the sibling's admission rules would not accept."""
        if self.terminal_state == "published":
            if self.absence_reason is not None:
                raise AvailabilityDocumentError("a published availability row cannot carry an absence reason")
            if self.completion_receipt is None:
                raise AvailabilityDocumentError("a published availability row requires a completion receipt")
            if self._is_published_empty_rung():
                return
            if self.row_count <= 0:
                raise AvailabilityDocumentError("a published availability row must carry a positive row_count")
            return
        if self.row_count != 0:
            raise AvailabilityDocumentError("a governed absence must carry row_count=0")
        if self.data_receipts or self.completion_receipt is not None:
            raise AvailabilityDocumentError("a governed absence cannot carry data or completion receipts")
        if self.absence_reason is None or self.absence_reason != self.absence_reason.strip() or not self.absence_reason:
            raise AvailabilityDocumentError("a governed absence requires a non-blank, trimmed reason")

    def _is_published_empty_rung(self) -> bool:
        """True for the one published shape carrying no rows: a DERIVED rung that generalised to none.

        The base rung is excluded: its emptiness is a governed absence with its own marker, and
        mixing the two vocabularies makes a day unselectable at every rung rather than merely
        unindexed.
        """
        return self.rung != BASE_PARTITION_ZOOM and self.row_count == 0 and not self.data_receipts

    @property
    def grain(self) -> tuple[date, int]:
        """Return the unique generation grain."""
        return self.day, self.rung

    @property
    def provenance(self) -> AvailabilityProvenance:
        """Classify how well this row's parts are proven, FROM ITS SHAPE ALONE."""
        if self.terminal_state == "published" and self.row_count > 0 and not self.data_receipts:
            return MANIFEST_TRUSTED_PROVENANCE
        return DIGESTED_PROVENANCE

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON and Arrow-compatible projection."""
        completion = self.completion_receipt
        return {
            "absence_reason": self.absence_reason,
            "completion_receipt_key": None if completion is None else completion.key,
            "completion_receipt_sha256": None if completion is None else completion.sha256,
            "data_receipts": [receipt.to_wire() for receipt in self.data_receipts],
            "day": self.day.isoformat(),
            "lane": self.lane,
            "nature": self.nature,
            "product": self.product,
            "published_at": format_datetime(self.published_at),
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
            raise AvailabilityDocumentError(f"schema_version must be {AVAILABILITY_SCHEMA_VERSION}")
        if self.required_rungs != self.identity.required_rungs:
            raise AvailabilityDocumentError("pointer required_rungs do not match its identity")
        require_sha256(self.generation_sha256, "generation_sha256")
        require_sha256(self.generation_receipt_sha256, "generation_receipt_sha256")
        if generation_sha_from_key(self.identity.lane_root, self.generation_key) != self.generation_sha256:
            raise AvailabilityDocumentError("generation_key digest does not match generation_sha256")
        if not 0 < self.generation_bytes <= GENERATION_MAX_BYTES:
            raise AvailabilityDocumentError("pointer generation_bytes exceeds the bounded generation contract")
        if not 0 < self.rows <= MAX_AVAILABILITY_ROWS:
            raise AvailabilityDocumentError("pointer rows exceed the bounded generation contract")
        if self.earliest_terminal_day > self.latest_terminal_day:
            raise AvailabilityDocumentError("pointer terminal-day range is inverted")
        if self.latest_terminal_day > self.source_ceiling:
            raise AvailabilityDocumentError("pointer latest terminal day exceeds source ceiling")
        require_prior_binding(self.prior_generation_key, self.prior_generation_sha256, self.identity.lane_root)
        format_datetime(self.created_at)

    def to_wire(self) -> dict[str, object]:
        """Return the exact pointer document."""
        return {
            "bootstrap_receipt_key": self.bootstrap_receipt.key,
            "bootstrap_receipt_sha256": self.bootstrap_receipt.sha256,
            "created_at": format_datetime(self.created_at),
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

    def to_json_bytes(self) -> bytes:
        """Serialize the pointer as canonical JSON, refusing anything past its byte ceiling."""
        payload = canonical_json(self.to_wire()).encode("utf-8")
        if len(payload) > POINTER_MAX_BYTES:
            raise AvailabilityDocumentError("availability pointer exceeds its byte ceiling")
        return payload


def lane_identity_for(layer: str, kind: PartitionKind, *, verified_source_inventory_root: str) -> AvailabilityIdentity:
    """Build the identity of one physical lane root, with the full ladder as its rung contract."""
    lane_root = availability_lane_root(layer, kind)
    return AvailabilityIdentity(
        lane_root=lane_root,
        lane=layer,
        product=kind,
        nature="daily_series",
        required_rungs=AVAILABILITY_REQUIRED_RUNGS,
        verified_source_inventory_root=verified_source_inventory_root,
    )


def generation_key_for(layer: str, kind: PartitionKind, generation_sha256: str) -> str:
    """Return the immutable content-addressed generation key for one lane."""
    return availability_generation_path(layer, kind, require_sha256(generation_sha256, "generation sha256"))


def generation_sha_from_key(lane_root: str, key: str) -> str:
    """Read the content digest back out of a generation key, refusing a key outside the layout."""
    prefix = f"{lane_root}/availability/generation="
    suffix = "/availability.parquet"
    if not key.startswith(prefix) or not key.endswith(suffix):
        raise AvailabilityDocumentError("generation key is outside the lane's immutable availability layout")
    return require_sha256(key[len(prefix) : -len(suffix)], "generation key digest")


def require_prior_binding(key: str | None, sha256: str | None, lane_root: str) -> None:
    """Refuse a prior-generation binding whose key and digest disagree, or that is half present."""
    if key is None and sha256 is None:
        return
    if key is None or sha256 is None:
        raise AvailabilityDocumentError("prior generation key and SHA-256 must both be null or both be present")
    require_sha256(sha256, "prior_generation_sha256")
    if generation_sha_from_key(lane_root, key) != sha256:
        raise AvailabilityDocumentError("prior generation key digest does not match prior_generation_sha256")


def generation_receipt_sha256(
    config: AvailabilityConfig,
    rows: Sequence[AvailabilityRow],
    *,
    prior_generation_key: str | None,
    prior_generation_sha256: str | None,
    created_at: datetime,
) -> str:
    """Digest the whole generation claim, so the pointer and the Parquet metadata cannot disagree."""
    payload = {
        "bootstrap_receipt": config.bootstrap_receipt.to_wire(),
        "created_at": format_datetime(created_at),
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


def generation_metadata(
    config: AvailabilityConfig,
    *,
    receipt_sha256: str,
    row_span: tuple[int, date, date],
    prior_generation: tuple[str | None, str | None],
    created_at: datetime,
) -> dict[bytes, bytes]:
    """Return the seventeen `availability.*` Parquet metadata keys, spelled as the sibling spells them."""
    row_count, earliest_terminal_day, latest_terminal_day = row_span
    prior_generation_key, prior_generation_sha256 = prior_generation
    values = {
        "bootstrap_receipt_key": config.bootstrap_receipt.key,
        "bootstrap_receipt_sha256": config.bootstrap_receipt.sha256,
        "created_at": format_datetime(created_at),
        "earliest_terminal_day": earliest_terminal_day.isoformat(),
        "generation_receipt_sha256": receipt_sha256,
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


def selectable_days(pointer: AvailabilityPointer, rows: Sequence[AvailabilityRow]) -> tuple[date, ...]:
    """Return days whose exact authoritative rung set agrees on one terminal state."""
    by_day: dict[date, list[AvailabilityRow]] = {}
    for row in rows:
        by_day.setdefault(row.day, []).append(row)
    expected = pointer.required_rungs
    selectable: list[date] = []
    for day in sorted(by_day):
        day_rows = sorted(by_day[day], key=lambda item: expected.index(item.rung) if item.rung in expected else -1)
        if tuple(row.rung for row in day_rows) != expected:
            continue
        states = {row.terminal_state for row in day_rows}
        source_receipts = {row.source_receipt.sha256 for row in day_rows}
        absence_reasons = {row.absence_reason for row in day_rows}
        if len(states) == 1 and len(source_receipts) == 1 and len(absence_reasons) == 1:
            selectable.append(day)
    return tuple(selectable)
