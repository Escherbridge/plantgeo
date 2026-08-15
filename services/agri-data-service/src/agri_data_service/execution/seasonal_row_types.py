"""Typed narrowing for untrusted result-set values, plus the read-only session this track uses."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import TYPE_CHECKING, Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# The CLI/procedure convention (code_styleguides/sql.md); applied transaction-locally.
STATEMENT_TIMEOUT_MILLISECONDS: Final = 120_000

# A whole-plane census over the 46M-row signal plane does not fit the interactive convention; the
# Phase 0 report raises it deliberately rather than silently splitting the count into partial reads.
CENSUS_STATEMENT_TIMEOUT_MILLISECONDS: Final = 600_000

_SET_READ_ONLY: Final = text("SET TRANSACTION READ ONLY")
_SET_UTC: Final = text("SET LOCAL \"TimeZone\" = 'UTC'")


class SeasonalRowTypeError(ValueError):
    """A result-set value did not have the type its column contract promises."""


def require_str(value: object, column: str) -> str:
    """Narrow a result-set value to ``str`` or name the column that broke its contract."""
    if not isinstance(value, str):
        raise SeasonalRowTypeError(f"{column} must be text, got {type(value).__name__}")
    return value


def require_int(value: object, column: str) -> int:
    """Narrow a result-set value to ``int``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SeasonalRowTypeError(f"{column} must be an integer, got {type(value).__name__}")
    return value


def require_float(value: object, column: str) -> float:
    """Narrow a result-set value to ``float``, accepting an integral value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SeasonalRowTypeError(f"{column} must be a number, got {type(value).__name__}")
    return float(value)


def require_bool(value: object, column: str) -> bool:
    """Narrow a result-set value to ``bool``."""
    if not isinstance(value, bool):
        raise SeasonalRowTypeError(f"{column} must be a boolean, got {type(value).__name__}")
    return value


def require_uuid(value: object, column: str) -> UUID:
    """Narrow a result-set value to ``UUID``."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise SeasonalRowTypeError(f"{column} must be a uuid, got {type(value).__name__}")


def require_date(value: object, column: str) -> date:
    """Narrow a result-set value to ``date``; a ``datetime`` is rejected, not silently truncated."""
    if isinstance(value, datetime) or not isinstance(value, date):
        raise SeasonalRowTypeError(f"{column} must be a date, got {type(value).__name__}")
    return value


def require_datetime(value: object, column: str) -> datetime:
    """Narrow a result-set value to a timezone-aware ``datetime``."""
    if not isinstance(value, datetime):
        raise SeasonalRowTypeError(f"{column} must be a timestamp, got {type(value).__name__}")
    if value.tzinfo is None:
        raise SeasonalRowTypeError(f"{column} must be timezone-aware")
    return value


def optional_float(value: object, column: str) -> float | None:
    """Narrow a nullable numeric result-set value."""
    return None if value is None else require_float(value, column)


def optional_datetime(value: object, column: str) -> datetime | None:
    """Narrow a nullable timestamp result-set value."""
    return None if value is None else require_datetime(value, column)


@asynccontextmanager
async def read_only_session(
    database_url: str,
    *,
    statement_timeout_milliseconds: int = STATEMENT_TIMEOUT_MILLISECONDS,
) -> AsyncIterator[AsyncSession]:
    """Open one session whose transaction is READ ONLY, UTC-pinned and statement-timeout bounded.

    The whole Phase 0/1 evidence path runs against the retained warehouse, so the transaction
    itself refuses writes rather than relying on the caller not issuing any.
    """
    if statement_timeout_milliseconds <= 0:
        raise SeasonalRowTypeError("statement timeout must be positive; an unbounded read is never correct")
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await session.execute(_SET_READ_ONLY)
            await session.execute(text(f"SET LOCAL statement_timeout = {int(statement_timeout_milliseconds)}"))
            await session.execute(_SET_UTC)
            yield session
            await session.rollback()
    finally:
        await engine.dispose()
