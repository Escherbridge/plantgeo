"""Reusable execution framework for resumable chunked source lanes.

See ``execution/AGENTS.md`` section "Chunked source-lane workflow".
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

_PENDING_CHUNK_PREVIEW = 8


class ChunkedLaneBackfillError(Exception):
    """A bounded fetch ended with one or more explicit chunk failures."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error", "chunked_lane_failed")))
        self.payload = payload


class LaneChunk(Protocol):
    """One bounded unit of work a chunked lane fetches."""

    @property
    def key(self) -> str: ...


class LaneReceipt(Protocol):
    """One chunk a lane already completed."""

    @property
    def chunk_key(self) -> str: ...


class LanePlan[ChunkT: LaneChunk](Protocol):
    """The reviewed-plan surface read by the shared driver."""

    @property
    def cells(self) -> Sequence[object]: ...

    @property
    def chunks(self) -> Sequence[ChunkT]: ...


class LaneCheckpoint(Protocol):
    """Durable resumable state rewritten after every recorded chunk."""

    @property
    def state(self) -> str: ...

    @property
    def reason(self) -> str | None: ...

    @property
    def receipts(self) -> Sequence[LaneReceipt]: ...

    def model_copy(self, *, update: Mapping[str, Any]) -> Self: ...


class LaneChunkRunner[PlanT, ChunkT, ResultT](Protocol):
    """Bounded-concurrency wave runner exposed by a chunked lane."""

    async def __call__(
        self,
        plan: PlanT,
        chunks: Sequence[ChunkT],
        *,
        concurrency: int,
    ) -> Sequence[ResultT | BaseException]: ...


