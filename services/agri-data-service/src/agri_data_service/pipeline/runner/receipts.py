"""Per stream-day turn receipts: the source digest (S11), unit counts and input digests a day was written from.

Stored beside the source checkpoints, outside every `layer=` prefix, so no census or serving walk
ever reads one as a partition. See `pipeline/runner/AGENTS.md` "Turn receipts".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal, Protocol

from agri_data_service.foundation.canonical import canonical_json

if TYPE_CHECKING:
    from collections.abc import Mapping

TURN_RECEIPT_PREFIX: Final = "lane-turn-receipts/v1"
#: v2 carries `publication_state` and `source_resolved`; every reader before v2 refuses it (rollback note in AGENTS.md).
TURN_RECEIPT_VERSION: Final = "lane-turn-receipt-v2"
#: v1 is still read: without `publication_state` it is complete, and with it (696f1ae5..v2) it is read as written.
TURN_RECEIPT_READABLE_VERSIONS: Final = frozenset({"lane-turn-receipt-v1", TURN_RECEIPT_VERSION})
TURN_RECEIPT_MAX_BYTES: Final = 64 * 1024
_CONTENT_TYPE: Final = "application/json"


class ConditionalObjectStorage(Protocol):
    """The conditional object calls a receipt needs; `pipeline/parquet/availability_storage.py` satisfies it."""

    def read(self, key: str, *, max_bytes: int) -> object | None: ...

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool: ...


class TurnReceiptError(RuntimeError):
    """A receipt could not be decoded, or a write lost its compare-and-swap."""


@dataclass(frozen=True, slots=True)
class DayReceipt:
    """What one stream-day was last written from; the S11 rewrite rules compare against it."""

    stream: str
    day: date
    lane: str
    outcome: str
    source_digest: str | None = None
    present_units: int | None = None
    expected_units: int | None = None
    expected_unit_ids: frozenset[str] | None = None
    present_unit_ids: frozenset[str] | None = None
    input_digests: Mapping[str, str] = field(default_factory=dict)
    #: Input streams a transform's rebuild superseded and pruned; kept so the audit outlives the partition.
    pruned_inputs: Mapping[str, str] = field(default_factory=dict)
    run_id: str = ""
    recorded_at: datetime | None = None
    publication_state: Literal["pending", "complete"] = "complete"
    #: `Written.source_resolved`: False never earns a completeness proof, however complete the coverage.
    source_resolved: bool = True

    def __post_init__(self) -> None:
        if self.publication_state not in {"pending", "complete"}:
            raise TurnReceiptError("turn receipt has an unknown publication state")
        if not isinstance(self.source_resolved, bool):
            raise TurnReceiptError("turn receipt has a non-boolean source resolution")
        expected, present = self.expected_unit_ids, self.present_unit_ids
        if expected is None and present is None:
            return
        if (
            not expected
            or present is None
            or not present <= expected
            or len(expected) != self.expected_units
            or len(present) != self.present_units
            or any(not unit for unit in expected)
        ):
            raise TurnReceiptError("turn receipt coverage identities disagree with its unit counts")

    def to_payload(self) -> bytes:
        """Canonical JSON bytes, bounded."""
        recorded_at = self.recorded_at or datetime.now(UTC)
        value = {
            "schema_version": TURN_RECEIPT_VERSION,
            "stream": self.stream,
            "day": self.day.isoformat(),
            "lane": self.lane,
            "outcome": self.outcome,
            "source_digest": self.source_digest,
            "present_units": self.present_units,
            "expected_units": self.expected_units,
            "expected_unit_ids": None if self.expected_unit_ids is None else sorted(self.expected_unit_ids),
            "present_unit_ids": None if self.present_unit_ids is None else sorted(self.present_unit_ids),
            "input_digests": dict(sorted(self.input_digests.items())),
            "pruned_inputs": dict(sorted(self.pruned_inputs.items())),
            "run_id": self.run_id,
            "recorded_at": recorded_at.astimezone(UTC).isoformat(),
            "publication_state": self.publication_state,
            "source_resolved": self.source_resolved,
        }
        payload = canonical_json(value).encode("utf-8")
        if len(payload) > TURN_RECEIPT_MAX_BYTES:
            raise TurnReceiptError(f"turn receipt for {self.stream} {self.day.isoformat()} exceeds its ceiling")
        return payload

    @classmethod
    def from_payload(cls, payload: bytes) -> DayReceipt:
        """Decode one stored v1 or v2 receipt, refusing any other schema version."""
        try:
            value = json.loads(payload)
        except ValueError as error:
            raise TurnReceiptError("turn receipt is not JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") not in TURN_RECEIPT_READABLE_VERSIONS:
            raise TurnReceiptError("turn receipt has an unknown schema version")
        recorded_at = value.get("recorded_at")
        return cls(
            stream=str(value["stream"]),
            day=date.fromisoformat(str(value["day"])),
            lane=str(value["lane"]),
            outcome=str(value["outcome"]),
            source_digest=value.get("source_digest"),
            present_units=value.get("present_units"),
            expected_units=value.get("expected_units"),
            expected_unit_ids=_unit_ids(value.get("expected_unit_ids")),
            present_unit_ids=_unit_ids(value.get("present_unit_ids")),
            input_digests=dict(value.get("input_digests") or {}),
            pruned_inputs=dict(value.get("pruned_inputs") or {}),
            run_id=str(value.get("run_id", "")),
            recorded_at=datetime.fromisoformat(recorded_at) if isinstance(recorded_at, str) else None,
            publication_state=value.get("publication_state", "complete"),
            source_resolved=value.get("source_resolved", True),
        )


def _unit_ids(value: object) -> frozenset[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or any(not isinstance(unit, str) or not unit for unit in value):
        raise TurnReceiptError("turn receipt has invalid coverage identities")
    if len(set(value)) != len(value):
        raise TurnReceiptError("turn receipt has duplicate coverage identities")
    return frozenset(value)


#: A transform lane's own per-day receipt lives under this prefix plus the lane id; no stream slug starts with `_`.
TRANSFORM_RECEIPT_STREAM_PREFIX: Final = "_lane."


def transform_receipt_stream(lane_id: str) -> str:
    """The receipt key a transform lane keeps its lane-level day receipt under (M5)."""
    return f"{TRANSFORM_RECEIPT_STREAM_PREFIX}{lane_id}"


def turn_receipt_key(stream: str, day: date) -> str:
    """Where one stream-day's receipt lives."""
    return f"{TURN_RECEIPT_PREFIX}/{stream}/{day.isoformat()}.json"


