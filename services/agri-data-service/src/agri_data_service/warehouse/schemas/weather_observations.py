"""Parquet schema for the `weather-observations` lane.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.

SOURCE CONFIRMATION (lane contract `docs/lanes/weather-observations.md` section 5, item 3): the
name `weather-observations` is overloaded in this repo. The governed historical archive (NASA
POWER + Open-Meteo ERA5-Land) that backs `agri.signal_observation` is a DIFFERENT plane, already
exported by the `signal` stream (`warehouse/parquet/schema.py::SIGNAL_PLANE_SCHEMA`) -- this module
does not duplicate it. This schema is for the OTHER producer: `ingest/open_meteo.py`'s
`WEATHER_LAYER` (`ingest/open_meteo.py:62-69`), which polls Open-Meteo's *current-conditions*
forecast endpoint (`https://api.open-meteo.com/v1/forecast`, not the archive host) and writes
`FeatureWrite` rows through `ingest/writer.py::ingest_features` into `geo.features`
(`ingest/open_meteo.py:399-419`), never `agri.signal_observation`. Confirmed by reading
`ingest/open_meteo.py:399-419` (`build_weather_write` returns a `FeatureWrite`, not a signal-plane
row) and `execution/weather_observations/AGENTS.md:49-51` (the same trap, stated independently).
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

# The layer slug verbatim from `geo.layers.name` -- `WEATHER_LAYER.default` in
# `ingest/open_meteo.py:64`. Also this stream's `layer=<slug>/` object prefix.
WEATHER_OBSERVATIONS_STREAM: Final = "weather-observations"

# The lane's true grain: one instantaneous point-in-time reading. This is NOT a daily aggregate --
# `MAX_OBSERVATION_AGE = timedelta(hours=3)` (`ingest/open_meteo.py:73`) and a cron that polls
# faster than the upstream refreshes its `current.time` can land several distinct `observed_at`
# instants per sample point per day. `observed_day` is a real column (matching every other lane's
# convention) but is constant within one exported partition and therefore not part of the sort key;
# sorting by (latitude, longitude, observed_at) is what clusters one point's readings together for a
# multi-day read, which is the query shape this side lane serves (map hover / point history).
WEATHER_OBSERVATIONS_GRAIN: Final[tuple[str, ...]] = (
    "latitude",
    "longitude",
    "observed_at",
)

# Column-by-column provenance, cited against the writer that actually produced them:
#   latitude / longitude       -- properties->'geometry'->'coordinates' (GeoJSON [lon, lat]),
#                                  `ingest/open_meteo.py:416`. Full float precision as sampled, NOT
#                                  the 4dp-rounded identity string (`ingest/identity.py:246-254`).
#   observed_at / observed_day -- properties->>'observedAt', the upstream instant
#                                  (`ingest/open_meteo.py:371,375`, `format_javascript_timestamp`).
#                                  `observed_day` reproduces `geo.feature_observation_day`
#                                  (`drizzle/0018_fire_discovery_observation_day.sql:36-64`), whose
#                                  first COALESCE key is this same `observedAt` field, so the two
#                                  never disagree.
#   external_id                -- properties->>'id', `FeatureWrite.external_id`
#                                  (`ingest/writer.py:66-69`): "{lat4dp}:{lon4dp}:{observedAtISO}".
#   temperature_c / relative_humidity_pct / wind_speed_ms / wind_direction_deg / precipitation_mm --
#                                  `CURRENT_VALUE_BOUNDS` (`ingest/open_meteo.py:88-94`). Every one
#                                  is validated in-range or the whole observation is dropped before
#                                  it reaches `geo.features` (`_bounded_value`,
#                                  `ingest/open_meteo.py:348-356`) -- no row in the table can be
#                                  missing one, so these are NOT NULL here too. Wind speed is
#                                  Celsius/m-per-second/percent/degrees/millimetres: `wind_speed_unit`
#                                  is pinned to `"ms"` (`ingest/open_meteo.py:340`); temperature and
#                                  precipitation take Open-Meteo's un-overridden metric defaults.
#   source                      -- properties->>'source', always the literal `OPEN_METEO_PROPERTY_SOURCE`
#                                  ("Open-Meteo", `ingest/open_meteo.py:60,415`) -- read off the row,
#                                  not hard-coded, so a future second producer on this layer would
#                                  show up as a different value rather than being silently relabelled.
#   feature_id / ingested_at   -- `geo.features.id` / `geo.features.created_at`
#                                  (`src/lib/server/db/schema.ts:221,233`): the warehouse row's own
#                                  identity and persistence time, distinct from `observed_at`.
# `data_available_at` (`src/lib/server/db/schema.ts:239`) is deliberately absent: this producer's
# `FeatureIdentity` never sets it (`ingest/open_meteo.py:407` calls
# `build_weather_observation_identity`, which supplies no `data_available_at`), so every row's value
# is NULL today. Adding a column that is unconditionally NULL is not provenance, it is a placeholder;
# add it back the day a producer is wired to supply it.
WEATHER_OBSERVATIONS_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=WEATHER_OBSERVATIONS_STREAM,
        arrow_schema=pa.schema(
            [
                pa.field("latitude", pa.float64(), nullable=False),
                pa.field("longitude", pa.float64(), nullable=False),
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                pa.field("observed_day", pa.date32(), nullable=False),
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges many stations, so no single upstream id describes it
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("external_id", pa.string(), nullable=True),
                pa.field("temperature_c", pa.float64(), nullable=False),
                pa.field("relative_humidity_pct", pa.float64(), nullable=False),
                pa.field("wind_speed_ms", pa.float64(), nullable=False),
                # NULLABLE because the coarse rungs null it:
                # wind direction is circular and has no arithmetic mean, so the coarse rungs null it rather than
                # fabricate a bearing
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("wind_direction_deg", pa.float64(), nullable=True),
                pa.field("precipitation_mm", pa.float64(), nullable=False),
                pa.field("source", pa.string(), nullable=False),
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges many stations, so no single geo.features row backs it
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("feature_id", pa.string(), nullable=True),
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        ),
        sort_columns=WEATHER_OBSERVATIONS_GRAIN,
    )
)

WEATHER_OBSERVATIONS_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=WEATHER_OBSERVATIONS_STREAM,
        strategy=GridAggregation(
            longitude_column="longitude",
            latitude_column="latitude",
            # `observed_at` is NOT a key column, and that distinction is the whole coarsening.
            # Keying on an instant would keep two stations 0.005 degrees apart but reporting a few
            # seconds apart as two rows, so a z0 tier would hold very nearly as many rows as z13 and
            # the rung would cost bytes without buying resolution. The DAY is the time grain here;
            # the instant becomes provenance.
            key_columns=("observed_day",),
            aggregations=(
                ColumnAggregation("observed_at", "max"),  # newest instant among the merged readings
                ColumnAggregation("external_id", "null"),  # one base row's identity; a merged cell has none
                ColumnAggregation("temperature_c", "mean"),  # intensive: does not add across cells
                ColumnAggregation("relative_humidity_pct", "mean"),  # intensive: does not add across cells
                ColumnAggregation("wind_speed_ms", "mean"),  # intensive: does not add across cells
                # WIND DIRECTION IS CIRCULAR AND HAS NO ARITHMETIC MEAN. Averaging 350 and 10
                # degrees gives 180 -- due south for two nearly-north winds. An honest coarse
                # bearing needs a VECTOR mean (atan2 of the mean sine and cosine, weighted by
                # speed), which the closed aggregate vocabulary cannot express per column. Nulled
                # rather than fabricated: no direction is recoverable, a wrong one is not.
                ColumnAggregation("wind_direction_deg", "null"),
                # NOT `sum`. Depth is not additive across the stations reporting it: four stations
                # in one coarse cell each measuring 1 mm did not see 4 mm fall, and this lane's rows
                # are per-station instantaneous polls, so summing double-counts along BOTH axes at
                # once -- stations per cell and polls per station. Every sibling measurement above
                # takes `mean` for the same reason; precipitation only looks additive.
                ColumnAggregation("precipitation_mm", "mean"),
                ColumnAggregation("source", "first"),  # constant across the lane
                ColumnAggregation("feature_id", "null"),  # one base row's identity; a merged cell has none
                ColumnAggregation("ingested_at", "max"),  # newest persistence instant among merged rows
            ),
        ),
        # Relaxed to nullable ONLY so the coarse rungs above may null them. Named here so a
        # NULL at the base rung still fails the write loudly, as it did before the zoom axis.
        base_non_null_columns=("external_id", "feature_id", "wind_direction_deg"),
    )
)
