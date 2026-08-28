"""Process-wide admission stays coupled to the worker that can own a DuckDB session."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agri_data_service.parquet_ops import duckdb_session
from agri_data_service.parquet_ops.faults import ServingRefusalError


def test_every_parquet_read_runs_on_the_bounded_pool() -> None:
    """The default executor would permit many independently memory-capped DuckDB instances."""
    assert duckdb_session._read_pool._max_workers == duckdb_session.SERVING_MAX_CONCURRENT_READS


@pytest.mark.asyncio
async def test_work_never_starts_when_no_admission_slot_is_available() -> None:
    """Acquiring a session is downstream of admission, never merely adjacent to it."""
    for _ in range(duckdb_session.SERVING_MAX_CONCURRENT_READS):
        assert duckdb_session._read_slot.acquire(blocking=False)
    started = False

    def work() -> None:
        nonlocal started
        started = True

    try:
        with pytest.raises(ServingRefusalError) as raised:
            await duckdb_session.run_bounded_read(
                work,
                operation="test",
                slot_wait_seconds=0.01,
            )
    finally:
        for _ in range(duckdb_session.SERVING_MAX_CONCURRENT_READS):
            duckdb_session._read_slot.release()

    assert raised.value.code == "serving_at_capacity"
    assert started is False


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_release_a_worker_s_slot_early() -> None:
    """A timed-out caller cannot admit a fourth connection while its worker still runs."""
    started = [threading.Event() for _ in range(duckdb_session.SERVING_MAX_CONCURRENT_READS)]
    finish = threading.Event()

    def blocking(index: int) -> None:
        started[index].set()
        assert finish.wait(timeout=5.0)

    tasks = [
        asyncio.create_task(duckdb_session.run_bounded_read(lambda index=index: blocking(index), operation="test"))
        for index in range(duckdb_session.SERVING_MAX_CONCURRENT_READS)
    ]
    try:
        assert await asyncio.to_thread(lambda: all(event.wait(timeout=1.0) for event in started))
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]

        with pytest.raises(ServingRefusalError) as raised:
            await duckdb_session.run_bounded_read(
                lambda: None,
                operation="test",
                slot_wait_seconds=0.01,
            )
        assert raised.value.code == "serving_at_capacity"
    finally:
        finish.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_admission_is_shared_across_event_loops() -> None:
    """A second adapter loop cannot enqueue work behind slots owned by another loop."""
    for _ in range(duckdb_session.SERVING_MAX_CONCURRENT_READS):
        assert duckdb_session._read_slot.acquire(blocking=False)

    async def attempt() -> str:
        with pytest.raises(ServingRefusalError) as raised:
            await duckdb_session.run_bounded_read(
                lambda: None,
                operation="other-loop",
                slot_wait_seconds=0.01,
            )
        return raised.value.code

    try:
        code = await asyncio.to_thread(lambda: asyncio.run(attempt()))
    finally:
        for _ in range(duckdb_session.SERVING_MAX_CONCURRENT_READS):
            duckdb_session._read_slot.release()

    assert code == "serving_at_capacity"