@dataclass(frozen=True, slots=True)
class TurnReceipts:
    """Read and replace stream-day receipts through conditional object storage."""

    storage: ConditionalObjectStorage

    def read(self, stream: str, day: date) -> DayReceipt | None:
        """The stored receipt, or `None` when the day was never written by the runner."""
        stored = self.storage.read(turn_receipt_key(stream, day), max_bytes=TURN_RECEIPT_MAX_BYTES)
        if stored is None:
            return None
        payload = getattr(stored, "payload", None)
        if not isinstance(payload, bytes):
            raise TurnReceiptError(f"turn receipt for {stream} {day.isoformat()} has no payload")
        return DayReceipt.from_payload(payload)

    def write(self, receipt: DayReceipt) -> None:
        """Replace the stream-day's receipt; raises when a concurrent writer won the swap."""
        key = turn_receipt_key(receipt.stream, receipt.day)
        stored = self.storage.read(key, max_bytes=TURN_RECEIPT_MAX_BYTES)
        expected_etag = None if stored is None else getattr(stored, "etag", None)
        if not self.storage.compare_and_swap(
            key, receipt.to_payload(), expected_etag=expected_etag, content_type=_CONTENT_TYPE
        ):
            raise TurnReceiptError(f"turn receipt for {receipt.stream} {receipt.day.isoformat()} lost its swap")


__all__ = [
    "TRANSFORM_RECEIPT_STREAM_PREFIX",
    "TURN_RECEIPT_PREFIX",
    "TURN_RECEIPT_READABLE_VERSIONS",
    "TURN_RECEIPT_VERSION",
    "ConditionalObjectStorage",
    "DayReceipt",
    "TurnReceiptError",
    "TurnReceipts",
    "transform_receipt_stream",
    "turn_receipt_key",
]
