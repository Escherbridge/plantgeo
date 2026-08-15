-- Purpose: append one per-origin metric row under a candidate evaluation receipt.
-- Loaded by: agri_data_service.execution.seasonal_lineage_persist
-- Params: evaluation_id (uuid), origin_cutoff_time (timestamptz), fold_kind (text),
--         scored_step_count (int), mean_absolute_error/root_mean_squared_error/bias (float8),
--         interval_coverage_fraction/skill_versus_persistence (float8, nullable),
--         origin_checksum (hex64 text)
INSERT INTO agri.forecast_candidate_evaluation_origin (
    evaluation_id,
    origin_cutoff_time,
    fold_kind,
    scored_step_count,
    mean_absolute_error,
    root_mean_squared_error,
    bias,
    interval_coverage_fraction,
    skill_versus_persistence,
    origin_checksum
) VALUES (
    CAST(:evaluation_id AS uuid),
    :origin_cutoff_time,
    :fold_kind,
    :scored_step_count,
    :mean_absolute_error,
    :root_mean_squared_error,
    :bias,
    :interval_coverage_fraction,
    :skill_versus_persistence,
    :origin_checksum
)
RETURNING id
