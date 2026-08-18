-- Repoint `geo.v_observation_day_census`'s feature leg at the day axis 0031 created, once that
-- relation is actually populated. This is the half of the re-grain that has a blast radius, and it
-- is a separate file from 0031 for exactly that reason.
--
-- DO NOT APPLY THIS UNTIL `geo.mv_feature_observation_day_axis` HAS BEEN REFRESHED. The precondition
-- below enforces it, so a mis-ordered deploy fails this migration instead of production. The reason
-- is not stylistic:
--
--   select count(*) from geo.mv_soil_survey_union;
--     ERROR:  materialized view "mv_soil_survey_union" has not been populated
--
-- measured on production against a relation that is `relispopulated = false` today. PostgreSQL
-- REFUSES TO READ an unpopulated matview -- a thrown query, not an empty result -- and no join type
-- avoids it: `... full join geo.mv_soil_survey_union u on false` raises identically. Both
-- `readObservationWindows` (environmental-read-model.ts:3401) and the signal/drought axis (:3479)
-- select from this view, so repointing it at an unpopulated relation 500s the whole layer-catalogue
-- request and mounts no slider at all.
--
-- The populate step is:
--   SET statement_timeout = '900s';
--   REFRESH MATERIALIZED VIEW geo.mv_feature_observation_day_axis;   -- NOT concurrently: illegal
--                                                                   -- on an unpopulated matview
-- MEASURED on production 2026-08-17 (inside a rolled-back transaction): 286.8 s for 11,231 rows. The
-- 900 s timeout is not padding -- an earlier draft of this note said "~22 s" from a per-layer
-- extrapolation and was wrong by more than 13x. Budget five minutes of maintenance window for it.
--
-- No CONCURRENTLY in this file, and the drizzle statement-separator marker never appears inside a
-- comment here -- both for the reasons 0029's and 0031's headers give.

-- ---------------------------------------------------------------------------------------------
-- 0. PRECONDITION -- the axis must hold data, not merely exist
-- ---------------------------------------------------------------------------------------------
-- `to_regclass` is deliberately NOT the test. It resolves for a matview created WITH NO DATA, which
-- is precisely the state that breaks this view, so an existence check here would pass and then hand
-- production a 500. `pg_class.relispopulated` is the fact that gates correctness.
--
-- This is the CONTRAST 0031's header draws with 0029:72-77. That assertion guarantees an index the
-- live plan does not use; this one guarantees the single condition without which the statement below
-- is a production outage.
DO $$ BEGIN
  IF to_regclass('geo.mv_feature_observation_day_axis') IS NULL THEN
    RAISE EXCEPTION
      'geo.mv_feature_observation_day_axis does not exist: apply drizzle/0031 before 0032';
  END IF;
  IF NOT (SELECT relispopulated FROM pg_class WHERE oid = 'geo.mv_feature_observation_day_axis'::regclass) THEN
    RAISE EXCEPTION
      'geo.mv_feature_observation_day_axis exists but has never been refreshed: run REFRESH '
      'MATERIALIZED VIEW geo.mv_feature_observation_day_axis (non-concurrently, statement_timeout '
      '900s) before applying 0032, or every read of geo.v_observation_day_census will raise 55000';
  END IF;
END $$;
--> statement-breakpoint

