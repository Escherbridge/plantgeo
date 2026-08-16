-- agent_materialized_plane_populated
-- Purpose: report, for each named relation, whether it exists and whether it has ever been
--          populated, so a tool can refuse honestly instead of raising or returning an empty
--          result that reads as an absence.
-- Loaded by: agri_data_service.agent.tools
-- Params: relation_names (text[] -- schema-qualified relation names, e.g. geo.mv_signal_cell_daily)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- WHY THIS EXISTS. Every pre-aggregated relation in this design is a MATERIALIZED VIEW, and a
-- materialized view can exist while holding nothing: PostgreSQL creates it WITH NO DATA and
-- refuses to read it until a REFRESH has run, raising "materialized view has not been populated"
-- rather than returning zero rows. agri.mv_forecast_ml_daily_serving is in exactly that state in
-- production today -- created, indexed, never refreshed, and with a refresher that was reading an
-- environment variable nobody set.
--
-- That leaves an agent tool two bad options and one good one. It can let the raise escape, which
-- surfaces to the model as a tool error with no explanation. It can catch the raise and return
-- nothing, which is the worst outcome of the three because "no forecast published here" and "the
-- forecast plane has never been built" are different facts and the model cannot tell them apart.
-- Or it can ask this question first and return a TYPED REFUSAL naming the relation that is not
-- built. That is what the callers do.
--
-- The check is deliberately cheap enough to run before every read: it is a lookup in the system
-- catalog, touching no user data at all, over an array that is never longer than three entries.
--
-- How this query works, clause by clause:
--
--   unnest(relation_names) AS wanted(name)
--     unnest() turns an array bind parameter into rows, one per element, so the statement answers
--     about every relation the caller named in a single round trip instead of one query each.
--     The `AS wanted(name)` part gives that derived table and its single column a name to join on.
--
--   to_regclass(wanted.name)
--     Resolves a relation name to its catalog identifier, or to NULL when no such relation exists.
--     It is the non-raising form -- a plain cast to regclass raises on an unknown name, which is
--     precisely the behaviour this statement exists to avoid. A NULL here is the "the migration
--     has not been applied" case, distinct from the "applied but never refreshed" case below.
--
--   LEFT JOIN pg_catalog.pg_class ON pg_class.oid = to_regclass(wanted.name)
--     LEFT, so a relation that does not exist still comes back as a row saying so, rather than
--     vanishing from the answer. A missing row would be read by the caller as "not asked about".
--
--   pg_class.relispopulated
--     The catalog's own record of whether a matview has been refreshed at least once. False means
--     reading it would raise. For an ordinary table or view it is always true, which is correct:
--     those are always readable.
--
--   pg_class.relkind
--     Returned so the caller can tell a matview ('m') from a view ('v') or a table ('r'). A plain
--     view over matviews -- geo.v_observation_day_census is one -- reports itself populated even
--     when the matviews beneath it are not, so a caller must probe the matviews by name and not
--     the view that unions them. Returning relkind is what makes that mistake visible.
--
--   coalesce(pg_class.relispopulated, false)
--     Turns the NULL a LEFT JOIN leaves for a missing relation into an explicit false, so the
--     caller has one boolean to test rather than a three-valued one.
SELECT
    wanted.name AS relation_name,
    to_regclass(wanted.name) IS NOT NULL AS relation_exists,
    pg_class.relkind AS relation_kind,
    coalesce(pg_class.relispopulated, false) AS is_populated
FROM unnest(:relation_names) AS wanted(name)
LEFT JOIN pg_catalog.pg_class ON pg_class.oid = to_regclass(wanted.name)
ORDER BY wanted.name
