-- weather_observations_day_export
-- Purpose: produce ONE calendar day of the `weather-observations` current-conditions side lane
--          (Open-Meteo current-conditions rows in geo.features, NOT the governed archive behind
--          agri.signal_observation -- see warehouse/schemas/weather_observations.py's docstring
--          for the source confirmation), for writing to
--          `layer=weather-observations/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.weather_observations
-- Params: layer_id (uuid, passed as text) -- geo.layers.id for the `weather-observations` layer,
--         resolved by the caller once (see the read function's docstring for why this stays a
--         bound literal rather than a `layers.name` join predicate),
--         observed_day (date -- the one UTC calendar day being exported, matching what
--         geo.feature_observation_day would compute for a row in the returned set)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- WHY layer_id IS A BOUND LITERAL, NOT A JOIN ON geo.layers.name: ingest/validation/queries.py's
-- own one-layer census (`_ONE_LAYER_SCOPE`) binds `features.layer_id = CAST(:layer_id AS uuid)`
-- for exactly this reason, proven on production -- a literal equality on the leading column of
-- `ix_features_layer_observation_day` (over `(layer_id, geo.feature_observation_day(properties))`,
-- see sql/ingest/observed_days.sql) gives the planner an Index Cond directly, with no join whose
-- plan shape depends on `geo.layers` row-count estimates. This statement reuses that same proven
-- predicate shape rather than re-deriving one.
--
-- UNLIKE THE SIGNAL PLANE, THIS QUERY NEEDS NO CELL-BATCHING LOOP. `agri.signal_observation` has
-- no index leading on `observed_at`, which is why `signal_plane_day_export.sql` shards its read by
-- cell_id. `geo.features` carries `ix_features_layer_observation_day` precisely for a day-scoped,
-- one-layer read like this one, so a single execution rides the index end to end.
--
-- THE THREE GOVERNANCE FILTERS BELOW MIRROR sql/ingest/observed_days.sql'S CENSUS EXACTLY --
-- published status, geometry-linked, and a day the row can actually be dated to -- so this export
-- can never disagree with what the map itself is willing to serve for this layer.
--
-- THE KEY-PRESENCE GUARD (properties ?& ARRAY[...]) is defensive, not decorative: every write this
-- producer performs validates and supplies all seven keys before a row reaches geo.features
-- (`_bounded_value`, ingest/open_meteo.py:348-356, raises rather than persisting a partial
-- reading), so today this filter should remove nothing. It exists so a future producer change, or
-- a pre-existing row from before that validation shipped, cannot silently cast a missing key's NULL
-- into a column this schema declares NOT NULL.
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
--     exporting it would hand a downstream reader data no serving surface agrees exists.
--
--   AND features.geometry_id IS NOT NULL
--     Only rows linked into the geometry dimension, matching the same gate observed_days.sql
--     applies -- an unlinked row has no shape a serving reader could draw.
--
--   AND geo.feature_observation_day(features.properties) = CAST(:observed_day AS date)
--     The day predicate. Equality (not a range) is correct here because this statement always
--     exports exactly one day; equality against a NULL-returning function excludes an undated row
--     for free, with no separate IS NOT NULL needed.
--
--   AND features.properties ?& ARRAY[...]
--     `?&` (jsonb "has all of these keys") is the defensive key-presence guard described above.
--
--   properties -> 'geometry' -> 'coordinates' ->> 0 / ->> 1
--     `->` descends into the jsonb document keeping the result as jsonb; the final `->>` on the
--     GeoJSON Point's coordinate array reads element 0 (longitude) or 1 (latitude) out as text,
--     which the CAST below turns into a real double. GeoJSON's own ordering is [longitude,
--     latitude] (ingest/open_meteo.py:416), which is why longitude is index 0.
--
--   (features.properties ->> 'observedAt')::timestamptz
--     `->>` reads the field as text; PostgreSQL parses the stored `...Z`-suffixed ISO-8601 string
--     (`format_javascript_timestamp`, ingest/identity.py:126-136) directly as a UTC instant.
--
--   geo.feature_observation_day(features.properties)
--     The same database function the census and the map's date slider both call
--     (drizzle/0018_fire_discovery_observation_day.sql). Reusing it rather than re-deriving the day
--     from observed_at in SQL means this export can never bucket a row onto a different day than
--     the one the map already shows it on.
--
--   features.id::text / features.created_at
--     The warehouse row's own identity and persistence time -- distinct from observed_at (when the
--     reading happened) -- carried through as provenance so a Parquet row can always be traced back
--     to the geo.features row it came from.
SELECT
    (features.properties -> 'geometry' -> 'coordinates' ->> 0)::double precision AS longitude,
    (features.properties -> 'geometry' -> 'coordinates' ->> 1)::double precision AS latitude,
    (features.properties ->> 'observedAt')::timestamptz AS observed_at,
    geo.feature_observation_day(features.properties) AS observed_day,
    features.properties ->> 'id' AS external_id,
    (features.properties ->> 'temperature')::double precision AS temperature_c,
    (features.properties ->> 'humidity')::double precision AS relative_humidity_pct,
    (features.properties ->> 'windSpeed')::double precision AS wind_speed_ms,
    (features.properties ->> 'windDirection')::double precision AS wind_direction_deg,
    (features.properties ->> 'precipitation')::double precision AS precipitation_mm,
    features.properties ->> 'source' AS source,
    features.id::text AS feature_id,
    features.created_at AS ingested_at
FROM geo.features AS features
WHERE features.layer_id = CAST(:layer_id AS uuid)
  AND features.status = 'published'
  AND features.geometry_id IS NOT NULL
  AND geo.feature_observation_day(features.properties) = CAST(:observed_day AS date)
  AND features.properties ?& ARRAY[
        'id', 'observedAt', 'geometry', 'source',
        'temperature', 'humidity', 'windSpeed', 'windDirection', 'precipitation'
      ]
ORDER BY latitude, longitude, observed_at
