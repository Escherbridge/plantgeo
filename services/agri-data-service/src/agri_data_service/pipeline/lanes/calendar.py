"""Write one version of the conformed calendar dimension to Parquet.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
STATIC LOOKUP, `horizon: none` -- only `kind=observed` is ever written.

THIS LANE TAKES NO `AsyncSession`, AND THAT IS THE POINT. Every other lane in this directory reads
Postgres; this one has no source system, so a session parameter it ignored would be a lie in the
signature. The registry's adapter absorbs the uniform shape instead (see
`pipeline/parquet/lane_registry.py::_fill_calendar`), which keeps the untruth in one place where it
is annotated, rather than in the lane itself.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.calendar import (
    CALENDAR_STREAM,
    calendar_days,
    calendar_version_span,
)
from agri_data_service.warehouse.schemas.calendar import CALENDAR_SCHEMA

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

# ~27 years of days per part. The whole dimension is roughly 10,000 rows of eleven small scalars,
# so one part is the norm; the spill exists only so an absurdly deep floor cannot mint one
# unreasonably large object, matching the part-N convention every other lane already uses.
ROWS_PER_PART: Final = 10_000


def build_calendar_table(version_day: date, *, floor: date) -> pa.Table:
    """Materialise the days one calendar version covers, conformed to the registered schema."""
    first_day, last_day = calendar_version_span(version_day, floor=floor)
    days = calendar_days(first_day, last_day)
    return pa.table(
        {
            "calendar_day": pa.array([entry.calendar_day for entry in days], pa.date32()),
            "year": pa.array([entry.year for entry in days], pa.int16()),
            "quarter": pa.array([entry.quarter for entry in days], pa.int8()),
            "month": pa.array([entry.month for entry in days], pa.int8()),
            "day_of_month": pa.array([entry.day_of_month for entry in days], pa.int8()),
            "day_of_year": pa.array([entry.day_of_year for entry in days], pa.int16()),
            "iso_year": pa.array([entry.iso_year for entry in days], pa.int16()),
            "iso_week": pa.array([entry.iso_week for entry in days], pa.int8()),
            "iso_day_of_week": pa.array([entry.iso_day_of_week for entry in days], pa.int8()),
            "is_month_start": pa.array([entry.is_month_start for entry in days], pa.bool_()),
            "is_month_end": pa.array([entry.is_month_end for entry in days], pa.bool_()),
        }
    ).cast(CALENDAR_SCHEMA.arrow_schema)


def export_calendar_version(
    store: ObjectStore,
    *,
    day: date,
    floor: date,
) -> tuple[ParquetWriteReceipt, ...]:
    """Write the calendar version stamped `day`, covering `floor` forward past every lane's horizon."""
    table = build_calendar_table(day, floor=floor)
    part_count = max(1, math.ceil(table.num_rows / ROWS_PER_PART))
    return tuple(
        store.write_partition(
            table.slice(part_index * ROWS_PER_PART, ROWS_PER_PART),
            layer=CALENDAR_STREAM,
            kind="observed",
            day=day,
            part_index=part_index,
        )
        for part_index in range(part_count)
    )
