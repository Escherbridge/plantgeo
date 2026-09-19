"""Parquet schema for the `weather-forecast` lane: Open-Meteo NWP model runs, one row per
(cell, valid hour, variable).

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

NATURE: `release_series` (`conductor/code_styleguides/layer-lanes.md:70`), the same nature already
registered for `drought` (`pipeline/parquet/lane_registry.py:538-539`). For a release series the
`day=` partition is the **publication's own valid/issue date** -- here, the model run's issue date
-- never the day a value verifies. That is the whole resolution to `layer-lanes.md` section 2's
"future dates are served from `kind=forecast`; settled dates from `kind=observed`": that rule is
about the partition day axis, and an admitted NWP run's issue day is never in the future, so this
stream writes **`kind=observed` only**. The future-ness of an individual row lives entirely in its
`valid_time` COLUMN, which may be hours or days ahead of the partition day. `layer=weather-forecast/
kind=forecast/**` is RESERVED to the ML service for an ML-corrected or downscaled product; this lane
never writes it, and the ML service never writes `kind=observed` here (`.omc/ultrapilot-20260918/
W8-E-PLAN.md` section 1.5).

`missing_reason` carries `outside_domain` | `not_generated` | `upstream_failed` | `stale_run` and is
NEVER used to mean zero -- a null `value` with a populated `missing_reason` is a governed absence;
a null `value` with a null `missing_reason` is a bug, not a reading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema, register_stream_schema

WEATHER_FORECAST_STREAM: Final = "weather-forecast"

# The lane's true grain, frozen at `.omc/ultrapilot-20260918/W8-E-PLAN.md` section 1.4: one variable
# reading at one lattice cell for one valid instant. `run_id` is constant within a partition (one
# model run is admitted per publication) and is therefore provenance, not part of the sort key.
WEATHER_FORECAST_GRAIN: Final[tuple[str, ...]] = ("cell_id", "valid_time", "variable")

# Governed-absence reasons a null `value` may carry. Exhaustive; a fifth reason needs a plan
# amendment, not a silent string.
MISSING_REASONS: Final[frozenset[str]] = frozenset({"outside_domain", "not_generated", "upstream_failed", "stale_run"})

# What a row's `value` measures, per `layer-lanes.md` section 1b: the platform normalizes provider
# output to one of these support kinds before admission.
SUPPORT_KINDS: Final[frozenset[str]] = frozenset({"native_grid", "sampled_point", "derived_field"})


@dataclass(frozen=True, slots=True)
class Variable:
    """One admitted NWP variable's unit, statistic, and plausible-value bounds."""

    unit: str
    statistic: str
    minimum: float
    maximum: float


# Ported from `codex/weather-forecast-local-slice`'s fixture catalogue (`warehouse/schemas/
# weather_forecast.py:24-42` on that branch), minus `VERSION`, `validate_run_id`, `SAMPLES`,
# `MAX_POINTS`, and the `synthetic-fixture-only` metadata it carried: those describe a deterministic
# fixture generator, and this lane admits real provider data. Wind keeps the earth-relative
# component split (`wind_u_10m`/`wind_v_10m`) as two `variable` rows alongside the two DERIVED rows
# (`wind_speed_10m`/`wind_direction_10m`); a scalar mean of a bearing is not a bearing
# (`.omc/ultrapilot-20260918/W8-E-PLAN.md` section 1.4: "scalar direction averages are forbidden").
VARIABLES: Final[dict[str, Variable]] = {
    "temperature_2m": Variable("degC", "instantaneous", -100, 70),
    "relative_humidity_2m": Variable("%", "instantaneous", 0, 100),
    "cloud_cover": Variable("%", "instantaneous", 0, 100),
    "precipitation": Variable("mm", "sum_over_following_hour", 0, 1000),
    "wind_u_10m": Variable("m/s", "instantaneous_earth_relative", -150, 150),
    "wind_v_10m": Variable("m/s", "instantaneous_earth_relative", -150, 150),
    "wind_speed_10m": Variable("m/s", "derived_vector_magnitude", 0, 150),
    "wind_direction_10m": Variable("degree", "meteorological_from_true_north", 0, 360),
}

# Column-by-column provenance, against the frozen contract table (`.omc/ultrapilot-20260918/
# W8-E-PLAN.md` section 1.4):
#   run_id                                        -- `<provider>:<model>:<init_time ISO>`; pins one
#                                                     published series (ML spec.md "Readers pin one
#                                                     published run").
#   model_init_time                                -- the model's own initialization instant.
#   provider_issue_time                            -- the provider's release instant, when supplied;
#                                                     nullable because not every provider publishes one.
#   fetched_at / admitted_at / published_at        -- PlantGeo's own lifecycle: three separate
#                                                     instants, never collapsed to one "ingested_at".
#   valid_time                                     -- the instant this row's value verifies at.
#   interval_start / interval_end                  -- nullable; populated only for an accumulation
#                                                     statistic (e.g. `sum_over_following_hour`), null
#                                                     for an instantaneous reading.
#   lead_hours                                     -- `valid_time - model_init_time`, in hours.
#   cell_id, latitude, longitude                   -- the lattice cell this row was floored onto, per
#                                                     `weather_observations`'s convention -- NOT the
#                                                     raw provider sample point. `cell_id` is nullable
#                                                     for the same reason `signal`'s and
#                                                     `weather_observations`'s is: a coarse rung merges
#                                                     many source cells and can honestly name none of
#                                                     them (`warehouse/parquet/schema.py:185-187`,
#                                                     `warehouse/schemas/weather_observations.py:94`).
#   variable, value, unit, statistic                -- tall layout: one row per `VARIABLES` key.
#                                                     `unit`/`statistic` are carried on the row itself
#                                                     (not looked up from `VARIABLES` alone) so a
#                                                     reader never needs this module's code to
#                                                     interpret a published file.
#   support                                        -- one of `SUPPORT_KINDS`.
#   missing_reason                                 -- one of `MISSING_REASONS`, or null when `value`
#                                                     is present. Never used to encode a zero reading.
WEATHER_FORECAST_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WEATHER_FORECAST_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("run_id", pa.string(), nullable=False),
                pa.field("model_init_time", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("provider_issue_time", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("fetched_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("admitted_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("published_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("valid_time", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("interval_start", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("interval_end", pa.timestamp("us", tz="UTC"), nullable=True),
                pa.field("lead_hours", pa.int16(), nullable=False),
                # NULLABLE because the coarse rungs null it: a coarse cell merges many source
                # cells and can honestly name none of them. The base z13 rung always carries it.
                pa.field("cell_id", pa.string(), nullable=True),
                pa.field("latitude", pa.float64(), nullable=False),
                pa.field("longitude", pa.float64(), nullable=False),
                pa.field("variable", pa.string(), nullable=False),
                # NULLABLE: a governed absence carries a populated `missing_reason` instead.
                pa.field("value", pa.float64(), nullable=True),
                pa.field("unit", pa.string(), nullable=False),
                pa.field("statistic", pa.string(), nullable=False),
                pa.field("support", pa.string(), nullable=False),
                pa.field("missing_reason", pa.string(), nullable=True),
            ]
        ),
        sort_columns=WEATHER_FORECAST_GRAIN,
    )
)
