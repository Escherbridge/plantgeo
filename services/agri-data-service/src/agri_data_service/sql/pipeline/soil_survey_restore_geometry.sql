-- pipeline_soil_survey_restore_geometry
-- Purpose: refuse invalid native soil-survey support before preparing any rung.
-- Loaded by: pipeline.soil_survey_restore.prepare
-- Params: none; soil_restore_native is a bounded in-memory Arrow view.
-- The inner projection decodes each preserved WKB polygon without changing it.
-- The aggregate is true only when every geometry is nonempty, valid, polygonal,
-- and inside the longitude/latitude range declared by the EPSG 4326 schema.
-- This query reads only the registered local batch; it never opens PostgreSQL.
SELECT bool_and(
    ST_IsValid(geom) AND NOT ST_IsEmpty(geom)
    AND ST_GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
    AND ST_XMin(geom) >= -180 AND ST_XMax(geom) <= 180
    AND ST_YMin(geom) >= -90 AND ST_YMax(geom) <= 90
) AS native_support_valid
FROM (SELECT ST_GeomFromWKB(geometry_wkb) AS geom FROM soil_restore_native)
