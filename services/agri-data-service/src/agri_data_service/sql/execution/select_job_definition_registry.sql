-- select_job_definition_registry
-- Purpose: list every distinct job_definition.name this database's ledger has ever written, and
--          whether any version of that name is still enabled -- the one round trip `jobs-pulse`
--          needs to discover which durable archive definitions actually exist here, rather than
--          guessing from the static lane registry in code.
-- Loaded by: agri_data_service.execution.jobs_pulse_command
-- Params: none
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, so a colon-led word in a
-- comment mints a bind parameter nobody supplies and execution fails.
--
-- What this returns: one row per distinct definition NAME (not per version row), covering every
-- name ever registered -- a lane planned once and never re-planned still has exactly one row here,
-- because agri.job_definition keeps every version it has ever upserted rather than only the newest.
--
-- How this query works, clause by clause:
--
--   SELECT name, coalesce(bool_or(enabled), false) AS any_version_enabled
--     One output row per name (see GROUP BY below). bool_or is the boolean OR aggregate: it comes
--     back true as soon as ONE row in the group has enabled = true. coalesce only matters in
--     theory -- a name that reaches this GROUP BY always has at least one version row behind it --
--     but it keeps the column honestly NOT NULL rather than leaning on that fact silently.
--
--   FROM agri.job_definition
--     No JOIN: every fact this query needs -- the name, and whether it is enabled -- already lives
--     on this one table. A definition with several (name, version) rows, from an operator
--     re-declaring a lane's shape, folds into the single output row its name gets.
--
--   GROUP BY name
--     Collapses every version row of one definition name into the single row the caller reads.
--     This has to match how a pause is written: pausing a lane sets enabled = false on EVERY
--     version row of its name (see jobs/dispatch.py's own pause-state query, which reads the same
--     way), not just the newest one, so "any version enabled" is the honest paused/unpaused test.
--
--   ORDER BY name
--     A stable, total order over a small result set (today: a handful of definitions at most), so
--     two callers reading the same ledger state see the rows in the same order.
SELECT name,
       coalesce(bool_or(enabled), false) AS any_version_enabled
FROM agri.job_definition
GROUP BY name
ORDER BY name
