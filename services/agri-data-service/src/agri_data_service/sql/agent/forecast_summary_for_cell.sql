-- Purpose: return published forecast values for the analysis cell nearest one point.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), radius_meters (double precision),
--         valid_from (timestamptz), metric_names (text[], empty array means "every metric"),
--         row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Everything selected here comes from agri.v_forecast_series_serving, the view that already
-- encodes what "published" means: publication state published, receipt finalized, run
-- validated, release set matching. Reading the view rather than re-deriving that join is
-- the point -- an agent must never be able to quote a draft or an unvalidated forecast.
--
-- How this query works, clause by clause:
--
--   WITH nearest_cell AS (... LIMIT 1)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced
--     below like a table. Forecast series are attached to analysis cells, not to arbitrary
--     coordinates, so the first job is turning the caller's point into exactly one cell.
--     LIMIT 1 makes that "exactly one": the nearest cell whose centroid falls inside the
--     radius, and no rows at all when the point is outside every cell's radius.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) / ::geography / ST_DWithin
--     Builds the caller's coordinate into a PostGIS point stamped with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude), then casts to geography so distance is measured in
--     metres on the curved earth rather than in degrees, whose real length varies with
--     latitude. ST_DWithin is the index-friendly "within radius metres" test.
--
--   INNER JOIN nearest_cell ON nearest_cell.cell_id = serving.spatial_cell_id
--     Restricts the serving view to that one cell. INNER, so a cell with no published
--     forecast returns nothing rather than a row of nulls -- "no forecast here" and "a
--     forecast whose value is unknown" are different claims and must not be conflated.
--
--   serving.valid_time >= valid_from
--     Drops horizons that have already elapsed. A forecast for last Tuesday is not evidence
--     about what happens next.
--
--   cardinality(metric_names) = 0 OR serving.metric_name = ANY(metric_names)
--     The optional filter. cardinality() is the array's length, so an empty array means
--     "no filter requested" and every metric passes. ANY(array) is the SQL spelling of
--     "is in this list" for an array-typed bind parameter.
--
--   p10_value / p90_value
--     The uncertainty band around point_value, carried so a reader can see how wide the
--     forecast's own spread is instead of treating the point estimate as exact.
--
--   ORDER BY serving.valid_time, serving.series_key ... LIMIT row_limit
--     A total order before the limit. Paging or truncating without one can repeat or skip
--     rows, because the database is otherwise free to return equal rows in any order.
WITH nearest_cell AS (
    SELECT
        cell.id AS cell_id,
        cell.cell_key,
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
    LIMIT 1
)
SELECT
    nearest_cell.cell_key,
    nearest_cell.distance_m,
    serving.series_key,
    serving.entity_type,
    serving.entity_key,
    serving.metric_name,
    serving.metric_unit,
    serving.issue_time,
    serving.valid_time,
    serving.horizon_step,
    serving.point_value,
    serving.p10_value,
    serving.p90_value,
    serving.published_at
FROM agri.v_forecast_series_serving AS serving
INNER JOIN nearest_cell ON nearest_cell.cell_id = serving.spatial_cell_id
WHERE serving.valid_time >= :valid_from
  AND (cardinality(:metric_names) = 0 OR serving.metric_name = ANY(:metric_names))
ORDER BY serving.valid_time, serving.series_key
LIMIT :row_limit
