-- insert_job_run
-- Purpose: create one logical run of a lane if it does not exist yet, and say nothing at all if it
--          already does, so opening a run is safe to repeat.
-- Loaded by: agri_data_service.jobs.worker
-- Params: job_definition_id (uuid) -- the lane this run belongs to.
--         logical_run_key (text) -- the run-level idempotency key; UNIQUE on the table.
--         scheduled_for (timestamptz, nullable) -- when the run was meant to start; NULL means now.
--         requested_by (text, nullable) -- who asked for it, for the audit trail.
--         target_partitions (text holding JSON) -- what the run is meant to cover, as canonical JSON.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row holding the new run's id when THIS call created it, and NO rows when
-- the run already existed. Zero rows is not a failure -- it is the answer "somebody else already
-- opened this run", and the caller responds by reading the existing row with select_job_run.
--
-- WHY DO NOTHING PLUS A FOLLOW-UP SELECT, and not something cleverer. logical_run_key is UNIQUE and
-- is the run-level idempotency key. This two-step is the only shape that stays correct when two
-- schedulers race: the loser gets zero rows back and reads the winner's row, instead of minting a
-- duplicate run for the same logical campaign.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_run (...) VALUES (...)
--     The optimistic path -- assume this run is new.
--
--   COALESCE(CAST(scheduled_for AS timestamptz), now())
--     COALESCE returns its first non-NULL argument, so a caller that names no scheduled time gets
--     the current moment. The CAST exists purely to pin the parameter's type: a bare bound
--     parameter inside COALESCE gives PostgreSQL nothing to infer from.
--
--   'queued' as the status literal
--     Every run starts queued. The rollup statement moves it to 'running' and then to a terminal
--     status as its shards settle.
--
--   CAST(target_partitions AS jsonb)
--     The column is jsonb, PostgreSQL's parsed JSON type, and the value arrives as text. The CAST
--     pins the parameter's type and parses it, so malformed JSON is refused rather than stored.
--
--   ON CONFLICT (logical_run_key) DO NOTHING
--     If a row with this key already exists, do not raise and do not overwrite -- simply skip the
--     insert. DO NOTHING rather than DO UPDATE is the point: a second caller must not be able to
--     rewrite a live run's scheduling metadata. And because a skipped insert produces no row, the
--     RETURNING below is empty, which is how the caller detects the case at all.
--
--   RETURNING id
--     RETURNING hands back the database-generated id in the same round trip, for the rows the
--     statement actually inserted. Here that is exactly one row on a fresh run and zero rows on a
--     repeat, which makes this single value carry both the id and the created/existed verdict.
INSERT INTO agri.job_run (
    job_definition_id, logical_run_key, scheduled_for, status, requested_by, target_partitions
)
VALUES (
    :job_definition_id,
    :logical_run_key,
    COALESCE(CAST(:scheduled_for AS timestamptz), now()),
    'queued',
    :requested_by,
    CAST(:target_partitions AS jsonb)
)
ON CONFLICT (logical_run_key) DO NOTHING
RETURNING id
