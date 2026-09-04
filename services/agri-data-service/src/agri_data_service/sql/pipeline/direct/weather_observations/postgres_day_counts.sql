-- Purpose: D2's parity ground list -- count every calendar day PostgreSQL's `geo.features` holds an
--          exportable weather-observations row for, and how many rows land on each day.
-- Loaded by: agri_data_service.pipeline.direct.weather_observations.parity
-- Params: layer_id (uuid, passed as text) -- geo.layers.id for the `weather-observations` layer,
--         resolved by the caller once via resolve_layer_id.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- THIS MIRRORS `sql/pipeline/weather_observations_day_export.sql`'S WHERE CLAUSE AND KEY-PRESENCE
-- GUARD EXACTLY, minus that file's single-day equality predicate (this counts every day the layer
-- holds, not one) and its per-row SELECT list (this returns one row per day, not one row per
-- reading). Matching that predicate matters: a parity receipt built on a looser filter could report
-- "matched" against rows the existing Postgres-reading lane adapter would never actually export.
--
-- WHY POSTGRES IS STILL THE GROUND LIST, AND WHY THIS NEVER LISTS THE WHOLE PARQUET STREAM. This
-- lane's direct writer can publish a day Postgres never held (any day from its own deployment
-- forward), and that is not under-coverage -- D2 only requires Parquet to cover what Postgres
-- ALREADY holds. This query is bounded by construction to exactly that: it counts Postgres days and
-- nothing else, so the caller's day-by-day walk never needs a whole-bucket `list_partition_keys()`
-- over the Parquet stream (see `parity.py`'s module docstring for why that matters).
--
-- How this query works, clause by clause:
--
--   FROM geo.features AS features
--     No join to geo.layers: the caller already resolved `weather-observations` to a layer_id, so
--     the WHERE clause below can bind it directly.
--
--   WHERE features.layer_id = CAST(:layer_id AS uuid)
--     Scopes the scan to this one lane's rows and is the leading column
--     ix_features_layer_observation_day needs to be used as an Index Cond.
--
--   AND features.status = 'published'
--     Only rows the map itself can show. A draft or superseded row is real but invisible, so
--     counting it would compare Parquet against a population nothing would ever have exported.
--
--   AND features.geometry_id IS NOT NULL
--     Only rows linked into the geometry dimension, matching the same gate the day-export query and
--     the observed-day census both apply -- an unlinked row has no shape a serving reader could draw.
--
--   AND features.properties ?& ARRAY[...]
--     `?&` (jsonb "has all of these keys") is a defensive key-presence guard: every write this
--     producer performs validates and supplies all seven value keys before a row reaches
--     geo.features (`_bounded_value`, ingest/open_meteo.py), so today this filter should remove
--     nothing. It exists so a future producer change, or a pre-existing row from before that
--     validation shipped, cannot silently count a row the export query itself would reject.
--
--   geo.feature_observation_day(features.properties) AS observed_day
--     The same database function the census, the export, and the map's date slider all call
--     (drizzle/0018_fire_discovery_observation_day.sql). Reusing it rather than re-deriving the day
--     from observedAt in SQL means this count can never bucket a row onto a different day than the
--     one the map already shows it on.
--
--   GROUP BY geo.feature_observation_day(features.properties)
--     Collapses the per-row scan into one row per calendar day, which is the shape the caller's
--     day-by-day Parquet comparison walks.
--
--   HAVING geo.feature_observation_day(features.properties) IS NOT NULL
--     Excludes an undated row (the function returned NULL) from the count entirely, rather than
--     reporting it under a NULL "day" the caller could never compare against a real Parquet day.
--
--   ORDER BY observed_day
--     A stable order for a deterministic, reproducible day-by-day walk; not required for
--     correctness, since the caller reduces this into a dict, but it keeps a printed receipt or a
--     debugging session reading the rows in calendar order.
SELECT
    geo.feature_observation_day(features.properties) AS observed_day,
    COUNT(*) AS row_count
FROM geo.features AS features
WHERE features.layer_id = CAST(:layer_id AS uuid)
  AND features.status = 'published'
  AND features.geometry_id IS NOT NULL
  AND features.properties ?& ARRAY[
        'id', 'observedAt', 'geometry', 'source',
        'temperature', 'humidity', 'windSpeed', 'windDirection', 'precipitation'
      ]
GROUP BY geo.feature_observation_day(features.properties)
HAVING geo.feature_observation_day(features.properties) IS NOT NULL
ORDER BY observed_day
