-- ssurgo_part_bounds
-- Purpose: re-read one finished part's WKB and prove its stored bbox columns, key count and
--          quality counts before the part may receive a manifest receipt.
-- Loaded by: pipeline.direct.soil_survey.prepare
-- Params: none. The caller registers a bounded Arrow table named ssurgo_part holding one part
--         (at most 500 rows) in the soil-survey Parquet schema.
-- Dialect: DuckDB with the spatial extension, run offline in an in-memory derivation session;
--          no PostgreSQL involvement.
--
-- How this query works, clause by clause:
--
--   FROM (SELECT ... ST_GeomFromWKB(geometry_wkb) AS geom FROM ssurgo_part)
--     Decode each row's served geometry from the exact bytes about to be written. Bytes that are
--     not WKB make DuckDB raise, and the caller refuses the part.
--
--   COUNT(*) and COUNT(DISTINCT mupolygonkey)
--     Row count and native-key count. They must be equal, so no delineation appears twice.
--
--   SUM(CASE WHEN ST_XMin(geom) = bbox_west AND ... THEN 0 ELSE 1 END)
--     Counts rows whose stored box disagrees with the geometry's own coordinate extent. Serving
--     selects rows by these four columns alone (never by a GEOS predicate, which would reject a
--     labelled invalid ring), so a mismatch would hide or misplace a delineation. It must be zero.
--     ST_XMin and friends read coordinates only; they work on invalid geometry too.
--
--   COUNT(*) FILTER (WHERE geometry_quality = ...)
--     FILTER keeps only matching rows inside one aggregate. These two counts must equal the
--     repaired and labelled counts Python recorded, so the manifest ledger matches the bytes.
--
--   MIN(bbox_west), MIN(bbox_south), MAX(bbox_east), MAX(bbox_north)
--     The part's own bounding box: the union of its rows' boxes, used to prune parts at serving.
SELECT COUNT(*),
       COUNT(DISTINCT mupolygonkey),
       SUM(CASE WHEN ST_XMin(geom) = bbox_west AND ST_YMin(geom) = bbox_south
                 AND ST_XMax(geom) = bbox_east AND ST_YMax(geom) = bbox_north THEN 0 ELSE 1 END),
       COUNT(*) FILTER (WHERE geometry_quality = 'repaired'),
       COUNT(*) FILTER (WHERE geometry_quality = 'invalid_unrepaired'),
       MIN(bbox_west), MIN(bbox_south), MAX(bbox_east), MAX(bbox_north)
FROM (
    SELECT mupolygonkey, geometry_quality, bbox_west, bbox_south, bbox_east, bbox_north,
           ST_GeomFromWKB(geometry_wkb) AS geom
    FROM ssurgo_part
)
