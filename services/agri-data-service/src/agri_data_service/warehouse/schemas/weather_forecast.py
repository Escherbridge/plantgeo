"""Parquet schema for the `weather-forecast` lane: one provider NWP run, per cell, valid hour and variable.

THIS IS NOT A REVERSAL OF THE OWNER'S 2026-09-19 DELETION. That decision was about ADMISSION: agri
admits no provider projection, and this service ingests none -- the deleted `ingest` packages stay
deleted and no forward, backfill or cron here fetches Open-Meteo forecast hours. What this module
restores is the READ side of a stream some other writer publishes. Since 2026-09-18
(`conductor/tracks/plantgeo_ml_service_20260918/spec.md` FR-12, owner: *"let ml take over any
projections, they don't need to be in the lanes"*) the writer is `services/plantgeo-ml-service`,
which owns the fetcher, the variable catalogue and the partition writer. agri only reads this stream
for serving, and registers its schema so the lane registry, the Parquet readers and the slider
catalogue can resolve the slug.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

THE NINETEEN COLUMNS ARE THE FROZEN ONES OF `e66dbc36`, and the object here is EXACTLY the ML
service's `WEATHER_FORECAST_SCHEMA`
(`services/plantgeo-ml-service/src/plantgeo_ml_service/warehouse/weather_forecast.py`): same field
names, types, nullability, order and sort columns. The two are copies held apart because the
services deploy independently and neither imports the other, and
`tests/parquet/test_ml_schema_parity.py` reads that module off disk and asserts the equality rather
than leaving it as a sentence here.

THE BASE-NON-NULL LIST IS HOUSED DIFFERENTLY, and that is a housing difference, not a contract one
-- the same divergence `fire_risk.py` records. Over there it sits on the schema object, because that
service has no tier-derivation engine to hang it on. Here it is `base_non_null_columns=("cell_id",)`
on the `TierDerivation` below, which is where this service's derivation engine reads it: a coarse
rung may null `cell_id`, so the guard has to name the BASE rung specifically, and only the
derivation knows which rung that is.

NATURE `release_series`, AND `kind=observed`. The partition day is the provider's own ISSUE date,
never the day a value verifies; the future-ness of a row lives entirely in its `valid_time` column.
That is the `drought` pattern. `kind=forecast` under this slug stays RESERVED for an ML-corrected
product: provider runs are deterministic, so the ensemble provenance a forecast partition requires
(`random_seed`, `ensemble_size`) would be invented.

TWO PROVIDER CONVENTIONS A READER MUST NOT RE-DERIVE (probe-settled,
`.omc/research/forecast-s3-probe-20260919/`): the hourly precipitation timestamp labels the START of
its accumulation hour -- the provider's own daily sums prove it and its documentation says otherwise
and is wrong -- so no one-hour shift is applied anywhere; and `wind_speed_10m`/`wind_direction_10m`
are what the provider answers, while `wind_u_10m`/`wind_v_10m` are DERIVED by the writer so a coarse
rung can average a vector pair instead of a bearing.
"""

from __future__ import annotations

from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema
from agri_data_service.warehouse.parquet.tiers import (
    ColumnAggregation,
    GridAggregation,
    TierDerivation,
    register_tier_derivation,
)

# The layer slug, this stream's `layer=<slug>/` object prefix, and this module's own name.
WEATHER_FORECAST_STREAM: Final = "weather-forecast"

# One variable reading at one lattice cell for one valid instant.
WEATHER_FORECAST_GRAIN: Final[tuple[str, ...]] = ("cell_id", "valid_time", "variable")

