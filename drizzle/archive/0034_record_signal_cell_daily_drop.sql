-- DORMANT. Not registered in drizzle/meta/_journal.json; do not register it as part of this task.
--
-- WHAT THIS RECORDS. `geo.mv_signal_cell_daily` was DROPPED out-of-band against production on
-- 2026-08-18: it was 6,349 MB / 24,958,092 rows and its last full rebuild measured 1,729 s (~29
-- minutes) against a 2 GB-capped container mid live-ingest. That DROP was never captured as a
-- migration. `drizzle/0029_pre_aggregation_layer.sql:533` still contains
-- `CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_signal_cell_daily AS ...`, so replaying migration
-- history from scratch against a fresh database -- or against any environment that is missing this
-- file -- silently resurrects the 6.3 GB relation this file exists to keep gone.
--
-- This migration makes the drop itself replayable and idempotent: it is a no-op wherever the
-- relation is already absent (the common case, since prod already has it dropped), and it performs
-- the same drop wherever some other environment still has 0029's CREATE applied and unremoved.
--
-- DEGRADED AGENT TOOLS -- KNOWN, ACCEPTED, NOT FIXED HERE. Four agent SQL tools read this relation
-- as their sole source:
--   services/agri-data-service/src/agri_data_service/sql/agent/signal_value_on_day.sql
--   services/agri-data-service/src/agri_data_service/sql/agent/signal_neighbors_in_time.sql
--   services/agri-data-service/src/agri_data_service/sql/agent/signals_near_point.sql
--   services/agri-data-service/src/agri_data_service/sql/agent/nearest_signal_cells.sql
-- With the view gone, each of these throws a hard database error (relation does not exist) rather
-- than returning stale or empty data -- worse than silence, but a state the owner has explicitly
-- accepted as correct until the Parquet-backed replacement path exists. Do NOT attempt to restore
-- `geo.mv_signal_cell_daily`, wrap these four callers in an existence guard, or otherwise paper
-- over the error as part of this migration. That is separate, scoped work gated on the Parquet
-- path landing, not a migration-history bookkeeping fix.
--
-- ORDERING. This file must be understood as recording history, not performing an operation anyone
-- is waiting on: production already has the view dropped, so applying this migration against prod
-- changes nothing there. Its only live effect is on any OTHER environment (a fresh database build,
-- a restored snapshot predating 2026-08-18, or a from-scratch migration replay) that would
-- otherwise re-materialize the view via 0029 and never learn it was supposed to go away again.

DROP MATERIALIZED VIEW IF EXISTS geo.mv_signal_cell_daily;
--> statement-breakpoint

-- Assertion mirroring the DO $$ style in drizzle/0030 -- confirm the relation is genuinely gone
-- (not merely renamed or replaced by something `to_regclass` still resolves as non-matview), so a
-- partial failure upstream of this file cannot leave the guard silently satisfied on the wrong
-- object.
DO $$
DECLARE
  relation_ref regclass := to_regclass('geo.mv_signal_cell_daily');
BEGIN
  IF relation_ref IS NOT NULL THEN
    RAISE EXCEPTION
      'geo.mv_signal_cell_daily still resolves to a relation (oid=%) after DROP MATERIALIZED VIEW IF EXISTS -- something recreated it in the same transaction, or the name now refers to a non-matview object this migration does not know how to remove. Investigate before proceeding; do not re-run blindly.',
      relation_ref;
  END IF;
END $$;
