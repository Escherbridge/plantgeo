"""The content-addressed typed evidence a lane-day must produce before it may be published.

Rationale and the contracts these enforce: see `AGENTS.md` in this directory, "Availability index".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.availability_documents import (
    AvailabilityIdentity,
    AvailabilityRow,
    EvidenceReceipt,
    _is_published_empty_rung,
    _require_sorted_nonempty_receipts,
    _validate_data_receipt_collection,
    availability_row_provenance,
)
from agri_data_service.pipeline.parquet.availability_primitives import (
    BOOTSTRAP_INVENTORY_SCHEMA_VERSION,
    BOOTSTRAP_RECEIPT_MAX_BYTES,
    DIGESTED_PROVENANCE,
    MANIFEST_TRUSTED_PROVENANCE,
    PROVENANCE_FIELD,
    SOURCE_EVIDENCE_SCHEMA_VERSION,
    TERMINAL_EVIDENCE_SCHEMA_VERSION,
    TYPED_RECEIPT_MAX_BYTES,
    AvailabilityMalformedError,
    AvailabilityProvenance,
    TerminalState,
    _format_datetime,
    _require_rung,
    _require_utc,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date, datetime


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
