-- vegetation_source_reconciliation
-- Purpose: for a bounded window, return every (cell, day) the SOURCE governed plane
--          (agri.forecast_observation, reached through agri.forecast_series) holds for this
--          lane, together with how many agri.source_release rows produced it -- the evidence
--          pipeline/validation/vegetation.py reconciles against written Parquet, never against
--          the lane's own intermediate state.
-- Loaded by: agri_data_service.pipeline.validation.vegetation
-- Params: cell_ids (uuid[]), first_day (date), last_day (date) -- inclusive UTC calendar window
--
-- Mirrors sql/pipeline/vegetation_day_export.sql's own FROM/JOIN/WHERE shape exactly (same lane
-- literals: metric_name, source_transform_version, source.key, quality_flag) so "what the source
-- holds" is defined identically to what the exporter itself reads -- a validator with a
-- different filter would manufacture false gaps rather than find real ones.
--
-- WHY source_release_count IS HERE, NOT release_count FROM THE EXPORT: `register_governed_plane`
-- mints a NEW `agri.source_release` whenever the raw corpus digest changes, and
-- `load_observations.sql`'s write-time idempotency is scoped PER RELEASE
-- (`agri.forecast_observation`'s unique constraint on `(source_release_id, series_id,
-- source_event_key)`), so two overlapping registration runs write a SECOND observation row for
-- the same (series_id, observed_day) under a different release. The exporter's own
-- `release_count` already reports this per exported row, but it is read AFTER the exporter's
-- newest-release-wins dedup already picked a winner -- a validator that only reads the written
-- Parquet is trusting the exporter to have counted itself correctly. This query counts release
-- rows directly at the source, independent of the export path, so the duplication is visible even
-- if the exporter's own count were ever wrong.
SELECT
    cell.id::text AS cell_id,
    (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
    COUNT(*)::bigint AS source_release_count
FROM agri.forecast_series AS series
INNER JOIN agri.spatial_cell AS cell ON cell.id = series.spatial_cell_id
INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE series.spatial_cell_id = ANY(CAST(:cell_ids AS uuid[]))
  AND series.metric_name = 'ndvi'
  AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
  AND source.key = 'sentinel2-ndvi-l2a'
  AND observation.observed_at >= (:first_day)::timestamp AT TIME ZONE 'UTC'
  AND observation.observed_at < ((:last_day)::date + 1)::timestamp AT TIME ZONE 'UTC'
  AND observation.quality_flag = 'accepted'
GROUP BY cell.id, observed_day
ORDER BY observed_day, cell_id
