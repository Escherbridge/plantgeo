-- vegetation_forward_affected_days
-- Purpose: list the governed observation days changed by one bounded vegetation cell selection.
-- Loaded by: agri_data_service.pipeline.parquet.vegetation_forward
-- Params: prefixed_cell_keys (text[]), cutoff_day (date), observed_days (date[]), source_release_id (uuid)
--
-- The equal-length arrays are exact selected cell-day pairs. Reading them back from the governed
-- source proves every accepted pair landed before the Parquet writer may author its full day.
WITH selected AS (
    SELECT selected.cell_key, selected.observed_day
    FROM unnest(
        CAST(:prefixed_cell_keys AS text[]),
        CAST(:observed_days AS date[])
    ) AS selected(cell_key, observed_day)
)
SELECT DISTINCT
    cell.cell_key,
    (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day
FROM selected
INNER JOIN agri.spatial_cell AS cell ON cell.cell_key = selected.cell_key
INNER JOIN agri.forecast_series AS series ON series.spatial_cell_id = cell.id
INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
  AND (observation.observed_at AT TIME ZONE 'UTC')::date = selected.observed_day
INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE series.metric_name = 'ndvi'
  AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
  AND source.key = 'sentinel2-ndvi-l2a'
  AND observation.source_release_id = CAST(:source_release_id AS uuid)
  AND observation.observed_at < ((:cutoff_day)::date + 1)::timestamp AT TIME ZONE 'UTC'
  AND observation.quality_flag = 'accepted'
ORDER BY observed_day DESC, cell.cell_key
