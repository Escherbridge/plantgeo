-- sensors_day_export
-- Purpose: produce ONE calendar day of the sensors lane's day-grain export
--          (sensor_id, observed_day, measurement_name), for writing to
--          layer=sensors/kind=observed/year=/month=/day=/part-N.parquet.
-- Loaded by: agri_data_service.pipeline.lanes.sensors
-- Params: observed_day (date -- the one calendar day being exported; matches the day
--         geo.feature_observation_day derives, never a re-zoned re-derivation of it),
--         station_ids (text[] -- the station batch; NEVER empty, see the batching note below)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS QUERY REPRODUCES geo.sensor_tiles' OWN REDUCTION, THEN GOES FURTHER.
-- geo.sensor_tiles (drizzle/0038_tile_low_zoom_routing.sql:382-431) already picks one winning
-- row per (sensor_id, geom, day) with DISTINCT ON, ordered by observedAt DESC then id DESC, but
-- its SELECT list stops at four columns (network, sensor_id, station_name, observed_at) -- it
-- never reads the `readings` object a station actually reported. Per docs/lanes/sensors.md
-- section 4, no serving surface in this repo reads a single measurement from this lane today. This query
-- exports the sixteen captured measurement fields anyway (OBSERVATION_MEASUREMENTS,
-- ingest/sensors.py:104-121), because api.weather.gov keeps only a rolling 6-day window
-- (ingest/sensors.py:94-96): a measurement this query does not capture today is gone from the
-- source within a week and can never be recovered later. Exporting only the four columns the
-- tile currently draws would make that loss permanent.
--
-- THE DAY BOUNDARY IS geo.feature_observation_day(properties), NEVER A CAST OF THE TIMESTAMP.
-- That function reads the first 10 characters of properties->>'observedAt'
-- (drizzle/0015_tile_observation_day.sql:21-57) -- the day the PUBLISHER named. Deriving the day
-- by casting the timestamp to a zoned instant and truncating would silently disagree with it --
-- that exact drift moved 6,279 production water-gauge rows onto the day after the one they name,
-- per that function's own header -- and with whatever the map's time slider currently shows for
-- this lane. `observed_day` below is therefore projected straight from the bound parameter,
-- which the WHERE clause already constrains to agree with geo.feature_observation_day; the
-- `observed_at` column is a plain timestamptz cast of the same property for provenance only and
-- must never be used to recompute the day.
--
-- WHY THE STATION BATCH IS A PARAMETER RATHER THAN ONE ALL-STATIONS READ PER DAY:
-- geo.features carries no index on the expression geo.feature_observation_day(properties), so a
-- day-scoped read still walks every row idx_features_layer_status hands back for this layer --
-- a few hundred thousand across this lane's whole history as of the last measurement
-- (drizzle/0038's header: 186,904 rows, 23 days, measured 2026-08-21) and growing every hour the
-- producer runs (the source's 6-day retention bounds what NWS will still ANSWER for, not how
-- many rows this warehouse has already kept). Batching by station_ids bounds each round trip's
-- array parameter and result set the same way CELL_BATCH_SIZE does for the signal plane
-- (sql/pipeline/signal_plane_day_export.sql), so a caller enumerating the roster from
-- geo.geometry (this lane's per-station Type-2 dimension, ingest/sensors.py:433-439) can walk it
-- in bounded chunks instead of one unbounded IN-list.
--
-- THE MALFORMED-TIMESTAMP GUARD: pg_input_is_valid(..., 'timestamptz').
-- The sensors layer has a second, older producer -- a plain HTTP push route
-- (src/app/api/ingest/sensors/route.ts, docs/lanes/sensors.md section 3) that never ran through this
-- service's `_parse_upstream_timestamp` UTC-offset check (ingest/sensors.py:414-422). A pushed
-- row's `observedAt` is therefore not guaranteed parseable. Casting it straight to timestamptz
-- would raise and blank the whole day's export on one bad row; pg_input_is_valid instead lets
-- that one row's measurements drop out quietly, the same defensive shape
-- geo.feature_observation_day itself uses before calling to_date (drizzle/0015, line ~42-44).
--
-- THE READING FAN-OUT: CROSS JOIN LATERAL jsonb_each(...).
-- Each winning row's `readings` property is a JSON object keyed by whichever measurements that
-- one station reported that instant (observation_readings, ingest/sensors.py:367-377) -- a
-- station that did not report windGust simply omits the key, never a null or a zero. jsonb_each
-- is a set-returning function that unnests that sparse object into one (key, value) pair per
-- call; LATERAL lets it run once per winning_observation row, seeing that row's own `readings`
-- value, which an ordinary (non-LATERAL) join cannot do. `textDescription` is excluded: it is
-- free text, not one of the sixteen measurement fields, and carries no numeric `value` to cast.
WITH winning_observation AS (
    -- DISTINCT ON keeps one row per station: the latest report that day, matching
    -- geo.sensor_tiles' own reduction. `station_ids`/`observed_day` narrow the candidate set
    -- before the DISTINCT ON ever runs.
    SELECT DISTINCT ON (feature.properties ->> 'sensor_id')
        feature.id AS feature_id,
        feature.properties ->> 'sensor_id' AS sensor_id,
        feature.properties ->> 'station_name' AS station_name,
        feature.properties ->> 'network' AS network,
        (:observed_day)::date AS observed_day,
        (feature.properties ->> 'observedAt')::timestamptz AS observed_at,
        feature.properties -> 'readings' AS readings,
        feature.data_available_at,
        feature.geom
    FROM geo.features AS feature
    JOIN geo.layers AS layer ON layer.id = feature.layer_id
    WHERE layer.name = 'sensors'
      AND layer.is_public IS TRUE
      AND feature.status = 'published'
      AND feature.properties ->> 'sensor_id' = ANY(:station_ids)
      AND pg_input_is_valid(feature.properties ->> 'observedAt', 'timestamptz')
      AND geo.feature_observation_day(feature.properties) = (:observed_day)::date
    -- The DISTINCT ON key first, as PostgreSQL requires; then the tie-break, matching
    -- geo.sensor_tiles exactly -- observedAt DESC as TEXT (ISO-8601 with a UTC offset sorts
    -- chronologically when compared lexically) then feature id DESC so the winner is total,
    -- never arbitrary, among equal timestamps.
    ORDER BY
        feature.properties ->> 'sensor_id',
        feature.properties ->> 'observedAt' DESC,
        feature.id DESC
)
SELECT
    winning_observation.sensor_id,
    winning_observation.station_name,
    winning_observation.network,
    winning_observation.observed_day,
    winning_observation.observed_at,
    measurement.key AS measurement_name,
    (measurement.value ->> 'value')::double precision AS value,
    measurement.value ->> 'unitCode' AS unit_code,
    measurement.value ->> 'qualityControl' AS quality_control,
    winning_observation.feature_id::text AS feature_id,
    winning_observation.data_available_at,
    -- Station coordinates from the feature's geometry. These are the station's location
    -- (maintained by geo.sync_feature_geom_from_properties), NOT the representative point of
    -- any cell. A pushed row may lack geometry, so these are NULLABLE; ST_X(NULL) returns NULL
    -- as wanted, never a fabricated 0,0 (the Gulf of Guinea, the classic version of this bug).
    ST_X(winning_observation.geom) AS station_longitude,
    ST_Y(winning_observation.geom) AS station_latitude
FROM winning_observation
CROSS JOIN LATERAL jsonb_each(COALESCE(winning_observation.readings, '{}'::jsonb)) AS measurement(key, value)
WHERE measurement.key <> 'textDescription'
