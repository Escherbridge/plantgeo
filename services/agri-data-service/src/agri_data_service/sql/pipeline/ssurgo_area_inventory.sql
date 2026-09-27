-- ssurgo_area_inventory
-- Purpose: list every SSURGO survey area whose own extent intersects the region envelope, with
--          its current publication watermark, before any per-area capture begins.
-- Loaded by: pipeline.direct.soil_survey.source
-- Params: west, south, east, north -- WGS84 envelope ordinates, checked finite and correctly
--         ordered, then formatted with Python's repr() of a float so no user-controlled text ever
--         reaches this template.
-- Dialect: T-SQL, run remotely by USDA SDA (Tabular/post.rest); no PostgreSQL involvement.
--
-- NEW in the 2026-09-27 native-geometry port (revision 2, findings F1/F9): a wave's shard plan and
-- every subsequent capture invocation's --area argument are meant to come from this census, never
-- from a literal area list.
--
-- P1-02: sacatalog also carries STATSGO2's generalized areas -- each state's own 2-character
-- symbol (e.g. 'ID') and the national 'US' -- which do not fit this layer's SSURGO area-symbol
-- shape (^[A-Z]{2}[0-9]{3}$, AreaSymbol) and would otherwise raise an uncaught pydantic
-- ValidationError instead of a clean SoilSurveyError when the census tries to record them. The
-- LEN() = 5 filter below excludes exactly that class of row -- every real SSURGO area symbol is a
-- 2-letter state/territory code plus a 3-digit sequence, five characters total.
--
-- Safety: repr() of a Python float that already passed math.isfinite() and the WGS84 range check
-- can only ever render as a plain decimal literal (never a string, never a comma) -- the same
-- validated-literal boundary the other three T-SQL files in this directory rest on
-- (AGENTS.md, "Literal substitution is still the safety boundary").
--
-- How this query works, clause by clause:
--
--   FROM sacatalog sac
--   INNER JOIN legend lg ON lg.areasymbol = sac.areasymbol
--     Start from the catalog, same as ssurgo_summary.sql, so every area in the answer carries its
--     own current saverest vintage.
--
--   INNER JOIN sapolygon sp ON sp.lkey = lg.lkey
--     sapolygon holds each survey area's own outline geometry -- distinct from mupolygon, the
--     per-map-unit delineations a later capture call fetches. One area has exactly one (possibly
--     multi-part) outline, so this join does not multiply catalog rows the way joining mupolygon
--     directly would.
--
--   WHERE sp.sapolygongeo.STIntersects(
--           geometry::STGeomFromText('POLYGON((...))', 4326)
--         ) = 1
--     STGeomFromText builds the envelope as a SQL Server geometry value at SRID 4326 (WGS84);
--     STIntersects keeps only survey areas whose own outline overlaps that envelope at all -- an
--     area entirely outside the requested extent contributes no row. Comparing against literal 1
--     because STIntersects returns a SQL Server bit, not a native boolean this dialect can use
--     bare in a WHERE clause the way `= 1` makes explicit here.
--
--   AND LEN(sac.areasymbol) = 5
--     Excludes STATSGO2's generalized areas (each state's own 2-character symbol, plus the
--     national 'US'), which are not SSURGO detailed surveys and do not fit this layer's
--     ^[A-Z]{2}[0-9]{3}$ area-symbol shape.
--
--   ORDER BY sac.areasymbol
--     A total order, so two identical censuses always list their areas in the same sequence and a
--     diff between them is never just a row-ordering artifact.
SELECT DISTINCT sac.areasymbol,
       CONVERT(varchar(33), sac.saverest, 126) AS saverest
FROM sacatalog sac
INNER JOIN legend lg ON lg.areasymbol = sac.areasymbol
INNER JOIN sapolygon sp ON sp.lkey = lg.lkey
WHERE sp.sapolygongeo.STIntersects(
    geometry::STGeomFromText('POLYGON(({west} {south}, {east} {south}, {east} {north}, {west} {north}, {west} {south}))', 4326)
) = 1
AND LEN(sac.areasymbol) = 5
ORDER BY sac.areasymbol
