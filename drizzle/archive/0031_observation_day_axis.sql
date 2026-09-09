-- Split the feature observation-day census in two: ADD the day axis here, as a relation nothing
-- reads yet. The repoint of `geo.v_observation_day_census` onto it is a SEPARATE migration, 0032,
-- and the split between the two files is a safety property, not tidiness -- see "WHY THIS IS TWO
-- MIGRATIONS" below.
--
-- WHY, and it is a measurement rather than an argument. `geo.mv_feature_observation_day` is one
-- statement doing three jobs at three wildly different costs. Decomposed on production with
-- EXPLAIN (ANALYZE, BUFFERS) over a single layer (`vegetation`, 184,943 rows):
--
--   variant                             plan                        spill                  exec
--   -----------------------------------------------------------------------------------------
--   COUNT(*) FILTER only                HashAggregate, 8,281 kB     none                    0.82 s
--   + COUNT(DISTINCT geometry_id)       GroupAggregate + Sort       none (quicksort 14 MB)  2.17 s
--   + MAX(...properties...)             GroupAggregate + Sort       external merge, 118 MB  7.74 s
--   both (the shipped 0029 DDL)         GroupAggregate + Sort       external merge, 118 MB 27.00 s
--
-- Two independent causes, and neither is the day axis itself. `COUNT(DISTINCT geometry_id)` has no
-- hash implementation, so its presence alone converts the whole aggregate from HashAggregate to
-- Sort + GroupAggregate. `MAX(...COALESCE(properties ->> ...)...)` then makes each sorted tuple
-- 511 bytes wide, because the sort input has to carry `properties` through -- which is how one
-- layer pushes 118 MB to disk and the full relation pushes roughly 1.4 GiB.
--
-- WHAT THE AXIS ACTUALLY BUYS, AND WHAT IT DOES NOT. The per-layer table above invites a wrong
-- conclusion, so the full-relation numbers are stated first and plainly:
--
--   * IT IS NOT FASTER. Refreshing this relation on production 2026-08-17 took 286,800 ms against the
--     combined statement's 283,049 ms. Extrapolating 0.82 s/layer to "~22 s over 5.0M rows" -- which
--     an earlier draft of this header did -- was wrong by more than 13x. At full scale the sequential
--     scan of geo.features and the detoast of `properties` dominate, and BOTH variants pay them.
--   * IT DOES STILL SPILL. `HashAggregate ... Planned Partitions: 32` over 5,001,027 rows partitions
--     to disk. "Spills nothing" is true only of the one-layer plan.
--   * WHAT CHANGES IS THE WIDTH. 33 bytes per tuple against 511 -- roughly 165 MB against roughly
--     1.4 GiB. On a 3 GB-capped box where RAM, not disk, is the stated constraint, an ~8.5x cut in
--     the maintenance path's largest single allocation is the entire justification for this file.
--   * AND RELIABILITY. Under a 900 s statement timeout a 287 s refresh completes; the shipped census
--     under a 300 s timeout does not, and a refresh that cannot complete re-queues forever.
--
-- Total refresh SECONDS per day go slightly UP under this design (an hourly 287 s axis plus a
-- six-hourly 283 s wide relation, against up to 24 failing 300 s wide attempts). That is the trade,
-- and it is a trade of seconds for peak allocation and for correctness, not a free win.
--
-- The combined statement's whole-relation cost, from two reads of `agri.matview_refresh_state` on
-- 2026-08-17: 283,049 ms `refreshed_concurrently`, then 300,257 ms `failed` -- the latter being its
-- 300 s statement timeout to the millisecond. So the shipped census is not merely marginal, it is
-- already failing, and the retry loop that produces is part of what this change set removes.
--
-- THE NEXT LEVER, RECORDED SO IT IS NOT RE-DERIVED. `ix_features_layer_observation_day` is
-- (layer_id, geo.feature_observation_day(properties)) INCLUDE (geometry_id) WHERE status =
-- 'published' -- every column this aggregate reads. The single thing forcing a heap scan is the
-- fire-perimeters `properties` COALESCE guard below, which the planner cannot push into the index.
-- Splitting the defining query into an index-only branch for the ten layers that carry no
-- `fireDiscoveryDateTime` plus a heap branch for fire-perimeters alone could remove the detoast
-- outright. That is a DDL redesign that needs its own EXPLAIN, deliberately not attempted here.
--
-- WHY THIS IS TWO MIGRATIONS, AND IT IS THE WHOLE REASON THE FILE IS SPLIT.
-- `CREATE MATERIALIZED VIEW ... WITH NO DATA` leaves a relation PostgreSQL flatly refuses to read:
--
--   select count(*) from geo.mv_soil_survey_union;
--     ERROR:  materialized view "mv_soil_survey_union" has not been populated
--
-- and that is a THROWN QUERY, not an empty result. It is not avoidable by join type -- measured on
-- production, `... full join geo.mv_soil_survey_union u on false` raises identically. Since
-- `readObservationWindows` (environmental-read-model.ts:3401) and the signal/drought axis (:3479)
-- both select from `geo.v_observation_day_census`, repointing that view at an unpopulated relation
-- would 500 the ENTIRE layer-catalogue request and mount no slider at all -- until the hourly lane's
-- next tick, and indefinitely if that refresh failed. Keeping the repoint in 0032, behind an
-- assertion that this relation is populated, makes that ordering impossible to get wrong: if the
-- populate step is skipped or fails, 0032 refuses and production is untouched, because nothing reads
-- this relation until 0032 lands.
--
-- The three structural rules 0029's header sets out apply here unchanged and are not restated: WITH
-- NO DATA on every matview (creating with data would run the defining query inside preDeployCommand
-- on the 3 GB box), a unique index on every matview (a matview without one is a matview that will
-- one day blank the layer it feeds, because a non-concurrent REFRESH holds ACCESS EXCLUSIVE for its
-- whole duration), and no CONCURRENTLY of any kind (the drizzle migrator wraps each migration file
-- in one transaction, where it raises 25001 before it even checks IF NOT EXISTS). The drizzle
-- statement-separator marker is likewise never written inside a comment in this file: the migrator
-- splits the text on that literal string even in a comment, and doing so cuts the header in half and
-- kills the migration with a 42601.
--
-- THIS FILE ASSERTS NOTHING ABOUT `ix_features_layer_observation_day`, deliberately, and that is a
-- departure from 0029:72-77 recorded rather than left silent. That assertion raises unless the index
-- exists, on the stated grounds that without it the census refresh sequentially scans a 3,677 MB
-- heap. The index is present in production and the live plan seq-scans `geo.features` anyway, twice,
-- so the assertion's causal claim is not what is happening. It stays where it is -- an applied
-- migration is immutable and is not edited -- but it is not carried forward, because a migration must
-- not RAISE over a belief. Note the honest tension: the measurement above says the access path IS
-- what dominates this aggregate's cost, so a future index-only rewrite (see "THE NEXT LEVER") would
-- make an index precondition genuinely load-bearing here. It is not load-bearing for the statement
-- actually written below, which seq-scans either way, and that is the only thing an assertion in this
-- file may claim. (Contrast 0032's precondition, which asserts a fact that gates correctness today.)

-- ---------------------------------------------------------------------------------------------
-- THE DAY AXIS -- the hash-aggregate half of `geo.mv_feature_observation_day`
-- ---------------------------------------------------------------------------------------------
-- Column contract: the first five columns of the census contract 0029 section 1 defines, verbatim.
-- The three this relation does NOT carry -- distinct_key_count, newest_observed_at, metric_counts
-- -- are exactly the three that cost the sort, and 0032 serves them from the wide relation.
--
-- EVERY PREDICATE BELOW IS COPIED FROM `geo.mv_feature_observation_day`'s `day_total` CTE AND MUST
-- STAY IDENTICAL TO IT. The two relations are joined on (surface_name, observed_day) by 0032's view,
-- and a day one of them admits and the other does not is a row with half its columns -- so this is a
-- parity requirement, not a stylistic one:
--
--   status = 'published'                      the same floor readObservationWindows applied.
--   feature_observation_day(...) IS NOT NULL  rows whose publisher-named day does not PARSE are
--                                             excluded rather than grouped under NULL: REFRESH
--                                             CONCURRENTLY diffs through the unique index and NULLs
--                                             never compare equal, so a NULL-day row would be
--                                             deleted and re-inserted on every refresh forever.
--   the fire-perimeters COALESCE guard        PUBLISHER_NAMED_DAY_RULE parity. `feature_observation_day`
--                                             COALESCEs FOUR keys; the slider axis buckets from the
--                                             THREE shared ones (0018 added `fireDiscoveryDateTime`
--                                             as a tile-only fallback and recorded that split;
--                                             observation-day-contract.test.ts pins it). Without the
--                                             guard the 13 published fire-perimeters rows whose only
--                                             timestamp is `fireDiscoveryDateTime` would be counted
--                                             on an axis that renders none of them. Scoped to the one
--                                             layer because 0018:34 records that no other layer in
--                                             the warehouse carries the key at all, and PostgreSQL
--                                             short-circuits OR left to right, so the other ten
--                                             layers never evaluate the COALESCE.
--
-- `observation_count` counts ONLY linked rows, because that is what `readObservationWindows`
-- counted and an axis offering days no reader can draw would be worse than no axis. The orphans
-- are reported beside it as `unlinked_count`, never discarded -- `getMetricAtDate` needs that
-- number to say "N of M observations on this date are not yet linked to a place" instead of
-- reporting a partially-orphaned day as complete. This is also why neither this relation nor the
-- covering index is predicated on `geometry_id IS NOT NULL`.
--
-- This relation still READS `properties`: it is the argument of the grouping function and of the
-- fire-perimeters guard. What it no longer does is CARRY `properties` through a sort, and that --
-- not the access path -- is the width reduction above.
CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_feature_observation_day_axis AS
SELECT
  'feature'::text AS surface_kind,
  l.name AS surface_name,
  geo.feature_observation_day(f.properties) AS observed_day,
  COUNT(*) FILTER (WHERE f.geometry_id IS NOT NULL)::bigint AS observation_count,
  COUNT(*) FILTER (WHERE f.geometry_id IS NULL)::bigint AS unlinked_count
FROM geo.layers AS l
JOIN geo.features AS f ON f.layer_id = l.id
WHERE f.status = 'published'
  AND geo.feature_observation_day(f.properties) IS NOT NULL
  AND (
    l.name <> 'fire-perimeters'
    OR COALESCE(
         f.properties ->> 'observedAt',
         f.properties ->> 'updatedAt',
         f.properties ->> 'polygonDateTime'
       ) IS NOT NULL
  )
GROUP BY l.name, geo.feature_observation_day(f.properties)
WITH NO DATA;
--> statement-breakpoint

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_feature_observation_day_axis
  ON geo.mv_feature_observation_day_axis (surface_name, observed_day);
--> statement-breakpoint

COMMENT ON MATERIALIZED VIEW geo.mv_feature_observation_day_axis IS
  'The feature day axis alone: one row per (geo.layers.name, publisher-named day) over published '
  'geo.features, carrying only the two counts that hash. Same population and same day rule as '
  'geo.mv_feature_observation_day, which keeps the three columns that force a Sort + GroupAggregate '
  '-- distinct_key_count, newest_observed_at and metric_counts -- on a slower refresh cadence. '
  'Measured 2026-08-17 over 5,001,027 rows: this aggregate is HashAggregate with Planned Partitions '
  '32 at 33 bytes per tuple (~165 MB spilled), against the combined statement''s Sort at 511 bytes '
  'per tuple (~1.4 GiB). It partitions to disk; it partitions ~8.5x less of it. It is NOT faster -- '
  '286,800 ms against 283,049 ms -- because the scan and the properties detoast dominate at scale.';
