-- agent_fire_history_near_point
-- Purpose: summarise served fire features (satellite detections, burn perimeters) near one point,
--          with each layer's warehouse-wide served day range beside the point-scoped counts.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         bbox_degrees (double precision -- half-width of the index prefilter box, computed for
--         this latitude), layer_names (text[]), observed_day_from (text, an ISO yyyy-mm-dd
--         prefix), feature_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the one agent query that reads the `geo` schema's feature store rather than `agri`. Fire
-- evidence is served from `geo.features`, whose declarative source of truth is the Next.js Drizzle
-- schema (`src/lib/server/db/schema.ts`), not this service's ORM models. Only columns that schema
-- actually declares are referenced here: id, layer_id, geom, properties, status. Property keys are
-- read defensively -- `observedAt` is written by both the FIRMS detection lane and the MTBS
-- burn-severity lane, and a layer that does not carry it yields NULL day bounds rather than a
-- wrong answer.
--
-- THE BOUNDING-BOX PREFILTER IS THE POINT OF THIS REVISION, and it is a plan fix rather than a
-- rollup. `ST_DWithin(feature.geom::geography, ...)` is exact and correct, but the cast to
-- geography makes the predicate unusable by `idx_features_geom`, which is a GiST index over the
-- GEOMETRY column -- and no geography index exists on that table. The planner's only remaining
-- option was to scan every published feature of the layer, detoasting each row's `properties`
-- JSONB on the way past. Adding `feature.geom && ST_Expand(point, bbox_degrees)` in front of it
-- restores the index: the cheap box test discards almost everything, and the exact geographic
-- distance is then computed only on the survivors. The answer is unchanged; the work is not.
--
-- bbox_degrees is computed in Python for the caller's own latitude, because a degree of longitude
-- shrinks toward the poles while a degree of latitude does not. A single hard-coded conversion
-- would clip the eastern and western edges of the search box at high latitude, silently dropping
-- real features -- so the wider of the two axes is used, with a margin. See the constant in
-- agent/tools.py for the measurement it is derived from.
--
-- How this query works, clause by clause:
--
--   WITH probe AS (SELECT ... AS geom)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. It builds the caller's coordinate once so both the box test and the exact
--     distance read the same value.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the coordinate into a PostGIS point stamped with SRID 4326 (WGS84 -- ordinary GPS
--     longitude/latitude). Without the stamp PostGIS refuses to compare it against the stored
--     geometries, which are all 4326.
--
--   nearby AS (...)
--     The second CTE. It selects the nearest matching features and, critically, applies the LIMIT
--     *before* aggregation, so the summary below is computed over a bounded set no matter how many
--     detections exist in a fire-heavy region.
--
--   INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
--     Features carry only a layer id; the human-readable layer name lives on geo.layers. The join
--     exists so the caller can name layers ("fire-detections") rather than pass opaque uuids.
--
--   layer.name = ANY(layer_names)
--     ANY(array) is the SQL spelling of "is in this list" for an array-typed bind parameter.
--
--   feature.status = 'published'
--     geo.features carries a review status; drafts and rejected rows must not be summarised as
--     evidence.
--
--   feature.geom && ST_Expand(probe.geom, bbox_degrees)
--     The bounding-box overlap operator against a box centred on the point. It asks only whether
--     two bounding boxes overlap, which the GiST index answers without reading the geometry, and
--     it is what makes this query index-driven. It is a SUPERSET of the exact test below -- any
--     feature within the radius necessarily overlaps the box -- so adding it cannot change which
--     rows survive, only how many are examined.
--
--   ::geography / ST_DWithin(a, b, radius)
--     The exact test. Casting to geography measures distance in metres on the curved earth rather
--     than in degrees, whose real length varies with latitude. ST_DWithin is the "within radius
--     metres" form; it runs here only on the rows the box prefilter admitted.
--
--   substring(feature.properties->>'observedAt', 1, 10)
--     `->>` extracts a JSON field as text. The stored value is a full ISO timestamp; the first ten
--     characters are its calendar day. Comparing day prefixes as text is safe because ISO dates
--     sort lexicographically in the same order they sort chronologically, and it avoids casting a
--     malformed upstream string to date and failing the whole query.
--
--   GROUP BY layer_name with count/min/max
--     Collapses the bounded feature set into one row per layer: how many features fell in the
--     radius, how close the nearest one was, and the day span they cover. That is the shape a
--     language model can reason over; a few thousand point detections is not.
--
--   round(min(distance_m)::numeric, 1)
--     ST_Distance returns double precision, whose round() has no two-argument form in PostgreSQL.
--     The ::numeric cast selects the overload that accepts a decimal count.
--
--   LEFT JOIN LATERAL over geo.mv_feature_observation_day
--     LATERAL lets the subquery on the right see each row on the left, so it can be evaluated once
--     per layer. This one reads the pre-aggregated per-layer-per-day census -- about 16,000 rows
--     in total, indexed on (surface_name, observed_day) -- to report the layer's WAREHOUSE-WIDE
--     served day range and total observation count. Those columns are prefixed `layer_` and are
--     deliberately NOT scoped to the radius: they answer "does this layer have anything at all for
--     this period", which is what distinguishes "no fire near you" from "this layer stopped
--     ingesting". Confusing the two is the failure the layer-lane standard names. LEFT keeps the
--     layer's point-scoped counts even when the census carries no row for it at all.
WITH probe AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326) AS geom
),
nearby AS (
    SELECT
        layer.name AS layer_name,
        feature.properties AS properties,
        ST_Distance(
            feature.geom::geography,
            probe.geom::geography
        ) AS distance_m
    FROM geo.features AS feature
    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
    CROSS JOIN probe
    WHERE layer.name = ANY(:layer_names)
      AND feature.geom IS NOT NULL
      AND feature.status = 'published'
      AND feature.geom && ST_Expand(probe.geom, :bbox_degrees)
      AND ST_DWithin(
          feature.geom::geography,
          probe.geom::geography,
          :radius_meters
      )
      AND (
          feature.properties->>'observedAt' IS NULL
          OR substring(feature.properties->>'observedAt', 1, 10) >= :observed_day_from
      )
    ORDER BY distance_m
    LIMIT :feature_limit
),
per_layer AS (
    SELECT
        nearby.layer_name,
        count(*) AS feature_count,
        round(min(nearby.distance_m)::numeric, 1) AS nearest_distance_m,
        min(substring(nearby.properties->>'observedAt', 1, 10)) AS earliest_observed_day,
        max(substring(nearby.properties->>'observedAt', 1, 10)) AS latest_observed_day
    FROM nearby
    GROUP BY nearby.layer_name
)
SELECT
    per_layer.layer_name,
    per_layer.feature_count,
    per_layer.nearest_distance_m,
    per_layer.earliest_observed_day,
    per_layer.latest_observed_day,
    census.layer_earliest_observed_day,
    census.layer_latest_observed_day,
    census.layer_observed_day_count
FROM per_layer
LEFT JOIN LATERAL (
    SELECT
        min(day_census.observed_day) AS layer_earliest_observed_day,
        max(day_census.observed_day) AS layer_latest_observed_day,
        count(*) AS layer_observed_day_count
    FROM geo.mv_feature_observation_day AS day_census
    WHERE day_census.surface_name = per_layer.layer_name
) AS census ON TRUE
ORDER BY per_layer.layer_name
