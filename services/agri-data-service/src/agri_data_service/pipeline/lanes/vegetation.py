"""Export one day of the governed vegetation (Sentinel-2 NDVI) plane from Postgres to Parquet.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `docs/lanes/vegetation.md` for the source system, grain, and known-gap evidence -- in
particular section 5.3: this series is sparse and cloud-gated, so a day a batch of cells has no
row for is routinely a governed absence, not a defect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA, VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/vegetation_day_export.sql"))

# Reuses the batch size this lane's own registration path already settled on for bounding one
# statement's array parameter and result set (`execution/vegetation_ndvi_plane.py:57`), rather than
# inventing a new number. `agri.forecast_observation` is a modest corpus (184,409 rows across 1,568
# series as of `docs/lanes/vegetation.md` section 3) next to the signal plane's 11 GB heap, so this
# is statement-size discipline and parity with the established lane constant, not a fresh
# index-starvation measurement.
CELL_BATCH_SIZE: Final = 200


class VegetationExportError(RuntimeError):
    """Raised when a vegetation-plane day cannot be exported as written."""


async def read_vegetation_day(
    session: AsyncSession,
    *,
    day: date,
    cell_ids: Sequence[UUID],
) -> pa.Table:
    """Return one day of the governed vegetation plane for `cell_ids`, at the exported grain.

    Reads in `CELL_BATCH_SIZE` batches and concatenates. The result is unsorted; `write_partition`
    sorts it to the grain, which is what produces the clustering the compression depends on.
    """
    if not cell_ids:
        raise VegetationExportError("a vegetation-plane export needs at least one cell; an empty batch scans nothing")

    columns: dict[str, list[object]] = {name: [] for name in VEGETATION_PLANE_SCHEMA.column_names}
    for start in range(0, len(cell_ids), CELL_BATCH_SIZE):
        batch = [str(cell_id) for cell_id in cell_ids[start : start + CELL_BATCH_SIZE]]
        result = await session.execute(_DAY_EXPORT_SQL, {"observed_day": day, "cell_ids": batch})
        for row in result.mappings():
            for name, values in columns.items():
                values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        VEGETATION_PLANE_SCHEMA.arrow_schema
    )


async def export_vegetation_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    cell_ids: Sequence[UUID],
) -> ParquetWriteReceipt:
    """Export one settled day of the vegetation plane to its `kind=observed` partition.

    A day the source genuinely cannot serve -- no cloud-free Sentinel-2 scene, the structural
    property `docs/lanes/vegetation.md` section 5.3 measures at scale -- is a governed absence and
    is recorded with `store.write_absence`, never written as an empty partition:
    `write_partition` refuses a zero-row table precisely so that cannot happen by accident.
    """
    table = await read_vegetation_day(session, day=day, cell_ids=cell_ids)
    return store.write_partition(table, layer=VEGETATION_PLANE_STREAM, kind="observed", day=day)
