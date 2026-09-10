"""Optional timing never changes a serving outcome, payload, or worker admission."""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from agri_data_service.parquet_ops import duckdb_session, read_telemetry

if TYPE_CHECKING:
    from agri_data_service.config import ObjectStoreCredentials

MAX_EVENT_BYTES = 4096
SECRET = "https://user:secret@example.test/private?bbox=-125,42,-111,49&token=secret"


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []

    def log(event: str, **fields: object) -> None:
        events.append({"event": event, **fields})

    monkeypatch.setattr(read_telemetry, "logger", SimpleNamespace(info=log))
    return events


def test_fixed_metrics_are_deterministic_and_do_not_log_operation_input(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture(monkeypatch)
    clocks = iter(((0.0, 0.0, 0.0), (1.0, 0.1, 0.2), (3.0, 0.4, 1.2), (4.0, 0.5, 1.5)))
    monkeypatch.setattr(read_telemetry, "_clock", lambda: next(clocks))
    with (
        read_telemetry.observe_read(enabled=True, operation=SECRET, cancelled=threading.Event()),
        read_telemetry.stage("data_scan"),
    ):
        pass
    event = events[0]
    assert event["operation"] == "other"
    assert event["stages"] == {
        "data_scan": {"calls": 1, "elapsed_seconds": 2.0, "thread_cpu_seconds": 0.3, "process_cpu_seconds": 1.0}
    }
    assert event["http_transfer_bytes"] is None
    assert event["http_transfer_status"] == "unavailable"
    assert event["thread_cpu_scope"] == "calling_worker_thread_only"
    assert event["process_cpu_scope"] == "entire_process_includes_other_reads"
    serialized = json.dumps(events)
    assert SECRET not in serialized
    assert "secret" not in serialized
    assert len(serialized.encode()) < MAX_EVENT_BYTES


def test_telemetry_disabled_avoids_clocks_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture(monkeypatch)

    def clock() -> None:
        raise AssertionError("disabled telemetry read a clock")

    monkeypatch.setattr(read_telemetry, "_clock", clock)
    with (
        read_telemetry.observe_read(enabled=False, operation="release", cancelled=threading.Event()),
        read_telemetry.stage("schema"),
    ):
        pass
    assert events == []


def test_broken_clock_does_not_hide_the_original_error_or_log_its_text(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture(monkeypatch)

    def broken() -> float:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(read_telemetry.time, "thread_time", broken)
    original = ValueError(SECRET)
    with (
        pytest.raises(ValueError, match="user:secret") as caught,
        read_telemetry.observe_read(enabled=True, operation="day", cancelled=threading.Event()),
        read_telemetry.stage("data_scan"),
    ):
        raise original
    assert caught.value is original
    assert events[0]["outcome"] == "failed"
    assert events[0]["clock_unavailable"] is True
    assert SECRET not in json.dumps(events)
    assert read_telemetry._active.get() is None


@pytest.mark.parametrize("fail_work", [False, True])
def test_broken_logger_preserves_result_or_original_error(monkeypatch: pytest.MonkeyPatch, fail_work: bool) -> None:
    def broken(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("logger unavailable")

    monkeypatch.setattr(read_telemetry, "logger", SimpleNamespace(info=broken))
    original = ValueError("original read refusal")

    def work() -> object:
        with read_telemetry.observe_read(enabled=True, operation="window", cancelled=threading.Event()):
            if fail_work:
                raise original
            return original

    if fail_work:
        with pytest.raises(ValueError, match="original read refusal") as caught:
            work()
        assert caught.value is original
    else:
        assert work() is original


def test_event_population_and_durations_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture(monkeypatch)
    clock = 0.0

    def advance() -> tuple[float, float, float]:
        nonlocal clock
        clock += read_telemetry.MAX_SECONDS * 2
        return clock, clock, clock

    monkeypatch.setattr(read_telemetry, "_clock", advance)
    with read_telemetry.observe_read(enabled=True, operation="release", cancelled=threading.Event()):
        for _ in range(100):
            for name in read_telemetry.STAGES:
                with read_telemetry.stage(name):
                    pass
    stages = cast("dict[str, dict[str, float]]", events[0]["stages"])
    assert set(stages) == set(read_telemetry.STAGES)
    assert all(stage["elapsed_seconds"] == read_telemetry.MAX_SECONDS for stage in stages.values())
    assert len(json.dumps(events).encode()) < MAX_EVENT_BYTES


async def test_deadline_logs_only_when_worker_finishes_and_keeps_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    events = _capture(monkeypatch)
    started, finish, closed = threading.Event(), threading.Event(), threading.Event()
    slot = threading.BoundedSemaphore(1)
    monkeypatch.setattr(duckdb_session, "_read_slot", slot)
    session = SimpleNamespace(close=closed.set)
    monkeypatch.setattr(duckdb_session, "_open_serving_session", lambda *_args, **_kwargs: session)

    def work(_session: object) -> str:
        with read_telemetry.stage("data_scan"):
            started.set()
            assert finish.wait(timeout=5)
        return "unchanged payload"

    async def request() -> str:
        async with asyncio.timeout(0.05):
            return await duckdb_session.run_serving_read(
                cast("ObjectStoreCredentials", object()), work, operation="release", telemetry=True
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(duckdb_session, "_read_pool", pool)
        task = asyncio.create_task(request())
        try:
            assert await asyncio.to_thread(started.wait, 1)
            with pytest.raises(TimeoutError):
                await task
            assert events == []
            assert not slot.acquire(blocking=False)
        finally:
            finish.set()
            await asyncio.gather(task, return_exceptions=True)
    assert closed.is_set()
    assert len(events) == 1
    assert events[0]["outcome"] == "completed"
    assert events[0]["caller_cancelled"] is True
    assert slot.acquire(blocking=False)
    slot.release()
