-- vegetation_forward_revision
-- Purpose: return the monotonic checkpoint revision of the governed vegetation observation plane.
-- Loaded by: agri_data_service.pipeline.parquet.vegetation_forward
-- Params: none
--
-- forecast_observation is append-only under the governed release contract. A new release, or a
-- later bounded registration that fills more cells into an existing release, increases this count.
-- Completion markers carry it so an older concurrent forward run never replaces a newer ladder.
SELECT COUNT(*)::bigint AS observation_count
FROM agri.forecast_series AS series
INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE series.metric_name = 'ndvi'
  AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
  AND source.key = 'sentinel2-ndvi-l2a'
  AND observation.quality_flag = 'accepted'
