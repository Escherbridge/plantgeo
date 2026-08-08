-- Purpose: return the U.S. Drought Monitor severity that covered one point, week by week.
-- Loaded by: agri_data_service.agent.tools
-- Params: longitude/latitude (double precision), issue_date_from (date), row_limit (int)
--
-- Parameter names appear above WITHOUT a leading colon -- see "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- USDM publishes one polygon set per week, with a separate polygon per severity class
-- (0 = D0 "abnormally dry" through 4 = D4 "exceptional drought"). The classes nest: a point
-- inside the D3 polygon is also inside D0, D1 and D2. So a point that intersects four
-- polygons in one week is in one drought condition, not four, and the honest answer for
-- that week is the highest class it fell inside.
--
-- How this query works, clause by clause:
--
--   ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
--     Builds the caller's coordinate into a PostGIS point stamped with SRID 4326 (WGS84 --
--     ordinary GPS longitude/latitude). Without the stamp PostGIS refuses to compare it
--     against the stored polygons, which are all 4326.
--
--   ST_Intersects(snapshot.geometry, point)
--     True when the point falls inside (or on the edge of) the polygon. No ::geography cast
--     is needed here because nothing is being measured -- this is a containment test, not a
--     distance, so the degree-based coordinate system is not a hazard.
--
--   SELECT DISTINCT ON (snapshot.issue_date) ... ORDER BY issue_date DESC, severity_class DESC
--     DISTINCT ON is a PostgreSQL extension that keeps only the first row of each group,
--     where "first" is decided by the ORDER BY. Its expressions must be the leading ORDER BY
--     terms, which is why issue_date leads. Ordering severity_class DESC inside each week
--     therefore keeps the worst class and discards the milder ones it nests inside -- the
--     nesting collapse described above, expressed as an ordering rather than a subquery.
--
--   issue_date >= issue_date_from
--     The lookback window. Bounded by the caller, and paired with row_limit so a wide window
--     over a long history still returns a predictable number of rows.
--
--   data_available_at column
--     Carried through because it is not the same as issue_date: it is when the release
--     actually became readable. A caller reasoning about what was knowable at some past
--     moment needs the publication time, not the week the map describes.
SELECT DISTINCT ON (snapshot.issue_date)
    snapshot.issue_date,
    snapshot.severity_class,
    snapshot.impact_type,
    snapshot.data_available_at
FROM agri.drought_polygon_snapshot AS snapshot
WHERE snapshot.issue_date >= :issue_date_from
  AND ST_Intersects(
      snapshot.geometry,
      ST_SetSRID(ST_MakePoint(:longitude, :latitude), 4326)
  )
ORDER BY snapshot.issue_date DESC, snapshot.severity_class DESC
LIMIT :row_limit
