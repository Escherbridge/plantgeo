-- Purpose: D2's parity ground list for the sensors lane -- count every calendar day PostgreSQL's
--          geo.features holds an exportable sensors reading for, and how many (sensor_id, day,
--          measurement) rows -- the registered Parquet grain -- land on each day.
-- Loaded by: agri_data_service.pipeline.direct.sensors.parity
-- Params: layer_id (uuid, passed as text) -- geo.layers.id for the `sensors` layer, resolved once
--         by the caller via resolve_layer_id.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS MIRRORS sql/pipeline/sensors_day_export.sql'S winning_observation REDUCTION AND MEASUREMENT
-- FAN-OUT EXACTLY, generalized across every day rather than one bound `observed_day`, and across
-- every station rather than one bound `station_ids` batch. Matching that reduction matters: a
-- parity receipt built on a looser count (e.g. one row per feature rather than one row per winning
-- measurement) could report "matched" against a row shape the existing Postgres-reading lane
-- adapter (`pipeline/lanes/sensors.py::export_sensors_day`) would never actually export.
--
-- WHY layer.id = CAST(:layer_id AS uuid) INSTEAD OF THE EXPORT QUERY'S layer.name = 'sensors': the
-- caller already resolved `sensors` to a layer_id via resolve_layer_id, the same convention
-- sql/pipeline/direct/weather_observations/postgres_day_counts.sql uses -- both predicates resolve
-- to the identical single row, and binding the id keeps this file consistent with its sibling
-- parity queries rather than re-deriving a name join. `layer.is_public IS TRUE` is kept because the
-- export query enforces it and a parity count must count exactly what the export would.
--
-- WHY POSTGRES IS STILL THE GROUND LIST, AND WHY THIS NEVER LISTS THE WHOLE PARQUET STREAM. Same
-- reasoning as the weather-observations twin: this lane's direct writer can publish a day Postgres
-- never held -- any day from its own deployment forward, or any day it RECOVERS out of NWS's live
-- retention before Postgres last ran (see pipeline/direct/sensors/source.py, "rolling floor") -- and
-- that is not under-coverage. D2 only requires Parquet to cover what Postgres ALREADY holds, so this
-- query is bounded by construction to exactly the days Postgres counts; the caller never needs a
-- whole-bucket `list_partition_keys()` over the Parquet stream (see parity.py's module docstring).
--
-- THE MALFORMED-TIMESTAMP GUARD (pg_input_is_valid) and THE DAY-BOUNDARY RULE
-- (geo.feature_observation_day, never a cast of observed_at) are inherited verbatim from
-- sensors_day_export.sql's own header -- see that file for the full rationale, including why a
-- pushed row's `observedAt` cannot be assumed parseable and why the day must never be re-derived
-- from a zoned instant.
--
-- How this query works, clause by clause:
--
--   winning_observation CTE
--     Reproduces the day-export query's DISTINCT ON reduction, widened from `(sensor_id)` (that
--     query already fixes the day via its WHERE clause) to `(sensor_id, observed_day)` here, since
--     this query counts every day at once. The ORDER BY keeps the identical tie-break --
--     `observedAt DESC` as TEXT, then `feature.id DESC` -- so the winner picked here is always the
--     same physical row the day-export query would have picked for that station-day.
--
--   CROSS JOIN LATERAL jsonb_each(...) AS measurement(key, value)
--     The same per-measurement fan-out the day-export query runs, excluding `textDescription` (free
--     text, not one of the sixteen measurement fields).
--
--   GROUP BY / ORDER BY winning_observation.observed_day
--     Collapses the per-measurement rows into one row per calendar day -- the shape the caller's
--     day-by-day Parquet comparison walks -- in deterministic calendar order.
WITH winning_observation AS (
    SELECT DISTINCT ON (feature.properties ->> 'sensor_id', geo.feature_observation_day(feature.properties))
        geo.feature_observation_day(feature.properties) AS observed_day,
        feature.properties -> 'readings' AS readings
    FROM geo.features AS feature
    JOIN geo.layers AS layer ON layer.id = feature.layer_id
    WHERE layer.id = CAST(:layer_id AS uuid)
      AND layer.is_public IS TRUE
      AND feature.status = 'published'
      AND pg_input_is_valid(feature.properties ->> 'observedAt', 'timestamptz')
      AND geo.feature_observation_day(feature.properties) IS NOT NULL
    ORDER BY
        feature.properties ->> 'sensor_id',
        geo.feature_observation_day(feature.properties),
        feature.properties ->> 'observedAt' DESC,
        feature.id DESC
)
SELECT
    winning_observation.observed_day,
    COUNT(*) AS row_count
FROM winning_observation
CROSS JOIN LATERAL jsonb_each(COALESCE(winning_observation.readings, '{}'::jsonb)) AS measurement(key, value)
WHERE measurement.key <> 'textDescription'
GROUP BY winning_observation.observed_day
ORDER BY winning_observation.observed_day
