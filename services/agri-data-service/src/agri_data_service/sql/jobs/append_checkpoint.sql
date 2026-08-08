-- append_checkpoint
-- Purpose: write the durable resume point for one step of a window -- the cursor, its checksum,
--          and how far through the window the step reached.
-- Loaded by: agri_data_service.jobs.lease
-- Params: work_item_id (uuid) -- the shard this checkpoint belongs to.
--         attempt_id (uuid) -- the attempt that produced it.
--         sequence (int) -- the number reserved a moment ago by advance_checkpoint_sequence.
--         fencing_token (int) -- the token this worker holds, recorded so a later reader can tell
--         which claim wrote this checkpoint.
--         cursor (text holding JSON) -- the handler's opaque resume point, rendered as canonical
--         JSON by the caller.
--         cursor_checksum (text) -- the sha256 of that canonical JSON.
--         progress_fraction (double precision) -- the RAW per-step fraction, not a high-water mark.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row, the new checkpoint's id. The checkpoint table is append-only: rows
-- are inserted and never updated, so a shard's history is the ordered list of its checkpoints and
-- "resume point" simply means the newest of them.
--
-- The fence is NOT repeated here, and does not need to be. advance_checkpoint_sequence ran first
-- in this same transaction and carried the full fence; if it had matched nothing the caller would
-- have abandoned the shard and this INSERT would never be reached.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_checkpoint (...) VALUES (...)
--     A plain insert with no conflict handling, on purpose. uq_job_checkpoint_item_sequence makes
--     (shard, sequence) unique, so a duplicate here would mean the sequence reservation above was
--     bypassed -- a real invariant break that should fail loudly rather than be swallowed.
--
--   CAST(cursor AS jsonb)
--     The cursor arrives as a JSON string and the column is jsonb (PostgreSQL's parsed, indexable
--     JSON type). A bare bound parameter in a VALUES list still needs its type pinned in this
--     codebase's driver setup, and the cast both pins it and parses the text into jsonb. It also
--     means a malformed cursor is rejected by the database rather than stored as unusable text.
--
--   sequence and fencing_token, stored rather than derived
--     sequence orders the checkpoints; fencing_token records which claim produced this one, which
--     is what lets defer_work_item later count "parks since the last real progress" by comparing
--     attempt tokens against checkpoint tokens.
--
--   progress_fraction, stored raw
--     The honest per-step value. Unlike the item's column this one is never clamped upward, so
--     the checkpoint history remains a truthful record of each step even when a step reports zero.
--
--   RETURNING id
--     RETURNING hands back the database-generated id in the same round trip, so no follow-up
--     SELECT is needed to confirm the write landed.
INSERT INTO agri.job_checkpoint (
    job_work_item_id, job_attempt_id, sequence, fencing_token, cursor, cursor_checksum, progress_fraction
)
VALUES (
    :work_item_id,
    :attempt_id,
    :sequence,
    :fencing_token,
    CAST(:cursor AS jsonb),
    :cursor_checksum,
    :progress_fraction
)
RETURNING id
