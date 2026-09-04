-- agent_feature_value_near_point
-- Purpose: return the published features of one geo.features layer that are dated to the caller's
--          day and lie nearest a point, each with its real distance in metres and its own
--          observation day.
-- Loaded by: agri_data_service.agent.tools
-- Params: surface_name (text -- a geo.layers.name, verbatim), day (date -- the day the map has
--         selected), longitude/latitude (double precision), radius_meters (double precision),
--         bbox_degrees (double precision -- half-width of the index prefilter box, computed for
--         this latitude), property_keys (text[] -- the property names allowed into the answer),
--         row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the spatial-proximity leg of section 11 of docs/layer-lane-standard.md, in one statement
-- parameterised by layer name rather than a bespoke one per layer.
--
-- IT NOW SERVES EXACTLY ONE LAYER, AND THE CALLER ENFORCES THAT. As of 2026-09-04 the ten
-- environmental feature layers are answered from their own Parquet lanes, and `interventions` is
-- the only surface still routed here. RUNBOOK section 0.26.1 keeps that lane in PostgreSQL on
-- purpose: an intervention is community data a user writes, not environmental data an upstream
-- publishes, so it has no registered Parquet lane and inventing one for it would be a fiction.
-- The statement stays parameterised because the surface still arrives from the caller and is still
-- checked against the catalogue there; it is not hard-coded here, so a second PostgreSQL-resident
-- community layer needs no new statement.
--
-- Its counterparts moved with the data: `observation_coverage_on_day` and
-- `observation_temporal_neighbors` are answered from the Parquet availability index in
-- `agent/warehouse.py`, and their two `.sql` files were deleted rather than left orphaned.
--
-- TWO INDEX DECISIONS CARRY THIS QUERY, and both are the reason it can run on a 3 GB box at all.
--
--   1. The DAY restriction rides geo.feature_observation_day(properties), the IMMUTABLE function
--      that defines what day a feature is dated to, through the expression index
--      ix_features_layer_observation_day on (layer_id, geo.feature_observation_day(properties))
--      WHERE status = 'published'. Without that index the day filter is evaluated per row, which
--      means detoasting every published feature's whole properties JSONB -- about 1,467 MB of
--      TOAST across 4.97 million rows. With it, one day of one layer is an index range.
--
--      Calling the function rather than re-deriving the day inline is not a style choice. The
--      tile functions and the slider axis both call it, and a second spelling of the same rule is
--      how the agent starts dating a feature to a different day than the map paints it on.
--
--   2. The DISTANCE restriction rides idx_features_geom, the GiST index over the geometry column,
--      through the `&&` bounding-box operator. The exact answer needs metres on the curved earth,
--      which means casting to geography -- and that cast makes the predicate unusable by a
--      geometry index, with no geography index on the table to fall back on. So the box test goes
--      first and the exact distance is computed only on what survives it. `&&` is a strict
--      superset of the exact test, so this changes how many rows are examined and never which
--      rows are returned.
--
-- bbox_degrees is computed in Python for the caller's own latitude, because a degree of longitude
-- shrinks toward the poles while a degree of latitude does not. A single hard-coded conversion
-- would clip the eastern and western edges of the box at high latitude and silently drop real
-- features, which is the failure mode this whole tool exists to prevent.
--
-- PROPERTIES ARE PROJECTED BY EXPLICIT KEY, never wholesale. `SELECT properties` on this table is
-- how a bounded row count becomes an unbounded byte count -- a fifty-row answer can still be tens
-- of megabytes of JSONB. The caller passes the handful of keys it is willing to show and this
-- statement builds a small object from exactly those.
--
-- How this query works, clause by clause:
--
--   WITH probe AS (SELECT ... AS geom)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. It builds the caller's coordinate once so the box test and the exact distance
--     read the same value.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the coordinate into a PostGIS point stamped with SRID 4326 (WGS84 -- ordinary GPS
--     longitude/latitude). Without the stamp PostGIS refuses to compare it against the stored
--     geometries, which are all 4326.
--
--   feature.layer_id = (SELECT id FROM geo.layers WHERE name = :surface_name)
--     Resolves the caller's layer NAME to its id via an uncorrelated subquery instead of joining
--     geo.layers onto the scan. geo.features is partitioned by layer_id, and :surface_name is a
--     bind parameter -- strictly worse for a join-based filter than even a literal, since the
--     planner has no value to fold at all until execution. A scalar subquery on the
--     (unpartitioned, 11-row) layers table runs once as an init-plan, and its single result is what
--     lets the Append node prune to one partition at execution time instead of opening all eleven.
--
--   feature.status = 'published'
--     geo.features carries a review status; drafts and rejected rows must not be quoted as
--     evidence. This also matches the partial index's own WHERE clause, which is what lets the
--     index answer the query.
--
--   geo.feature_observation_day(feature.properties) = CAST(day AS date)
--     The day restriction. The bind is wrapped in CAST(... AS date) rather than written with a
--     trailing double-colon cast because a double colon immediately after a bind name would stop
--     SQLAlchemy from recognising the bind at all.
--
--   ::geography / ST_DWithin / ST_Distance
--     Casts to the geography type so distance is measured in metres on the curved earth rather
--     than in degrees, whose real length varies with latitude. ST_DWithin is the exact radius
--     test and it runs only on the rows the box prefilter admitted. It is NOT redundant with the
--     box: a box drawn around a circle contains the circle plus its four corners, so a feature up
--     to about 1.4 times the radius away survives the box. Returning those would make the tool's
--     stated radius a lie, and a reader comparing distances against the radius it asked for would
--     find rows outside it. ST_Distance then reports the real distance, so relevance is earned
--     rather than assumed.
--
--   (SELECT jsonb_object_agg(...) FROM jsonb_each_text(...) WHERE key = ANY(property_keys))
--     A correlated subquery -- one that references the row it is attached to, so it is evaluated
--     once per returned feature. jsonb_each_text turns that feature's properties into key/value
--     rows, the WHERE keeps only the keys the caller allowed, and jsonb_object_agg reassembles
--     those into a small object. coalesce(..., '{}') supplies an empty object when a feature
--     carries none of the wanted keys, so the column is never null and a reader never has to
--     special-case it. The cost is bounded because the subquery runs only on the row_limit rows
--     that already survived the LIMIT above it.
--
--   ORDER BY distance_m ... LIMIT row_limit
--     A total order before the limit, nearest first. Truncating without one can repeat or skip
--     rows, because the database is otherwise free to return equal rows in any order.
WITH probe AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326) AS geom
),
nearby AS (
    SELECT
        feature.id AS feature_id,
        feature.properties AS properties,
        feature.geometry_id,
        feature.data_available_at,
        geo.feature_observation_day(feature.properties) AS observed_day,
        ST_Distance(feature.geom::geography, probe.geom::geography) AS distance_m,
        ST_X(ST_Centroid(feature.geom)) AS centroid_longitude,
        ST_Y(ST_Centroid(feature.geom)) AS centroid_latitude
    FROM geo.features AS feature
    CROSS JOIN probe
    WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = :surface_name)
      AND feature.status = 'published'
      AND feature.geom IS NOT NULL
      AND geo.feature_observation_day(feature.properties) = CAST(:day AS date)
      AND feature.geom && ST_Expand(probe.geom, :bbox_degrees)
      AND ST_DWithin(
          feature.geom::geography,
          probe.geom::geography,
          :radius_meters
      )
    ORDER BY distance_m
    LIMIT :row_limit
)
SELECT
    nearby.feature_id,
    nearby.observed_day,
    round(nearby.distance_m::numeric, 1) AS distance_meters,
    nearby.centroid_longitude,
    nearby.centroid_latitude,
    nearby.geometry_id IS NOT NULL AS has_geometry_link,
    nearby.data_available_at,
    coalesce(
        (
            SELECT jsonb_object_agg(entry.key, entry.value)
            FROM jsonb_each_text(nearby.properties) AS entry
            WHERE entry.key = ANY(:property_keys)
        ),
        '{}'::jsonb
    ) AS properties
FROM nearby
ORDER BY nearby.distance_m, nearby.feature_id
