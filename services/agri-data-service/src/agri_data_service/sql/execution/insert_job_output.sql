-- Purpose: record one validated output of a local training job -- either the fitted model or
--          the backtest metric bundle -- as the lineage row the forecast receipts point at.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: job_run_id (uuid), artifact_id (uuid, nullable), output_key (text), kind (text),
--         checksum_sha256 (text), row_count (bigint), metadata_json (jsonb as text),
--         validated_at (timestamptz)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- One file, two call sites, on purpose: the model output and the backtest output differ only
-- in their parameters (the backtest one binds no artifact and passes NULL). Two files whose
-- text was identical would be two things to keep in step for no gain.
--
-- What agri.validate_forecast_training_run checks about the MODEL output, so that the values
-- bound here are not arbitrary:
--   - state must be 'validated' or 'published';
--   - artifact_id must equal the forecast model's artifact;
--   - checksum_sha256 must equal the model checksum passed to the validator;
--   - metadata_json must carry a 'validation_checksum' key equal to the validation checksum
--     passed to the validator;
--   - row_count must be exactly 1.
-- The caller therefore derives all of those from the same digests it hands the validator,
-- rather than writing a plausible-looking value here and hoping.
--
-- How this query works, clause by clause:
--
--   state = 'validated'
--     Written directly rather than staged-then-promoted because the forecast writer role
--     holds INSERT on job_output and no UPDATE; there is no second statement available to
--     promote it. The row is written after the work it describes has already succeeded, so
--     the state is a report, not a prediction.
--
--   CAST(... AS uuid) on artifact_id
--     A NULL parameter still needs a type. Without the cast, PostgreSQL would be handed an
--     untyped NULL for a uuid column and refuse to infer it.
--
--   ON CONFLICT (job_run_id, output_key) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". The output key is derived from the pinned run identity, so a durable lane
--     re-claiming a shard whose work already committed re-derives the same key and this
--     statement becomes a no-op instead of raising. Nothing is inserted on that path, so
--     RETURNING yields no row and the caller resolves the existing id with a one-line lookup.
INSERT INTO agri.job_output (
    job_run_id,
    artifact_id,
    output_key,
    kind,
    state,
    checksum_sha256,
    row_count,
    metadata_json,
    validated_at
)
VALUES (
    CAST(:job_run_id AS uuid),
    CAST(:artifact_id AS uuid),
    CAST(:output_key AS varchar),
    CAST(:kind AS varchar),
    'validated',
    CAST(:checksum_sha256 AS varchar),
    CAST(:row_count AS bigint),
    CAST(:metadata_json AS jsonb),
    CAST(:validated_at AS timestamptz)
)
ON CONFLICT (job_run_id, output_key) DO NOTHING
RETURNING id
