"""Parquet schema for the conformed calendar dimension: one row per civil day.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
STATIC LOOKUP, `horizon: none`. A date is not observed and cannot be forecast, so this stream
never writes `kind=forecast`; the field values below are functions of `calendar_day` alone.

The columns are deliberately few. `foundation/parquet/calendar.py`'s module docstring records why
no fiscal year, holiday flag or trading-day column appears here: they are business policy, and a
dimension that every lane keys to is the worst place to smuggle unsourced policy in.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.calendar import CALENDAR_GRAIN, CALENDAR_STREAM
from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

# One row = one civil day. `calendar_day` is the whole grain and the whole key: every other column
# is derived from it, so two rows sharing a day would be two answers to the same question.
CALENDAR_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=CALENDAR_STREAM,
        arrow_schema=pa.schema(
            [
                # THE KEY. A lane keys its own role-named date column -- `observed_day`,
                # `release_day`, `snapshot_day`, `valid_date`, `issued_on` -- to this column. No
                # lane's schema gains a foreign-key column for it; the join is by value, and the
                # roles stay distinct (docs/holonic-kimball-modeling.md, "Kimball analytical plane").
                pa.field("calendar_day", pa.date32(), nullable=False),
                pa.field("year", pa.int16(), nullable=False),
                pa.field("quarter", pa.int8(), nullable=False),
                pa.field("month", pa.int8(), nullable=False),
                pa.field("day_of_month", pa.int8(), nullable=False),
                pa.field("day_of_year", pa.int16(), nullable=False),
                # ISO year and ISO week travel TOGETHER and neither is the civil `year`: 2027-01-01
                # is ISO week 53 of ISO year 2026. Splitting them across a join is the classic
                # week-boundary defect, which is why both are carried rather than derived later.
                pa.field("iso_year", pa.int16(), nullable=False),
                pa.field("iso_week", pa.int8(), nullable=False),
                # ISO 8601 numbering: Monday 1 through Sunday 7. USDM's Tuesday release cadence and
                # every other weekday rhythm read off this column.
                pa.field("iso_day_of_week", pa.int8(), nullable=False),
                pa.field("is_month_start", pa.bool_(), nullable=False),
                pa.field("is_month_end", pa.bool_(), nullable=False),
            ]
        ),
        sort_columns=CALENDAR_GRAIN,
    )
)
