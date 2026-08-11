-- reconcile_reopen_gap_windows
-- Purpose: reopen a batch of ALREADY-SUCCEEDED backfill windows whose days the layer does not in fact
--          serve -- moving each back to `queued`, granting it a fresh attempt budget, and stamping its
--          payload with the measurement that reopened it. The exact inverse of
--          ingest/mark_windows_reconciled.sql. Returns the shard keys it actually reopened.
-- Loaded by: agri_data_service.ingest.reconcile
-- Params: marker_key (text) -- the payload key the reopen evidence is filed under,
--         generation_key (text) -- the payload key holding the walk generation counter,
--         attempt_budget (int) -- a fresh number of attempts to grant on top of what this shard has
--         already spent, taken from the lane's own declared max_attempts,
--         reopened (text holding JSON) -- an array of objects, each with a `shard_key` and a `marker`;
--         one entry per window to reopen, so the whole batch is one statement and one transaction,
--         job_run_id (text holding a UUID) -- the run whose windows are being reopened.
--
-- The first line above is a dispatch marker, not documentation. The unit tests answer
-- AsyncSession.execute from a recording stub and route each statement by the first single-word comment
-- line in it, so that line must stay first and stay spelled exactly as it is. No other comment line in
-- this file may be a bare `--` followed by one word, or it would become a rival marker. See "Marker
-- protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies.
--
-- What this returns: one row per window this statement actually reopened, carrying just its shard key.
-- Fewer rows than were asked for is a normal, meaningful answer, not an error -- a shard a cron tick
-- claimed between the read and this write simply is not in the result set. The caller reports what came
-- back, never what it sent.
--
-- WHY ONLY `succeeded` IS REOPENED, and why that list is not longer:
--   queued / retry_wait / deferred  already open. There is nothing to reopen; planning them again would
--                                   only reset progress a live walk is making.
--   leased / running                a live worker holds the fence. Writing a status behind a live lease
--                                   is precisely the corruption the fencing token exists to prevent.
--   dead_letter                     EVIDENCE. A dead letter is the durable record that every attempt
--                                   failed on that window. Flipping it back to queued because its days
--                                   are missing erases the one row that says so; the caller reports it
--                                   instead, and a person requeues it deliberately.
--   cancelled                       an operator's decision, for reasons this statement cannot see.
--                                   Reported, never overridden.
--
-- WHY `attempt_count` IS NOT RESET, AND `max_attempts` IS RAISED INSTEAD. The claim refuses a shard
-- whose attempt_count has reached max_attempts, so a reopened shard that had spent its budget would sit
-- in `queued` forever, unclaimable -- a hole wearing the word "reopened". The obvious fix, zeroing
-- attempt_count, is wrong for a documented reason: the next claim derives its attempt NUMBER from that
-- counter and `uq_job_attempt_item_number` is UNIQUE (work item, attempt number), so a reset makes the
-- next claim collide with an attempt row that already exists and the shard can never run again. Raising
-- the ceiling is the same primitive a deferral already uses (see jobs/AGENTS.md, "A deferral must not
-- spend the retry budget"), it keeps attempt numbers dense-unique, and GREATEST keeps it monotone so a
-- shard that has deferred its way to a high ceiling is never quietly lowered.
--
-- WHY THE WALK GENERATION IS BUMPED, AND WHAT BREAKS WITHOUT IT. Resume position is read from the
-- newest `agri.job_checkpoint` row for the work item, and a window that COMPLETED left its last
-- checkpoint pointing at its final chunk. Reopened without a generation bump, a five-day FIRMS window
-- would resume at day five, walk one chunk, and report itself complete again -- re-succeeding over the
-- four days that were missing. The handler (ingest/archive_walk.py) therefore discards any cursor whose
-- generation does not match the payload's, and the reopen is what moves the payload's. Appending a
-- corrective checkpoint instead is not available here: `fk_job_checkpoint_attempt_fence` requires a real
-- `job_attempt` row behind every checkpoint, and this process performed no attempt.
--
-- What is deliberately NOT touched: `attempt_count` (above), `fencing_token` (monotonicity is the whole
-- mechanism -- see jobs/AGENTS.md), `checkpoint_sequence` (the next checkpoint must not collide with a
-- stored one under uq_job_checkpoint_item_sequence), `started_at` (when this shard first did anything,
-- stamped once), and `last_error_class`/`last_error_summary` (the record of how it failed before, which
-- is history rather than current state).
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_work_item AS item ... FROM jsonb_to_recordset(...) AS reopened(...)
--     A write, not a read: it changes rows in place. The FROM clause of an UPDATE is a join source --
--     each target row is paired with a matching row from `reopened`, and the SET expressions may then
--     refer to the matched row's columns. This is what lets one statement reopen a whole batch with
--     per-row evidence instead of issuing one statement per window.
--
--   jsonb_to_recordset(CAST(<the reopened parameter> AS jsonb)) AS reopened(shard_key text, marker text)
--     Expands one JSON array into a table of rows -- the JSON equivalent of `unnest`, which does the
--     same for a PostgreSQL array. Each object in the array becomes one row. Because JSON carries no
--     column types, the shape has to be declared in the alias, which is what
--     `AS reopened(shard_key text, marker text)` is: it names the two columns and their types. Fields
--     the alias does not name are ignored.
--
--   CAST(<parameter> AS jsonb) / CAST(<parameter> AS text) / CAST(<parameter> AS integer)
--     Casts that exist purely to pin a bound parameter's type. A parameter arrives untyped, and
--     PostgreSQL refuses a statement it cannot resolve one for -- `jsonb_build_object` is
--     `variadic "any"`, so an untyped bind in a key position fails outright with "could not determine
--     data type of parameter". The value is unchanged; only its declared type is fixed.
--
--   completed_at = NULL, progress_fraction = 0
--     A queued window has not completed and has walked none of its chunks. Leaving a completion time on
--     a queued row would make every operator query that reads "completed" disagree with the status
--     beside it. The fact that it HAD completed is not lost -- it is written into the payload marker
--     below, and every `job_attempt` row it produced is untouched.
--
--   lease_owner = NULL, lease_expires_at = NULL
--     Already NULL by the predicate below; assigned anyway because
--     ck_job_work_item_complete_lease_pair moves both or neither, and an explicit pair cannot be half
--     satisfied by a future edit to this statement.
--
--   max_attempts = GREATEST(item.max_attempts, item.attempt_count + <the attempt budget>)
--     GREATEST returns the largest of its arguments. See the attempt-budget note above for why this is
--     a raise rather than a reset.
--
--   payload = item.payload || jsonb_build_object(<marker key>, <evidence>)
--                          || jsonb_build_object(<generation key>, <previous generation> + 1)
--     `jsonb_build_object` builds a small JSON object from alternating key and value arguments. The
--     `||` operator concatenates two JSON objects, with the right-hand side winning on any key they
--     share -- so each of these adds one key to the existing payload, replacing it if it was already
--     there, and leaves every other key untouched. Re-running the statement therefore rewrites the same
--     two keys rather than accumulating copies. `ArchiveWindowRequest.from_payload` reads only the keys
--     it names and ignores the rest, so both are inert to the walk itself.
--
--   COALESCE(CASE WHEN jsonb_typeof(...) = 'number' THEN CAST(... AS integer) END, 0) + 1
--     The generation counter, read defensively. `jsonb_typeof` names a JSON value's type; a key that is
--     absent, or present as something other than a number, yields NULL from the CASE and COALESCE turns
--     that into 0, so the first reopen of a window planned before this key existed lands on generation
--     1. Casting a non-numeric JSON value straight to integer would abort the whole batch instead.
--
--   CAST(reopened.marker AS jsonb) || jsonb_build_object('reopened_at', now())
--     The evidence itself, with the reopening time added to it. `now()` is the moment the transaction
--     began, so every window reopened in one batch carries the same timestamp.
--
--   WHERE item.job_run_id = CAST(<parameter> AS uuid)
--     Scoped to one run. The CAST again pins a text parameter against a `uuid` column, which PostgreSQL
--     will not compare without being told which type to resolve to.
--
--   AND item.shard_key = reopened.shard_key
--     The join condition proper -- it is what pairs each target row with its entry in the batch.
--     Without it every target row would match every batch row.
--
--   AND item.status = 'succeeded' AND item.lease_owner IS NULL
--     The state gate described above, repeated here rather than trusted to the Python that read it: a
--     cron tick can claim a window between the read and this write. `IS NULL` rather than `= NULL`
--     because comparing anything to NULL in SQL yields NULL -- neither true nor false -- so `IS` is the
--     only test that works.
--
--   RETURNING item.shard_key AS shard_key
--     RETURNING makes a write also behave like a read, handing back a row for each row it actually
--     changed. It is the only honest way to learn what this statement did.
UPDATE agri.job_work_item AS item
SET status = 'queued',
    completed_at = NULL,
    progress_fraction = 0,
    next_attempt_at = NULL,
    lease_owner = NULL,
    lease_expires_at = NULL,
    max_attempts = GREATEST(item.max_attempts, item.attempt_count + CAST(:attempt_budget AS integer)),
    payload = item.payload
        || jsonb_build_object(
            CAST(:marker_key AS text),
            CAST(reopened.marker AS jsonb) || jsonb_build_object('reopened_at', now())
        )
        || jsonb_build_object(
            CAST(:generation_key AS text),
            COALESCE(
                CASE
                    WHEN jsonb_typeof(item.payload -> CAST(:generation_key AS text)) = 'number'
                        THEN CAST(item.payload ->> CAST(:generation_key AS text) AS integer)
                END,
                0
            ) + 1
        )
FROM jsonb_to_recordset(CAST(:reopened AS jsonb)) AS reopened(shard_key text, marker text)
WHERE item.job_run_id = CAST(:job_run_id AS uuid)
  AND item.shard_key = reopened.shard_key
  AND item.status = 'succeeded'
  AND item.lease_owner IS NULL
RETURNING item.shard_key AS shard_key
