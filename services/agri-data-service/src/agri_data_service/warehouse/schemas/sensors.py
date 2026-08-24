"""Parquet schema for the `sensors` lane: NOAA NWS ground-station readings, one row per
station-day-measurement.

Layer L1: may import `foundation`; may NOT import method, pipeline, planes, or interface.
See `docs/lanes/sensors.md` for the source, cadence, and grain evidence this schema is built
from, and `sql/pipeline/sensors_day_export.sql` for how one day's rows are produced.

DECISION -- export the sixteen captured measurement fields, not just the four currently served.
Two measured facts (docs/lanes/sensors.md sections 4-5): this lane captures sixteen NWS measurements
per hourly report (`OBSERVATION_MEASUREMENTS`, ingest/sensors.py:104-121) but `geo.sensor_tiles`
-- the only thing that reads this layer today -- projects just four (network, sensor_id,
station_name, observed_at); and api.weather.gov keeps only a rolling ~6-day window
(`NWS_OBSERVATION_RETENTION`, ingest/sensors.py:94-96), so a measurement not captured here is
gone from the source within a week, permanently. Exporting only the served columns would freeze
today's serving gap into the warehouse forever, with no way to backfill it later. This schema
carries the full sixteen instead, at a tall (one-row-per-measurement) grain so a station that
did not report a given field simply contributes no row for it, rather than a wide table of
mostly-null columns.
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

SENSORS_STREAM: Final = "sensors"

# The day-grain the map already renders and the grain a future 30-day Monte Carlo must share
# (docs/lanes/sensors.md sections 4 and 7): one row per station per day per reported measurement, taken
# from that day's LATEST NWS observation -- the same DISTINCT ON winner `geo.sensor_tiles`
# computes (drizzle/0038_tile_low_zoom_routing.sql:397-427), fanned out past that function's four
# served columns into the sixteen captured measurement fields.
SENSORS_GRAIN: Final[tuple[str, ...]] = ("sensor_id", "observed_day", "measurement_name")

SENSORS_SCHEMA: Final = register_stream_schema(
    ParquetStreamSchema(
        name=SENSORS_STREAM,
        arrow_schema=pa.schema(
            [
                # The station's native id (`stationIdentifier`), the entity key `geo.geometry`
                # keys one Type-2 version chain by (ingest/sensors.py:433-439) -- never the
                # per-reading id.
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges many stations, so no single sensor_id describes it
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("sensor_id", pa.string(), nullable=True),
                pa.field("station_name", pa.string(), nullable=True),
                pa.field("network", pa.string(), nullable=True),
                # Derived from `geo.feature_observation_day`, never a re-zoned cast of
                # `observed_at` -- see the SQL file's header for why those two must not be
                # conflated.
                pa.field("observed_day", pa.date32(), nullable=False),
                # The winning report's own instant. Provenance only: the day this row is filed
                # under comes from `observed_day` above, not from truncating this column.
                pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
                # One of the sixteen `OBSERVATION_MEASUREMENTS` names, verbatim.
                pa.field("measurement_name", pa.string(), nullable=False),
                pa.field("value", pa.float64(), nullable=False),
                # NWS's own unit and quality tag, kept verbatim and never normalized
                # (docs/lanes/sensors.md SS4) -- a consumer reads `unit_code` per row, never
                # assumes one fixed unit across the stream. Nullable: NWS does not always send
                # `qualityControl`, and the ingest code does not fabricate one when absent
                # (`_measurement`, ingest/sensors.py:350-364).
                pa.field("unit_code", pa.string(), nullable=True),
                pa.field("quality_control", pa.string(), nullable=True),
                # The winning `geo.features.id` this station-day's readings were read from --
                # traceable provenance back to the row Postgres actually holds.
                # NULLABLE because the coarse rungs null it:
                # a coarse cell merges many stations, so no single geo.features row backs it
                # (see this module's TierDerivation). The base z13 rung always carries it.
                pa.field("feature_id", pa.string(), nullable=True),
                # The ML leakage boundary (`geo.features.data_available_at`,
                # src/lib/server/db/schema.ts:235-239) -- distinct from `observed_at` (when the
                # reading happened). Left UNMEASURED for this layer specifically as of
                # conductor/RUNBOOK.md:907 (an existence probe timed out on it); carried through
                # rather than dropped or fabricated, so a training consumer can filter on it once
                # its population status is actually confirmed.
                pa.field("data_available_at", pa.timestamp("us", tz="UTC"), nullable=True),
                # Station position from `geo.features.geom`, the maintained point
                # `geo.sync_feature_geom_from_properties` derives from the station's NWS-reported
                # location. NULL when a station was ingested with no position in its GeoJSON
                # (ingest/sensors.py:433-439), so GridAggregation must permit it. Holds the cell
                # ORIGIN's centroid, not any single observation's location.
                pa.field("station_longitude", pa.float64(), nullable=True),
                pa.field("station_latitude", pa.float64(), nullable=True),
            ]
        ),
        sort_columns=SENSORS_GRAIN,
    )
)

SENSORS_TIER_DERIVATION: Final = register_tier_derivation(
    TierDerivation(
        stream=SENSORS_STREAM,
        strategy=GridAggregation(
            longitude_column="station_longitude",
            latitude_column="station_latitude",
            # Coarse grain: one row per day per measurement type per coarsened grid cell. A cell
            # that contains multiple stations reports one aggregated value per measurement, not one
            # per station — sensor_id/feature_id/station_name all null because no single station
            # represents the merge.
            key_columns=("observed_day", "measurement_name"),
            aggregations=(
                ColumnAggregation("sensor_id", "null"),  # unique to one station, no honest merge
                ColumnAggregation("station_name", "null"),  # unique to one station
                ColumnAggregation("network", "first"),  # constant across stations (all NWS)
                ColumnAggregation("observed_at", "max"),  # newest reading among merged stations
                ColumnAggregation("value", "mean"),  # intensive measurement: temperature, humidity, etc.
                ColumnAggregation("unit_code", "first"),  # constant per measurement_name
                ColumnAggregation("quality_control", "first"),  # NWS QC code, assumed constant
                ColumnAggregation("feature_id", "null"),  # unique to one station's feature row
                ColumnAggregation("data_available_at", "max"),  # newest availability across stations
            ),
        ),
    )
)
