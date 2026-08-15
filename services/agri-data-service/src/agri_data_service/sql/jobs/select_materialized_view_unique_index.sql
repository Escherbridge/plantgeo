-- select_materialized_view_unique_index
-- Purpose: answer whether a materialized view carries at least one UNIQUE index with no partial
--          WHERE clause -- the one precondition REFRESH MATERIALIZED VIEW CONCURRENTLY requires
--          PostgreSQL to accept the command at all.
-- Loaded by: agri_data_service.jobs.strategy_mv_refresh
-- Params: schema_name (text) -- the view's schema, e.g. "geo".
--         view_name (text) -- the view's bare name, e.g. "mv_strategy_recommendations_coarse".
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, so a colon-led word in
-- a comment mints a bind parameter nobody supplies and execution fails.
--
-- What this returns: exactly one row, always -- EXISTS never returns no rows -- holding one
-- boolean. true means CONCURRENTLY is legal; false means the caller must fall back to a plain
-- REFRESH, which locks the view against readers for the duration of the rebuild.
--
-- How this query works, clause by clause:
--
--   pg_index idx JOIN pg_class view_class ON view_class.oid = idx.indrelid
--     pg_index lists every index in the database by the table/matview it indexes
--     (indrelid); joining pg_class resolves that oid back to a name so the WHERE below can
--     name the view the way an operator would.
--
--   JOIN pg_namespace view_ns ON view_ns.oid = view_class.relnamespace
--     A bare relname is not unique across schemas, so the schema is joined in too and checked
--     explicitly, rather than trusting search_path -- this runtime pins none (see jobs/AGENTS.md
--     "Every statement is agri.-qualified, always", the same reasoning applied to geo here).
--
--   idx.indisunique
--     PostgreSQL's own precondition: REFRESH ... CONCURRENTLY refuses to run at all unless the
--     view carries a unique index, and raises only once the refresh is already underway --
--     checking first here is what lets this lane choose the safe path up front instead of
--     discovering the refusal from a driver exception mid-refresh.
--
--   idx.indpred IS NULL
--     A partial index (one built WITH a WHERE clause) does not satisfy PostgreSQL's own
--     precondition either; indpred is the stored predicate expression and is NULL on every
--     non-partial index.
SELECT EXISTS (
    SELECT 1
    FROM pg_index AS idx
    JOIN pg_class AS view_class ON view_class.oid = idx.indrelid
    JOIN pg_namespace AS view_ns ON view_ns.oid = view_class.relnamespace
    WHERE view_ns.nspname = :schema_name
      AND view_class.relname = :view_name
      AND idx.indisunique
      AND idx.indpred IS NULL
) AS has_unique_index
