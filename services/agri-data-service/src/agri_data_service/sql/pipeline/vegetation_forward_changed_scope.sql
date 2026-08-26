-- vegetation_forward_changed_scope
-- Purpose: select exact valid raw vegetation cell-days changed in an operator-pinned window.
-- Loaded by: agri_data_service.pipeline.parquet.vegetation_forward
-- Params: since (timestamptz), through_day (date)
WITH changed AS (
    SELECT
        btrim(feature.properties->>'cellKey') AS cell_key,
        CASE
            WHEN pg_input_is_valid(substring(feature.properties->>'observedAt', 1, 10), 'date')
            THEN substring(feature.properties->>'observedAt', 1, 10)::date
        END AS observed_day
    FROM geo.features AS feature
    INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
    WHERE layer.name = 'vegetation'
      AND (feature.created_at >= :since OR feature.updated_at >= :since)
)
SELECT DISTINCT cell_key, observed_day
FROM changed
WHERE cell_key IS NOT NULL
  AND cell_key <> ''
  AND observed_day IS NOT NULL
  AND observed_day <= :through_day
ORDER BY observed_day, cell_key
