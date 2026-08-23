-- Purpose: read ONE PAGE of the SSURGO delineation keys currently published -- the mupolygonkeys
--          argument pipeline/lanes/soil_survey.py's release export streams over.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: after_key (text -- exclusive lower bound; '' starts the walk)
--         page_size (integer -- keys per page, POLYGON_KEY_BATCH_SIZE)
--
-- PAGED, NOT CAPPED. This query used to take a `key_ceiling` one above a 200,000-key budget so a
-- truncated result was detectable, and the caller refused anything that reached it. Measured against
-- production on 2026-08-23 the real population is past that budget, so the refusal fired on every
-- tick and this lane has never written a single object; docs/lanes/soil-survey.md section 5 point 8
-- puts the PNW envelope at 1,507,623 delineations, which the cap's own comment already conceded it
-- was never a claim about. The fix is to stop reading the set at once: the caller walks pages and
-- flushes each one to a part file, so nothing here is ever truncated and nothing is ever whole.
--
-- KEYSET, NOT OFFSET. `after_key` is an exclusive lower bound on the key itself, so each page reads
-- forward from where the last one stopped. An OFFSET walk would re-derive and re-sort the whole
-- DISTINCT set for every page, making page N cost N times page 1 -- at ~7,500 pages that is the
-- difference between a lane that finishes and one that does not. '' is the opening bound rather than
-- NULL because `properties ->> 'id' IS NOT NULL` is already required below and no real mupolygonkey
-- is the empty string, so `> ''` admits exactly the first key without needing a nullable comparison.
--
-- The four predicates are transcribed from soil_survey_day_export.sql's own WHERE clause so the
-- key list and the export select the same population. mupolygonkey is properties->>'id' there too,
-- and the export re-namespaces it to the 'usda-sda' producer prefix itself, so the BARE key is
-- what a caller must hand back. ORDER BY is the stream's own grain (SOIL_SURVEY_GRAIN), which is
-- what makes the streamed part files globally ordered rather than merely sorted within each part.
SELECT DISTINCT f.properties ->> 'id' AS mupolygonkey
FROM geo.geometry AS g
JOIN geo.features AS f ON f.geometry_id = g.geometry_id
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE g.producer = 'usda-sda'
  AND g.version_valid_to IS NULL
  AND l.name = 'soil-survey'
  AND f.status = 'published'
  AND f.properties ->> 'id' IS NOT NULL
  AND f.properties ->> 'id' > :after_key
ORDER BY mupolygonkey
LIMIT :page_size
