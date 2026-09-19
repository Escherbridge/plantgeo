"""What is INSIDE a completion marker and a governed-absence marker; their keys live in `parquet_paths`.

Layer L0: stdlib only. A COPY of agri-data-service's `foundation/parquet/completion.py` and
`foundation/parquet/absence.py` payload halves, held honest by `tests/test_parquet_markers_parity.py`.
Why completion is ASSERTED by a separate object rather than inferred lives in `AGENTS.md`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

#: What a marker carrying no per-part digests declares.
COMPLETION_SCHEMA_VERSION: Final = 1

#: What a marker carrying `parts` declares: the writer hashed each part as it uploaded it, so a
#: later reader binds the day's parts by key and sha256 without downloading one.
COMPLETION_PARTS_SCHEMA_VERSION: Final = 2

ABSENCE_SCHEMA_VERSION: Final = 1

#: Serialized ONLY WHEN TRUE, and the same for `parts`. Every marker already in the bucket must
#: round-trip byte for byte, and the sibling's verifier re-serializes a stored marker and refuses any
#: difference, so a `derived_empty: false` key on an ordinary marker would fail verification.
DERIVED_EMPTY_FIELD: Final = "derived_empty"
PARTS_FIELD: Final = "parts"

_SHA256_LENGTH: Final = 64
_SHA256_ALPHABET: Final = frozenset("0123456789abcdef")


class PartitionCompletionError(ValueError):
    """Raised when a completion payload is incomplete or a marker object cannot be decoded."""


class GovernedAbsenceError(ValueError):
    """Raised when an absence payload is incomplete or a marker object cannot be decoded."""


@dataclass(frozen=True, slots=True)
class CompletedPart:
    """One part file the export uploaded, as the export itself measured it."""

    relative_path: str
    row_count: int
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.relative_path.strip() or self.relative_path != self.relative_path.strip():
            raise PartitionCompletionError("a completion part requires a trimmed non-empty relative path")
        if self.row_count <= 0:
            raise PartitionCompletionError(f"a completion part holds at least one row, got {self.row_count}")
        if self.byte_count <= 0:
            raise PartitionCompletionError(f"a completion part holds at least one byte, got {self.byte_count}")
        if len(self.sha256) != _SHA256_LENGTH or not set(self.sha256) <= _SHA256_ALPHABET:
            raise PartitionCompletionError("a completion part digest must be a lowercase SHA-256 hex digest")

    def to_wire(self) -> dict[str, object]:
        """Return the canonical JSON projection."""
        return {
            "byte_count": self.byte_count,
            "relative_path": self.relative_path,
            "row_count": self.row_count,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class PartitionCompletion:
    """The receipt a finished stream-day writes LAST: what the export uploaded, and under which run."""

    part_count: int
    row_count: int
    completed_at: datetime
    run_id: str
    #: True for a DERIVED rung that generalised every base row away, so it honestly holds no parts.
    derived_empty: bool = False
    parts: tuple[CompletedPart, ...] = ()

    def __post_init__(self) -> None:
        if self.part_count < 0 or self.row_count < 0:
            raise PartitionCompletionError("a completion receipt cannot claim a negative count")
        if not self.run_id.strip():
            raise PartitionCompletionError("a completion receipt requires a non-blank run_id")
        if self.completed_at.tzinfo is None:
            raise PartitionCompletionError("completed_at must be timezone-aware")
        if self.derived_empty and (self.part_count or self.row_count or self.parts):
            raise PartitionCompletionError("a derived-empty receipt cannot also claim parts or rows")
        if self.parts and len(self.parts) != self.part_count:
            raise PartitionCompletionError("a completion receipt naming parts must name every part it counted")

    @property
    def schema_version(self) -> int:
        """Return which of the two conventions this receipt serializes under: `parts` decides."""
        return COMPLETION_PARTS_SCHEMA_VERSION if self.parts else COMPLETION_SCHEMA_VERSION

    def to_json_bytes(self) -> bytes:
        """Serialize the receipt as canonical UTF-8 JSON with sorted keys and a UTC timestamp."""
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "part_count": self.part_count,
            "row_count": self.row_count,
            "completed_at": self.completed_at.astimezone(UTC).isoformat(),
            "run_id": self.run_id,
        }
        if self.derived_empty:
            payload[DERIVED_EMPTY_FIELD] = True
        if self.parts:
            payload[PARTS_FIELD] = [part.to_wire() for part in self.parts]
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class GovernedAbsence:
    """Why one stream-day is deliberately empty, justified by the upstream that refused it."""

    reason: str
    upstream_response: str
    recorded_at: datetime
    run_id: str

    def __post_init__(self) -> None:
        for field_name in ("reason", "upstream_response", "run_id"):
            if not str(getattr(self, field_name)).strip():
                raise GovernedAbsenceError(f"a governed absence requires a non-blank {field_name}")
        if self.recorded_at.tzinfo is None:
            raise GovernedAbsenceError("recorded_at must be timezone-aware")

    def to_json_bytes(self) -> bytes:
        """Serialize the evidence as canonical UTF-8 JSON with sorted keys and a UTC timestamp."""
        payload = {
            "schema_version": ABSENCE_SCHEMA_VERSION,
            "reason": self.reason,
            "upstream_response": self.upstream_response,
            "recorded_at": self.recorded_at.astimezone(UTC).isoformat(),
            "run_id": self.run_id,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
