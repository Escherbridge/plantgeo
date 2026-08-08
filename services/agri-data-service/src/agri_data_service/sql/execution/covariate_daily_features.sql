-- Purpose: read one spatial cell's pinned covariate vectors as one row per (day, feature),
--          which is the design matrix the evaluation-only wind ridge is fitted on.
-- Loaded by: agri_data_service.execution.covariate_wind_model
-- Params: cell_id (uuid), window_start (timestamptz), window_end (timestamptz),
--         as_of_time (timestamptz), schema_version (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy's text() scans comments for colon-prefixed words too, and
-- writing "cell_id" in a comment with a leading colon would mint a bind parameter no
-- caller supplies.
--
-- How this query works, clause by clause:
--
--   FROM agri.covariate_daily_features(...)
--     This is a set-returning function: unlike an ordinary function that returns one value,
--     it returns a whole table, so it can sit in FROM exactly where a table name would. The
--     function itself is declarative schema (db/agri/functions/covariate_daily_features.sql)
--     and it is what applies the availability gate -- it admits a source observation only
--     when that observation's data_available_at is at or before the as_of_time passed in.
--     That gate is the leakage argument for the whole feature plane, and it lives in the
--     function rather than here so one definition serves every caller.
--
--   CAST(... AS uuid) / CAST(... AS timestamptz) / CAST(... AS varchar)
--     The casts are not decoration. The driver sends parameters without types, and PostgreSQL
--     cannot infer a function argument's type from an untyped placeholder, so it would refuse
--     the call with "could not determine data type of parameter". Naming the type here is what
--     makes the call resolvable.
--
--   input_count / expected_input_count
--     Returned so the caller can tell a feature that was fully computed from one that was
--     computed from fewer inputs than its window asked for. A partial feature is NOT an error
--     and is NOT silently repaired here; the caller decides what to do and reports how many
--     days it excluded and which feature caused each exclusion.
--
--   ORDER BY observed_date, feature_index
--     A total order over the whole result. The caller pivots these rows into a dense matrix
--     and checksums evidence derived from it, so a stable row order is what makes two runs of
--     the same window produce the same digest instead of two orders of the same rows.
SELECT feature_row.observed_date,
       feature_row.feature_index,
       feature_row.feature_name,
       feature_row.feature_value,
       feature_row.input_count,
       feature_row.expected_input_count
FROM agri.covariate_daily_features(
    CAST(:cell_id AS uuid),
    CAST(:window_start AS timestamptz),
    CAST(:window_end AS timestamptz),
    CAST(:as_of_time AS timestamptz),
    CAST(:schema_version AS varchar)
) AS feature_row
ORDER BY feature_row.observed_date, feature_row.feature_index
