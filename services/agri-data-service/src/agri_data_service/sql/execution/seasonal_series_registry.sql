-- Purpose: how many forecast series are registered per input adapter and metric. The track's spec
--          assumes a registered Boise WS2M metric series; this read is what says whether one exists
--          in the warehouse being evaluated rather than assuming the spec is current.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: none
SELECT
    series.input_adapter AS input_adapter,
    series.metric_name AS metric_name,
    coalesce(series.signal_name, '(none)') AS signal_name,
    count(*) AS series_count,
    count(DISTINCT series.spatial_cell_id) AS cell_count
FROM agri.forecast_series AS series
GROUP BY
    series.input_adapter,
    series.metric_name,
    coalesce(series.signal_name, '(none)')
ORDER BY
    series.input_adapter,
    series.metric_name,
    coalesce(series.signal_name, '(none)')
