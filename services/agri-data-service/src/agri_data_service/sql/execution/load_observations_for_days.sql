-- load_observations_for_days
-- Purpose: materialize only explicitly touched vegetation days for bounded forward ingestion.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: layer_name, cell_keys, cutoff_day, observed_days, source_release_id, day_bucket_rule,
--         grid_name, metric_name, transform_version
--
-- This is the forward-only sibling of load_observations.sql. The two equal-length arrays are
-- zipped by unnest into exact accepted cell-day pairs; they are never independent set filters.
WITH selected AS (
    SELECT selected.cell_key, selected.observed_day
    FROM unnest(
        CAST(:cell_keys AS text[]),
        CAST(:observed_days AS date[])
    ) AS selected(cell_key, observed_day)
),
daily AS (
    SELECT
        feature.properties->>'cellKey' AS entity_key,
        substring(feature.properties->>'observedAt', 1, 10)::date AS observed_day,
        avg((feature.properties->>'ndvi')::double precision) AS metric_value,
        count(*)::integer AS source_row_count,
        max(feature.created_at) AS data_available_at,
        sum((feature.properties->>'sampleCount')::integer)::integer AS pixel_sample_count,
        max((feature.properties->>'cloudCover')::double precision) AS max_cloud_cover,
        array_agg(
            DISTINCT feature.properties->>'sceneId'
            ORDER BY feature.properties->>'sceneId'
        ) AS scene_ids
    FROM geo.features AS feature
    INNER JOIN selected
        ON selected.cell_key = feature.properties->>'cellKey'
       AND selected.observed_day = substring(feature.properties->>'observedAt', 1, 10)::date
    WHERE feature.layer_id = (SELECT id FROM geo.layers WHERE name = :layer_name)
      AND substring(feature.properties->>'observedAt', 1, 10)::date <= :cutoff_day
    GROUP BY 1, 2
)
INSERT INTO agri.forecast_observation (
    series_id, source_release_id, observed_at, valid_from, valid_to,
    data_available_at, metric_value, quality_flag, source_event_key,
    observation_checksum, metadata_json
)
SELECT
    series.id,
    :source_release_id,
    daily.observed_day::timestamptz,
    daily.observed_day::timestamptz,
    (daily.observed_day + 1)::timestamptz,
    daily.data_available_at,
    daily.metric_value,
    'accepted',
    concat_ws(':', daily.entity_key, daily.observed_day::text),
    encode(
        digest(
            concat_ws(
                '|',
                'sentinel2_ndvi_daily_cell_mean_v1',
                daily.entity_key,
                daily.observed_day::text,
                daily.metric_value::text,
                daily.source_row_count::text,
                daily.data_available_at::text,
                daily.pixel_sample_count::text,
                daily.max_cloud_cover::text,
                array_to_string(daily.scene_ids, ',')
            ),
            'sha256'
        ),
        'hex'
    ),
    jsonb_build_object(
        'dayBucketRule', CAST(:day_bucket_rule AS text),
        'sourceRowCount', daily.source_row_count,
        'pixelSampleCount', daily.pixel_sample_count,
        'maxSceneCloudCoverPercent', daily.max_cloud_cover,
        'sceneIds', to_jsonb(daily.scene_ids)
    )
FROM daily
INNER JOIN agri.spatial_cell AS cell
    ON cell.cell_key = CAST(:grid_name AS text) || ':' || daily.entity_key
INNER JOIN agri.forecast_series AS series
    ON series.spatial_cell_id = cell.id
   AND series.metric_name = CAST(:metric_name AS varchar)
   AND series.source_transform_version = CAST(:transform_version AS varchar)
ON CONFLICT DO NOTHING
RETURNING id
