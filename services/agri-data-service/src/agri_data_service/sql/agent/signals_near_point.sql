-- agent_signals_near_point
-- Purpose: summarise governed signal observations recorded near one point across a window of
--          calendar days, reading the pre-aggregated daily rollup instead of the raw
--          46-million-row observation plane.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         cell_limit (int), day_from/day_through (date -- inclusive calendar bounds),
--         signal_names (text[], empty array means "every governed signal"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy's text() scans comments too, and a colon-prefixed word here would
-- mint a phantom bind parameter no caller supplies.
--
-- WHAT CHANGED, AND WHY IT MATTERS FOR CORRECTNESS AS WELL AS COST.
-- This statement used to read agri.signal_observation directly. That table is about 26 GB on a
-- database capped at 3 GB of memory, and its physical row order is ingest order interleaved
-- across every lane, so a read for one signal over one neighbourhood scattered across the whole
-- 11 GB heap. It now reads geo.mv_signal_cell_daily, a rollup at one row per
-- (support key, signal, unit, cell, day) that is physically clustered in exactly that order, so
-- the same question is a short contiguous range read.
--
-- The rollup is ALSO what the map paints from. That is the point: if the agent answered from the
-- raw plane while the map painted from the rollup, the two could disagree about the same day and
-- the agent would state something the user can see is false on screen.
--
-- TWO PROPERTIES ARE INHERITED FROM THE ROLLUP'S OWN DEFINITION RATHER THAN FILTERED HERE, and a
-- reader must know that, because there is no column left in this statement to enforce them with.
--   * Quality. The rollup's defining query keeps only rows that are observed and whose quality
--     flag is accepted, so an imputed or rejected reading can never reach this summary. There is
--     no is_observed or quality_flag column on the rollup to re-filter on.
--   * Scope. The rollup covers only the 19 signal names under contract
--     (execution/coverage_contract.py, verified against agri.data_source 2026-08-11). A signal
--     outside that set is absent from this answer because it is outside the governed plane, not
--     because it was unmeasured -- the tool's note says so.
--
-- The rollup carries no source_parameter column, so this statement no longer groups by one. Under
-- the governed contract a signal name resolves to a single upstream parameter within one support
-- key anyway, and reporting a parameter the rollup cannot distinguish would be inventing it.
--
-- How this query works, clause by clause:
--
--   WITH nearby_cells AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one turns "near this point" into a concrete, bounded set of analysis
--     cells, so everything downstream joins against a short list instead of scanning a rollup
--     that spans every cell and every day.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the caller's coordinate into a PostGIS point and stamps it with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude). Without the stamp PostGIS refuses to compare it against
--     the stored geometries, which are all 4326.
--
--   ::geography
--     Casts a geometry to the geography type so distances come back in metres on the curved
--     earth. Comparing 4326 geometries directly would measure in degrees, and one degree is a
--     different real distance near the poles than at the equator.
--
--   ST_DWithin(a, b, radius)
--     "Are these within radius metres of each other" -- the index-friendly form. Written as
--     ST_Distance(...) <= radius it would be logically identical but would compute an exact
--     distance for every row before filtering, instead of letting the spatial index discard most
--     of them first.
--
--   ORDER BY distance_m ... LIMIT cell_limit
--     Keeps the nearest cells and drops the rest. agri.spatial_cell is about 2,000 rows and is
--     trivially resident in memory, which is precisely why the rollup carries no geometry of its
--     own -- the geometry is joined in here, once, over a tiny table.
--
--   INNER JOIN nearby_cells ON nearby_cells.cell_id = rollup.cell_id
--     Restricts the rollup to those cells. INNER rather than LEFT is deliberate: a cell holding
--     nothing in the window contributes nothing to a summary, so there is nothing to preserve.
--     (The nearest-cells statement makes the opposite choice, for the opposite reason.)
--
--   rollup.observed_day >= day_from AND rollup.observed_day <= day_through
--     The window, expressed in whole calendar days because the rollup's grain IS a calendar day.
--     Both bounds are inclusive. Paired with the cell restriction above this rides the rollup's
--     (cell_id, observed_day) index.
--
--   cardinality(signal_names) = 0 OR rollup.signal_name = ANY(signal_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no
--     filter requested" and every governed signal passes. ANY(array) is the SQL spelling of
--     "is in this list" for an array-typed bind parameter.
--
--   sum(rollup.observation_count)
--     How many raw readings stand behind the summary. The rollup already collapsed each cell-day
--     to one row while remembering how many readings it collapsed, so this recovers the true
--     count rather than counting rollup rows.
--
--   sum(rollup.avg_value * rollup.observation_count) / nullif(sum(rollup.observation_count), 0)
--     The mean, weighted by how many readings each cell-day contributed. A plain avg() of the
--     per-cell-day averages would weigh a cell-day built from one reading the same as one built
--     from twenty-four, which is a different (and wrong) number. nullif(...) guards the divide
--     by returning NULL instead of raising when a group somehow carries no readings at all.
--
--   min(rollup.min_value) / max(rollup.max_value)
--     The true extremes, recovered by taking the extreme of the per-cell-day extremes. Aggregates
--     of this shape compose exactly; the mean above is the one that does not, which is why it is
--     weighted.
--
--   count(DISTINCT rollup.cell_id) / count(DISTINCT rollup.observed_day)
--     How many separate cells and how many separate days contributed. One cell reporting daily
--     for a month and thirty cells reporting once each produce the same observation count; these
--     two columns are what separate them.
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
    rollup.signal_name,
    rollup.support_key,
    rollup.normalized_unit,
    sum(rollup.observation_count) AS observation_count,
    count(DISTINCT rollup.cell_id) AS cell_count,
    count(DISTINCT rollup.observed_day) AS day_count,
    min(rollup.observed_day) AS first_observed_day,
    max(rollup.observed_day) AS last_observed_day,
    max(rollup.newest_observed_at) AS last_observed_at,
    min(rollup.min_value) AS minimum_value,
    max(rollup.max_value) AS maximum_value,
    sum(rollup.avg_value * rollup.observation_count)
        / nullif(sum(rollup.observation_count), 0) AS mean_value,
    min(nearby_cells.distance_m) AS nearest_cell_distance_m
FROM geo.mv_signal_cell_daily AS rollup
INNER JOIN nearby_cells ON nearby_cells.cell_id = rollup.cell_id
WHERE rollup.observed_day >= :day_from
  AND rollup.observed_day <= :day_through
  AND (cardinality(:signal_names) = 0 OR rollup.signal_name = ANY(:signal_names))
GROUP BY rollup.signal_name, rollup.support_key, rollup.normalized_unit
ORDER BY sum(rollup.observation_count) DESC, rollup.signal_name
LIMIT :row_limit
