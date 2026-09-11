"""Read PostgreSQL MTBS rows for retained reconciliation; see pipeline/AGENTS.md."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


_BURN_SEVERITY_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/burn_severity_day_export.sql"))


async def read_burn_severity_release_day(session: AsyncSession, *, release_day: date) -> pa.Table:
    """Return schema-conforming PostgreSQL rows for one release-publication day."""
    result = await session.execute(_BURN_SEVERITY_DAY_EXPORT_SQL, {"release_day": release_day})
    columns: dict[str, list[object]] = {name: [] for name in BURN_SEVERITY_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        BURN_SEVERITY_SCHEMA.arrow_schema
    )
