-- Purpose: report when the published USDA SSURGO delineation set last CHANGED -- the source
--          watermark the soil-survey static lane stamps its release with, instead of stamping
--          the cron's run date.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: none
--
-- SSURGO IS A VINTAGED REFERENCE SET, NOT A SERIES. It issues no periodic re-observation, only a
-- survey area's own irregular republication (docs/lanes/soil-survey.md section 2), and the day in
-- the partition path is that vintage rather than an observation time. The lane writes ONE release
-- dated at whatever day this query reports, and nothing at all while a partition dated at or
-- after that day already exists.
--
-- TWO CHANGE EVENTS, GREATEST OF THE TWO:
--   * geometry.version_valid_from -- SSURGO's own `saverest` vintage for this delineation, which
--     docs/lanes/soil-survey.md section 2 measured spanning 2025-08-26 to 2026-03-19. This is the
--     source's own answer to "when did this last change" and is the primary term.
--   * feature.created_at -- the day a delineation first entered THIS warehouse's published set.
--     It is here because the lane is warmed lazily by viewport reads (docs/lanes/soil-survey.md
--     section 2): a newly warmed survey area can carry an OLD saverest, so the vintage term alone
--     would not advance and the newly captured delineations would never reach a release. An
--     insert moves created_at and nothing else moves it afterwards
--     (0022_features_write_time_indexes.sql:13), so it is a change event, not a poll clock.
--
-- geo.geometry.last_confirmed_at IS DELIBERATELY EXCLUDED, and this is the lane that proves why.
-- src/lib/server/services/usda-soil.ts:833 sets `DO UPDATE SET last_confirmed_at = now()` on
-- conflict, and its own comment at line 769 says "a re-fetch of unchanged ground only advances
-- last_confirmed_at". Using it would put the polling clock into the version stamp and restore the
-- daily churn this model removes. It still rides every exported row as provenance
-- (soil_survey_day_export.sql) -- it is simply not allowed to decide what version exists.
--
-- THE PREDICATES ARE TRANSCRIBED from soil_survey_day_export.sql's own WHERE clause MINUS its
-- per-batch `natural_key = ANY(...)` filter, because the watermark is a property of the whole
-- release rather than of one batch. Everything else -- version_valid_to IS NULL, producer
-- 'usda-sda', layer 'soil-survey', status 'published' -- is character-for-character the export's,
-- so the two queries answer about exactly the same population. `uq_geometry_current` and
-- `ix_features_geometry_id` carry the same joins the export rides.
--
-- row_count is returned so an operator can see the population this watermark describes; the release
-- itself has no ceiling to breach -- lane_registry_soil_survey_polygon_keys.sql pages the key walk
-- and the export flushes each page to a part file, so the whole set is never read at once.
SELECT
    max(g.version_valid_from) AS source_vintage_at,
    max(f.created_at) AS feature_created_at,
    GREATEST(max(g.version_valid_from), max(f.created_at)) AS watermark_at,
    count(*) AS row_count
FROM geo.geometry AS g
JOIN geo.features AS f ON f.geometry_id = g.geometry_id
JOIN geo.layers AS l ON l.id = f.layer_id
WHERE g.producer = 'usda-sda'
  AND g.version_valid_to IS NULL
  AND l.name = 'soil-survey'
  AND f.status = 'published'
