"""Export one day of the governed signal plane from Postgres to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `AGENTS.md` in this directory for the batching rationale and the grain evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_SCHEMA, SIGNAL_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/signal_plane_day_export.sql"))

# `agri.signal_observation` has no index leading on `observed_at`, so a day-scoped read across all
# cells is a full scan of an 11 GB heap. Batching by `cell_id` rides
# `ix_signal_observation_cell_time_signal` instead. 250 keeps each statement's array parameter and
# its result set small enough to stay well inside the proxy's timeout on a dense day.
# RUNBOOK §0.22.5.
CELL_BATCH_SIZE: Final = 250


class SignalExportError(RuntimeError):
    """Raised when a signal-plane day cannot be exported as written."""


async def read_signal_day(
    session: AsyncSession,
    *,
    day: date,
    cell_ids: Sequence[int],
) -> pa.Table:
    """Return one day of the governed signal plane for `cell_ids`, at the exported grain.

    Reads in `CELL_BATCH_SIZE` batches and concatenates. The result is unsorted; `write_partition`
    sorts it to the grain, which is what produces the clustering the compression depends on.
    """
    if not cell_ids:
        raise SignalExportError("a signal-plane export needs at least one cell; an empty batch scans nothing")

    columns: dict[str, list[object]] = {name: [] for name in SIGNAL_PLANE_SCHEMA.column_names}
    for start in range(0, len(cell_ids), CELL_BATCH_SIZE):
        batch = list(cell_ids[start : start + CELL_BATCH_SIZE])
        result = await session.execute(_DAY_EXPORT_SQL, {"observed_day": day, "cell_ids": batch})
        for row in result.mappings():
            for name, values in columns.items():
                values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        SIGNAL_PLANE_SCHEMA.arrow_schema
    )


async def export_signal_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    cell_ids: Sequence[int],
) -> ParquetWriteReceipt:
    """Export one settled day of the signal plane to its `kind=observed` partition.

    A day the source genuinely cannot serve is a governed absence and is recorded with
    `store.write_absence`, never written as an empty partition -- `write_partition` refuses a
    zero-row table precisely so that cannot happen by accident.
    """
    table = await read_signal_day(session, day=day, cell_ids=cell_ids)
    return store.write_partition(table, layer=SIGNAL_PLANE_STREAM, kind="observed", day=day)
