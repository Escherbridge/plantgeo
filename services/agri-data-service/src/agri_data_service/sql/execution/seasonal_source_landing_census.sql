-- Purpose: per data source, how many releases landed rows in each of the three
--          observation planes and how many landed nowhere. The distinct-release
--          pre-aggregation is deliberate: three correlated NOT EXISTS subqueries
--          over the 46M-row signal plane measured 346 s against production, while
--          the anti-join below runs inside the 120 s statement timeout.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: none
WITH signal_landing AS (
    SELECT DISTINCT source_release_id FROM agri.signal_observation
),
forecast_landing AS (
    SELECT DISTINCT source_release_id FROM agri.forecast_observation
),
feature_landing AS (
    SELECT DISTINCT source_release_id FROM agri.normalized_source_feature
)
SELECT
    data_source.key AS source_key,
    count(*) AS release_count,
    count(*) FILTER (WHERE signal_landing.source_release_id IS NOT NULL) AS signal_observation_releases,
    count(*) FILTER (WHERE forecast_landing.source_release_id IS NOT NULL) AS forecast_observation_releases,
    count(*) FILTER (WHERE feature_landing.source_release_id IS NOT NULL) AS normalized_feature_releases,
    count(*) FILTER (
        WHERE signal_landing.source_release_id IS NULL
          AND forecast_landing.source_release_id IS NULL
          AND feature_landing.source_release_id IS NULL
    ) AS zero_landing_releases
FROM agri.source_release AS release
JOIN agri.data_source AS data_source
    ON data_source.id = release.data_source_id
LEFT JOIN signal_landing ON signal_landing.source_release_id = release.id
LEFT JOIN forecast_landing ON forecast_landing.source_release_id = release.id
LEFT JOIN feature_landing ON feature_landing.source_release_id = release.id
GROUP BY data_source.key
ORDER BY data_source.key