-- ---------------------------------------------------------------------------------------------
-- 1. THE UNION VIEW, REPOINTED
-- ---------------------------------------------------------------------------------------------
-- Still a plain view over roughly 35,000 rows, for the reason 0029 section 1d gives; the only change
-- is that its FEATURE leg now reads two relations instead of one.
--
-- WHY A FULL JOIN. Not as an outage mitigation -- it is not one, per the precondition above, and an
-- earlier draft of this file wrongly claimed it was. It is here because the two relations refresh on
-- DIFFERENT cadences (the axis hourly, the wide relation six-hourly), so each can legitimately know
-- a (surface, day) the other does not, and the census must be the union of both rather than whichever
-- one happens to lead. Once both are populated the FULL JOIN also degrades rather than empties if one
-- of the two refreshes starts failing, which is a real property, just not the one that keeps the
-- deploy safe.
--
-- COALESCE ORDER IS FRESH-FIRST, and it is the correctness argument for the join: both relations
-- compute `observation_count` and `unlinked_count` from byte-identical predicates (0031's header
-- pins that as a parity requirement), so they can differ only by refresh time, and the axis is the
-- one that refreshes hourly. The preference is by RELATION, not by recency, so it is stable: the
-- same (surface, day) always answers from the axis when the axis has it, and cannot flicker between
-- sources between two reads.
--
-- THE THREE DETAIL COLUMNS ARE NOT COALESCED AND ARE NOT DEFAULTED. A (surface, day) the axis has
-- reached and the wide relation has not yet reports NULL for distinct_key_count, newest_observed_at
-- and metric_counts. NULL there means "not yet computed", which is the honest encoding; filling
-- metric_counts with '{}' would report zero candidates for a day that has not been counted, which is
-- a fabricated absence and the exact class of false-zero this pre-aggregation layer exists to remove.
-- No reader consumes those three columns through this view today -- `getMetricAtDate` reads
-- `geo.mv_feature_observation_day` directly (environmental-read-model.ts:4137), as does the
-- vegetation recency probe (:1420) -- so the NULLs are reachable only by a future reader, which is
-- exactly the one that must see them.
--
-- KNOWN, ACCEPTED, AND STATED SO NOBODY REDISCOVERS IT AS A BUG: A GHOST DAY. Because this is a FULL
-- JOIN, a (surface, day) the wide relation still holds and the axis has correctly dropped -- the last
-- row of that day was deleted or unpublished -- keeps appearing on the axis until the wide relation's
-- next refresh, up to six hours. The slider therefore offers a day that draws nothing, rather than
-- hiding a day that has data. That is the safer of the two errors and is why the FULL JOIN is kept
-- over an axis-led LEFT JOIN, but it IS an error and it is bounded only by the wide relation's
-- cadence (jobs/matview_refresh.py, `geo.mv_feature_observation_day`'s min_interval_seconds).
CREATE OR REPLACE VIEW geo.v_observation_day_census AS
SELECT
  'feature'::text AS surface_kind,
  COALESCE(axis.surface_name, detail.surface_name) AS surface_name,
  COALESCE(axis.observed_day, detail.observed_day) AS observed_day,
  COALESCE(axis.observation_count, detail.observation_count) AS observation_count,
  COALESCE(axis.unlinked_count, detail.unlinked_count) AS unlinked_count,
  detail.distinct_key_count,
  detail.newest_observed_at,
  detail.metric_counts
FROM geo.mv_feature_observation_day_axis AS axis
FULL JOIN geo.mv_feature_observation_day AS detail
  ON detail.surface_name = axis.surface_name
 AND detail.observed_day = axis.observed_day
UNION ALL
SELECT surface_kind, surface_name, observed_day, observation_count,
       unlinked_count, distinct_key_count, newest_observed_at, metric_counts
FROM geo.mv_signal_observation_day
UNION ALL
SELECT surface_kind, surface_name, observed_day, observation_count,
       unlinked_count, distinct_key_count, newest_observed_at, metric_counts
FROM geo.mv_drought_observation_day;
--> statement-breakpoint

COMMENT ON VIEW geo.v_observation_day_census IS
  'The 24-surface slider day axis in one relation: 11 geo.layers names, 12 signal streams and '
  'drought-areas. The OUTER relation of the section-9 LEFT JOIN stays in TypeScript; this is the '
  'inner one. A surface_name this view cannot emit is silently dropped from the capability '
  'payload -- tiles render, history reports zero, no slider mounts. Since 0032 the feature leg is '
  'the hourly geo.mv_feature_observation_day_axis FULL JOINed to the six-hourly '
  'geo.mv_feature_observation_day: counts answer from the axis wherever it holds the row, the three '
  'detail columns are NULL for a day the six-hourly relation has not reached, and a day the wide '
  'relation still holds after the axis dropped it lingers for up to one wide-relation cadence.';
