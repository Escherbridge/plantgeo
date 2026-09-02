"""`postgres_lane_day_lock` against a REAL PostgreSQL backend, not a fake that always yields True.

WHY THIS FILE EXISTS. Every other assertion about the lane-day lock in this suite runs against
`unlocked_lane_day` or a hand-written context manager -- seams that answer whatever the test wants.
The primitive the whole concurrency story rests on (`gap_fill.fill_one_lane_day` and
`drain._derive_one_day` are two writers of the same three objects, and RUNBOOK 0.33.3 B has them
running at the same time by design) had NO test against a real `pg_try_advisory_lock`.

THREE PROPERTIES, EACH OF WHICH A FAKE CANNOT SHOW:

  * TRY, NEVER WAIT. A second holder gets `False` immediately rather than blocking, which is what
    lets a wall-clock-budgeted tick move on instead of queueing to redo work another run is doing.
  * SESSION-SCOPED, NOT TRANSACTION-SCOPED. This is the entire reason the module does not reuse
    `execution/provenance.py::advisory_lock`: that helper takes `pg_advisory_xact_lock`, which the
    next `rollback()` releases -- and both writers roll back BEFORE the prune that deletes objects
    and the mark that publishes the day. The test below rolls back INSIDE the held block and proves
    the exclusion survives it. Under a transaction lock that assertion goes red.
  * THE KEY IS THE UNIT. A different lane-day is grantable while one is held, so the lock serialises
    writers of one day rather than the whole warehouse.

Gated on `AGRI_TEST_DATABASE_URL` through `tests/conftest.py`, which pins the Alembic head and
refuses the persistent `plantgeo` warehouse. NOTHING HERE WRITES: advisory locks live in shared
memory, so this test leaves no row, no object and no schema change behind.

Each session gets its own `pool_size=1, max_overflow=0` engine, mirroring `db/engine.py`'s
`local_source_loader_engine`. That pin is a PRECONDITION of the lock, not an incidental setting:
a session lock belongs to one BACKEND, SQLAlchemy returns the connection to the pool on every
rollback, and this driver rolls back between acquire and release -- so with a larger pool the unlock
would land on a different backend, the original would hold the lock for its lifetime, and every
later tick would report `contended` for that lane-day forever on a green tick.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.db.advisory_keys import parquet_lane_publication_barrier_from_day_lock_key
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    postgres_lane_day_lock,
    postgres_lane_publication_barrier,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STREAM: Final = "fire-detections"
OTHER_STREAM: Final = "signal"
DAY: Final = dt.date(2026, 8, 1)
OTHER_DAY: Final = dt.date(2026, 7, 30)


@pytest.fixture
async def two_sessions(agri_db_async_dsn: str) -> AsyncIterator[tuple[AsyncSession, AsyncSession]]:
    """Two sessions on two engines, each pinned to ONE backend exactly as the loader engine is."""
    engines = [
        create_async_engine(agri_db_async_dsn, pool_size=1, max_overflow=0, pool_pre_ping=True) for _ in range(2)
    ]
    sessions = [AsyncSession(engine) for engine in engines]
    try:
        yield sessions[0], sessions[1]
    finally:
        for session in sessions:
            await session.rollback()
            await session.close()
        for engine in engines:
            await engine.dispose()


async def test_a_second_holder_is_refused_immediately_and_takes_it_after_the_first_lets_go(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """TRY, NEVER WAIT -- and the release must actually release, or every later tick reports contended."""
    first, second = two_sessions
    key = _lane_day_lock_key(LANE_REGISTRY[STREAM], DAY)

    async with postgres_lane_day_lock(first, key) as granted:
        assert granted is True
        async with postgres_lane_day_lock(second, key) as contended:
            assert contended is False, "two writers took one lane-day at the same time"

    async with postgres_lane_day_lock(second, key) as granted_after:
        assert granted_after is True, "the lock was never released"


async def test_the_lock_survives_a_rollback_inside_the_held_block(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """DO NOT DELETE. This is the difference between `pg_advisory_lock` and `pg_advisory_xact_lock`.

    Both writers roll back after their read and BEFORE the prune that deletes objects and the mark
    that publishes the day. A transaction-scoped lock would cover the read and leave the destructive
    half unguarded -- exactly backwards -- and this assertion is the only thing that would notice.
    """
    first, second = two_sessions
    lane = LANE_REGISTRY[STREAM]
    key = _lane_day_lock_key(lane, DAY)

    async with postgres_lane_day_lock(first, key) as granted:
        assert granted is True
        await first.rollback()
        async with postgres_lane_publication_barrier(second, f"layer={lane.slug}/kind=observed") as exclusive:
            assert exclusive is False, "the shared lane barrier did not survive its holder's rollback"
        async with postgres_lane_day_lock(second, key) as contended:
            assert contended is False, "the lane-day lock did not survive its holder's rollback"


async def test_a_different_lane_day_is_grantable_while_one_is_held(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """The key is the unit: one held day must not serialise the whole warehouse."""
    first, second = two_sessions

    async with postgres_lane_day_lock(first, _lane_day_lock_key(LANE_REGISTRY[STREAM], DAY)) as granted:
        assert granted is True
        other_key = _lane_day_lock_key(LANE_REGISTRY[STREAM], OTHER_DAY)
        async with postgres_lane_day_lock(second, other_key) as elsewhere:
            assert elsewhere is True, f"{other_key} was excluded by another day in its lane"


async def test_a_different_lane_is_independent_of_the_held_lane_barrier(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """Shared/exclusive publication barriers are lane-scoped, never warehouse-scoped."""
    first, second = two_sessions

    async with postgres_lane_day_lock(first, _lane_day_lock_key(LANE_REGISTRY[STREAM], DAY)) as granted:
        assert granted is True
        other_key = _lane_day_lock_key(LANE_REGISTRY[OTHER_STREAM], DAY)
        async with postgres_lane_day_lock(second, other_key) as elsewhere:
            assert elsewhere is True, f"{other_key} was excluded by an unrelated lane"


async def test_a_writer_blocks_availability_for_its_lane_then_release_allows_it(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """Availability must never verify while even one day writer owns the lane shared lock."""
    first, second = two_sessions
    lane = LANE_REGISTRY[STREAM]
    lane_root = f"layer={lane.slug}/kind=observed"

    async with postgres_lane_day_lock(first, _lane_day_lock_key(lane, DAY)) as writer_granted:
        assert writer_granted is True
        async with postgres_lane_publication_barrier(second, lane_root) as publication_granted:
            assert publication_granted is False

    async with postgres_lane_publication_barrier(second, lane_root) as publication_after:
        assert publication_after is True


async def test_availability_blocks_a_writer_for_its_lane_then_release_allows_it(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """A writer cannot enter the former evidence-verification-to-pointer-CAS race window."""
    first, second = two_sessions
    lane = LANE_REGISTRY[STREAM]
    lane_root = f"layer={lane.slug}/kind=observed"
    day_key = _lane_day_lock_key(lane, DAY)

    async with postgres_lane_publication_barrier(first, lane_root) as publication_granted:
        assert publication_granted is True
        async with postgres_lane_day_lock(second, day_key) as writer_granted:
            assert writer_granted is False

    async with postgres_lane_day_lock(second, day_key) as writer_after:
        assert writer_after is True


async def test_availability_barrier_survives_rollback_inside_its_held_block(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """The exclusive barrier spans long verification even if its session rolls back a snapshot."""
    first, second = two_sessions
    lane = LANE_REGISTRY[STREAM]
    lane_root = f"layer={lane.slug}/kind=observed"

    async with postgres_lane_publication_barrier(first, lane_root) as publication_granted:
        assert publication_granted is True
        await first.rollback()
        async with postgres_lane_day_lock(second, _lane_day_lock_key(lane, DAY)) as writer_granted:
            assert writer_granted is False


def test_lane_barrier_key_derivation_rejects_noncanonical_day_lock_shapes() -> None:
    """A second historical spelling would create a lock namespace that excludes nothing."""
    lane = LANE_REGISTRY[STREAM]
    canonical = _lane_day_lock_key(lane, DAY)

    assert parquet_lane_publication_barrier_from_day_lock_key(canonical) == (
        f"parquet-lane-publication:{lane.slug}:observed:v1"
    )
    for malformed in (
        canonical.replace(":z13:", ":z09:"),
        canonical.replace(DAY.isoformat(), "2026-02-30"),
        canonical + ":suffix",
        canonical.replace(lane.slug, "UPPER"),
    ):
        with pytest.raises(ValueError, match="lane-day lock key"):
            parquet_lane_publication_barrier_from_day_lock_key(malformed)


async def test_one_session_may_retake_its_own_lane_day(
    two_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    """PostgreSQL advisory locks are re-entrant per session, and the ladder walk relies on it.

    A contended day is pushed back onto the SAME session's queue and retried up to
    `MAX_CONTENDED_RETRIES_PER_DAY` times. If a session could not retake a lock it already released,
    a requeued day would be permanently unreachable within the run that queued it.
    """
    first, _ = two_sessions
    key = _lane_day_lock_key(LANE_REGISTRY[STREAM], DAY)

    async with postgres_lane_day_lock(first, key) as granted:
        assert granted is True
    async with postgres_lane_day_lock(first, key) as again:
        assert again is True
