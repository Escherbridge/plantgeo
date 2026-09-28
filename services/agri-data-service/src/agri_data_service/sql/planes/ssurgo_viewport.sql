-- ssurgo_viewport
-- Purpose: return every admitted SSURGO delineation whose PRECOMPUTED bounding-box columns
--         overlap a requested map viewport, at the one geometry rung this port ever publishes
--         (z13, "native").
-- Loaded by: agri_data_service.planes.soil_survey (registers the temporary view "ssurgo_view"
--            from an in-memory Arrow table before running this query; see that module's
--            "Admitted release read path" section)
-- Params (positional, bound by DuckDB, in this order):
--   1 west, 2 south, 3 east, 4 north -- the requested viewport, WGS84 degrees
--   5 row_limit -- rows fetched; one extra row over the caller's own limit proves truncation
--     without a second COUNT(*) query
-- Dialect: DuckDB with the spatial extension, run inside the serving session's bounded read;
--          no PostgreSQL involvement, and no GEOS predicate (see below).
--
-- Owner decision (Q4, 2026-09-27): repair a delineation's geometry with ST_MakeValid when
-- possible, otherwise KEEP the original ring and label it "invalid_unrepaired", but ALWAYS serve
-- it -- nothing is withheld for bad geometry. That decision has one hard consequence for THIS
-- query: an invalid ring cannot go anywhere near a GEOS predicate, because ST_Intersects would
-- silently reject it and turn "always serve" into "usually serve". So viewport membership here is
-- decided ENTIRELY by the four bbox_* columns every row already carries
-- (warehouse/schemas/soil_survey.py), never by testing geometry_wkb itself. See planes/AGENTS.md,
-- "Point lookups read bbox, not polygon" for the matching point-query trade.
--
-- How this query works, clause by clause:
--
--   SELECT * EXCLUDE (geometry_wkb), ST_AsGeoJSON(ST_GeomFromWKB(geometry_wkb)) AS geometry_json
--     Every stored column comes back except the raw WKB bytes, which the wire format never sends
--     directly; in their place, ST_GeomFromWKB parses those bytes into a DuckDB spatial value and
--     ST_AsGeoJSON renders it as a GeoJSON geometry object. These two calls only RENDER the stored
--     ring for the response -- they never test whether it is valid, so an invalid_unrepaired
--     delineation still comes back with real coordinates for the map to draw as-is, exactly as
--     Q4 requires.
--
--   WHERE bbox_east >= ? AND bbox_north >= ? AND bbox_west <= ? AND bbox_south <= ?
--     A row's bbox overlaps the requested viewport when the row's east edge is not west of the
--     viewport's west edge, and symmetrically for the other three sides -- the standard "two boxes
--     overlap" test, written as four independent comparisons that DuckDB can evaluate directly on
--     these plain DOUBLE columns without touching the geometry column at all.
--
--   ORDER BY mupolygonkey
--     Orders the bounded result by the delineation's own native key, so a repeated call for the
--     same viewport and the same limit always returns the same page in the same order -- useful for
--     a caller diffing two reads, and required for the truncation check below to mean anything
--     stable.
--
--   LIMIT ?
--     Bounded by the caller's row cap plus one: if DuckDB returns exactly `row_limit`, the Python
--     caller (`planes.soil_survey.run_admitted_soil_survey_query`) knows the true match count is
--     over the cap and refuses the whole read (`SoilSurveyError`, 503 `soil_survey_read_refused`)
--     rather than serving a spatially arbitrary truncated page (review finding 3, disposition F2)
--     -- one extra row is enough to prove that without a second, separate COUNT(*) query.
SELECT
    * EXCLUDE (geometry_wkb),
    ST_AsGeoJSON(ST_GeomFromWKB(geometry_wkb)) AS geometry_json
FROM ssurgo_view
WHERE bbox_east >= ?
  AND bbox_north >= ?
  AND bbox_west <= ?
  AND bbox_south <= ?
ORDER BY mupolygonkey
LIMIT ?
