-- agent_nearest_signal_cells
-- Purpose: list the analysis cells nearest a point, each with its real distance in metres and how
--          much it actually holds on the caller's day, so a reader can see whether the nearest
--          cell is also the nearest cell that has anything to say.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int -- how many cells the scan may gather), grid_names (text[], empty array
--         means "every grid"), day_start/day_end (timestamptz -- the UTC midnight the day opens on
--         and the UTC midnight the next day opens on), row_limit (int -- how many cells come back)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Spatial relevance has to be earned rather than assumed. A cell 3 km away holding nothing on the
-- day in question is not better evidence than a cell 40 km away that holds a full set of readings,
-- and a caller that only ever saw "the nearest cell" would never learn the difference. So this
-- statement returns cells whether or not they carry anything, each with its measured distance and
-- its count for the day, and lets the reader decide which one it is honest to quote.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one is the answer's spine: the cells inside the radius, nearest first.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the caller's coordinate into a PostGIS point and stamps it with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude). Without the SRID stamp PostGIS refuses to compare it
--     against the stored geometries, which are all 4326.
--
--   ::geography
--     Casts a geometry to the geography type so distances come back in metres on the curved earth
--     rather than in degrees, whose real length varies with latitude.
--
--   ST_DWithin(a, b, radius)
--     "Are these within radius metres of each other" -- the index-friendly form, which lets the
--     spatial index discard most cells before any exact distance is computed.
--
--   ST_X(cell.centroid) / ST_Y(cell.centroid)
--     The centroid's longitude and its latitude as plain numbers. They are returned so a reader
--     can see where the cell actually sits instead of taking the distance on trust.
--
--   AND (cardinality(grid_names) = 0 OR cell.grid_name = ANY(grid_names))
--     The optional grid filter. cardinality() is the array's length, so an empty array means "no
--     filter requested" and every grid passes. ANY(array) is the SQL spelling of "is in this list"
--     for an array-typed bind parameter. Grids differ in spacing, so restricting to one is how a
--     caller compares like with like.
--
--   ORDER BY distance_m ... LIMIT cell_limit
--     Bounds the scan. cell_limit is deliberately larger than row_limit below: the counting join
--     is allowed to look at a generous neighbourhood, while the answer stays as short as the
--     caller asked for.
--
--   day_observations AS (...)
--     The second CTE, counting what each of those cells holds on the one day in question. The
--     filter is a half-open range over the two UTC midnights -- at or after the opening midnight,
--     strictly before the next -- which admits every instant of the day exactly once and lets the
--     database use its index on observed_at, which a per-row date cast would defeat.
--
--   observation.is_observed AND observation.quality_flag = 'accepted'
--     Excludes rows that stand in for an absence and rows the ingest lane flagged, so a cell whose
--     only rows are placeholders is correctly counted as holding nothing.
--
--   LEFT JOIN day_observations
--     LEFT, not INNER, and that is the whole design. An INNER join would silently drop every cell
--     with no data that day, and the result would read as "the nearest cells" while actually being
--     "the nearest cells that happened to have data" -- exactly the substitution this statement
--     exists to make visible.
--
--   coalesce(day_observations.observation_count, 0)
--     Turns the null a LEFT JOIN leaves for an unmatched cell into an explicit zero, so "no rows
--     that day" arrives as a number a reader can act on rather than as an empty field.
WITH nearby_cells AS (
    SELECT
        cell.id AS cell_id,
        cell.cell_key,
        cell.grid_name,
        cell.resolution_m,
        ST_X(cell.centroid) AS centroid_longitude,
        ST_Y(cell.centroid) AS centroid_latitude,
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
      AND (cardinality(:grid_names) = 0 OR cell.grid_name = ANY(:grid_names))
    ORDER BY distance_m
    LIMIT :cell_limit
),
day_observations AS (
    SELECT
        observation.cell_id,
        count(*) AS observation_count,
        count(DISTINCT observation.signal_name) AS signal_count,
        min(observation.observed_at) AS first_observed_at,
        max(observation.observed_at) AS last_observed_at
    FROM agri.signal_observation AS observation
    INNER JOIN nearby_cells ON nearby_cells.cell_id = observation.cell_id
    WHERE observation.observed_at >= :day_start
      AND observation.observed_at < :day_end
      AND observation.is_observed
      AND observation.quality_flag = 'accepted'
    GROUP BY observation.cell_id
)
SELECT
    nearby_cells.cell_key,
    nearby_cells.grid_name,
    nearby_cells.resolution_m,
    nearby_cells.centroid_longitude,
    nearby_cells.centroid_latitude,
    nearby_cells.distance_m AS distance_meters,
    coalesce(day_observations.observation_count, 0) AS observation_count_on_day,
    coalesce(day_observations.signal_count, 0) AS signal_count_on_day,
    day_observations.first_observed_at,
    day_observations.last_observed_at
FROM nearby_cells
LEFT JOIN day_observations ON day_observations.cell_id = nearby_cells.cell_id
ORDER BY nearby_cells.distance_m, nearby_cells.cell_key
LIMIT :row_limit
