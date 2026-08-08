-- Purpose: summarise governed signal observations recorded near one point inside a time window.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), window_start/window_end (timestamptz),
--         signal_names (text[], empty array means "every signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy's text() scans comments too, and a colon-prefixed word here
-- would mint a phantom bind parameter no caller supplies.
--
-- This statement is read-only and bounded twice over: once by cell_limit, which caps how
-- many analysis cells the radius may pull in, and again by row_limit on the summary rows.
-- It never returns raw observations -- an agent reading this wants "what is measured here
-- and over what range", not a few thousand individual readings.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced
--     below like a table. This one turns "near this point" into a concrete, bounded set of
--     analysis cells, so everything downstream joins against a small list instead of
--     scanning the whole observation table.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the caller's coordinate into a PostGIS point and stamps it with SRID 4326
--     (WGS84 -- ordinary GPS longitude/latitude). Without the SRID stamp PostGIS refuses to
--     compare it against the stored geometries, which are all 4326.
--
--   ::geography
--     Casts a geometry to the geography type so distances come back in metres on the
--     curved earth. Comparing 4326 geometries directly would measure in degrees, where one
--     unit is a different real distance at the equator than near the poles.
--
--   ST_DWithin(a, b, radius)
--     "Are these within radius metres of each other" -- the index-friendly form. Written as
--     ST_Distance(...) <= radius it would be logically identical but would compute an exact
--     distance for every row before filtering, instead of letting the spatial index discard
--     most of them first.
--
--   ORDER BY distance_m ... LIMIT cell_limit
--     Keeps the nearest cells and drops the rest. A generous radius over a dense grid could
--     otherwise match thousands of cells; this makes the worst case predictable.
--
--   INNER JOIN nearby_cells ON nearby_cells.cell_id = observation.cell_id
--     Restricts observations to those cells. INNER (rather than LEFT) is deliberate: a cell
--     with no observations in the window contributes nothing to a summary, so there is
--     nothing to preserve.
--
--   observation.is_observed AND observation.quality_flag = 'accepted'
--     Excludes rows that stand in for an absence and rows the ingest lane flagged. An
--     imputed or rejected reading must never be summarised as something measured here.
--
--   cardinality(signal_names) = 0 OR observation.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means
--     "no filter requested" and every signal passes. ANY(array) is the SQL spelling of
--     "is in this list" for an array-typed bind parameter.
--
--   GROUP BY ... count/min/max/avg
--     Collapses many readings into one row per signal, parameter and unit, carrying how
--     many readings there were, the window they actually span, and their value range. This
--     is what makes the result small enough to hand to a language model.
--
--   count(DISTINCT observation.cell_id)
--     How many separate cells contributed. One cell reporting daily for a month and thirty
--     cells reporting once each produce the same observation_count; this separates them.
WITH nearby_cells AS (
    SELECT
        cell.id AS cell_id,
        ST_Distance(
            cell.centroid::geography,
            ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography
        ) AS distance_m
    FROM agri.spatial_cell AS cell
    WHERE ST_DWithin(
        cell.centroid::geography,
        ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)::geography,
        :radius_meters
    )
    ORDER BY distance_m
    LIMIT :cell_limit
)
SELECT
    observation.signal_name,
    observation.source_parameter,
    observation.normalized_unit,
    count(*) AS observation_count,
    count(DISTINCT observation.cell_id) AS cell_count,
    min(observation.observed_at) AS first_observed_at,
    max(observation.observed_at) AS last_observed_at,
    min(observation.normalized_value) AS minimum_value,
    max(observation.normalized_value) AS maximum_value,
    avg(observation.normalized_value) AS mean_value,
    min(nearby_cells.distance_m) AS nearest_cell_distance_m
FROM agri.signal_observation AS observation
INNER JOIN nearby_cells ON nearby_cells.cell_id = observation.cell_id
WHERE observation.observed_at >= :window_start
  AND observation.observed_at <= :window_end
  AND observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND (cardinality(:signal_names) = 0 OR observation.signal_name = ANY(:signal_names))
GROUP BY observation.signal_name, observation.source_parameter, observation.normalized_unit
ORDER BY count(*) DESC, observation.signal_name
LIMIT :row_limit
