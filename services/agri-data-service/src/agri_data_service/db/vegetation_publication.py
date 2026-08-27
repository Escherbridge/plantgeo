"""Durable vegetation publication state and the source/publication advisory barrier."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select, text

from agri_data_service.db.advisory_keys import VEGETATION_PUBLICATION_BARRIER_KEY
from agri_data_service.db.sql_queries import load_query_sql

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

# Matches the ingestion timestamp lookback; inclusive UTC dates can therefore number 46.
VEGETATION_PUBLICATION_LOOKBACK_DAYS: Final = 45
VEGETATION_FINGERPRINT_HEX_LENGTH: Final = 64

_DAY_FINGERPRINTS_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_day_fingerprints.sql"))
_ENQUEUE_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_enqueue.sql"))
_PENDING_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_pending.sql"))
_ATTEMPT_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_attempt.sql"))
_ACK_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_ack.sql"))
_FULLY_ENROLLED_SQL: Final = text(load_query_sql("pipeline/vegetation_publication_fully_enrolled.sql"))


@dataclass(frozen=True, slots=True)
class VegetationPublicationTarget:
    """One governed source day and the fingerprint of its exact exported projection."""

    day: date
    source_fingerprint: str


@asynccontextmanager
async def postgres_vegetation_publication_barrier(session: AsyncSession) -> AsyncIterator[None]:
    """Hold the vegetation-wide session advisory barrier across commits and rollbacks."""
    await session.execute(select(func.pg_advisory_lock(func.hashtextextended(VEGETATION_PUBLICATION_BARRIER_KEY, 0))))
    try:
        yield
    finally:
        with suppress(Exception):
            await session.execute(
                select(func.pg_advisory_unlock(func.hashtextextended(VEGETATION_PUBLICATION_BARRIER_KEY, 0)))
            )


@asynccontextmanager
async def try_postgres_vegetation_publication_barrier(session: AsyncSession) -> AsyncIterator[bool]:
    """Try the barrier once so routine ingestion never waits behind a long exact audit."""
    held = await session.execute(
        select(func.pg_try_advisory_lock(func.hashtextextended(VEGETATION_PUBLICATION_BARRIER_KEY, 0)))
    )
    granted = bool(held.scalar())
    try:
        yield granted
    finally:
        if granted:
            with suppress(Exception):
                await session.execute(
                    select(func.pg_advisory_unlock(func.hashtextextended(VEGETATION_PUBLICATION_BARRIER_KEY, 0)))
                )


@asynccontextmanager
async def unlocked_vegetation_publication_barrier(session: AsyncSession) -> AsyncIterator[None]:  # noqa: ARG001
    """No-op barrier for a caller already holding the vegetation-wide barrier."""
    yield


async def vegetation_day_fingerprints(
    session: AsyncSession,
    *,
    first_day: date | None = None,
    last_day: date | None = None,
) -> tuple[VegetationPublicationTarget, ...]:
    """Fingerprint every exact exported source row for each source-backed day in a window."""
    result = await session.execute(_DAY_FINGERPRINTS_SQL, {"first_day": first_day, "last_day": last_day})
    targets: list[VegetationPublicationTarget] = []
    for row in result.mappings():
        day = row["observed_day"]
        fingerprint = str(row["source_fingerprint"])
        if not isinstance(day, date) or len(fingerprint) != VEGETATION_FINGERPRINT_HEX_LENGTH:
            raise ValueError("governed vegetation day fingerprint query returned an invalid row")
        targets.append(VegetationPublicationTarget(day=day, source_fingerprint=fingerprint))
    return tuple(targets)


async def enqueue_vegetation_publication(
    session: AsyncSession,
    targets: Sequence[VegetationPublicationTarget],
    *,
    force: bool = False,
) -> int:
    """Upsert exact day fingerprints, optionally invalidating a physically stale checkpoint."""
    if not targets:
        return 0
    result = await session.execute(
        _ENQUEUE_SQL,
        {
            "observed_days": [target.day for target in targets],
            "source_fingerprints": [target.source_fingerprint for target in targets],
            "force": force,
        },
    )
    return len(result.mappings().all())


async def pending_vegetation_publication(
    session: AsyncSession,
    *,
    limit: int,
) -> tuple[VegetationPublicationTarget, ...]:
    """Select the least-recently-attempted pending days, then the oldest day, for fair draining."""
    if limit <= 0:
        raise ValueError("publication pending limit must be positive")
    result = await session.execute(_PENDING_SQL, {"limit": limit})
    return tuple(
        VegetationPublicationTarget(day=row["observed_day"], source_fingerprint=str(row["source_fingerprint"]))
        for row in result.mappings()
    )


async def record_vegetation_publication_attempt(
    session: AsyncSession,
    target: VegetationPublicationTarget,
    *,
    error: str | None,
) -> None:
    """Record one bounded drain attempt without changing the target fingerprint."""
    await session.execute(
        _ATTEMPT_SQL,
        {"observed_day": target.day, "source_fingerprint": target.source_fingerprint, "last_error": error},
    )


async def acknowledge_vegetation_publication(
    session: AsyncSession,
    target: VegetationPublicationTarget,
) -> bool:
    """Compare-and-ack only when the queued fingerprint still equals the verified objects."""
    result = await session.execute(
        _ACK_SQL,
        {"observed_day": target.day, "source_fingerprint": target.source_fingerprint},
    )
    return result.mappings().one_or_none() is not None


async def vegetation_publication_is_fully_enrolled(session: AsyncSession) -> bool:
    """Return whether every governed vegetation source day has durable queue state."""
    result = await session.execute(_FULLY_ENROLLED_SQL)
    return bool(result.scalar_one())


__all__ = [
    "VEGETATION_PUBLICATION_BARRIER_KEY",
    "VEGETATION_PUBLICATION_LOOKBACK_DAYS",
    "VegetationPublicationTarget",
    "acknowledge_vegetation_publication",
    "enqueue_vegetation_publication",
    "pending_vegetation_publication",
    "postgres_vegetation_publication_barrier",
    "record_vegetation_publication_attempt",
    "try_postgres_vegetation_publication_barrier",
    "unlocked_vegetation_publication_barrier",
    "vegetation_day_fingerprints",
    "vegetation_publication_is_fully_enrolled",
]
