-- Purpose: one row per catalogued upstream in agri.data_source -- which observation planes it has
--          ever landed in, when a release that carried data was last written, how far forward its
--          newest observation reaches, and how many of its releases landed nothing anywhere -- so
--          /ops/backfill can show which sources nothing is keeping current.
-- Loaded by: agri_data_service.routes.ops
-- Params: none.
--
-- Parameter names would appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and would mint a bind
-- parameter nobody supplies. This file binds nothing.
--
-- VISIBILITY ONLY. THIS STATEMENT REGISTERS NOTHING AND SCHEDULES NOTHING.
--
-- Nothing here writes, and nothing downstream of it may arm a source: no Railway cron, no Windows
-- scheduled task, no job definition. Which upstreams deserve a forward load is an infrastructure
-- and cost decision with a human owner. This is the page that makes the decision VISIBLE; it is
-- not the thing that makes it.
--
-- READ THIS FIRST: "LANDED" MEANS THREE TABLES, NOT ONE. GETTING IT WRONG INVENTS A DEAD LANE.
--
-- This platform writes observations into THREE separate planes, and which one a source lands in is
-- a property of the source, not a fallback chain:
--
--   agri.signal_observation        the governed warehouse time series. NASA POWER and both
--                                  Open-Meteo archives write here and nowhere else.
--   agri.normalized_source_feature the normalized feature plane, for sources whose output is
--                                  features rather than a per-cell series.
--   agri.forecast_observation      the governed forecast-input plane. The Sentinel-2 NDVI
--                                  registration path writes here and NEVER touches
--                                  signal_observation -- that is deliberate and documented
--                                  (sql/execution/load_observations.sql, execution/AGENTS.md).
--
-- An audit that tests only the first two planes reports sentinel2-ndvi-l2a as having never landed a
-- single row in its life. That is FALSE, and it was believed on this project until it was checked:
-- verified against production 2026-08-09, that source holds 184,409 rows across 1,568 series
-- spanning 2022-08-05 to 2026-08-04, its release validates and its quality_summary claim matches
-- the landed count exactly. The mistake is worth a paragraph rather than a footnote because it is
-- the governing philosophy of this whole page -- reconstruct state from what actually landed --
-- failing on the DEFINITION of landed, and it fails in the most dangerous direction available: it
-- turns a healthy lane into a red alert on the one page whose only value is being trustworthy.
-- Any future plane must be added to the union below on the day it is created.
--
-- WHAT THIS ROW MEASURES, AND WHAT IT DOES NOT
--
-- A source row measures the GOVERNED path: releases in agri.source_release and the rows behind
-- them. It does NOT measure raw ingest. The two can and do diverge, and the divergence is the
-- interesting part: as of 2026-08-09 Sentinel-2's raw ingest into geo.features is scheduled and
-- healthy, while the step that promotes it into the governed plane (`agri-service forecast vegetation-register`,
-- a manual CLI verb in interface/cli/commands.py with no launcher and no entry in jobs/registry.py) is scheduled by
-- nothing at all. That sentence is a claim about CODE and may be armed at any time; every number
-- this statement returns is read live and needs no such caveat. So
-- the source reads increasingly stale here while its upstream is perfectly current -- which is the
-- correct reading, because what is unarmed is the REGISTRATION STEP. A promotion step can be
-- unarmed while the ingest beneath it is fine, and this panel sees the promotion.
--
-- WHAT "UNARMED" CAN AND CANNOT MEAN HERE
--
-- This statement CANNOT see a scheduler. There is no table in this database that records which
-- crons or scheduled tasks exist, so "unarmed" is an INFERENCE from landed evidence and must be
-- read as one: it means no release carrying data has been written for this source recently, which
-- is consistent with an unscheduled promotion step, a scheduled task that stopped firing, a
-- finished archive walk with no forward cron behind it, and an upstream that published nothing. It
-- distinguishes none of those, and the route's note says so. What it does prove, in the direction
-- that matters, is the negative: a source with a recent landed release IS being fed by something,
-- whatever that something is. This is the same discipline the rest of the page keeps -- reconstruct
-- state from what actually landed, never from what a ledger or a hand-written flag claims.
--
-- WHY A RELEASE THAT LANDED NOTHING IS COUNTED SEPARATELY
--
-- A load can succeed, publish an agri.source_release as provenance of what it attempted, and write
-- not one row behind it in ANY of the three planes. That is a real bug class. Audited across all
-- three planes against production 2026-08-09, exactly ONE source carries it: 8 releases of
-- open-meteo-era5-land-archive, written 05:02-05:03 UTC that morning by the radiation lane asking
-- era5_land for shortwave_radiation_sum. The fixed lane republished the same window against
-- open-meteo-era5-archive at 05:36-05:46 the SAME MORNING and landed data, so what the page shows
-- is a lane that wrote nothing beside the same work done correctly half an hour later. Those
-- releases are IMMUTABLE PROVENANCE and are deliberately never deleted -- they are the honest
-- record of what was attempted -- so counting them is the only thing that stops them reading as
-- healthy work. newest_zero_landing_release_at travels beside the count because a source whose
-- zero-landing releases are all months old is a closed chapter and one whose newest is three hours
-- old is a lane failing right now, and the count alone cannot tell those apart.
--
-- normalized_source_feature currently holds zero rows for every catalogued source, so that plane
-- carries no signal today. It stays in the union for completeness and because a feature source is
-- exactly the case that would otherwise be misreported, but nothing here may assume it is non-empty.
--
-- WHY EACH PLANE IS READ WITH A DIFFERENT SHAPE. THIS WAS MEASURED; THE OBVIOUS SPELLING HANGS.
--
-- The natural way to write this is one correlated subquery per plane per release --
-- (SELECT max(observed_at) ... WHERE source_release_id = release.id) three times over. Against
-- production 2026-08-09 that shape did not finish inside two minutes, for two separate reasons
-- worth naming because neither is visible without an EXPLAIN:
--
--   1. agri.forecast_observation currently holds exactly ONE distinct source_release_id, so the
--      planner's selectivity estimate for `source_release_id = <anything>` is 100% of the table and
--      a sequential scan genuinely is the cheaper plan FOR ONE PROBE. It is catastrophic for 1,811
--      of them: 1,811 sequential scans of 184,409 rows. The estimate is not wrong, the shape is.
--      Rolling that plane up ONCE with a GROUP BY reads those 184,409 rows a single time instead,
--      and joins the result. The same argument does not apply to agri.signal_observation, where a
--      GROUP BY would have to read 46 million rows and the per-release probe rides
--      ix_signal_observation_release_time to answer from one index entry -- so that plane keeps the
--      probe. Two planes, two shapes, each chosen by which side is small.
--
--   2. A CTE that PostgreSQL inlines is re-evaluated at every reference, and the signal probe's
--      result is read in several places below -- the frontier, the landed test, the landing-planes
--      array and the filtered aggregates. MATERIALIZED pins each CTE to exactly one evaluation,
--      which is precisely what that keyword is for, and it is load-bearing here rather than
--      decoration. Measured on production, best of three each, same session:
--
--          as written (MATERIALIZED)   1 SubPlan     1,811 signal-plane index searches   29.9 ms
--          hint stripped               6 SubPlans   10,866 signal-plane index searches   51.4 ms
--
--      Exactly 6.0x the index work. Do not remove the hint to "simplify" this statement.
--
-- How this query works, clause by clause:
--
--   WITH release_signal AS MATERIALIZED (...)
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. One row per source release, carrying the newest signal observation behind it.
--     MATERIALIZED forces PostgreSQL to evaluate it once and reuse the result; see reason 2 above.
--
--   (SELECT max(observation.observed_at) ... WHERE source_release_id = source_release.id)
--     A correlated scalar subquery: it can see the row it sits beside, so it runs once per release
--     with that release's id as a constant, and is answered by walking
--     ix_signal_observation_release_time -- which is (source_release_id, observed_at) -- backwards
--     to the end of that release's range and stopping at the first row. observed_at is NOT NULL in
--     that table, so a NULL result IS the landed test: the only way a max over a NOT NULL column
--     comes back NULL is that there were no rows at all.
--
--   forecast_plane / feature_plane AS MATERIALIZED (SELECT ... GROUP BY source_release_id)
--     The other two planes, each rolled up to the release in one pass. GROUP BY collapses many
--     rows to one per release. Both are small -- 184,409 rows and zero rows respectively -- which
--     is the whole reason they can afford a shape the 46-million-row signal plane cannot.
--
--     WHEN THIS CHOICE INVERTS, AND WHY IT IS LIKELY TO. "Small" here is an assumption nothing
--     enforces, and this panel exists partly to argue for arming the very step that grows
--     agri.forecast_observation -- so the shape should be expected to age. The forecast rollup is
--     currently a parallel sequential scan of the whole plane, about 9,706 buffers (~76 MB) and
--     20 ms warm, and ops_forecast_state.sql independently counts the same table on the same page
--     load. The rollup wins only while that plane holds FEW DISTINCT source_release_id values --
--     today exactly one, which is also why a per-release probe degenerates into a full scan there
--     (the planner's selectivity estimate for one distinct value is 100% of the table, correctly).
--     Once the registration step is armed and the plane carries many releases, that estimate falls,
--     ix_forecast_observation_release_available -- which already exists and leads with
--     source_release_id -- becomes usable, and the per-release probe used for the signal plane
--     becomes the cheaper shape here too. The trigger to revisit is therefore the DISTINCT RELEASE
--     COUNT in agri.forecast_observation rising off one, not the row count; re-measure both shapes
--     then rather than assuming either.
--
--   LEFT JOIN forecast_plane ON ... / LEFT JOIN feature_plane ON ...
--     LEFT so a release that landed in no plane still produces a row with NULLs beside it. An
--     INNER join would silently drop exactly the releases this panel exists to count. Because the
--     rollups contain one row per release that HAS rows, `... .release_id IS NOT NULL` after the
--     join is the landed test for those two planes -- and it is used rather than their max columns
--     because normalized_source_feature.observed_to is NULLABLE, so a feature row carrying no end
--     time would make its max NULL and the release would be misread as having landed nothing.
--
--   GROUP BY release.data_source_id in source_landing
--     Collapses every release to one row per source. Doing it here, once for all sources, rather
--     than inside a LATERAL evaluated once per source, is what keeps the plane reads above from
--     being repeated per catalogued source.
--
--   LEFT JOIN source_landing ... with coalesce(...) around every count
--     The catalog drives the result, so every catalogued source appears whether or not it has ever
--     published anything -- driving from agri.source_release instead would make a source with zero
--     releases vanish, which is precisely the source most worth seeing. A source with no releases
--     has no row in the rollup, so its counts arrive NULL and are coalesced to an honest 0. The
--     TIMESTAMPS are deliberately NOT coalesced: there is no date on which nothing happened, and a
--     fabricated one would be worse than a blank.
--
--   GREATEST(max(...), max(...), max(...))
--     The newest observation across all three planes. GREATEST returns the largest of its arguments
--     and IGNORES NULL ones, so a source that lands in exactly one plane still answers with that
--     plane's frontier rather than being dragged to NULL by the two it does not use.
--
--   array_remove(ARRAY[CASE WHEN bool_or(...) THEN '...' END, ...], NULL)
--     Which planes this source has EVER landed in, as a short array the panel prints under the
--     source name. bool_or is the OR of a boolean across every release of the source. A CASE with
--     no ELSE yields NULL when the plane was never used, and array_remove strips those, so a
--     source that lands only in the forecast plane reports exactly that and a reader can see at a
--     glance which definition of "landed" applies to it. This column exists because the
--     three-plane mistake described at the top of this file is invisible when the answer is a bare
--     boolean.
--
--   ORDER BY source.key
--     A stable, total order so the panel's rows do not shuffle between refreshes.

with release_signal as materialized (
    select
        source_release.id as release_id,
        source_release.data_source_id,
        source_release.created_at as written_at,
        (
            select max(observation.observed_at)
            from agri.signal_observation as observation
            where observation.source_release_id = source_release.id
        ) as newest_signal_at
    from agri.source_release as source_release
),

forecast_plane as materialized (
    select
        forecast_input.source_release_id as release_id,
        max(forecast_input.observed_at) as newest_forecast_at
    from agri.forecast_observation as forecast_input
    group by forecast_input.source_release_id
),

feature_plane as materialized (
    select
        feature.source_release_id as release_id,
        max(feature.observed_to) as newest_feature_at
    from agri.normalized_source_feature as feature
    group by feature.source_release_id
),

release_landing as (
    select
        release.data_source_id,
        release.written_at,
        release.newest_signal_at,
        forecast_plane.newest_forecast_at,
        feature_plane.newest_feature_at,
        (feature_plane.release_id is not null) as has_feature,
        (
            release.newest_signal_at is not null
            or forecast_plane.release_id is not null
            or feature_plane.release_id is not null
        ) as has_landed
    from release_signal as release
    left join forecast_plane on forecast_plane.release_id = release.release_id
    left join feature_plane on feature_plane.release_id = release.release_id
),

source_landing as (
    select
        release.data_source_id,
        count(*) as release_count,
        min(release.written_at) as first_release_at,
        max(release.written_at) as last_release_at,
        count(*) filter (where release.has_landed) as landed_release_count,
        max(release.written_at) filter (where release.has_landed) as last_landed_release_at,
        count(*) filter (where not release.has_landed) as zero_landing_releases,
        max(release.written_at) filter (where not release.has_landed) as newest_zero_landing_release_at,
        greatest(
            max(release.newest_signal_at),
            max(release.newest_feature_at),
            max(release.newest_forecast_at)
        ) as newest_observation_at,
        array_remove(
            array[
                case when bool_or(release.newest_signal_at is not null) then 'signal_observation' end,
                case when bool_or(release.has_feature) then 'normalized_source_feature' end,
                case when bool_or(release.newest_forecast_at is not null) then 'forecast_observation' end
            ],
            null
        ) as landing_planes
    from release_landing as release
    group by release.data_source_id
)

select
    source.key as source_key,
    source.name as source_name,
    source.is_active,
    source.review_state,
    coalesce(landed.release_count, 0) as release_count,
    landed.first_release_at,
    landed.last_release_at,
    coalesce(landed.landed_release_count, 0) as landed_release_count,
    landed.last_landed_release_at,
    coalesce(landed.zero_landing_releases, 0) as zero_landing_releases,
    landed.newest_zero_landing_release_at,
    landed.newest_observation_at,
    coalesce(landed.landing_planes, array[]::text[]) as landing_planes
from agri.data_source as source
left join source_landing as landed on landed.data_source_id = source.id
order by source.key
