"""Export one day of the water-gauges reading log from Postgres to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `docs/lanes/water-gauges.md` for the source contract and the day-naming trap this exporter
takes care to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_SCHEMA, WATER_GAUGES_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/water_gauges_day_export.sql"))

# `geo.features` carries `ix_features_layer_observation_day` on
# (layer_id, geo.feature_observation_day(properties)) -- exactly this query's (layer, day) filter --
# so one indexed statement per day needs no cell-id batching the way signal_plane_day_export.sql
# needs (agri.signal_observation has no such index and would force a full heap scan without one).
# ~953 gauges (docs/lanes/water-gauges.md section 3) polled hourly by the forward path is
# ~22,872 rows/day; 200,000 leaves roughly 8x headroom for gauge-count growth and any archive
# daily-values rows landing in the same window. Reaching it is treated as a truncated day, never
# a real answer -- see read_water_gauges_day below.
MAX_ROWS_PER_DAY: Final = 200_000


class WaterGaugesExportError(RuntimeError):
    """Raised when a water-gauges day cannot be exported as written."""


async def read_water_gauges_day(session: AsyncSession, *, day: date) -> pa.Table:
    """Return one day of the water-gauges reading log at its true (site, instant) grain.

    Unlike the signal plane, this needs no cell-id batching -- see `MAX_ROWS_PER_DAY` above for
    why one indexed statement is already bounded. The result is unsorted; `write_partition` sorts
    it to the grain, which is what produces the clustering the compression depends on.
    """
    result = await session.execute(_DAY_EXPORT_SQL, {"observed_day": day, "row_limit": MAX_ROWS_PER_DAY})
    rows = list(result.mappings())
    if len(rows) >= MAX_ROWS_PER_DAY:
        raise WaterGaugesExportError(
            f"{day} returned at least {MAX_ROWS_PER_DAY} water-gauges rows; "
            "raise MAX_ROWS_PER_DAY or narrow the export rather than writing a truncated day"
        )

    columns: dict[str, list[object]] = {name: [] for name in WATER_GAUGES_SCHEMA.column_names}
    for row in rows:
        for name, values in columns.items():
            values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        WATER_GAUGES_SCHEMA.arrow_schema
    )


async def export_water_gauges_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
) -> ParquetWriteReceipt:
    """Export one settled day of the water-gauges reading log to its `kind=observed` partition.

    A day with genuinely zero published readings (a real gap, or a day before this warehouse's
    dense record begins -- docs/lanes/water-gauges.md section 3) is a governed absence and belongs
    behind `store.write_absence` with real `GovernedAbsence` evidence, never worked around here:
    `write_partition` refuses a zero-row table by design.
    """
    table = await read_water_gauges_day(session, day=day)
    return store.write_partition(table, layer=WATER_GAUGES_STREAM, kind="observed", day=day)
