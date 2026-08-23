-- Purpose: list the SSURGO delineation keys currently published -- the mupolygonkeys argument
--          pipeline/lanes/soil_survey.py's release export batches over.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: key_ceiling (integer -- one MORE than the caller's declared key budget; see below)
--
-- BOUNDED BY DESIGN, AND THE BOUND IS DETECTABLE. docs/lanes/soil-survey.md section 5 measures the
-- PNW envelope alone at 1,507,623 delineations, so "read every key" is not a safe assumption at
-- this lane's scale. The limit is bound one ABOVE the caller's budget so a complete result set is
-- distinguishable from a truncated one: the caller that sees budget+1 rows refuses loudly rather
-- than exporting a silently truncated release, which would read back as a complete one and is the
-- exact wrong-but-plausible output the engineering principles forbid.
--
-- The four predicates are transcribed from soil_survey_day_export.sql's own WHERE clause so the
-- key list and the export select the same population. mupolygonkey is properties->>'id' there too,
-- and the export re-namespaces it to the 'usda-sda' producer prefix itself, so the BARE key is
-- what a caller must hand back.
SELECT DISTINCT f.properties ->> 'id' AS mupolygonkey
FROM geo.geometry AS g
JOIN geo.features AS f ON f.geometry_id = g.geometry_id
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE g.producer = 'usda-sda'
  AND g.version_valid_to IS NULL
  AND l.name = 'soil-survey'
  AND f.status = 'published'
  AND f.properties ->> 'id' IS NOT NULL
ORDER BY mupolygonkey
LIMIT :key_ceiling
