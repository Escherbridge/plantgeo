-- agent_forecast_summary_for_cell
-- Purpose: return published daily forecast values for the analysis cell nearest one point, read
--          from the pre-aggregated serving matview rather than from the eight-table join beneath
--          it.
-- Loaded by: agri_data_service.agent.tools
-- Params: cell_id (text -- the analysis cell nearest the probe, resolved from the Parquet signal
--         plane), cell_distance_m (double precision -- how far that cell's centroid sits from the
--         probe, in metres), valid_day_from (timestamptz -- the UTC midnight the earliest wanted
--         day opens on), metric_names (text[], empty array means "every metric"), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- THE CELL ARRIVES AS A PARAMETER NOW. This statement used to resolve the nearest analysis cell
-- itself, from agri.spatial_cell -- a relation in the retirement track's "drop now" class that is
-- already absent from production, so that CTE could no longer run. The cell is resolved instead
-- from the Parquet signal plane, which carries cell_longitude/cell_latitude beside every row, and
-- bound here by id. The forecast plane it joins to is NOT environmental data and stays in
-- PostgreSQL: agri.mv_forecast_ml_daily_serving is the governed ML serving plane, which the
-- retirement inventory classes "keep -- out of D4 scope".
--
-- One consequence names itself in the projection: agri.spatial_cell carried a human-readable
-- cell_key and the Parquet plane carries only the cell's id, so the answer reports cell_id. A
-- column named cell_key holding a uuid would be a worse answer than a renamed one.
--
-- THE SOURCE IS agri.mv_forecast_ml_daily_serving. That matview is built ON TOP OF
-- agri.v_forecast_series_serving, the view that already encodes what "published" means --
-- publication state published, receipt finalized, run validated, release set matching -- so
-- reading the matview inherits that guarantee rather than re-deriving it. An agent must never be
-- able to quote a draft or an unvalidated forecast, and it still cannot.
--
-- What the matview adds is the pre-aggregation: one row per publication, receipt, series and
-- valid DAY, with the day's point values already averaged and its p10/p90 band already collapsed.
-- The view underneath resolves eight joins per request; the matview is a single indexed lookup.
--
-- TWO CONSEQUENCES OF THE NARROWING, both of which the caller states in its note rather than
-- hiding, because a forecast the agent cannot see is a forecast the agent will report as absent:
--   * The matview covers ML-method forecasts on series flagged `allow_ml_daily_aggregate` ONLY.
--     A published non-ML forecast exists in the view beneath and is not visible here.
--   * Its grain is a DAY, not an issue-time step. `horizon_step` is gone; a day is described by
--     its mean point value and the min/max of the band across the steps that fell inside it.
--
-- THERE IS DELIBERATELY NO FALLBACK TO THE VIEW. Falling back would reintroduce the eight-table
-- join this exists to remove, and would do it precisely when the box is least able to afford it.
-- The caller instead probes whether the matview is populated BEFORE issuing this statement (see
-- materialized_plane_populated.sql) and returns a typed refusal naming the unpopulated plane. That
-- matters here more than anywhere else in the tree: this matview shipped with
-- relispopulated = false and no scheduled refresher, so "unpopulated" is its observed state, not a
-- hypothetical one, and a query against an unpopulated matview raises rather than returning rows.
--
-- How this query works, clause by clause:
--
--   WITH nearest_cell AS (SELECT CAST(cell_id AS uuid) ...)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. Forecast series are attached to analysis cells, not to arbitrary coordinates,
--     so the caller's point has already been turned into exactly one cell, before this statement
--     runs, by a bounded read of the Parquet signal plane. This CTE is that one cell, lifted into
--     a one-row relation so the join below reads exactly as it did when the cell was resolved here.
--     The caller issues NO query at all when no cell falls inside the radius, which is the same
--     answer the old LIMIT 1 gave by returning no rows.
--
--   CAST(cell_id AS uuid)
--     Parquet has no uuid type, so the plane stores the cell's id as text. The cast happens once,
--     here, rather than on every row of the join -- and a value that is not a uuid fails loudly at
--     the cast instead of silently matching no forecast.
--
--   INNER JOIN nearest_cell ON nearest_cell.cell_id = daily.spatial_cell_id
--     Restricts the matview to that one cell. INNER, so a cell with no published forecast returns
--     nothing rather than a row of nulls -- "no forecast here" and "a forecast whose value is
--     unknown" are different claims and must not be conflated.
--
--   daily.valid_day >= valid_day_from
--     Drops days that have already elapsed. A forecast for last Tuesday is not evidence about what
--     happens next. valid_day is date_trunc('day', valid_time), so it is a TIMESTAMP pinned to a
--     day boundary rather than a date, and the bind is supplied as an explicit UTC midnight -- a
--     bare date bind would be widened using the session's time zone, which is not guaranteed to be
--     UTC and would shift the boundary by hours on a differently-configured connection.
--
--   cardinality(metric_names) = 0 OR daily.metric_name = ANY(metric_names)
--     The optional filter. cardinality() is the array's length, so an empty array means "no filter
--     requested" and every metric passes. ANY(array) is the SQL spelling of "is in this list" for
--     an array-typed bind parameter.
--
--   lower_p10_value / upper_p90_value
--     The uncertainty band around mean_point_value, carried so a reader can see how wide the
--     forecast's own spread is instead of treating the central estimate as exact. At this grain
--     they are the widest band any contributing step reported for the day, which is the
--     conservative reading and the honest one.
--
--   contributing_forecast_points (issue-time steps folded in)
--     How many issue-time steps were folded into the day. A day built from one step and a day
--     built from twenty-four are different evidence, and without this column they would look
--     identical.
--
--   ORDER BY daily.valid_day, daily.series_key ... LIMIT row_limit
--     A total order before the limit. Paging or truncating without one can repeat or skip rows,
--     because the database is otherwise free to return equal rows in any order.
WITH nearest_cell AS (
    SELECT
        CAST(:cell_id AS uuid) AS cell_id,
        CAST(:cell_distance_m AS double precision) AS distance_m
)
SELECT
    nearest_cell.cell_id,
    nearest_cell.distance_m,
    daily.series_key,
    daily.entity_type,
    daily.entity_key,
    daily.metric_name,
    daily.metric_unit,
    daily.issue_time,
    daily.valid_day,
    daily.mean_point_value,
    daily.lower_p10_value,
    daily.median_p50_value,
    daily.upper_p90_value,
    daily.contributing_forecast_points,
    daily.aggregation_method
FROM agri.mv_forecast_ml_daily_serving AS daily
INNER JOIN nearest_cell ON nearest_cell.cell_id = daily.spatial_cell_id
WHERE daily.valid_day >= :valid_day_from
  AND (cardinality(:metric_names) = 0 OR daily.metric_name = ANY(:metric_names))
ORDER BY daily.valid_day, daily.series_key
LIMIT :row_limit
