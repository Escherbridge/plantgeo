"""Optional bounded worker-completion metrics; see AGENTS.md."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal
from uuid import uuid4

import structlog

if TYPE_CHECKING:
    from collections.abc import Iterator
    from threading import Event

type Stage = Literal["session", "listing", "schema", "data_scan", "point_null_probe"]
STAGES: Final[tuple[Stage, ...]] = ("session", "listing", "schema", "data_scan", "point_null_probe")
MAX_SECONDS: Final = 86_400.0
MAX_CALLS: Final = 1_000_000
logger = structlog.get_logger()


@dataclass
class ReadMetrics:
    stages: dict[str, dict[str, float | int]] = field(default_factory=dict)
    clock_unavailable: bool = False


_active: ContextVar[ReadMetrics | None] = ContextVar("parquet_read_metrics", default=None)


def _clock() -> tuple[float, float, float] | None:
    try:
        wall, cpu, process = time.monotonic(), time.thread_time(), time.process_time()
        if all(math.isfinite(value) for value in (wall, cpu, process)):
            return wall, cpu, process
    except Exception:
        pass
    return None


def _duration(start: tuple[float, float, float] | None) -> tuple[float, float, float] | None:
    end = _clock()
    if start is None or end is None:
        return None
    wall, thread, process = (round(min(MAX_SECONDS, max(0.0, b - a)), 6) for a, b in zip(start, end, strict=True))
    return wall, thread, process


@contextmanager
def stage(name: Stage) -> Iterator[None]:
    metrics = _active.get()
    if metrics is None or name not in STAGES:
        yield
        return
    start = _clock()
    try:
        yield
    finally:
        with suppress(Exception):
            duration = _duration(start)
            if duration is None:
                metrics.clock_unavailable = True
            else:
                held = metrics.stages.setdefault(
                    name, {"calls": 0, "elapsed_seconds": 0.0, "thread_cpu_seconds": 0.0, "process_cpu_seconds": 0.0}
                )
                held["calls"] = min(MAX_CALLS, held["calls"] + 1)
                for key, value in zip(
                    ("elapsed_seconds", "thread_cpu_seconds", "process_cpu_seconds"), duration, strict=True
                ):
                    held[key] = round(min(MAX_SECONDS, held[key] + value), 6)


@contextmanager
def observe_read(*, enabled: bool, operation: str, cancelled: Event) -> Iterator[None]:
    if not enabled:
        yield
        return
    metrics = ReadMetrics()
    token = _active.set(metrics)
    start = _clock()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        _active.reset(token)
        with suppress(Exception):
            duration = _duration(start)
            logger.info(
                "parquet_read_stages",
                schema_version=1,
                read_id=uuid4().hex,
                operation=operation if operation in ("day", "window", "release") else "other",
                outcome="failed" if failed else "completed",
                caller_cancelled=cancelled.is_set(),
                elapsed_seconds=None if duration is None else duration[0],
                thread_cpu_seconds=None if duration is None else duration[1],
                process_cpu_seconds=None if duration is None else duration[2],
                clock_unavailable=metrics.clock_unavailable or duration is None,
                stages=metrics.stages,
                http_transfer_bytes=None,
                http_transfer_status="unavailable",
                thread_cpu_scope="calling_worker_thread_only",
                process_cpu_scope="entire_process_includes_other_reads",
            )
