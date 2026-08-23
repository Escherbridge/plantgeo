"""Export one day of the `weather-observations` current-conditions side lane to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.

SOURCE CONFIRMATION: this exports `ingest/open_meteo.py`'s `WEATHER_LAYER` current-conditions rows
(`geo.features`), not the governed historical archive behind `agri.signal_observation` -- that plane
is already exported by the `signal` stream. See
`warehouse/schemas/weather_observations.py`'s module docstring for the full citation trail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/weather_observations_day_export.sql"))


class WeatherObservationsExportError(RuntimeError):
    """Raised when a weather-observations day cannot be exported as written."""


async def read_weather_observations_day(
    session: AsyncSession,
    *,
    day: date,
    layer_id: str,
) -> pa.Table:
    """Return one day of the current-conditions side lane for `layer_id`, at the exported grain.

    Unlike the signal plane, this needs no cell-batching loop: `ix_features_layer_observation_day`
    (over `(layer_id, geo.feature_observation_day(properties))`) makes one bounded, indexed
    execution sufficient for a day-scoped, one-layer read. `layer_id` is resolved by the caller
    once (e.g. via `ingest.writer.resolve_layer_id`) rather than joined on `geo.layers.name` here --
    see the SQL file's own note for why a literal bound value is the proven-fast shape. The result
    is unsorted; `write_partition` sorts it to the grain, which is what produces the clustering the
    compression depends on.
    """
    if not layer_id.strip():
        raise WeatherObservationsExportError("a weather-observations export needs a resolved layer_id")

    result = await session.execute(_DAY_EXPORT_SQL, {"layer_id": layer_id, "observed_day": day})
    columns: dict[str, list[object]] = {name: [] for name in WEATHER_OBSERVATIONS_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        WEATHER_OBSERVATIONS_SCHEMA.arrow_schema
    )


async def export_weather_observations_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    layer_id: str,
) -> ParquetWriteReceipt:
    """Export one settled day of the weather-observations side lane to its `kind=observed` partition.

    A day the source genuinely holds no readings for (the poller did not run, or every point came
    back stale/invalid) is a governed absence and is recorded with `store.write_absence`, never
    written as an empty partition -- `write_partition` refuses a zero-row table precisely so that
    cannot happen by accident.
    """
    table = await read_weather_observations_day(session, day=day, layer_id=layer_id)
    return store.write_partition(
        table,
        layer=WEATHER_OBSERVATIONS_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
    )
