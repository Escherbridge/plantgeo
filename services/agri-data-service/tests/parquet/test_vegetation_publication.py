"""Durable vegetation publication state is fair, exact, and barrier-protected."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.db.vegetation_publication import (
    VEGETATION_PUBLICATION_BARRIER_KEY,
    postgres_vegetation_publication_barrier,
    try_postgres_vegetation_publication_barrier,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_SQL_ROOT = _SERVICE_ROOT / "src" / "agri_data_service" / "sql" / "pipeline"


class _ScalarResult:
    def scalar(self) -> bool:
        return True


class _BarrierSession:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.rollbacks = 0

    async def execute(self, statement: Any) -> _ScalarResult:
        self.statements.append(str(statement))
        return _ScalarResult()

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_session_barrier_survives_a_caller_rollback_until_context_exit() -> None:
    session = _BarrierSession()

    async with postgres_vegetation_publication_barrier(cast("Any", session)):
        assert "pg_advisory_lock" in session.statements[0]
        assert VEGETATION_PUBLICATION_BARRIER_KEY == "vegetation-governed-publication-v1"
        await session.rollback()
        assert not any("pg_advisory_unlock" in statement for statement in session.statements)

    assert session.rollbacks == 1
    assert "pg_advisory_unlock" in session.statements[-1]


@pytest.fixture
async def two_barrier_sessions(agri_db_async_dsn: str) -> AsyncIterator[tuple[AsyncSession, AsyncSession]]:
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


async def test_real_barrier_excludes_a_second_backend_across_rollback(
    two_barrier_sessions: tuple[AsyncSession, AsyncSession],
) -> None:
    first, second = two_barrier_sessions

    async with postgres_vegetation_publication_barrier(first):
        await first.rollback()
        async with try_postgres_vegetation_publication_barrier(second) as granted:
            assert granted is False
    async with try_postgres_vegetation_publication_barrier(second) as granted_after:
        assert granted_after is True


def test_pending_selection_is_starvation_proof_and_ack_is_compare_and_set() -> None:
    pending = (_SQL_ROOT / "vegetation_publication_pending.sql").read_text(encoding="utf-8")
    ack = (_SQL_ROOT / "vegetation_publication_ack.sql").read_text(encoding="utf-8")
    enqueue = (_SQL_ROOT / "vegetation_publication_enqueue.sql").read_text(encoding="utf-8")

    assert "ORDER BY last_attempted_at ASC NULLS FIRST, first_enqueued_at, observed_day" in pending
    assert "published_fingerprint IS DISTINCT FROM source_fingerprint" in pending
    assert "source_fingerprint = CAST(:source_fingerprint AS text)" in ack
    assert "RETURNING observed_day" in ack
    assert "source_fingerprint IS DISTINCT FROM EXCLUDED.source_fingerprint" in enqueue
    assert "WHEN CAST(:force AS boolean)" in enqueue


def test_day_fingerprint_covers_every_mutable_exact_export_input() -> None:
    fingerprint = (_SQL_ROOT / "vegetation_publication_day_fingerprints.sql").read_text(encoding="utf-8")

    for field in (
        "cell_id",
        "grid_name",
        "metric_name",
        "metric_unit",
        "observed_day",
        "metric_value",
        "observation_checksum",
        "data_available_at",
        "release_count",
        "allowed_client_exposure",
        "ST_X(cell.centroid)",
        "ST_Y(cell.centroid)",
    ):
        assert field in fingerprint
    assert "digest(" in fingerprint
    assert "CAST(:first_day AS date)" in fingerprint
    assert "CAST(:last_day AS date)" in fingerprint
    assert ":first_day IS NULL" not in fingerprint
    assert ":last_day IS NULL" not in fingerprint


def test_registration_and_generic_vegetation_writer_share_the_global_barrier() -> None:
    registration = (_SERVICE_ROOT / "src" / "agri_data_service" / "execution" / "vegetation_ndvi_plane.py").read_text(
        encoding="utf-8"
    )
    gap_fill = (_SERVICE_ROOT / "src" / "agri_data_service" / "pipeline" / "parquet" / "gap_fill.py").read_text(
        encoding="utf-8"
    )

    assert "await advisory_lock(session, VEGETATION_PUBLICATION_BARRIER_KEY)" in registration
    assert "await enqueue_vegetation_publication(session, publication_targets)" in registration
    assert "if lane.slug == VEGETATION_PLANE_STREAM" in gap_fill
    assert "vegetation_publication_barrier" in gap_fill


def test_full_enrollment_gate_precedes_global_fingerprint_revalidation() -> None:
    enrolled = (_SQL_ROOT / "vegetation_publication_fully_enrolled.sql").read_text(encoding="utf-8")

    assert "EXCEPT" in enrolled
    assert "SELECT observed_day FROM agri.vegetation_publication_day" in enrolled
