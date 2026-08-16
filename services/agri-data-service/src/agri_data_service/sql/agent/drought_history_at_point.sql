-- agent_drought_history_at_point
-- Purpose: return the U.S. Drought Monitor severity that covered one point, release by release,
--          read from the SAME plane the map paints -- geo.drought_areas, indexed by
--          geo.mv_drought_release_index.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), valid_date_from (text -- an ISO yyyy-mm-dd day,
--         compared lexicographically), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- THIS STATEMENT USED TO READ agri.drought_polygon_snapshot, AND THAT WAS A TRUTHFULNESS BUG.
-- That table holds zero rows and has no forward producer anywhere in the tree, while the map
-- serves drought from geo.drought_areas -- 1,040 rows across 208 weekly releases spanning
-- 2022-08-09 to 2026-08-11, measured 2026-08-15. The old query therefore SUCCEEDED and returned
-- nothing, on every call, for every point, which the agent could only read as "no drought was
-- recorded here". It would state there was no drought on days the user could see drought painted
-- on the map in front of them. An empty result that reads as an absence is worse than an error.
--
-- The fix is to read the plane the map reads. That is also the governing rule of the whole
-- pre-aggregation design: the agent and the map must never be able to disagree, and the only way
-- to guarantee that is to have them read the same relation.
--
-- WHAT THE TWO RELATIONS CONTRIBUTE.
--   geo.mv_drought_release_index is the small rollup -- one row per published valid_date, about
--   208 of them, carrying the previous and next release dates and how many severity classes that
--   release published. It replaces an unbounded scan of geo.drought_areas that both drought
--   readers and the regional-context assembler used to run just to answer "which release covers
--   this day". It is the OUTER relation here, deliberately (see the LEFT JOIN note below).
--
--   geo.drought_areas holds the polygons. Only geo.drought_areas.dm_category and its dates are
--   ever projected -- NEVER geom. That table is 640 kB of visible heap hiding about 495 MB of
--   TOAST behind 1,040 rows (measured 2026-08-15), so any unbounded projection of the geometry
--   column drags the whole 495 MB through a 3 GB box. The geometry is used as a filter and never
--   as a result.
--
-- USDM publishes one polygon set per week, with a separate polygon per severity class
-- (0 = D0 "abnormally dry" through 4 = D4 "exceptional drought"). The classes nest: a point inside
-- the D3 polygon is also inside D0, D1 and D2. So a point that intersects four polygons in one
-- week is in one drought condition, not four, and the honest answer for that week is the highest
-- class it fell inside.
--
-- How this query works, clause by clause:
--
--   WITH probe AS (SELECT ... AS geom)
--     A CTE ("common table expression") -- a named subquery defined up front and referenced below
--     like a table. This one builds the caller's coordinate once so the two places that need it
--     read the same value rather than each re-deriving it.
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the coordinate into a PostGIS point stamped with SRID 4326 (WGS84 -- ordinary GPS
--     longitude/latitude). Without the stamp PostGIS refuses to compare it against the stored
--     polygons, which are all 4326. No ::geography cast appears anywhere in this statement,
--     because nothing here is measured -- these are containment tests, not distances, so the
--     degree-based coordinate system is not a hazard.
--
--   releases AS (...)
--     The second CTE, the bounded list of published releases the answer will report on, newest
--     first. Reading the release index rather than SELECT DISTINCT valid_date FROM the polygon
--     table is what turns an unbounded scan into a lookup over about 208 rows.
--
--   release.valid_date::text >= valid_date_from
--     The lookback window. The cast to text is defensive and free at this size -- the column is
--     an ISO yyyy-mm-dd string on the polygon table, and comparing ISO day strings is safe
--     because they sort lexicographically in the same order they sort chronologically. The bind
--     is supplied as a string for the same reason.
--
--   LEFT JOIN LATERAL (...) ON TRUE
--     LATERAL lets the subquery on the right see each row on the left, so it can be evaluated
--     once per release -- an ordinary subquery cannot reference the row it is being joined to.
--     ON TRUE means "no extra join condition", because the correlation already lives inside.
--
--     The load-bearing property is that the RELEASE LIST drives the answer and the polygon test
--     hangs off it, rather than the other way round. Every published release appears in the
--     result whether or not a drought polygon covered the point that week; a week with no
--     covering polygon comes back with a NULL severity_class and a covering_class_count of 0,
--     which the caller renders as an explicit "this release published no drought class over this
--     point". Driving from the polygon table instead would emit only the weeks that had drought,
--     and "no drought that week" would be indistinguishable from "no release that week" -- the
--     exact shape of the bug this file was rewritten to fix. (The subquery aggregates without a
--     GROUP BY, so it yields one row regardless; LEFT is written anyway so the intent survives a
--     later edit that adds a grouping.)
--
--   area.geom && probe.geom
--     The bounding-box overlap operator, and the index prefilter. It asks only whether the two
--     bounding boxes overlap, which the GiST index on geo.drought_areas.geom can answer without
--     reading the polygon itself. Cheap rejections happen here; only survivors pay for the exact
--     test below.
--
--   ST_Intersects(area.geom, probe.geom)
--     The exact test -- true when the point falls inside (or on the edge of) the polygon. Run
--     only on the handful of rows the bounding-box prefilter admitted.
--
--   max(area.dm_category)
--     The nesting collapse. Of the up-to-five class polygons a release may publish over one
--     point, the honest single answer is the worst class it fell inside, so the milder ones it
--     necessarily also falls inside are discarded rather than reported as separate facts.
--
--   prev_valid_date / next_valid_date
--     Carried from the release index so a caller asking about a day BETWEEN two releases can say
--     how far the nearest release on each side really is, instead of implying the release it
--     quotes was issued for the day asked about.
--
--   ORDER BY releases.valid_date DESC
--     A total order before the limit. Truncating without one can repeat or skip rows, because the
--     database is otherwise free to return equal rows in any order.
WITH probe AS (
    SELECT ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326) AS geom
),
releases AS (
    SELECT
        release.valid_date,
        release.prev_valid_date,
        release.next_valid_date,
        release.class_count
    FROM geo.mv_drought_release_index AS release
    WHERE release.valid_date::text >= :valid_date_from
    ORDER BY release.valid_date DESC
    LIMIT :row_limit
)
SELECT
    releases.valid_date,
    releases.prev_valid_date,
    releases.next_valid_date,
    releases.class_count AS published_class_count,
    covering.severity_class,
    covering.covering_class_count,
    covering.published_at
FROM releases
LEFT JOIN LATERAL (
    SELECT
        max(area.dm_category) AS severity_class,
        count(*) AS covering_class_count,
        max(area.ingested_at) AS published_at
    FROM geo.drought_areas AS area
    CROSS JOIN probe
    WHERE area.valid_date = releases.valid_date::text
      AND area.geom && probe.geom
      AND ST_Intersects(area.geom, probe.geom)
) AS covering ON TRUE
ORDER BY releases.valid_date DESC
