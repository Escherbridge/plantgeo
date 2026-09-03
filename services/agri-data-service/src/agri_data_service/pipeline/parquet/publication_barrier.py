"""One lane's exclusive availability publication barrier, held across verification and pointer CAS.

Layer L2 leaf: `db` and SQLAlchemy only. It lives apart from `gap_fill` because BOTH the contract
(`availability_index.py`) and the driver need it, and while it sat on the driver the contract had to
import the driver at module scope -- which forced every edge in the other direction to be a lazy
function-body import. See `AGENTS.md` in this directory, "the publication barrier is a leaf".
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select

from agri_data_service.db.advisory_keys import parquet_lane_publication_barrier_key

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_LANE_ROOT_SEGMENT_COUNT: Final = 2


@asynccontextmanager
async def postgres_lane_publication_barrier(session: AsyncSession, lane_root: str) -> AsyncIterator[bool]:
    """Try one lane's exclusive publication barrier across verification and pointer CAS."""
    segments = lane_root.split("/")
    if (
        len(segments) != _LANE_ROOT_SEGMENT_COUNT
        or not segments[0].startswith("layer=")
        or not segments[1].startswith("kind=")
    ):
        raise ValueError("lane_root must be exactly layer=<slug>/kind=<observed|forecast>")
    barrier_key = parquet_lane_publication_barrier_key(
        segments[0].removeprefix("layer="),
        segments[1].removeprefix("kind="),
    )
    held = await session.execute(select(func.pg_try_advisory_lock(func.hashtextextended(barrier_key, 0))))
    granted = bool(held.scalar())
    try:
        yield granted
    finally:
        if granted:
            with suppress(Exception):
                await session.execute(select(func.pg_advisory_unlock(func.hashtextextended(barrier_key, 0))))


__all__ = ["postgres_lane_publication_barrier"]
