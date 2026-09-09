-- geo.fire_detections has zero rows and zero inserts ever (measured 2026-08-09 via
-- pg_stat_user_tables.n_tup_ins=0, stats_reset=NULL). Every FIRMS write, forward NRT cron
-- and local archive-history walk alike, lands in geo.features under layer 'fire-detections'
-- instead -- this table was never wired to a producer. No application code queries or writes
-- it (only its own Drizzle schema declaration referenced it), so dropping it is safe.
--
-- IF EXISTS keeps a re-run of this file a no-op, matching the rest of this migration set.
DROP TABLE IF EXISTS geo.fire_detections;
