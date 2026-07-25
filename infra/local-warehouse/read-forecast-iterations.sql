\set ON_ERROR_STOP on
\pset pager off

\if :{?iteration_pattern}
\else
\set iteration_pattern '%'
\endif

BEGIN TRANSACTION READ ONLY;

\echo 'Registered time-series contract'
SELECT
    contract.series_id,
    contract.series_key,
    contract.data_source_key,
    contract.license_name,
    contract.entity_type,
    contract.entity_key,
    contract.metric_name,
    contract.metric_unit,
    contract.spatial_support_kind,
    contract.source_spatial_resolution_m,
    contract.output_spatial_resolution_m,
    contract.source_temporal_support,
    contract.desired_temporal_grain,
    contract.contract_version,
    contract.contract_checksum
FROM agri.v_forecast_timeseries_contract AS contract
ORDER BY contract.series_key;

\echo 'Forecast iterations'
SELECT
    iteration.id,
    iteration.iteration_key,
    iteration.purpose,
    iteration.availability_mode,
    iteration.method,
    iteration.status,
    iteration.as_of_time,
    iteration.cutoff_time,
    iteration.history_start,
    iteration.horizon_days,
    iteration.simulation_count,
    iteration.simulation_seed,
    iteration.gap_policy,
    iteration.training_day_count,
    iteration.increment_count,
    iteration.input_release_checksum,
    iteration.contract_checksum,
    iteration.history_checksum,
    iteration.parameter_checksum,
    iteration.receipt_checksum,
    iteration.recorded_at,
    count(value.id) AS value_count,
    count(actual.id) AS actual_count
FROM agri.forecast_iteration AS iteration
JOIN agri.forecast_iteration_value AS value ON value.iteration_id = iteration.id
LEFT JOIN agri.forecast_iteration_actual AS actual
    ON actual.iteration_value_id = value.id
WHERE iteration.iteration_key LIKE :'iteration_pattern'
GROUP BY iteration.id
ORDER BY iteration.recorded_at DESC;

\echo 'Low, median, high, actual, and outcome signals'
SELECT
    outcome.iteration_key,
    outcome.valid_time,
    outcome.horizon_step,
    outcome.low_value,
    outcome.median_value,
    outcome.high_value,
    outcome.actual_value,
    outcome.actual_release_set_id,
    outcome.actual_input_release_checksum,
    outcome.residual_actual_minus_forecast,
    outcome.absolute_error,
    outcome.interval_covered,
    outcome.forecast_available_at,
    outcome.actual_recorded_at,
    outcome.value_checksum,
    outcome.actual_checksum
FROM agri.v_forecast_iteration_outcome AS outcome
WHERE outcome.iteration_key LIKE :'iteration_pattern'
ORDER BY outcome.iteration_key, outcome.horizon_step;

\echo 'Forecast and actual provenance snapshots'
SELECT
    iteration.iteration_key,
    iteration.contract_snapshot,
    iteration.input_license_snapshots
FROM agri.forecast_iteration AS iteration
WHERE iteration.iteration_key LIKE :'iteration_pattern'
ORDER BY iteration.iteration_key;

SELECT
    iteration.iteration_key,
    value.horizon_step,
    actual.actual_digest_version,
    actual.actual_release_set_id,
    actual.actual_input_release_checksum,
    actual.actual_checksum,
    input.source_release_id,
    input.observation_checksum,
    input.license_snapshot
FROM agri.forecast_iteration AS iteration
JOIN agri.forecast_iteration_value AS value
    ON value.iteration_id = iteration.id
JOIN agri.forecast_iteration_actual AS actual
    ON actual.iteration_value_id = value.id
JOIN agri.forecast_iteration_actual_input AS input
    ON input.actual_id = actual.id
WHERE iteration.iteration_key LIKE :'iteration_pattern'
ORDER BY iteration.iteration_key, value.horizon_step, input.source_release_id;

\echo 'Aggregate evaluation metrics'
SELECT
    outcome.iteration_key,
    count(*) AS forecast_value_count,
    count(outcome.actual_checksum) AS actual_count,
    avg(outcome.absolute_error) AS mean_absolute_error,
    sqrt(avg(outcome.squared_error)) AS root_mean_squared_error,
    avg(
        CASE
            WHEN outcome.interval_covered THEN 1.0
            WHEN outcome.interval_covered IS FALSE THEN 0.0
        END
    ) AS low_high_interval_coverage
FROM agri.v_forecast_iteration_outcome AS outcome
WHERE outcome.iteration_key LIKE :'iteration_pattern'
GROUP BY outcome.iteration_key
ORDER BY outcome.iteration_key;

\echo 'Operational publication plane remains separate'
SELECT
    (SELECT count(*) FROM agri.forecast_receipt) AS operational_receipts,
    (SELECT count(*) FROM agri.forecast_value) AS operational_values,
    (SELECT count(*) FROM agri.forecast_publication) AS publications,
    (
        SELECT count(*)
        FROM agri.publication_pointer
        WHERE product = 'forecast_series'
    ) AS forecast_publication_pointers;

COMMIT;
