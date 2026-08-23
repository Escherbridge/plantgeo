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
                pa.field("external_id", pa.string(), nullable=False),
                pa.field("temperature_c", pa.float64(), nullable=False),
                pa.field("relative_humidity_pct", pa.float64(), nullable=False),
                pa.field("wind_speed_ms", pa.float64(), nullable=False),
                pa.field("wind_direction_deg", pa.float64(), nullable=False),
                pa.field("precipitation_mm", pa.float64(), nullable=False),
                pa.field("source", pa.string(), nullable=False),
                pa.field("feature_id", pa.string(), nullable=False),
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        ),
        sort_columns=WEATHER_OBSERVATIONS_GRAIN,
    )
)
