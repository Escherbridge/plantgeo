-- ssurgo_native_geometry
-- Purpose: parse one captured page of native SSURGO WKT and classify each delineation as valid,
--          repairable, or invalid-but-served (owner Q4, "repair else label and serve always").
-- Loaded by: pipeline.direct.soil_survey.prepare
-- Params: none. The caller registers a bounded Arrow table named ssurgo_source with two text
--         columns, mupolygonkey and wkt, holding at most one capture page (500 rows).
-- Dialect: DuckDB with the spatial extension, run offline in an in-memory derivation session;
--          no PostgreSQL involvement.
--
-- How this query works, clause by clause:
--
--   WITH parsed AS (... ST_GeomFromText(wkt) ...)
--     A CTE (a named subquery the rest of the statement reads like a table). It turns each WKT
--     string into a DuckDB geometry value once, keeping every ring and every polygon member. Text
--     that is not WKT at all makes DuckDB raise, and the caller refuses the whole page.
--
--   classified AS (... TRY(ST_IsValid(...)) ... TRY(ST_MakeValid(...)) ...)
--     ST_IsValid asks GEOS whether the polygon obeys the OGC rules (no self-crossing ring, no
--     inverted hole). Only an invalid polygon is handed to ST_MakeValid, which rebuilds it. The
--     CASE returns NULL for a valid polygon, so a valid row never carries a repaired copy. Both
--     calls are wrapped in TRY: GEOS raises for some rings it cannot even build for validity
--     checking (an unclosed ring), and owner Q4 ("serve always") means that must fall through to
--     the invalid_unrepaired label, not abort the page. Python's `_classified` already treats
--     anything other than `is True` as not-valid, so a NULL from TRY reads the same as `false`.
--
--   SELECT ... original facts ... repaired facts ...
--     Returns raw facts, not a verdict. For the source geometry: is it empty, its type, whether
--     it is valid, its WKB bytes and its four bounding-box edges. The same five facts for the
--     repaired copy (all NULL for a valid row). Python then decides the label. A repair is kept
--     only if it is non-empty, still a Polygon or MultiPolygon, and valid. Otherwise the ORIGINAL
--     bytes are served with the label invalid_unrepaired, and its box comes from ST_XMin and
--     friends, which only read coordinates and never ask GEOS for validity.
--
--   CAST(ST_GeometryType(...) AS VARCHAR)
--     ST_GeometryType returns an enum. The cast gives Python a plain string such as POLYGON.
WITH parsed AS (
    SELECT mupolygonkey, ST_GeomFromText(wkt) AS original
    FROM ssurgo_source
),
classified AS (
    SELECT mupolygonkey,
           original,
           TRY(ST_IsValid(original)) AS original_valid,
           CASE WHEN TRY(ST_IsValid(original)) THEN NULL ELSE TRY(ST_MakeValid(original)) END AS repaired
    FROM parsed
)
SELECT mupolygonkey,
       ST_IsEmpty(original),
       CAST(ST_GeometryType(original) AS VARCHAR),
       original_valid,
       ST_AsWKB(original),
       ST_XMin(original), ST_YMin(original), ST_XMax(original), ST_YMax(original),
       ST_IsEmpty(repaired),
       CAST(ST_GeometryType(repaired) AS VARCHAR),
       TRY(ST_IsValid(repaired)),
       ST_AsWKB(repaired),
       ST_XMin(repaired), ST_YMin(repaired), ST_XMax(repaired), ST_YMax(repaired)
FROM classified