WEATHER_FORECAST_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WEATHER_FORECAST_STREAM,
        arrow_schema=pa.schema(
            [
                # `<provider>:<model>:<init_time ISO>`; pins one published series.
                pa.field("run_id", pa.string(), nullable=False),
                pa.field("model_init_time", pa.timestamp("us", tz="UTC"), nullable=False),
                # Null for Open-Meteo, which echoes no release instant; a provider that does fills it.
                pa.field("provider_issue_time", pa.timestamp("us", tz="UTC"), nullable=True),
                # Three separate lifecycle instants, never collapsed into one `ingested_at`.
                pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("admitted_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("valid_time", pa.timestamp("us", tz="UTC"), nullable=False),
                # Accumulations only; null for an instantaneous reading.
                pa.field("interval_start", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("interval_end", pa.timestamp("us", tz="UTC"), nullable=True),
                # `valid_time - model_init_time` in whole hours; never negative.
                pa.field("lead_hours", pa.int16(), nullable=False),
                # Nullable ONLY so the coarse rungs may null it; the base rung always carries it.
                pa.field("cell_id", pa.string(), nullable=True),
                pa.field("latitude", pa.float64(), nullable=False),
                pa.field("longitude", pa.float64(), nullable=False),
                pa.field("variable", pa.string(), nullable=False),
                # Nullable: a governed absence carries a populated `missing_reason` instead.
                pa.field("value", pa.float64(), nullable=True),
                # Carried on the row so a reader never needs the writer to interpret a published file.
                pa.field("unit", pa.string(), nullable=False),
                pa.field("statistic", pa.string(), nullable=False),
                pa.field("support", pa.string(), nullable=False),
                pa.field("missing_reason", pa.string(), nullable=True),
            ]
        ),
        sort_columns=WEATHER_FORECAST_GRAIN,
    )
)

# Tier derivation: re-floor the lattice and re-aggregate onto the coarser cells.
#
# FOUR COLUMNS ARE KEYS RATHER THAN AGGREGATES. `valid_time` and `variable` are the base grain's own
# non-spatial half; `run_id` joins them because two runs must never merge into one row even though a
# partition holds only one, and `support` joins them because a native-grid reading and a sampled
# point are different measurements of the same variable.
#
# `value` IS THE ONLY ARITHMETIC HERE, and `mean` is right because every published variable is
# intensive -- a temperature, a percentage, a rate, a vector component. Nothing in this lane is a
# total, which is exactly why the writer derives `wind_u_10m`/`wind_v_10m`: a coarse rung averages
# the component pair and recomposes speed and direction, because the scalar mean of a bearing is not
# a bearing.
#
# `missing_reason` IS NULLED RATHER THAN CARRIED. The writer's invariant -- a null `value` carries a
# populated reason -- is a BASE-rung invariant over one cell. A coarse cell merging present readings
# with absent ones has no single reason to name, and carrying the first contributing row's reason
# beside a real averaged value would state an absence that did not happen.
WEATHER_FORECAST_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=WEATHER_FORECAST_STREAM,
        strategy=GridAggregation(
            longitude_column="longitude",
            latitude_column="latitude",
            key_columns=("run_id", "valid_time", "variable", "support"),
            aggregations=(
                ColumnAggregation("cell_id", "null"),  # a coarse cell names no single source cell
                ColumnAggregation("model_init_time", "first"),  # constant per run_id
                ColumnAggregation("provider_issue_time", "first"),  # constant per run_id
                ColumnAggregation("fetched_at", "first"),  # constant per run_id
                ColumnAggregation("admitted_at", "first"),  # constant per run_id
                ColumnAggregation("published_at", "first"),  # constant per run_id
                ColumnAggregation("interval_start", "first"),  # constant per valid_time and variable
                ColumnAggregation("interval_end", "first"),  # constant per valid_time and variable
                ColumnAggregation("lead_hours", "first"),  # valid_time minus model_init_time; both keyed
                ColumnAggregation("value", "mean"),  # intensive on every admitted variable
                ColumnAggregation("unit", "first"),  # constant per variable
                ColumnAggregation("statistic", "first"),  # constant per variable
                ColumnAggregation("missing_reason", "null"),  # no single reason survives a merge
            ),
        ),
        base_non_null_columns=("cell_id",),
    )
)
