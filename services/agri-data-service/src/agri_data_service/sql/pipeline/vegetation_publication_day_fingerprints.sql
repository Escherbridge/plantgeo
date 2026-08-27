-- vegetation_publication_day_fingerprints
-- Purpose: fingerprint the exact 12-field governed vegetation export projection per UTC day.
-- Loaded by: agri_data_service.db.vegetation_publication
-- Params: first_day (date nullable), last_day (date nullable)
WITH governed AS (
    SELECT
        cell.id AS cell_id,
        cell.grid_name,
        series.metric_name,
        series.metric_unit,
        (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_day,
        observation.metric_value,
        observation.observation_checksum,
        observation.data_available_at,
        observation.id AS observation_id,
        release.retrieved_at AS release_retrieved_at,
        source.allowed_client_exposure
    FROM agri.forecast_series AS series
    INNER JOIN agri.spatial_cell AS cell ON cell.id = series.spatial_cell_id
    INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
    INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
    INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
    WHERE series.metric_name = 'ndvi'
      AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
      AND source.key = 'sentinel2-ndvi-l2a'
      AND observation.quality_flag = 'accepted'
      AND (:first_day IS NULL OR observation.observed_at >= (:first_day)::date::timestamp AT TIME ZONE 'UTC')
      AND (:last_day IS NULL OR observation.observed_at < ((:last_day)::date + 1)::timestamp AT TIME ZONE 'UTC')
), aggregated AS (
    SELECT
        cell_id,
        grid_name,
        metric_name,
        metric_unit,
        observed_day,
        (array_agg(metric_value ORDER BY release_retrieved_at DESC, observation_id DESC))[1] AS metric_value,
        (array_agg(observation_checksum ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
            AS observation_checksum,
        (array_agg(data_available_at ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
            AS data_available_at,
        COUNT(*)::bigint AS release_count,
        (array_agg(allowed_client_exposure ORDER BY release_retrieved_at DESC, observation_id DESC))[1]
            AS allowed_client_exposure
    FROM governed
    GROUP BY cell_id, grid_name, metric_name, metric_unit, observed_day
), projected AS (
    SELECT
        aggregated.observed_day,
        aggregated.cell_id::text AS cell_id,
        jsonb_build_array(
            aggregated.cell_id::text,
            aggregated.grid_name,
            aggregated.metric_name,
            aggregated.metric_unit,
            aggregated.observed_day::text,
            encode(pg_catalog.float8send(aggregated.metric_value), 'hex'),
            aggregated.observation_checksum,
            encode(pg_catalog.timestamptz_send(aggregated.data_available_at), 'hex'),
            aggregated.release_count,
            aggregated.allowed_client_exposure,
            encode(pg_catalog.float8send(ST_X(cell.centroid)), 'hex'),
            encode(pg_catalog.float8send(ST_Y(cell.centroid)), 'hex')
        )::text AS canonical_row
    FROM aggregated
    INNER JOIN agri.spatial_cell AS cell ON cell.id = aggregated.cell_id
)
SELECT
    observed_day,
    encode(
        digest(convert_to(string_agg(canonical_row, E'\n' ORDER BY cell_id), 'UTF8'), 'sha256'),
        'hex'
    ) AS source_fingerprint
FROM projected
GROUP BY observed_day
ORDER BY observed_day
