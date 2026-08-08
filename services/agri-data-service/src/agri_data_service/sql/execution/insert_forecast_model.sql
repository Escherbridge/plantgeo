-- Purpose: register the identity of one fitted covariate wind ridge -- its algorithm, its
--          hyperparameters and the artifact holding its coefficients.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: model_key (text), model_version (text), algorithm (text),
--         model_code_checksum (text), artifact_id (uuid), metadata_json (jsonb as text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- model_kind is pinned to 'ml' and model_purpose to 'metric_forecast', and both are load
-- bearing rather than descriptive:
--   - agri.validate_forecast_training_run raises "only ML models have local training runs"
--     for any other kind, and a CHECK constraint requires an 'ml' model to carry an artifact;
--   - 'strategy_selection' would send the validator down a completely different branch that
--     demands a validated strategy label release. This model forecasts a metric from
--     covariates and has nothing to do with intervention labels, so it must say so.
--
-- agri.forecast_model rows are IMMUTABLE: a BEFORE UPDATE OR DELETE trigger,
-- agri.guard_forecast_immutable_rows, raises on any attempt to change one. A revised model is
-- therefore a new row with a new version, never an edit -- which is why model_version is
-- derived from the model's own content digest by the caller.
--
-- How this query works, clause by clause:
--
--   metadata_json column
--     Carries the fit hyperparameters -- ridge penalty, horizon count, calibration length,
--     the covariate schema version and the band quantiles. They live here rather than only in
--     the artifact because they are what a reader needs to compare two models without opening
--     the coefficient blob.
--
--   ON CONFLICT (model_key, model_version) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". Because model_version is derived from the model's content digest, an
--     identical fit resolves to the row that already exists rather than minting a second
--     identity for the same coefficients. Nothing is inserted on that path, so RETURNING
--     yields no row and the caller resolves the existing id with a one-line lookup.
INSERT INTO agri.forecast_model (
    model_key,
    model_version,
    model_kind,
    model_purpose,
    algorithm,
    model_code_checksum,
    artifact_id,
    metadata_json
)
VALUES (
    CAST(:model_key AS varchar),
    CAST(:model_version AS varchar),
    'ml',
    'metric_forecast',
    CAST(:algorithm AS varchar),
    CAST(:model_code_checksum AS varchar),
    CAST(:artifact_id AS uuid),
    CAST(:metadata_json AS jsonb)
)
ON CONFLICT (model_key, model_version) DO NOTHING
RETURNING id
