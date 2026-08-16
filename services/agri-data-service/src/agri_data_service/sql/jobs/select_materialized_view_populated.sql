-- select_materialized_view_populated
-- Purpose: answer whether a materialized view has ever been populated at all -- the precondition
--          REFRESH MATERIALIZED VIEW CONCURRENTLY refuses unconditionally (a plain WITH NO DATA
--          view has no rows to diff against). A view created WITH NO DATA (every matview
--          drizzle/0029 creates) starts life with pg_class.relispopulated = false.
-- Loaded by: agri_data_service.jobs.matview_refresh
-- Params: schema_name (text) -- the view's schema, e.g. "geo".
--         view_name (text) -- the view's bare name, e.g. "mv_signal_cell_daily".
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, so a colon-led word in
-- a comment mints a bind parameter nobody supplies and execution fails.
--
-- What this returns: zero or one row -- zero only when the caller's own to_regclass existence
-- check was skipped or raced, since every caller of this file has already confirmed the relation
-- exists. One boolean column. true means a REFRESH may use CONCURRENTLY (subject to the separate
-- unique-index check in select_materialized_view_unique_index.sql); false means the very next
-- refresh MUST be a plain, non-concurrent REFRESH -- CONCURRENTLY on an unpopulated matview raises
-- "cannot refresh materialized view ... concurrently" before it does anything at all.
--
-- How this query works, clause by clause:
--
--   pg_class view_class JOIN pg_namespace view_ns ON view_ns.oid = view_class.relnamespace
--     A bare relname is not unique across schemas, so the schema is joined in and checked
--     explicitly, matching select_materialized_view_unique_index.sql's own reasoning -- this
--     runtime pins no search_path (see jobs/AGENTS.md "Every statement is agri.-qualified, always").
--
--   view_class.relispopulated
--     PostgreSQL's own flag: false from CREATE MATERIALIZED VIEW ... WITH NO DATA until the first
--     REFRESH (concurrent or not) succeeds; a later REFRESH never resets it back to false, so once
--     true it stays true for the life of the relation.
SELECT view_class.relispopulated AS is_populated
FROM pg_class AS view_class
JOIN pg_namespace AS view_ns ON view_ns.oid = view_class.relnamespace
WHERE view_ns.nspname = :schema_name
  AND view_class.relname = :view_name
