-- agent_signal_value_on_day
-- Purpose: report what each governed signal actually measured on ONE caller-named calendar day
--          near a point, and on no other day, reading the same pre-aggregated daily rollup the
--          map paints from.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), day (date -- the one calendar day being asked about),
--         signal_names (text[], empty array means "every governed signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- Why this exists beside signals_near_point.sql, which looks similar: that statement summarises a
-- WINDOW and is free to answer from whichever days inside it happen to hold readings. This one is
-- asked "what did the map show on the day the user is looking at", and a reading borrowed from a
-- neighbouring day would answer a different question than the one asked. So the filter here is one
-- day wide and nothing widens it. A signal with no row simply had no accepted reading that day --
-- the caller pairs this result with the coverage audit and with the temporal-neighbour statement to
-- explain why, rather than quietly substituting a nearby day and calling it an answer.
--
-- THE SOURCE IS geo.mv_signal_cell_daily, the rollup at one row per
-- (support key, signal, unit, cell, day), and NOT agri.signal_observation. Two consequences a
-- reader must hold on to, because no column left in this statement can enforce them:
--   * The one-day filter is now a plain equality on a date column rather than a half-open pair of
--     UTC midnights. The rollup's grain IS the calendar day, derived once when the rollup is
--     built, so every reader of it -- map, agent, ops -- gets the same day boundary by
--     construction instead of by each re-deriving one.
--   * Quality and scope are inherited. The rollup's defining query keeps only observed rows whose
--     quality flag is accepted, and covers only the 19 signal names under contract
--     (execution/coverage_contract.py, verified against agri.data_source 2026-08-11). A signal
--     missing here may be outside the governed plane rather than unmeasured; the tool's note says
--     so, and signal_coverage_on_day.sql is what distinguishes the two.
--
-- The rollup carries no source_parameter column, so the grain of this answer is
-- (signal, support key, unit). Under the governed contract a signal name resolves to one upstream
-- parameter within one support key anyway, and reporting a parameter the rollup cannot
-- distinguish would be inventing one.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one turns "near this point" into a concrete, bounded set of analysis
--     cells, so the rollup read below joins against a short list instead of the whole relation.
--     agri.spatial_cell is about 2,000 rows and trivially resident, which is exactly why the
--     rollup carries no geometry of its own -- geometry is joined in here, over a tiny table.
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
--   rollup.observed_day = day
--     The one-day filter. An equality on the rollup's own day column, which together with the
--     cell restriction rides the rollup's (cell_id, observed_day) index.
--
--   cardinality(signal_names) = 0 OR rollup.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no filter
--     requested" and every governed signal passes. ANY(array) is the SQL spelling of "is in this
--     list" for an array-typed bind parameter.
--
--   scoped AS (...)
--     The second CTE, holding the one day's rollup rows for the cells in range with each row's
--     distance attached. Both halves below read from it, so the join is written and executed once.
--
--   SELECT DISTINCT ON (signal_name, support_key, normalized_unit) ... ORDER BY ..., distance_m
--     DISTINCT ON is a PostgreSQL extension that keeps only the FIRST row of each group, where
--     "first" is decided by the ORDER BY that follows -- and its expressions must be that
--     ORDER BY's leading terms, which is why the three key columns lead. Ordering by distance_m
--     after them makes the surviving row the NEAREST cell's row, so nearest_cell_value is the
--     reading from the closest cell rather than an arbitrary one. This replaces four
--     array_agg(... ORDER BY ...)[1] expressions that did the same job by materialising an array
--     per column; over a rollup already reduced to one row per cell-day, a sort and a pick is
--     both cheaper and easier to read.
--
--   spread AS (...) ... LEFT JOIN
--     The neighbourhood's agreement, computed separately because DISTINCT ON returns one row and
--     cannot also aggregate the rows it discarded. LEFT keeps the nearest row even in the
--     impossible case that the aggregate half returns nothing, so a value is never dropped by its
--     own summary.
--
--   sum(avg_value * observation_count) / nullif(sum(observation_count), 0)
--     The mean across contributing cells, weighted by how many raw readings each cell-day stood
--     for. A plain avg() of per-cell averages would weigh a cell built from one reading the same
--     as one built from twenty-four. nullif(...) returns NULL instead of raising on a zero divisor.
--
--   coverage_fraction / allowed_client_exposure
--     Carried straight through from the nearest cell's rollup row, un-aggregated, because they
--     describe that row's own provenance: how much of the expected lattice landed, and what
--     exposure the governed plane permits for it.
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
        rollup.observed_day,
        rollup.normalized_value,
        rollup.observation_count,
        rollup.min_value,
        rollup.max_value,
        rollup.avg_value,
        rollup.newest_observed_at,
        rollup.coverage_fraction,
        rollup.allowed_client_exposure,
        nearby_cells.cell_key,
        nearby_cells.grid_name,
        nearby_cells.distance_m
    FROM geo.mv_signal_cell_daily AS rollup
    INNER JOIN nearby_cells ON nearby_cells.cell_id = rollup.cell_id
    WHERE rollup.observed_day = :day
      AND (cardinality(:signal_names) = 0 OR rollup.signal_name = ANY(:signal_names))
),
nearest AS (
    SELECT DISTINCT ON (signal_name, support_key, normalized_unit)
        signal_name,
        support_key,
        normalized_unit,
        observed_day,
        normalized_value,
        newest_observed_at,
        coverage_fraction,
        allowed_client_exposure,
        cell_key,
        grid_name,
        distance_m
    FROM scoped
    ORDER BY signal_name, support_key, normalized_unit, distance_m, cell_key
),
spread AS (
    SELECT
        signal_name,
        support_key,
        normalized_unit,
        sum(observation_count) AS observation_count,
        count(DISTINCT cell_key) AS cell_count,
        min(min_value) AS minimum_value,
        max(max_value) AS maximum_value,
        sum(avg_value * observation_count) / nullif(sum(observation_count), 0) AS mean_value,
        max(newest_observed_at) AS last_observed_at
    FROM scoped
    GROUP BY signal_name, support_key, normalized_unit
)
SELECT
    nearest.signal_name,
    nearest.support_key,
    nearest.normalized_unit,
    nearest.observed_day,
    spread.observation_count,
    spread.cell_count,
    nearest.distance_m AS nearest_cell_distance_m,
    nearest.cell_key AS nearest_cell_key,
    nearest.grid_name AS nearest_cell_grid_name,
    nearest.normalized_value AS nearest_cell_value,
    nearest.newest_observed_at AS nearest_cell_observed_at,
    nearest.coverage_fraction AS nearest_cell_coverage_fraction,
    nearest.allowed_client_exposure AS nearest_cell_allowed_client_exposure,
    spread.minimum_value,
    spread.maximum_value,
    spread.mean_value,
    spread.last_observed_at
FROM nearest
LEFT JOIN spread
    ON spread.signal_name = nearest.signal_name
   AND spread.support_key = nearest.support_key
   AND spread.normalized_unit = nearest.normalized_unit
ORDER BY nearest.distance_m, nearest.signal_name
LIMIT :row_limit
