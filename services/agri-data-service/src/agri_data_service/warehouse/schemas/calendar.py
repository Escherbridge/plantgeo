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
from agri_data_service.warehouse.parquet.tiers import TierDerivation, TierPassthrough, register_tier_derivation

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
                # The WMO three-month grouping: a fixed month mapping, not a solar event. The
                # ASTRONOMICAL season is deliberately absent -- its boundaries are equinoxes and
                # solstices, which belong with RUNBOOK section 0.28.3's solar fact per (cell, date).
                pa.field("meteorological_season", pa.string(), nullable=False),
                # Cyclical day-of-year, per RUNBOOK section 0.28.3: the form a model consumes, with
                # no 31-December-to-1-January discontinuity. Phase is taken over the day's OWN year
                # length, so a leap year does not shift the cycle.
                pa.field("day_of_year_sin", pa.float64(), nullable=False),
                pa.field("day_of_year_cos", pa.float64(), nullable=False),
            ]
        ),
        sort_columns=CALENDAR_GRAIN,
    )
)

# TierPassthrough rather than base-only: this lane has no spatial extent (no coordinates, no geometry),
# so every rung is byte-identical. Publishing all four rungs is required because every plane resolves
# requests through serving_zoom_tier(requested_zoom), which returns z0 for a whole-world request
# REGARDLESS of lane. A lane that published only z13 would answer such a request from a prefix that
# does not exist -- an empty result indistinguishable from a genuinely empty day. The contract
# (RUNBOOK 0.33.4) warns that "a zoom=13 prefix does not imply geometry", and calendar is the proof.
CALENDAR_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=CALENDAR_STREAM,
        strategy=TierPassthrough(),
    )
)
