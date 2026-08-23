"""Export the current USGS WBD HUC12 boundary snapshot from Postgres to Parquet.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
STATIC layer, `horizon: none` (docs/lanes/watersheds.md section 7) -- there is no forecaster and
no `kind=forecast` stream for this lane; only `kind=observed` is ever written.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_SCHEMA, WATERSHEDS_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_RELEASE_EXPORT_SQL: Final = text(load_query_sql("pipeline/watersheds_day_export.sql"))

# 500 basins measured 12.4 MB as GeoJSON text at 6-digit coordinate precision (docs/lanes/
# watersheds.md section 1; ingest/watersheds.py:76-77) -- properties-plus-geometry as TEXT, a
# strictly heavier encoding than the WKB this lane writes. 1,000 rows/part is a deliberately
# conservative bound layered on top of that already-conservative baseline, not a fresh row-count
# guess: never size a geometry relation by row count alone -- geo.drought_areas is 995 rows and
# 500 MB. Multiple parts under one day still read as one present day; gap detection lists the
# day directory rather than opening a file (foundation/parquet/paths.py MAX_PART_INDEX).
ROWS_PER_PART: Final = 1_000


async def read_watersheds_release(session: AsyncSession, *, release_day: date) -> pa.Table:
    """Return every currently published HUC12 boundary, conformed and sorted to the grain.

    Unlike the signal plane there is no per-day slice to take: a HUC12 boundary is a snapshot
    refreshed in place, not a daily-resampled series (docs/lanes/watersheds.md sections 3, 7).
    `release_day` is bound into every row as the day this particular export represents; it is
    never derived from a per-row column.
    """
    result = await session.execute(_RELEASE_EXPORT_SQL, {"release_day": release_day})
    columns: dict[str, list[object]] = {name: [] for name in WATERSHEDS_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(WATERSHEDS_SCHEMA.arrow_schema)


async def export_watersheds_release(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
) -> tuple[ParquetWriteReceipt, ...]:
    """Export the current HUC12 boundary snapshot to `kind=observed`, spilling across parts.

    A source that genuinely has nothing published still reaches `store.write_partition` with a
    zero-row table on its one (and only) part, so the writer's own `EmptyPartitionError`
    (foundation/parquet writer contract) surfaces the governed absence, rather than this
    function silently returning no receipts for a caller to misread as "nothing to export".
    """
    table = await read_watersheds_release(session, release_day=day)
    part_count = max(1, math.ceil(table.num_rows / ROWS_PER_PART))
    return tuple(
        store.write_partition(
            table.slice(part_index * ROWS_PER_PART, ROWS_PER_PART),
            layer=WATERSHEDS_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
            part_index=part_index,
        )
        for part_index in range(part_count)
    )
