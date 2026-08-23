-- fire_detections_day_export
-- Purpose: produce ONE calendar day of NASA FIRMS active-fire detections, pre-aggregated to the
--          (cell_longitude, cell_latitude, observed_day) grain, for writing to
--          `layer=fire-detections/kind=observed/year=/month=/day=/part-N.parquet`.
-- Loaded by: agri_data_service.pipeline.lanes.fire_detections
-- Params: layer_id (uuid, passed as text) -- geo.layers.id for the `fire-detections` layer,
--         resolved by the caller once (see the read function's docstring for why this stays a
--         bound literal rather than a `layers.name` join predicate),
--         observed_day (date -- the one UTC calendar day being exported, matching what
--         geo.feature_observation_day would compute for a row in the returned set)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- WHY THIS IS AN AGGREGATE, NOT A ROW-FOR-ROW EXPORT OF geo.features: see
-- warehouse/schemas/fire_detections.py's module docstring, "GRAIN DECISION" -- the raw per-detection
-- grain cannot satisfy layer-lanes.md section 2's identical-observed/forecast-grain rule, so this
-- lane exports the cell-day aggregate its own contract recommends
-- (docs/lanes/fire-detections.md section 7).
--
-- WHY layer_id IS A BOUND LITERAL, NOT A JOIN ON geo.layers.name: the same proven shape
-- weather_observations_day_export.sql uses -- a literal equality on the leading column of
-- ix_features_layer_observation_day ((layer_id, geo.feature_observation_day(properties))
-- INCLUDE (geometry_id) WHERE status = 'published', drizzle/0031_observation_day_axis.sql:47-49)
-- gives the planner an Index Cond directly, with no join whose plan shape depends on geo.layers
-- row-count estimates.
--
-- WHY THIS QUERY NEEDS NO CELL-BATCHING LOOP, UNLIKE signal_plane_day_export.sql: that plane
-- shards by cell_id because agri.signal_observation carries no index leading on observed_at.
-- ix_features_layer_observation_day exists precisely for a day-scoped, one-layer read like this
-- one, so a single execution rides the index end to end -- the same reasoning
-- weather_observations_day_export.sql already documents for its own read.
--
-- THE GOVERNANCE FILTERS (status = 'published', geometry_id IS NOT NULL, geom IS NOT NULL) mirror
-- weather_observations_day_export.sql's three-filter contract: only rows the map itself is willing
-- to serve, only rows linked into the geometry dimension, and only rows with an actual shape to
-- grid. A detection failing any of these is real but cannot be placed, so it is excluded rather
-- than counted into a fabricated (NULL, NULL) cell.
--
-- THE GRID: 0.005 degrees, the finest of the three resolutions the existing production tile
-- rollups already use (geo.tile_fire_detections_z9, conductor/RUNBOOK.md:5130) --
-- see warehouse/schemas/fire_detections.py's "WHY 0.005 DEGREES" note. The literal is hardcoded
-- here rather than templated in from a Python constant: it is a fixed, non-user-supplied value
-- (sql.md's exception for a module constant baked in at load time), and a second, human-readable
-- copy of it lives in FIRE_DETECTIONS_CELL_SIZE_DEGREES purely as documented context.
--
-- How this query works, clause by clause:
--
--   ST_X(feature.geom) / ST_Y(feature.geom)
--     The detection's stored point (`geo.sync_feature_geom_from_properties`,
--     drizzle/0001_handy_riptide.sql:151-161), read as longitude/latitude in WGS84 degrees --
--     the same coordinate system every geometry in this warehouse uses.
--
--   ::numeric casts before floor()
--     Snapping is done in NUMERIC arithmetic, not double precision, specifically to avoid binary
--     floating-point noise at the 0.005 boundary (e.g. a double-precision 0.015 that is actually
--     stored as 0.014999999999998...). floor(numeric) is exact; the result is cast to double
--     precision only in the final SELECT, once every detection sharing a true grid cell has
--     already been grouped together on the exact numeric value.
--
--   CASE WHEN jsonb_typeof(feature.properties -> 'frp') = 'number' THEN ... END
--     The same defensive idiom drizzle/0015_tile_observation_day.sql's burn_severity_tiles uses
--     for `acres`: only read `frp` as a number when the stored value really is one, so a
--     detection whose product never publishes FRP (a genuine, expected absence -- see
--     ingest/firms.py:301-303, `frp` is added to properties only when the CSV column parses)
--     contributes NULL to the aggregate rather than raising or being silently miscast.
--
--   COUNT(frp)
--     Postgres's COUNT(<expression>) ignores NULLs by construction, so this counts exactly the
--     detections that contributed a real number to frp_sum -- the frp_observation_count column.
--
--   COUNT(*) FILTER (WHERE confidence_normalized = 'high')
--     FILTER restricts this one aggregate to high-confidence detections
--     (properties.confidenceNormalized, ingest/firms.py:157-171,232-243) while detection_count
--     still counts every detection in the group. A NULL confidence_normalized (a product FIRMS
--     did not let normalize) compares as neither true nor false and is correctly excluded from
--     this counter without a separate IS NOT NULL guard.
--
--   geo.feature_observation_day(feature.properties) = CAST(:observed_day AS date)
--     The day predicate. Equality, not a range, because this statement always exports exactly one
--     day; equality against a NULL-returning function excludes an undated row for free.
--
--   GROUP BY cell_longitude, cell_latitude
--     observed_day is not in the GROUP BY list because it is already pinned to one value by the
--     WHERE clause -- every row in the result shares the same day, which is why it can be selected
--     as the bound parameter directly rather than as an aggregate.
WITH day_detections AS (
    SELECT
        ST_X(feature.geom)::numeric AS longitude,
        ST_Y(feature.geom)::numeric AS latitude,
        CASE
            WHEN jsonb_typeof(feature.properties -> 'frp') = 'number'
                THEN (feature.properties ->> 'frp')::double precision
        END AS frp,
        feature.properties ->> 'confidenceNormalized' AS confidence_normalized,
        (feature.properties ->> 'observedAt')::timestamptz AS observed_at
    FROM geo.features AS feature
    WHERE feature.layer_id = CAST(:layer_id AS uuid)
      AND feature.status = 'published'
      AND feature.geometry_id IS NOT NULL
      AND feature.geom IS NOT NULL
      AND geo.feature_observation_day(feature.properties) = CAST(:observed_day AS date)
),
gridded AS (
    SELECT
        floor(longitude / 0.005) * 0.005 AS cell_longitude,
        floor(latitude / 0.005) * 0.005 AS cell_latitude,
        frp,
        confidence_normalized,
        observed_at
    FROM day_detections
)
SELECT
    cell_longitude::double precision AS cell_longitude,
    cell_latitude::double precision AS cell_latitude,
    CAST(:observed_day AS date) AS observed_day,
    COUNT(*)::bigint AS detection_count,
    SUM(frp) AS frp_sum,
    COUNT(frp)::bigint AS frp_observation_count,
    (COUNT(*) FILTER (WHERE confidence_normalized = 'high'))::bigint AS high_confidence_detection_count,
    MAX(observed_at) AS newest_observed_at
FROM gridded
GROUP BY cell_longitude, cell_latitude
ORDER BY cell_longitude, cell_latitude
