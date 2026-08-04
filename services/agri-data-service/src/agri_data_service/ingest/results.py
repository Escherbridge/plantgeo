"""The ingestion job result contract and the per-job isolation that keeps one failure from erasing the rest."""

from __future__ import annotations

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


def failure_reason(error: Exception) -> str:
    """Describe a job failure without echoing a statement, a payload, or an API-keyed URL."""
    if isinstance(error, SQLAlchemyError):
        return f"ingest write failed ({error.__class__.__name__})"
    message = str(error).strip()
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
