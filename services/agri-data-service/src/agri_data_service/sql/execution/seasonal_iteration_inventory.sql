-- Purpose: inventory every forecast iteration by method/status/purpose with its
--          origin span and the most recent server-recorded time, so staleness is
--          measured rather than assumed.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: none
SELECT
    iteration.method AS method,
    iteration.status AS status,
    iteration.purpose AS purpose,
    iteration.availability_mode AS availability_mode,
    count(*) AS iteration_count,
    count(DISTINCT iteration.series_id) AS series_count,
    min(iteration.cutoff_time) AS earliest_cutoff_time,
    max(iteration.cutoff_time) AS latest_cutoff_time,
    max(iteration.recorded_at) AS latest_recorded_at,
    max(iteration.horizon_days) AS max_horizon_days
FROM agri.forecast_iteration AS iteration
GROUP BY
    iteration.method,
    iteration.status,
    iteration.purpose,
    iteration.availability_mode
ORDER BY
    iteration.method,
    iteration.status,
    iteration.purpose,
    iteration.availability_mode
