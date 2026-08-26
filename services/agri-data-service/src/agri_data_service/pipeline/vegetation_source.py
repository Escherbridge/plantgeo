"""Bounded governed vegetation source census shared by Parquet writers and validators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_SOURCE_RECONCILIATION_SQL: Final = text(load_query_sql("pipeline/vegetation_source_reconciliation.sql"))
CELL_BATCH_SIZE: Final = 200


class VegetationValidationError(ValueError):
    """Raised when a governed vegetation census request is malformed."""


@dataclass(frozen=True, slots=True)
class SourceCellDay:
    """One governed source cell-day and its contributing release count."""

    cell_id: str
    observed_day: date
    source_release_count: int


async def fetch_source_cell_days(
    session: AsyncSession,
    *,
    cell_ids: Sequence[UUID | str],
    first_day: date,
    last_day: date,
) -> tuple[SourceCellDay, ...]:
    """Read the exact governed vegetation cell-day population in bounded cell batches."""
    if last_day < first_day:
        raise VegetationValidationError(f"window {first_day}..{last_day} runs backwards")
    rows: list[SourceCellDay] = []
    for start in range(0, len(cell_ids), CELL_BATCH_SIZE):
        batch = [str(cell_id) for cell_id in cell_ids[start : start + CELL_BATCH_SIZE]]
        result = await session.execute(
            _SOURCE_RECONCILIATION_SQL,
            {"cell_ids": batch, "first_day": first_day, "last_day": last_day},
        )
        rows.extend(
            SourceCellDay(
                cell_id=str(row["cell_id"]),
                observed_day=row["observed_day"],
                source_release_count=int(row["source_release_count"]),
            )
            for row in result.mappings()
        )
    return tuple(rows)


__all__ = [
    "CELL_BATCH_SIZE",
    "SourceCellDay",
    "VegetationValidationError",
    "fetch_source_cell_days",
]
