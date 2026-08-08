"""Result-row readers. A column that comes back in a shape the report's SQL does not declare is a typed
refusal, never a coerced guess."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.validation.errors import ValidationRowError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause


async def _fetch_rows(
    session: AsyncSession,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    """Execute one read-only statement and return its rows as plain mappings."""
    result = await session.execute(statement, dict(parameters))
    return tuple({str(column): value for column, value in row.items()} for row in result.mappings().all())


def _required_text(row: Mapping[str, object], column: str) -> str:
    """Read a NOT NULL text column, refusing anything that is not a string."""
    value = row.get(column)
    if not isinstance(value, str):
        raise ValidationRowError(f"column {column!r} must be text, got {type(value).__name__}")
    return value


def _optional_text(row: Mapping[str, object], column: str) -> str | None:
    """Read a nullable text column, refusing a non-string value."""
    value = row.get(column)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationRowError(f"column {column!r} must be text or NULL, got {type(value).__name__}")
    return value


def _required_count(row: Mapping[str, object], column: str) -> int:
    """Read a count column, refusing a non-integer and treating SQL NULL as zero (an empty FILTER sums to NULL)."""
    value = row.get(column)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationRowError(f"column {column!r} must be an integer count, got {type(value).__name__}")
    return value


def _required_day(row: Mapping[str, object], column: str) -> date:
    """Read a NOT NULL date column, refusing a datetime so a day can never silently carry a time."""
    value = row.get(column)
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValidationRowError(f"column {column!r} must be a date, got {type(value).__name__}")
    return value
