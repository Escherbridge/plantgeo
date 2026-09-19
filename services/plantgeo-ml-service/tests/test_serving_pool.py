"""The serving ceiling is the pool that RUNS the reads, and a timed-out read is actually stopped."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Final, cast

import pytest
from serving_harness import body_of

from plantgeo_ml_service.planes import refusals, routes
from plantgeo_ml_service.planes.wire import ClaimProvenance

if TYPE_CHECKING:
    from collections.abc import Iterator

    from plantgeo_ml_service.pipeline.duckdb_session import DuckDbSession

HTTP_OK: Final = 200
HTTP_SERVICE_UNAVAILABLE: Final = 503

#: Short enough to keep the test quick, long enough that a scheduled pool worker reaches its wait.
TIMEOUT_SECONDS: Final = 0.5

#: How long a helper waits on a threading event before calling the behaviour broken.
PATIENCE_SECONDS: Final = 10.0

ROUTE: Final = "fire-risk"


class _HangingSession:
    """A stand-in for one DuckDB session that blocks until it is INTERRUPTED, then unwinds."""

    def __init__(self) -> None:
        self.interrupted = threading.Event()
        self.closed = threading.Event()

    def interrupt(self) -> None:
        """Record that the read was cancelled, which is what releases the work below."""
        self.interrupted.set()

    def __enter__(self) -> _HangingSession:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.closed.set()


def _hanging_work(context: routes.ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
    """Block until this read's session is interrupted; a read nobody stops never returns."""
    session = cast("_HangingSession", context.require_session())
    if not session.interrupted.wait(PATIENCE_SECONDS):
        raise AssertionError("the read was never interrupted; the timeout abandoned it instead")
    return ({}, ClaimProvenance(artifact_sha256=None, artifact_absent_reason="never_resolved", issued_on=None))


def _quick_work(_context: routes.ServingContext) -> tuple[dict[str, object], ClaimProvenance]:
    """Answer at once, so a slot that was genuinely returned shows up as a completed read."""
    return (
        {"ok": True},
        ClaimProvenance(artifact_sha256=None, artifact_absent_reason="never_resolved", issued_on=None),
    )


@pytest.fixture
def hanging_session(monkeypatch: pytest.MonkeyPatch) -> Iterator[_HangingSession]:
    """A fresh pool, a short timeout, and one session every read in this test opens."""
    monkeypatch.setattr(routes, "_serving_pool", None)
    monkeypatch.setattr(routes, "ROW_READ_TIMEOUT_SECONDS", TIMEOUT_SECONDS)
    session = _HangingSession()
    monkeypatch.setattr(routes, "open_serving_session", lambda: cast("DuckDbSession", session))
    monkeypatch.setattr(routes, "open_store", lambda: None)
    yield session
    session.interrupt()
    routes.serving_pool().shutdown()


async def _until_pool_is_idle() -> None:
    """Wait for the pool to report no read in flight, so a released slot is observed, not assumed."""
    for _ in range(int(PATIENCE_SECONDS / 0.02)):
        if routes.serving_pool().in_flight == 0:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("a read held its serving slot after the request that started it had answered")


async def test_a_hung_read_is_interrupted_and_its_slot_returns_to_the_pool(
    hanging_session: _HangingSession,
) -> None:
    """B1: a timeout that only stops WAITING leaves the query running and its slot held."""
    response = await routes.bounded_read(_hanging_work, route=ROUTE)

    assert response.status == HTTP_SERVICE_UNAVAILABLE
    assert body_of(response)["error"]["code"] == refusals.READ_TIMED_OUT
    assert hanging_session.interrupted.is_set()
    assert hanging_session.closed.wait(PATIENCE_SECONDS)

    await _until_pool_is_idle()

    answered = [
        await routes.bounded_read(_quick_work, route=ROUTE, needs_session=False)
        for _ in range(routes.MAX_CONCURRENT_READS + 1)
    ]
    assert [response.status for response in answered] == [HTTP_OK] * (routes.MAX_CONCURRENT_READS + 1)


async def test_a_read_offered_past_the_ceiling_is_refused_rather_than_queued(
    hanging_session: _HangingSession,
) -> None:
    """Every slot holds its own memory-capped session, so a queue would exceed their sum."""
    holding = [
        asyncio.create_task(routes.bounded_read(_hanging_work, route=ROUTE)) for _ in range(routes.MAX_CONCURRENT_READS)
    ]
    for _ in range(int(PATIENCE_SECONDS / 0.02)):
        if routes.serving_pool().in_flight == routes.MAX_CONCURRENT_READS:
            break
        await asyncio.sleep(0.02)

    refused = await routes.bounded_read(_quick_work, route=ROUTE, needs_session=False)

    assert body_of(refused)["error"]["code"] == refusals.SERVING_AT_CAPACITY
    hanging_session.interrupt()
    await asyncio.gather(*holding)
