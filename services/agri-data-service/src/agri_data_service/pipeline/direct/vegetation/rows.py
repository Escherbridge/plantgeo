"""Assemble one fetched vegetation day into its registered base-rung Arrow table.

ONE ROW SHAPE, not soil's three: `VEGETATION_PLANE_SCHEMA` (`warehouse/schemas/vegetation.py`) is
the twelve-column governed plane with no lineage variant, so there is no dispatch table here --
`vegetation_day_table` is the only builder this module needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.ingest.vegetation import NDVI_GRID_NAME
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_PRODUCT

if TYPE_CHECKING:
    from datetime import date, datetime

    from agri_data_service.pipeline.direct.vegetation.source import VegetationCellValue

#: Sentinel-2 L2A is public AWS Open Data with no client-facing licence gate -- the same open-data
#: judgement `pipeline/direct/soil/rows.py` SOIL_ALLOWED_CLIENT_EXPOSURE records for ERA5-Land, and
#: the existing Postgres-governed rows this schema already holds are exposed the same way
#: (`sql/pipeline/vegetation_day_export.sql:127` reads `source.allowed_client_exposure`, which
#: `execution/vegetation_ndvi_plane.py`'s registered `agri.data_source` row for this key sets true).
VEGETATION_ALLOWED_CLIENT_EXPOSURE = True


class VegetationRowError(RuntimeError):
    """Raised when a fetched day cannot be assembled into the registered vegetation contract."""


def vegetation_day_table(
    *,
    day: date,
    values: tuple[VegetationCellValue, ...],
    data_available_at: datetime,
) -> pa.Table:
    """Build the base-rung table for one day, one row per cell this fetch actually filled.

    `data_available_at` is ONE instant for the whole day -- the fetch's own `retrieved_at`
    (`VegetationSourceReceipt.retrieved_at`), never invented and never per-cell, because that is when
    THIS writer made every one of the day's values available, distinct from each cell's own scene
    acquisition instant (`VegetationCellValue.observed_at`, carried in the receipt, not as a base-rung
    column: the registered schema has none at that grain).

    A day with no filled cells is a governed absence, never an empty table -- the same discipline
    `pipeline/lanes/vegetation.py::export_vegetation_day`'s docstring states for the Postgres path.
    Refusing here is what makes that true for the direct path too: an empty `pa.Table` would satisfy
    the registered schema but read downstream as a published, contentless day.
    """
    if not values:
        raise VegetationRowError(
            f"vegetation {day.isoformat()} has no filled cells; a day with none is a governed absence, and "
            "building a zero-row table would let it read as a published day"
        )
    if data_available_at.tzinfo is None:
        raise VegetationRowError("data_available_at must be timezone-aware")
    rows = [_row(day=day, value=value, data_available_at=data_available_at) for value in values]
    return pa.Table.from_pylist(rows, schema=VEGETATION_PRODUCT.stream_schema.arrow_schema)


def _row(*, day: date, value: VegetationCellValue, data_available_at: datetime) -> dict[str, object]:
    """Build one governed cell-day row, in the exact column set `VEGETATION_PLANE_SCHEMA` registers."""
    return {
        "cell_id": value.cell.cell_id,
        "grid_name": NDVI_GRID_NAME,
        "metric_name": VEGETATION_PRODUCT.metric_name,
        "metric_unit": VEGETATION_PRODUCT.metric_unit,
        "observed_day": day,
        "metric_value": value.metric_value,
        "observation_checksum": value.record_sha256,
        "data_available_at": data_available_at,
        "release_count": value.release_count,
        "allowed_client_exposure": VEGETATION_ALLOWED_CLIENT_EXPOSURE,
        "cell_longitude": value.cell.cell_longitude,
        "cell_latitude": value.cell.cell_latitude,
    }


__all__ = [
    "VEGETATION_ALLOWED_CLIENT_EXPOSURE",
    "VegetationRowError",
    "vegetation_day_table",
]
