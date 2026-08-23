-- Purpose: report when the published Oregon OEM evacuation-area set last CHANGED -- the source
--          watermark the evacuation-zones static lane stamps its snapshot with, instead of
--          stamping the cron's run date.
-- Loaded by: agri_data_service.pipeline.parquet.lane_registry
-- Params: none
--
-- THIS LANE IS A REFERENCE LOOKUP WITH A VERSION, NOT A DAILY SERIES. Oregon publishes current
-- state only and no past evacuation level is reconstructable (docs/lanes/evacuation-zones.md
-- section 3), so the day in the partition path is a version stamp. The lane writes ONE snapshot
-- dated at whatever day this query reports, and nothing at all while a partition dated at or
-- after that day already exists -- so a tick the cron skipped costs nothing, because no day ever
-- carried an obligation.
--
-- WHY updated_at IS A REAL CHANGE CLOCK HERE. This layer is refreshed in place through
-- sql/ingest/refresh_features.sql, whose UPDATE is gated on
-- `(properties - 'geometry' - 'geometry_repaired') IS DISTINCT FROM (next_properties - 'geometry')`.
-- An hourly poll that finds Oregon's feed unchanged moves nothing. Without that gate this column
-- would be a poll clock and this lane would churn a full re-snapshot every tick -- exactly the
-- behaviour being removed.
--
-- THREE CHANGE EVENTS, GREATEST OF THE THREE:
--   * feature.updated_at        -- an attribute changed (evacuation level, area name, counts).
--   * feature.created_at        -- a brand-new area appeared. An insert moves created_at and a
--                                 refresh of an already-walked row moves only updated_at
--                                 (0022_features_write_time_indexes.sql:13), so neither column
--                                 alone sees every change.
--   * geometry.version_valid_from -- the Type-2 chain minted a new polygon version. Reading this
--                                 matters because refresh_features.sql's change test strips the
--                                 geometry key before comparing, so a shape-only revision moves
--                                 neither of the two columns above.
-- geo.geometry.last_confirmed_at is deliberately NOT read: it advances on every re-confirmation
-- of unchanged ground (the pattern at src/lib/server/services/usda-soil.ts:833), which makes it a
-- poll clock, and a poll clock in a version stamp is the fabrication this model exists to refuse.
--
-- geo.geometry is LEFT JOINed for the same reason evacuation_zones_day_export.sql LEFT JOINs it: a
-- feature not yet linked to a geometry version still has a shape on feature.geom and still
-- exports, so an INNER JOIN here would compute the watermark over a narrower population than the
-- snapshot writes.
--
-- THE PREDICATES ARE TRANSCRIBED from evacuation_zones_day_export.sql's own WHERE clause, and must
-- stay transcribed -- a watermark over a different population than the export writes either
-- triggers a snapshot for rows that never land in it, or calls the lane current while a published
-- row is missing.
SELECT
    max(feature.updated_at) AS feature_updated_at,
    max(feature.created_at) AS feature_created_at,
    max(geometry.version_valid_from) AS geometry_version_valid_from,
    GREATEST(
        max(feature.updated_at),
        max(feature.created_at),
        max(geometry.version_valid_from)
    ) AS watermark_at,
    count(*) AS row_count
FROM geo.features AS feature
JOIN geo.layers AS layer ON layer.id = feature.layer_id
LEFT JOIN geo.geometry AS geometry ON geometry.geometry_id = feature.geometry_id
WHERE layer.name = 'evacuation-zones'
  AND layer.is_public IS TRUE
  AND feature.status = 'published'
  AND feature.geom IS NOT NULL
