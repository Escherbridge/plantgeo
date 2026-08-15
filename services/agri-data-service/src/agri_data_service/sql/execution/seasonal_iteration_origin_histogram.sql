-- Purpose: count iterations and scored actuals per simulated origin date, which is
--          the axis that decides whether model selection has independent origins.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: none
SELECT
    iteration.method AS method,
    (iteration.cutoff_time AT TIME ZONE 'UTC')::date AS origin_date,
    count(DISTINCT iteration.id) AS iteration_count,
    count(DISTINCT iteration.series_id) AS series_count,
    count(actual.id) AS scored_actual_count,
    max(actual.data_available_at) AS latest_actual_available_at
FROM agri.forecast_iteration AS iteration
LEFT JOIN agri.forecast_iteration_value AS value
    ON value.iteration_id = iteration.id
LEFT JOIN agri.forecast_iteration_actual AS actual
    ON actual.iteration_value_id = value.id
GROUP BY
    iteration.method,
    (iteration.cutoff_time AT TIME ZONE 'UTC')::date
ORDER BY
    iteration.method,
    (iteration.cutoff_time AT TIME ZONE 'UTC')::date
