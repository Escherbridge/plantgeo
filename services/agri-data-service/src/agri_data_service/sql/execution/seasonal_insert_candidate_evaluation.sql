-- Purpose: append one evaluation-only candidate receipt, letting the database compute the receipt
--          digest from its own pinned function so the stored digest is reproducible.
-- Loaded by: agri_data_service.execution.seasonal_lineage_persist
-- Params: evaluation_key/series_key/candidate_family/candidate_version/decision/decision_reason
--         (text), hyperparameters/metrics (jsonb), simulation_seed (bigint),
--         export_manifest_checksum (hex64 text), horizon_steps/development_origin_count/
--         final_holdout_origin_count (int)
INSERT INTO agri.forecast_candidate_evaluation (
    evaluation_key,
    series_key,
    candidate_family,
    candidate_version,
    hyperparameters,
    simulation_seed,
    export_manifest_checksum,
    horizon_steps,
    development_origin_count,
    final_holdout_origin_count,
    metrics,
    decision,
    decision_reason,
    receipt_checksum
) VALUES (
    :evaluation_key,
    :series_key,
    :candidate_family,
    :candidate_version,
    CAST(:hyperparameters AS jsonb),
    :simulation_seed,
    :export_manifest_checksum,
    :horizon_steps,
    :development_origin_count,
    :final_holdout_origin_count,
    CAST(:metrics AS jsonb),
    :decision,
    :decision_reason,
    agri.forecast_candidate_evaluation_receipt_checksum(
        :evaluation_key,
        :series_key,
        :candidate_family,
        :candidate_version,
        CAST(:hyperparameters AS jsonb),
        :simulation_seed,
        :export_manifest_checksum,
        :horizon_steps,
        :development_origin_count,
        :final_holdout_origin_count,
        :decision
    )
)
RETURNING id, receipt_checksum
