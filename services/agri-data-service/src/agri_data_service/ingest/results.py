"""The ingestion job result contract and the per-job isolation that keeps one failure from erasing the rest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

import structlog
from sqlalchemy.exc import SQLAlchemyError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

logger = structlog.get_logger()

JobStatus = Literal["ingested", "skipped", "failed"]

NO_DETAILS: Final[Mapping[str, int]] = MappingProxyType({})
UNKNOWN_FAILURE_REASON: Final = "unknown ingestion failure"
REDACTED_PLACEHOLDER: Final = "[redacted]"
FAILURE_REASON_MAX_LENGTH: Final = 500

# FIRMS embeds its API key in the request PATH (/api/area/csv/<MAP_KEY>/...), not in a query string, and
# a DSN embeds its password, so an httpx error carrying either publishes a live credential to every
# operator who reads a cron log. Each alternative substitutes a whole whitespace-delimited token rather
# than parsing it, because a partial match leaves the secret in the half that survived: scheme-shaped,
# user@host-shaped, and a bare query tail for a message that names a key without naming its scheme.
#
# Kept deliberately identical to jobs/lease.py::_SECRET_SHAPED and NOT shared with it: `jobs` is the
# reusable primitive `ingest` builds on, so importing back the other way would invert the layering.
# Change both together.
_SECRET_SHAPED = re.compile(r"[a-z][a-z0-9+.\-]*://\S+|\S+@\S+|\?\S+", re.IGNORECASE)


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
    """Substitute every URL-shaped, user@host-shaped and query-shaped token, whole, before it is reported."""
    return _SECRET_SHAPED.sub(REDACTED_PLACEHOLDER, value)


def failure_reason(error: Exception) -> str:
    """Describe a job failure without echoing a statement, a payload, a DSN, or an API-keyed URL."""
    if isinstance(error, SQLAlchemyError):
        # The SQLAlchemy message carries the whole statement and its bound parameters.
        return f"ingest write failed ({error.__class__.__name__})"
    # Redact before clamping, so a clamp can never be what spares a secret from substitution.
    message = redact_secrets(str(error).strip())[:FAILURE_REASON_MAX_LENGTH].strip()
    return message or UNKNOWN_FAILURE_REASON


async def run_isolated_job(source: str, run: Callable[[], Awaitable[IngestionJobResult]]) -> IngestionJobResult:
    """Run one job so its failure becomes a failed result rather than erasing the other jobs' progress."""
    try:
        return await run()
    except Exception as error:
        # Deliberate per-job boundary: one source's failure must not abort the other five.
        reason = failure_reason(error)
        logger.warning("ingestion_job_failed", source=source, error=reason, error_type=error.__class__.__name__)
        return IngestionJobResult(source=source, status="failed", records_seen=0, records_written=0, reason=reason)


def any_job_failed(results: Sequence[IngestionJobResult]) -> bool:
    """True when at least one job failed, which is what turns the cron run's exit code non-zero."""
    return any(result.status == "failed" for result in results)
