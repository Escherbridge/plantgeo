"""Governed-absence payload: the evidence a deliberately empty stream-day must carry.

Layer L0: stdlib only. The marker's object KEY lives in `paths.py`; this module owns what is
inside the marker. One convention for every stream — no lane defines its own (RUNBOOK §0.25.3).
An absence without evidence is indistinguishable from a silent failure, so every field here is
mandatory and non-blank.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

ABSENCE_SCHEMA_VERSION: Final = 1


class GovernedAbsenceError(ValueError):
    """Raised when an absence payload is incomplete or a marker object cannot be decoded."""


@dataclass(frozen=True, slots=True)
class GovernedAbsence:
    """Why one stream-day is deliberately empty, justified by the upstream that refused it."""

    reason: str
    upstream_response: str
    recorded_at: datetime
    run_id: str

    def __post_init__(self) -> None:
        for field_name in ("reason", "upstream_response", "run_id"):
            if not getattr(self, field_name).strip():
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

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> GovernedAbsence:
        """Decode a marker object, refusing anything that is not this convention's shape."""
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GovernedAbsenceError(f"absence marker is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise GovernedAbsenceError("absence marker must decode to a JSON object")
        version = decoded.get("schema_version")
        if version != ABSENCE_SCHEMA_VERSION:
            raise GovernedAbsenceError(f"absence marker schema_version {version!r} is not {ABSENCE_SCHEMA_VERSION}")
        missing = [name for name in ("reason", "upstream_response", "recorded_at", "run_id") if name not in decoded]
        if missing:
            raise GovernedAbsenceError(f"absence marker is missing {', '.join(missing)}")
        try:
            recorded_at = datetime.fromisoformat(str(decoded["recorded_at"]))
        except ValueError as exc:
            raise GovernedAbsenceError(f"absence marker recorded_at is not ISO-8601: {exc}") from exc
        return cls(
            reason=str(decoded["reason"]),
            upstream_response=str(decoded["upstream_response"]),
            recorded_at=recorded_at,
            run_id=str(decoded["run_id"]),
        )
