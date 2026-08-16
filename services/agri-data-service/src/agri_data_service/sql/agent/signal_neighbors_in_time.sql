-- agent_signal_neighbors_in_time
-- Purpose: for each governed signal near a point, find the nearest accepted reading BEFORE the
--          caller's day and the nearest one AFTER it, each carrying its own observation day and
--          the real number of days it sits away from the day that was asked about.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), day (date -- the day the caller asked about),
--         search_from/search_through (date -- the inclusive span the search may walk),
--         signal_names (text[], empty array means "every governed signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- The whole point of this statement is the distance column. A neighbouring reading handed back
-- without its gap is indistinguishable from an exact answer, and a reader who cannot see that the
-- number came from six days earlier will treat it as today's. So every row carries which side of
-- the day it fell on, its own observed day, and how many days away that is. The search span is
-- bounded, which means "no row on this side" is a statement about the searched window and not
-- about all of history -- the caller reports the span it searched alongside the result.
--
-- THE SOURCE IS geo.mv_signal_cell_daily, the pre-aggregated rollup at one row per
-- (support key, signal, unit, cell, day), and not agri.signal_observation. The walk backwards and
-- forwards from a day is exactly the access pattern the rollup's (cell_id, observed_day) index
-- serves, and because the rollup already holds one row per cell-day there is no per-row date
-- derivation left to do -- the old (observed_at AT TIME ZONE 'UTC')::date expression is gone,
-- along with the risk of the agent and the map deriving a day differently.
--
-- Quality and scope are inherited from the rollup's own definition. It keeps only observed rows
-- whose quality flag is accepted, and covers only the 19 signal names under contract
-- (execution/coverage_contract.py, verified against agri.data_source 2026-08-11). There is no
-- is_observed, quality_flag or source_parameter column left here to filter or group on, so the
-- grain of a neighbour is (signal, support key, unit).
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one turns "near this point" into a bounded set of analysis cells so the
--     rollup read joins against a short list rather than the whole relation. agri.spatial_cell is
--     about 2,000 rows and trivially resident, which is why the rollup carries no geometry itself.
--
--   ST_SetSRID / ST_MakePoint / ::geography / ST_DWithin
--     Builds the caller's coordinate into a PostGIS point stamped with SRID 4326 (WGS84 -- ordinary
--     GPS longitude/latitude), casts it to geography so distance is measured in metres on the
--     curved earth rather than in degrees, and tests "within radius metres" in the index-friendly
--     form that lets the spatial index discard most rows before any exact distance is computed.
--
--   scoped AS (...)
--     The second CTE, holding every rollup row for those cells inside the bounded search span.
--     Both halves below read from it, so the join and the filters are written once and run once.
--
--   rollup.observed_day >= search_from AND rollup.observed_day <= search_through
--     The bounded walk. Both bounds inclusive, in whole calendar days, because the rollup's grain
--     IS the calendar day.
--
--   cardinality(signal_names) = 0 OR rollup.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no filter
--     requested" and every governed signal passes. ANY(array) is the SQL spelling of "is in this
--     list" for an array-typed bind parameter.
--
--   SELECT DISTINCT ON (signal_name, support_key, normalized_unit)
--     A PostgreSQL feature: keep only the FIRST row of each group, where "first" is decided by the
--     ORDER BY that follows. It is how each half returns exactly one winner per measurement kind
--     without a self-join or a window function.
--
--   ORDER BY signal_name, support_key, normalized_unit, observed_day DESC, distance_m
--     The order DISTINCT ON obeys, and it has to open with the same three columns DISTINCT ON
--     names. After those, the before-half sorts days descending so the winner is the LATEST day
--     still earlier than the caller's; the after-half sorts ascending so the winner is the
--     EARLIEST day still later. distance_m breaks ties within a day, so the reading that wins is
--     the one from the closest cell rather than an arbitrary one.
--
--   UNION ALL
--     Stacks the two halves into one result. ALL rather than a plain UNION because the halves
--     cannot overlap by construction -- one is strictly before the day and the other strictly
--     after -- so paying for duplicate removal would buy nothing.
--
--   neighbours.observed_day - CAST(day AS date)
--     Whole days between the reading and the day asked about, signed: negative for a reading that
--     precedes it, positive for one that follows. Subtracting two dates in PostgreSQL yields a
--     plain integer count of days. The bind is wrapped in CAST(... AS date) rather than written
--     with a trailing double-colon cast because a double colon immediately after a bind name would
--     stop SQLAlchemy from recognising the bind at all.
--
--   abs(...) AS distance_days
--     The same gap as a magnitude, so a reader who only wants "how far off is this" does not have
--     to interpret a sign. The side column already says which direction.
--
--   ORDER BY signal_name, side, distance_days ... LIMIT row_limit
--     A total order before the limit. Truncating without one can repeat or skip rows, because the
--     database is otherwise free to return equal rows in any order.
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
),
scoped AS (
    SELECT
        rollup.signal_name,
        rollup.support_key,
        rollup.normalized_unit,
        rollup.normalized_value,
        rollup.observed_day,
        rollup.newest_observed_at,
        nearby_cells.cell_key,
        nearby_cells.grid_name,
        nearby_cells.distance_m
    FROM geo.mv_signal_cell_daily AS rollup
    INNER JOIN nearby_cells ON nearby_cells.cell_id = rollup.cell_id
    WHERE rollup.observed_day >= :search_from
      AND rollup.observed_day <= :search_through
      AND (cardinality(:signal_names) = 0 OR rollup.signal_name = ANY(:signal_names))
),
before_day AS (
    SELECT DISTINCT ON (signal_name, support_key, normalized_unit)
        'before' AS side,
        signal_name,
        support_key,
        normalized_unit,
        normalized_value,
        observed_day,
        newest_observed_at,
        cell_key,
        grid_name,
        distance_m
    FROM scoped
    WHERE observed_day < CAST(:day AS date)
    ORDER BY signal_name, support_key, normalized_unit, observed_day DESC, distance_m
),
after_day AS (
    SELECT DISTINCT ON (signal_name, support_key, normalized_unit)
        'after' AS side,
        signal_name,
        support_key,
        normalized_unit,
        normalized_value,
        observed_day,
        newest_observed_at,
        cell_key,
        grid_name,
        distance_m
    FROM scoped
    WHERE observed_day > CAST(:day AS date)
    ORDER BY signal_name, support_key, normalized_unit, observed_day, distance_m
),
neighbours AS (
    SELECT * FROM before_day
    UNION ALL
    SELECT * FROM after_day
)
SELECT
    neighbours.side,
    neighbours.signal_name,
    neighbours.support_key,
    neighbours.normalized_unit,
    neighbours.observed_day,
    neighbours.newest_observed_at AS nearest_cell_observed_at,
    neighbours.observed_day - CAST(:day AS date) AS day_offset,
    abs(neighbours.observed_day - CAST(:day AS date)) AS distance_days,
    neighbours.normalized_value AS nearest_cell_value,
    neighbours.cell_key AS nearest_cell_key,
    neighbours.grid_name AS nearest_cell_grid_name,
    neighbours.distance_m AS nearest_cell_distance_m
FROM neighbours
ORDER BY neighbours.signal_name, neighbours.side, distance_days
LIMIT :row_limit
