"""Export one day of the sensors lane's day-grain readings from Postgres to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `warehouse/schemas/sensors.py` for the captured-vs-served fields decision and
`sql/pipeline/sensors_day_export.sql` for the reduction and its day-boundary rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/sensors_day_export.sql"))

# `geo.features` carries no index on the expression `geo.feature_observation_day(properties)`, so
# a day-scoped read still walks every row this layer has ever held (see the SQL file's header).
# Batching by `station_ids` bounds each round trip's array parameter and result set, mirroring
# `CELL_BATCH_SIZE` in `pipeline/lanes/signal.py`. 250 keeps a full-roster walk
# (`SENSOR_MAX_STATIONS` defaults to 750, `ingest/sensors.py:85`) to three round trips.
STATION_BATCH_SIZE: Final = 250


class SensorsExportError(RuntimeError):
    """Raised when a sensors-lane day cannot be exported as written."""


async def read_sensors_day(
    session: AsyncSession,
    *,
    day: date,
    station_ids: Sequence[str],
) -> pa.Table:
    """Return one day of the sensors lane's day-grain readings for `station_ids`.

    Reads in `STATION_BATCH_SIZE` batches and concatenates. The result is unsorted;
    `write_partition` sorts it to the grain, which is what produces the clustering the
    compression depends on.
    """
    if not station_ids:
        raise SensorsExportError("a sensors-lane export needs at least one station; an empty batch scans nothing")

    columns: dict[str, list[object]] = {name: [] for name in SENSORS_SCHEMA.column_names}
    for start in range(0, len(station_ids), STATION_BATCH_SIZE):
        batch = list(station_ids[start : start + STATION_BATCH_SIZE])
        result = await session.execute(_DAY_EXPORT_SQL, {"observed_day": day, "station_ids": batch})
        for row in result.mappings():
            for name, values in columns.items():
                values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(SENSORS_SCHEMA.arrow_schema)


async def export_sensors_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    station_ids: Sequence[str],
) -> ParquetWriteReceipt:
    """Export one day of the sensors lane's readings to its `kind=observed` partition.

    A day with no qualifying reading for any requested station -- normal near the edge of NWS's
    ~6-day retention (docs/lanes/sensors.md section 3) or before this producer started -- is a
    governed absence, recorded elsewhere with `store.write_absence`, never written as an empty
    partition: `write_partition` refuses a zero-row table precisely so that cannot happen by
    accident.
    """
    table = await read_sensors_day(session, day=day, station_ids=station_ids)
    return store.write_partition(table, layer=SENSORS_STREAM, kind="observed", zoom=LANE_BASE_ZOOM_TIER, day=day)
