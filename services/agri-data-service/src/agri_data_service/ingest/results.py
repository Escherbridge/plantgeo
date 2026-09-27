"""The ingestion job result contract and the per-job isolation that keeps one failure from erasing the rest."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.foundation.observability.redaction import redact_strict

if TYPE_CHECKING:
    from collections.abc import Mapping

JobStatus = Literal["ingested", "skipped", "failed"]

NO_DETAILS: Final[Mapping[str, int]] = MappingProxyType({})
UNKNOWN_FAILURE_REASON: Final = "unknown ingestion failure"
FAILURE_REASON_MAX_LENGTH: Final = 500


@dataclass(frozen=True, slots=True)
class IngestionJobResult:
    """One job's outcome: what it saw, what it wrote, and why it did not write more."""

    source: str
    status: JobStatus
    records_seen: int
    records_written: int
    truncated: bool | None = None
    reason: str | None = None
    details: Mapping[str, int] = field(default=NO_DETAILS)

    def to_summary(self) -> dict[str, object]:
        """Render the operator-facing JSON object, omitting the optional fields that are unset."""
        summary: dict[str, object] = {
            "source": self.source,
            "status": self.status,
            "records_seen": self.records_seen,
            "records_written": self.records_written,
        }
        if self.truncated is not None:
            summary["truncated"] = self.truncated
        if self.reason is not None:
            summary["reason"] = self.reason
        if self.details:
            summary["details"] = dict(self.details)
        return summary


def skipped_result(source: str, reason: str) -> IngestionJobResult:
    """Build the "nothing to do, and that is fine" outcome; a skip is never a failure."""
    return IngestionJobResult(source=source, status="skipped", records_seen=0, records_written=0, reason=reason)


def redact_secrets(value: str) -> str:
    """Substitute every URL-shaped, user@host-shaped and query-shaped token, whole, before it is reported.

    Re-exports `foundation.observability.redaction.redact_strict` (GL-1; see its AGENTS.md
    "Redaction"). Kept as a thin wrapper, not an import-and-drop, so every existing caller and test
    of this name keeps working unchanged.
    """
    return redact_strict(value)


def failure_reason(error: Exception) -> str:
    """Describe a job failure without echoing a statement, a payload, a DSN, or an API-keyed URL."""
    if isinstance(error, SQLAlchemyError):
        # The SQLAlchemy message carries the whole statement and its bound parameters.
        return f"ingest write failed ({error.__class__.__name__})"
    # Redact before clamping, so a clamp can never be what spares a secret from substitution.
    message = redact_secrets(str(error).strip())[:FAILURE_REASON_MAX_LENGTH].strip()
    return message or UNKNOWN_FAILURE_REASON
