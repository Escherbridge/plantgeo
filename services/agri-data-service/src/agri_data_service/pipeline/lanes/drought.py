"""Export one USDM weekly release from Postgres to Parquet.

Layer L3: may import `foundation`, `warehouse`, `db`; may NOT import method, planes, or interface.
`horizon: none` (warehouse/schemas/drought.py) -- there is no forecaster and no `kind=forecast`
stream for this lane; only `kind=observed` is ever written, one dated partition per USDM release
Tuesday (conductor/RUNBOOK.md section 0.24.1, stream S2).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.drought import DROUGHT_SCHEMA, DROUGHT_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_RELEASE_EXPORT_SQL: Final = text(load_query_sql("pipeline/drought_release_export.sql"))

# geo.drought_areas measures ~500 KB/row (1,045 rows, 500 MB total relation size, measured
# 2026-08-22 -- conductor/RUNBOOK.md section 0.24.1 records the same relation at 995 rows/500 MB
# a week earlier). Row count is worthless as a size proxy wherever geometry is involved (RUNBOOK
# section 0.23.2), so a part's row budget here is derived from the ACTUAL serialized bytes of the
# table THIS export read -- via `_rows_per_part` below -- rather than a fixed row-count guess like
# watersheds.py's ROWS_PER_PART. One release carries at most five classes (D0 through D4), so an
# ordinary release comfortably fits one part; this budget exists for the release that does not.
PARQUET_TARGET_PART_BYTES: Final = 64 * 1024 * 1024  # 64 MiB.


def _rows_per_part(table: pa.Table) -> int:
    """Return how many rows of `table` fit `PARQUET_TARGET_PART_BYTES`, measured from its own bytes.

    Never a fixed row-count guess: `table.nbytes` is this specific release's actual in-memory
    footprint (dominated by the WKB `geom` column), so the budget tracks whatever a release
    actually weighs rather than an assumption baked in at write time. Always at least 1, so a
    genuinely empty release still reaches `store.write_partition` on its one (and only) part
    rather than this function dividing by zero or returning no parts at all.
    """
    row_count: int = table.num_rows
    if row_count == 0:
        return 1
    table_bytes: int = table.nbytes
    average_row_bytes: int = max(1, table_bytes // row_count)
    rows_per_part: int = max(1, PARQUET_TARGET_PART_BYTES // average_row_bytes)
    return rows_per_part


async def read_drought_release(session: AsyncSession, *, valid_date: date) -> pa.Table:
    """Return every drought-class polygon of one USDM release, conformed and sorted to the grain.

    `valid_date` is bound to the source's exact ISO YYYY-MM-DD text, matching
    `geo.drought_areas.valid_date` (a varchar, not a native date column -- see
    sql/pipeline/drought_release_export.sql). A Tuesday USDM never published returns zero rows,
    which `export_drought_release` below leaves for `write_partition`'s own empty-partition
    refusal to surface, rather than this function inventing a distinction between "no release"
    and "an error".
    """
    result = await session.execute(_RELEASE_EXPORT_SQL, {"valid_date": valid_date.isoformat()})
    columns: dict[str, list[object]] = {name: [] for name in DROUGHT_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(DROUGHT_SCHEMA.arrow_schema)


async def export_drought_release(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
) -> tuple[ParquetWriteReceipt, ...]:
    """Export one USDM release to `kind=observed`, spilling across parts sized by measured bytes.

    `day` is both the partition day and the release's `valid_date` -- USDM publishes on exactly
    this weekly cadence, so the two name the same Tuesday (ingest/usdm.py, `_require_tuesday`).
    A release the source did not publish for `day` is a governed absence and belongs behind
    `store.write_absence(...)`, never an empty partition -- `write_partition` refuses a zero-row
    table precisely so that cannot happen by accident, and this function lets that refusal
    surface rather than working around it.
    """
    table = await read_drought_release(session, valid_date=day)
    rows_per_part = _rows_per_part(table)
    part_count = max(1, math.ceil(table.num_rows / rows_per_part))
    return tuple(
        store.write_partition(
            table.slice(part_index * rows_per_part, rows_per_part),
            layer=DROUGHT_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
            part_index=part_index,
        )
        for part_index in range(part_count)
    )
