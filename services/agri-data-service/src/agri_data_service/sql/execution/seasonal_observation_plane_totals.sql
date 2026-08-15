-- Purpose: count the three observation planes separately. A landing audit that reads
--          only signal_observation reports the Sentinel-2 NDVI lane as dead, because
--          that lane writes forecast_observation instead.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: none
SELECT 'agri.signal_observation' AS plane, count(*) AS row_count
FROM agri.signal_observation
UNION ALL
SELECT 'agri.forecast_observation' AS plane, count(*) AS row_count
FROM agri.forecast_observation
UNION ALL
SELECT 'agri.normalized_source_feature' AS plane, count(*) AS row_count
FROM agri.normalized_source_feature
UNION ALL
SELECT 'agri.signal_coverage_audit' AS plane, count(*) AS row_count
FROM agri.signal_coverage_audit
ORDER BY 1
