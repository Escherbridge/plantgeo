-- Purpose: record the local training job that produced one covariate wind training receipt,
--          pinned to the governed release set whose inputs it was allowed to read.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: job_definition_id (uuid), release_set_id (uuid), logical_run_key (text),
--         scheduled_for (timestamptz), started_at (timestamptz), completed_at (timestamptz),
--         requested_by (text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Why the row is inserted already 'succeeded': this statement runs AFTER the fit and the
-- scoring have completed, in the same transaction that writes the receipt, so "succeeded" is
-- a statement of fact rather than an optimistic default. It is also the only shape the
-- least-privilege forecast writer role can produce -- that role holds INSERT on job_run and
-- no UPDATE at all, so a 'running' row could never be moved forward by the same connection.
-- agri.validate_forecast_training_run refuses a receipt whose job did not succeed and whose
-- completed_at is NULL, so both travel in this one INSERT.
--
-- How this query works, clause by clause:
--
--   total_work_items / succeeded_work_items
--     One unit of work -- this training run -- and it succeeded. The table carries a CHECK
--     that succeeded + failed may never exceed total, so these two move together with total
--     in a single statement rather than being adjusted afterwards.
--
--   ON CONFLICT (logical_run_key) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". logical_run_key is derived from the pinned run identity, so a durable lane
--     that re-claims a shard whose work already committed re-derives the SAME key and this
--     statement becomes a no-op instead of raising. DO NOTHING rather than DO UPDATE because
--     a job run that already recorded its outcome is history, not a row to revise.
--
--   RETURNING id
--     Yields the inserted row's key -- and yields NO row when the conflict path was taken,
--     because nothing was inserted. The caller resolves the pre-existing id with its own
--     one-line lookup.
INSERT INTO agri.job_run (
    job_definition_id,
    release_set_id,
    logical_run_key,
    scheduled_for,
    status,
    total_work_items,
    succeeded_work_items,
    failed_work_items,
    started_at,
    completed_at,
    requested_by
)
VALUES (
    CAST(:job_definition_id AS uuid),
    CAST(:release_set_id AS uuid),
    CAST(:logical_run_key AS varchar),
    CAST(:scheduled_for AS timestamptz),
    'succeeded',
    1,
    1,
    0,
    CAST(:started_at AS timestamptz),
    CAST(:completed_at AS timestamptz),
    CAST(:requested_by AS varchar)
)
ON CONFLICT (logical_run_key) DO NOTHING
RETURNING id
