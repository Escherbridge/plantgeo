-- Purpose: summarise served fire features (satellite detections, burn perimeters) near one point.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         layer_names (text[]), observed_day_from (text, an ISO yyyy-mm-dd prefix),
--         feature_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- This is the one agent query that reads the `geo` schema rather than `agri`. Fire evidence
-- is served from `geo.features`, a layer-keyed feature store whose declarative source of
-- truth is the Next.js Drizzle schema (`src/lib/server/db/schema.ts`), not this service's
-- ORM models. Only columns that schema actually declares are referenced here: id, layer_id,
-- geom, properties, status. Property keys are read defensively -- `observedAt` is written by
-- both the FIRMS detection lane and the MTBS burn-severity lane, and a layer that does not
-- carry it yields NULL day bounds rather than a wrong answer.
--
-- How this query works, clause by clause:
--
--   WITH nearby AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced
--     below like a table. It selects the nearest matching features and, critically, applies
--     the LIMIT *before* aggregation, so the summary below is computed over a bounded set
--     no matter how many detections exist in a fire-heavy region.
--
--   INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
--     Features carry only a layer id; the human-readable layer name lives on geo.layers.
--     The join exists so the caller can name layers ("fire-detections") rather than pass
--     opaque uuids.
--
--   layer.name = ANY(layer_names)
--     ANY(array) is the SQL spelling of "is in this list" for an array-typed bind parameter.
--
--   feature.status = 'published'
--     geo.features carries a review status; drafts and rejected rows must not be summarised
--     as evidence.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) / ::geography / ST_DWithin
--     Builds the caller's coordinate into a PostGIS point stamped with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude), then casts to geography so distance is measured in
--     metres on the curved earth rather than in degrees, whose real length varies with
--     latitude. ST_DWithin is the index-friendly "within radius metres" test: written as
--     ST_Distance(...) <= radius it would compute an exact distance for every row before
--     filtering, instead of letting the spatial index discard most rows first.
--
--   substring(feature.properties->>'observedAt', 1, 10)
--     `->>` extracts a JSON field as text. The stored value is a full ISO timestamp; the
--     first ten characters are its calendar day. Comparing day prefixes as text is safe
--     because ISO dates sort lexicographically in the same order they sort chronologically,
--     and it avoids casting a malformed upstream string to date and failing the whole query.
--
--   GROUP BY layer_name with count/min/max
--     Collapses the bounded feature set into one row per layer: how many features fell in
--     the radius, how close the nearest one was, and the day span they cover. That is the
--     shape a language model can reason over; a few thousand point detections is not.
--
--   round(min(distance_m)::numeric, 1)
--     ST_Distance returns double precision, whose round() has no two-argument form in
--     PostgreSQL. The ::numeric cast selects the overload that accepts a decimal count.
WITH nearby AS (
    SELECT
        layer.name AS layer_name,
        feature.properties AS properties,
        ST_Distance(
            feature.geom::geography,
            ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
        ) AS distance_m
    FROM geo.features AS feature
    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
    WHERE layer.name = ANY(:layer_names)
      AND feature.geom IS NOT NULL
      AND feature.status = 'published'
      AND ST_DWithin(
          feature.geom::geography,
          ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
          :radius_meters
      )
      AND (
          feature.properties->>'observedAt' IS NULL
          OR substring(feature.properties->>'observedAt', 1, 10) >= :observed_day_from
      )
    ORDER BY distance_m
    LIMIT :feature_limit
)
SELECT
    nearby.layer_name,
    count(*) AS feature_count,
    round(min(nearby.distance_m)::numeric, 1) AS nearest_distance_m,
    min(substring(nearby.properties->>'observedAt', 1, 10)) AS earliest_observed_day,
    max(substring(nearby.properties->>'observedAt', 1, 10)) AS latest_observed_day
FROM nearby
GROUP BY nearby.layer_name
ORDER BY nearby.layer_name
