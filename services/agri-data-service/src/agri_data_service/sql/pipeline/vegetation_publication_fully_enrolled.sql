-- vegetation_publication_fully_enrolled
-- Purpose: prove every governed vegetation source day has durable publication state.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: none
SELECT NOT EXISTS (
    SELECT DISTINCT (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day
    FROM agri.forecast_series AS series
    INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
    INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
    INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
    WHERE series.metric_name = 'ndvi'
      AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
      AND source.key = 'sentinel2-ndvi-l2a'
      AND observation.quality_flag = 'accepted'
    EXCEPT
    SELECT observed_day FROM agri.vegetation_publication_day
) AS fully_enrolled
