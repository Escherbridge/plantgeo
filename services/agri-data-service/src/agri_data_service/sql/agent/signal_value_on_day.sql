-- agent_signal_value_on_day
-- Purpose: report what each governed signal actually measured on ONE caller-named calendar day
--          near a point, and on no other day.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), day_start/day_end (timestamptz -- the UTC midnight the day opens on
--         and the UTC midnight the next day opens on), signal_names (text[], empty array means
--         "every signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy's text() scans comments too, and a colon-prefixed word here
-- would mint a phantom bind parameter no caller supplies.
--
-- Why this exists beside signals_near_point.sql, which looks similar: that statement summarises a
-- WINDOW and is free to answer from whichever days inside it happen to hold readings. This one is
-- asked "what did the map show on the day the user is looking at", and a reading borrowed from a
-- neighbouring day would answer a different question than the one asked. So the filter here is one
-- day wide and nothing widens it. A signal with no row simply had no accepted reading that day --
-- the caller pairs this result with the coverage audit and with the temporal-neighbour statement to
-- explain why, rather than quietly substituting a nearby day and calling it an answer.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one turns "near this point" into a concrete, bounded set of analysis
--     cells, so the observation scan below joins against a short list instead of the whole table.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the caller's coordinate into a PostGIS point and stamps it with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude). Without the SRID stamp PostGIS refuses to compare it
--     against the stored geometries, which are all 4326.
--
--   ::geography
--     Casts a geometry to the geography type so distances come back in metres on the curved earth.
--     Comparing 4326 geometries directly would measure in degrees, and one degree is a different
--     real distance near the poles than at the equator.
--
--   ST_DWithin(a, b, radius)
--     "Are these within radius metres of each other" -- the index-friendly form. Written as
--     ST_Distance(...) <= radius it would be logically identical but would compute an exact
--     distance for every row before filtering, instead of letting the spatial index discard most
--     of them first.
--
--   ORDER BY distance_m ... LIMIT cell_limit
--     Keeps the nearest cells and drops the rest, so a generous radius over a dense grid has a
--     predictable worst case.
--
--   observation.observed_at >= day_start AND observation.observed_at < day_end
--     The one-day filter, written as a half-open range over the two UTC midnights rather than by
--     casting every row's timestamp to a date. Half-open (>= the opening midnight, < the next)
--     admits every instant of the day exactly once and admits no instant of the next day. Range
--     comparisons also let the database use its index on observed_at, which a per-row cast would
--     defeat.
--
--   observation.is_observed AND observation.quality_flag = 'accepted'
--     Excludes rows that stand in for an absence and rows the ingest lane flagged. An imputed or
--     rejected reading must never be reported as something measured here.
--
--   cardinality(signal_names) = 0 OR observation.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no filter
--     requested" and every signal passes. ANY(array) is the SQL spelling of "is in this list" for
--     an array-typed bind parameter.
--
--   GROUP BY signal_name, source_parameter, support_key, normalized_unit
--     Collapses one day's readings across many cells into one row per distinct measurement kind.
--     support_key is in the key because two lanes can publish the same signal name over different
--     spatial support, and averaging across them would invent a number neither lane reported.
--
--   (array_agg(x ORDER BY distance_m, observed_at))[1]
--     A way to pick "the value belonging to the nearest cell" without a second query. array_agg
--     gathers the group's values into an array in the stated order -- nearest cell first -- and
--     [1] takes the head of that array. observed_at breaks ties so two readings at identical
--     distance still resolve to the same row every time rather than an arbitrary one.
--
--   min/max/avg over normalized_value
--     The spread across every contributing cell, carried beside the nearest cell's own reading so
--     a reader can see whether the neighbourhood agreed with it or not.
--
--   min(observation.observed_at) / max(observation.observed_at)
--     The real timestamps the group was built from. They are returned rather than assumed so the
--     answer carries the observation's own recorded time and not merely the day that was asked for.
WITH nearby_cells AS (
    SELECT
        cell.id AS cell_id,
        cell.cell_key,
        cell.grid_name,
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
    observation.support_key,
    observation.normalized_unit,
    count(*) AS observation_count,
    count(DISTINCT observation.cell_id) AS cell_count,
    min(nearby_cells.distance_m) AS nearest_cell_distance_m,
    (array_agg(nearby_cells.cell_key ORDER BY nearby_cells.distance_m, observation.observed_at))[1]
        AS nearest_cell_key,
    (array_agg(nearby_cells.grid_name ORDER BY nearby_cells.distance_m, observation.observed_at))[1]
        AS nearest_cell_grid_name,
    (array_agg(observation.normalized_value ORDER BY nearby_cells.distance_m, observation.observed_at))[1]
        AS nearest_cell_value,
    (array_agg(observation.observed_at ORDER BY nearby_cells.distance_m, observation.observed_at))[1]
        AS nearest_cell_observed_at,
    min(observation.normalized_value) AS minimum_value,
    max(observation.normalized_value) AS maximum_value,
    avg(observation.normalized_value) AS mean_value,
    min(observation.observed_at) AS first_observed_at,
    max(observation.observed_at) AS last_observed_at
FROM agri.signal_observation AS observation
INNER JOIN nearby_cells ON nearby_cells.cell_id = observation.cell_id
WHERE observation.observed_at >= :day_start
  AND observation.observed_at < :day_end
  AND observation.is_observed
  AND observation.quality_flag = 'accepted'
  AND (cardinality(:signal_names) = 0 OR observation.signal_name = ANY(:signal_names))
GROUP BY
    observation.signal_name,
    observation.source_parameter,
    observation.support_key,
    observation.normalized_unit
ORDER BY min(nearby_cells.distance_m), observation.signal_name
LIMIT :row_limit