@dataclass(frozen=True)
class ChunkedLane[ChunkT: LaneChunk, PlanT: LanePlan[Any], CheckpointT: LaneCheckpoint, ResultT]:
    """Bind one chunked fetch lane to the common checkpointed workflow."""

    error_token: str
    parse_plan: Callable[[bytes], PlanT]
    plan_checksum: Callable[[PlanT], str]
    checkpoint_path: Callable[[Path, PlanT], Path]
    initialize_checkpoint: Callable[[PlanT], CheckpointT]
    read_checkpoint: Callable[[Path], CheckpointT]
    rederive_checkpoint: Callable[[PlanT, CheckpointT], CheckpointT]
    write_checkpoint: Callable[[Path, CheckpointT], None]
    load_cached_result: Callable[[Path, PlanT, ChunkT], ResultT | None]
    cache_result: Callable[[Path, PlanT, ResultT], object]
    record_result: Callable[[PlanT, CheckpointT, ResultT], CheckpointT]
    run_chunks: LaneChunkRunner[PlanT, ChunkT, ResultT]
    release_manifest: Callable[[PlanT, CheckpointT], str]
    failure_reason: Callable[[BaseException], str]
    status_identity: Callable[[PlanT], dict[str, Any]]
    status_totals: Callable[[PlanT, CheckpointT], dict[str, Any]]
    backfill_extras: Callable[[PlanT, CheckpointT], dict[str, Any]]

    def load_checkpoint(self, plan: PlanT, checkpoint_path_value: Path) -> CheckpointT:
        """Load a checkpoint and rederive its state from receipts."""
        if not checkpoint_path_value.exists():
            return self.initialize_checkpoint(plan)
        return self.rederive_checkpoint(plan, self.read_checkpoint(checkpoint_path_value))

    def write_blocked_checkpoint(self, path: Path, checkpoint: CheckpointT, exc: Exception) -> None:
        """Record why a run stopped so resume starts from durable evidence."""
        with suppress(OSError, ValueError):
            self.write_checkpoint(
                path,
                checkpoint.model_copy(
                    update={
                        "state": "blocked",
                        "updated_at": datetime.now(UTC),
                        "reason": self.failure_reason(exc),
                    }
                ),
            )

    async def fetch_chunks(  # noqa: PLR0913 - explicit workflow state and bounded execution controls
        self,
        execution_root: Path,
        plan: PlanT,
        checkpoint: CheckpointT,
        checkpoint_path_value: Path,
        chunks: Sequence[ChunkT],
        concurrency: int,
    ) -> tuple[CheckpointT, list[dict[str, str]]]:
        """Reuse local cache, fetch bounded waves, and persist each receipt."""
        failures: list[dict[str, str]] = []
        pending: list[ChunkT] = []
        for chunk in chunks:
            cached = self.load_cached_result(execution_root, plan, chunk)
            if cached is None:
                pending.append(chunk)
                continue
            checkpoint = self.record_result(plan, checkpoint, cached)
            self.write_checkpoint(checkpoint_path_value, checkpoint)
        for start in range(0, len(pending), concurrency):
            wave = pending[start : start + concurrency]
            results = await self.run_chunks(plan, wave, concurrency=concurrency)
            for chunk, result in zip(wave, results, strict=True):
                if isinstance(result, BaseException):
                    failures.append({"chunk_key": chunk.key, "reason": self.failure_reason(result)})
                    continue
                self.cache_result(execution_root, plan, result)
                checkpoint = self.record_result(plan, checkpoint, result)
                self.write_checkpoint(checkpoint_path_value, checkpoint)
            if failures:
                break
        return checkpoint, failures

    def status_payload(self, execution_root: Path, plan_path: Path) -> dict[str, Any]:
        """Return completed and pending chunks without fetching warehouse data."""
        plan = self.parse_plan(plan_path.read_bytes())
        checkpoint_path_value = self.checkpoint_path(execution_root, plan)
        checkpoint = self.load_checkpoint(plan, checkpoint_path_value)
        completed = {receipt.chunk_key for receipt in checkpoint.receipts}
        pending = [chunk.key for chunk in plan.chunks if chunk.key not in completed]
        return {
            "plan_checksum": self.plan_checksum(plan),
            "checkpoint": str(checkpoint_path_value),
            "state": checkpoint.state,
            "reason": checkpoint.reason,
            **self.status_identity(plan),
            "cell_count": len(plan.cells),
            "chunk_count": len(plan.chunks),
            "completed_chunk_count": len(completed),
            "pending_chunk_count": len(pending),
            "pending_chunks": pending[:_PENDING_CHUNK_PREVIEW],
            **self.status_totals(plan, checkpoint),
        }

    async def backfill_payload(
        self,
        execution_root: Path,
        plan_path: Path,
        max_chunks: int | None,
        concurrency: int,
    ) -> dict[str, Any]:
        """Run a bounded resumable fetch and return its complete receipt payload."""
        failures: list[dict[str, str]] = []
        try:
            plan = self.parse_plan(plan_path.read_bytes())
            checkpoint_path_value = self.checkpoint_path(execution_root, plan)
            checkpoint = self.load_checkpoint(plan, checkpoint_path_value)
            self.write_checkpoint(checkpoint_path_value, checkpoint)
            completed = {receipt.chunk_key for receipt in checkpoint.receipts}
            outstanding = [chunk for chunk in plan.chunks if chunk.key not in completed]
            checkpoint, failures = await self.fetch_chunks(
                execution_root,
                plan,
                checkpoint,
                checkpoint_path_value,
                outstanding if max_chunks is None else outstanding[:max_chunks],
                concurrency,
            )
            extras = self.backfill_extras(plan, checkpoint)
        except Exception as exc:
            if "checkpoint_path_value" in locals() and "checkpoint" in locals():
                self.write_blocked_checkpoint(checkpoint_path_value, checkpoint, exc)
            raise
        receipted = {receipt.chunk_key for receipt in checkpoint.receipts}
        remaining = [chunk.key for chunk in plan.chunks if chunk.key not in receipted]
        payload = {
            "checkpoint": str(checkpoint_path_value),
            "state": checkpoint.state,
            "completed_chunk_count": len(checkpoint.receipts),
            "chunk_count": len(plan.chunks),
            "pending_chunk_count": len(remaining),
            "failed_chunks": failures,
            "release_receipt_manifest_checksum": (
                self.release_manifest(plan, checkpoint) if checkpoint.state == "validated" else None
            ),
            **extras,
        }
        if failures:
            self.write_blocked_checkpoint(
                checkpoint_path_value,
                checkpoint,
                ValueError(f"{len(failures)} chunk(s) failed; first: {failures[0]['reason']}"),
            )
            raise ChunkedLaneBackfillError({**payload, "error": self.error_token})
        return payload
