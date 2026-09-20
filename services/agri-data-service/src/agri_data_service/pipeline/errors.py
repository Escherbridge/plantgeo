"""Reusable typed failures for pipeline operations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineErrorContext:
    """Stable machine-readable classification for a pipeline operation failure."""

    code: str
    lane: str | None = None
    stage: str | None = None
    retryable: bool = False


class PipelineOperationError(RuntimeError):
    """A pipeline failure whose existing message remains the operator-facing contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "operation_failed",
        lane: str | None = None,
        stage: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.context = PipelineErrorContext(code=code, lane=lane, stage=stage, retryable=retryable)


__all__ = ["PipelineErrorContext", "PipelineOperationError"]
