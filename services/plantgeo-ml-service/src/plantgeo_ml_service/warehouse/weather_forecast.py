"""The `weather-forecast` lane: one Open-Meteo NWP run, one row per (cell, valid hour, variable).

Layer L2: may import `foundation` and `method`; may NOT import `pipeline`, `planes` or `interface`.
Spec FR-12. The schema lives in its own module rather than in `streams.py` because every other
stream there is a COPY of the sibling's; this one is ORIGINATED here and the sibling has deleted
its own. The nature argument, the variable catalogue's single-source rule and the provider's two
probe-settled conventions live in `pipeline/sources/AGENTS.md`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.warehouse.lanes import forecast_root_kind
from plantgeo_ml_service.warehouse.streams import (
    OBSERVED_STREAM_SCHEMAS,
    ParquetStreamSchema,
    StreamSchemaConflictError,
)

if TYPE_CHECKING:
    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind

WEATHER_FORECAST_STREAM: Final = "weather-forecast"

#: `release_series`: the partition day is the provider's own ISSUE date, never the day a value
#: verifies. The future-ness of a row lives entirely in its `valid_time` column, which is why this
#: lane writes `kind=observed` and leaves `kind=forecast` reserved for an ML-corrected product.
WEATHER_FORECAST_NATURE: Final = "release_series"

#: Asked of the lane contract rather than spelled here, so this lane and the readers that serve it
#: resolve the same root from one rule (`lanes.forecast_root_kind`).
WEATHER_FORECAST_KIND: Final[PartitionKind] = forecast_root_kind(WEATHER_FORECAST_STREAM)

#: The lane's grain: one variable reading at one lattice cell for one valid instant. `run_id` is
#: constant within a partition, so it is provenance rather than part of the sort key.
WEATHER_FORECAST_GRAIN: Final[tuple[str, ...]] = ("cell_id", "valid_time", "variable")

#: Governed-absence reasons a null `value` may carry. Exhaustive: a fifth reason is a plan
#: amendment, not a new string. A null `value` with a null `missing_reason` is a bug, never a zero.
MISSING_REASONS: Final[frozenset[str]] = frozenset({"outside_domain", "not_generated", "upstream_failed", "stale_run"})

#: What a row's `value` measures before admission.
SUPPORT_KINDS: Final[frozenset[str]] = frozenset({"native_grid", "sampled_point", "derived_field"})

#: The reason a variable that reached no reading carries when nothing finer is known.
NOT_GENERATED: Final = "not_generated"


@dataclass(frozen=True, slots=True)
class Variable:
    """One admitted variable: its unit, statistic, plausible bounds, and whether the provider sends it."""

    unit: str
    statistic: str
    minimum: float
    maximum: float
    #: True when the provider answers this field directly; False when this service derives it.
    requested_from_provider: bool


#: THE ONE VARIABLE CATALOGUE (style finding S6). The fetcher requests `UPSTREAM_VARIABLES`, which is
#: derived from this table rather than restated beside it, so the published schema and the request
#: cannot drift apart.
#:
#: WIND IS THE INVERSION THIS COMMENT ONCE GOT BACKWARDS, and the direction of the arrow matters to
#: anyone reading a published row. Open-Meteo answers `wind_speed_10m` and `wind_direction_10m` and
#: nothing else (probe: `.omc/research/forecast-s3-probe-20260919/`), so THOSE two are
#: `requested_from_provider=True` and it is the earth-relative pair `wind_u_10m`/`wind_v_10m` that
#: this service DERIVES from them (`sources/open_meteo.py::wind_components`). The components exist
#: because the aggregate needs them: a coarse rung averages u and v and recomposes speed and
#: direction from the mean pair, since a scalar mean of a bearing is not a bearing. Two consequences
#: a reader must not be surprised by: `wind_speed_10m`'s statistic reads `derived_vector_magnitude`
#: even at the base rung, where it is the provider's own scalar; and when the provider sends no
#: wind, all four variables are absent together rather than two of them being computed from nulls.
VARIABLES: Final[dict[str, Variable]] = {
    "cloud_cover": Variable("%", "instantaneous", 0.0, 100.0, requested_from_provider=True),
    # START-of-hour accumulation: the provider's hourly timestamp labels the hour the rain FALLS IN,
    # proven by its own daily sums (`.omc/research/forecast-s3-probe-20260919/`).
    "precipitation": Variable("mm", "sum_over_following_hour", 0.0, 1000.0, requested_from_provider=True),
    "relative_humidity_2m": Variable("%", "instantaneous", 0.0, 100.0, requested_from_provider=True),
    "temperature_2m": Variable("degC", "instantaneous", -100.0, 70.0, requested_from_provider=True),
    "wind_direction_10m": Variable(
        "degree", "meteorological_from_true_north", 0.0, 360.0, requested_from_provider=True
    ),
    "wind_speed_10m": Variable("m/s", "derived_vector_magnitude", 0.0, 150.0, requested_from_provider=True),
    "wind_u_10m": Variable("m/s", "instantaneous_earth_relative", -150.0, 150.0, requested_from_provider=False),
    "wind_v_10m": Variable("m/s", "instantaneous_earth_relative", -150.0, 150.0, requested_from_provider=False),
}

#: What the fetcher asks the provider for, sorted so one request string is one set of variables.
UPSTREAM_VARIABLES: Final[tuple[str, ...]] = tuple(
    sorted(name for name, variable in VARIABLES.items() if variable.requested_from_provider)
)

#: What this service computes from the upstream answers and publishes alongside them.
DERIVED_VARIABLES: Final[tuple[str, ...]] = tuple(
    sorted(name for name, variable in VARIABLES.items() if not variable.requested_from_provider)
)

#: Every variable a published row may name.
PUBLISHED_VARIABLES: Final[tuple[str, ...]] = tuple(sorted(VARIABLES))

WEATHER_FORECAST_SCHEMA: Final = ParquetStreamSchema(
    name=WEATHER_FORECAST_STREAM,
    arrow_schema=pa.schema(
        [
            # `<provider>:<model>:<init_time ISO>`; pins one published series.
            pa.field("run_id", pa.string(), nullable=False),
            pa.field("model_init_time", pa.timestamp("us", tz="UTC"), nullable=False),
            # Null for Open-Meteo, which echoes no release instant; a provider that does fills it.
            pa.field("provider_issue_time", pa.timestamp("us", tz="UTC"), nullable=True),
            # Three separate lifecycle instants, never collapsed into one "ingested_at".
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
            # Carried on the row so a reader never needs this module to interpret a published file.
            pa.field("unit", pa.string(), nullable=False),
            pa.field("statistic", pa.string(), nullable=False),
            pa.field("support", pa.string(), nullable=False),
            pa.field("missing_reason", pa.string(), nullable=True),
        ]
    ),
    sort_columns=WEATHER_FORECAST_GRAIN,
    base_non_null_columns=("cell_id",),
)

WEATHER_FORECAST_COLUMNS: Final[tuple[str, ...]] = WEATHER_FORECAST_SCHEMA.column_names


def register_weather_forecast_stream() -> ParquetStreamSchema:
    """Publish this schema in the observed registry `object_store.write_partition` resolves through.

    Idempotent: re-registering the same object is a no-op, and a DIFFERENT schema under this name is
    refused rather than silently swapped, because a partition written under an unregistered contract
    is unreadable by every reader that trusts the registry.
    """
    registered = OBSERVED_STREAM_SCHEMAS.get(WEATHER_FORECAST_STREAM)
    if registered is not None and registered is not WEATHER_FORECAST_SCHEMA:
        raise StreamSchemaConflictError(
            f"stream {WEATHER_FORECAST_STREAM!r} is already registered with a different contract"
        )
    OBSERVED_STREAM_SCHEMAS[WEATHER_FORECAST_STREAM] = WEATHER_FORECAST_SCHEMA
    return WEATHER_FORECAST_SCHEMA


#: Registration happens at import: `warehouse/streams.py` imports this module at its bottom, so any
#: reader of the registry sees this lane without having to know the module's name.
WEATHER_FORECAST_REGISTRATION: Final = register_weather_forecast_stream()


def variable_unit_and_statistic(variable: str) -> tuple[str, str]:
    """Return one variable's published unit and statistic, or refuse by naming the catalogue."""
    catalogued = VARIABLES.get(variable)
    if catalogued is None:
        raise StreamSchemaConflictError(
            f"variable {variable!r} is not in the weather-forecast catalogue {PUBLISHED_VARIABLES}"
        )
    return catalogued.unit, catalogued.statistic


def value_is_plausible(variable: str, value: float) -> bool:
    """Report whether a reading sits inside its variable's declared physical bounds."""
    catalogued = VARIABLES[variable]
    return catalogued.minimum <= value <= catalogued.maximum
