-- reconcile_mark_windows_succeeded
-- Purpose: settle a batch of backfill windows that reconciliation has PROVED are already covered by
--          the data -- moving each to `succeeded`, closing its lease fields, and stamping its payload
--          with the evidence that settled it. Returns the shard keys it actually settled.
-- Loaded by: agri_data_service.ingest.reconcile
-- Params: marker_key (text) -- the payload key the reconciliation evidence is filed under,
--         marked (text holding JSON) -- an array of objects, each with a `shard_key` and a `marker`;
--         one entry per window to settle, so the whole batch is one statement and one transaction,
--         job_run_id (text holding a UUID) -- the run whose windows are being settled.
--
-- NAMING NOTE: this file is named after its Python constant `_MARK_WINDOWS_RECONCILED`, per the
-- binding rule in sql/AGENTS.md, while the marker on the first line keeps the `reconcile_` prefix and
-- the `_succeeded` spelling because tests/test_ingest_reconcile.py hard-codes the marker strings. The
-- two are allowed to differ; the marker is a wire protocol, the filename is a naming convention.
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
-- What this returns: one row per window this statement actually settled, carrying just its shard key.
-- Fewer rows than were asked for is a normal, meaningful answer, not an error -- see the race note
-- below. The caller reports what came back, never what it sent.
--
-- WHY IT WRITES EXACTLY THESE COLUMNS: this is the landing that `complete_work_item` produces, minus
-- the attempt -- because there was no attempt. Every column touched here is one `_COMPLETE_WORK_ITEM`
-- touches, for the same constraint reasons. `completed_at` is set because the check constraint
-- ck_job_work_item_terminal_item_has_completion_time is IMMEDIATE, so a terminal status without a
-- completion time aborts the statement on the spot rather than at commit. The two lease columns move
-- together because ck_job_work_item_complete_lease_pair moves both or neither. And `attempt_count` is
-- deliberately left where it was -- 0, for a window nothing ever claimed -- because inventing an
-- attempt count would be the same fabrication as inventing an attempt row.
--
-- WHY THE STATUS FILTER IS REPEATED HERE AND NOT ONLY IN PYTHON: between the read that decided a
-- window was covered and this write, a cron tick can claim that very window. The predicate is what
-- makes that race a no-op instead of a terminal status written behind a live lease. A shard that moved
-- is simply not in the result set, and the caller sees it did not settle.
--
-- How this query works, clause by clause:
--
--   UPDATE agri.job_work_item AS item ... FROM jsonb_to_recordset(...) AS marked(...)
--     A write, not a read: it changes rows in place. The FROM clause of an UPDATE is a join source --
--     each target row is paired with a matching row from `marked`, and the SET expressions may then
--     refer to the matched row's columns. This is what lets one statement settle a whole batch with
--     per-row values instead of issuing one statement per window.
--
--   jsonb_to_recordset(CAST(<the marked parameter> AS jsonb)) AS marked(shard_key text, marker text)
--     Expands one JSON array into a table of rows -- the JSON equivalent of `unnest`, which does the
--     same for a PostgreSQL array. Each object in the array becomes one row. Because JSON carries no
--     column types, the shape has to be declared in the alias, which is what
--     `AS marked(shard_key text, marker text)` is: it names the two columns and their types. Fields
--     the alias does not name are ignored.
--
--   CAST(<parameter> AS jsonb)
--     A cast that exists purely to pin a bound parameter's type. The parameter arrives as text, and
--     jsonb_to_recordset accepts `jsonb`; without being told, PostgreSQL has nothing to resolve the
--     parameter's type from. `jsonb` is PostgreSQL's parsed binary JSON type. The value is unchanged;
--     only its declared type is fixed.
--
--   payload = item.payload || jsonb_build_object(<key>, <value>)
--     `jsonb_build_object` builds a small JSON object from alternating key and value arguments. The
--     `||` operator concatenates two JSON objects, with the right-hand side winning on any key they
--     share -- so this adds one key to the existing payload, replacing it if it was already there, and
--     leaves every other key untouched. Re-running the statement therefore rewrites the same key
--     rather than accumulating copies.
--
--   CAST(marked.marker AS jsonb) || jsonb_build_object('reconciled_at', now())
--     The evidence itself, with the settling time added to it. `now()` is the moment the transaction
--     began, so every window settled in one batch carries the same timestamp.
--
--   WHERE item.job_run_id = CAST(<parameter> AS uuid)
--     Scoped to one run. The CAST again pins a text parameter against a `uuid` column, which
--     PostgreSQL will not compare without being told which type to resolve to.
--
--   AND item.shard_key = marked.shard_key
--     The join condition proper -- it is what pairs each target row with its entry in the batch.
--     Without it every target row would match every batch row.
--
--   AND item.status IN ('queued', 'retry_wait', 'deferred') AND item.lease_owner IS NULL
--     The race guard described above. `IN (...)` is shorthand for a chain of OR-ed equality tests.
--     `IS NULL` rather than `= NULL` because comparing anything to NULL in SQL yields NULL -- neither
--     true nor false -- so `IS` is the only test that works. Together they mean "only settle a window
--     that is still parked and unclaimed".
--
--   RETURNING item.shard_key AS shard_key
--     RETURNING makes a write also behave like a read, handing back a row for each row it actually
--     changed. It is the only honest way to learn what this statement did: the count the caller
--     intended and the set the database settled can differ, and RETURNING reports the second.
UPDATE agri.job_work_item AS item
SET status = 'succeeded',
    completed_at = now(),
    progress_fraction = 1,
    next_attempt_at = NULL,
    lease_owner = NULL,
    lease_expires_at = NULL,
    payload = item.payload || jsonb_build_object(
        -- The cast is load-bearing, not decoration. `jsonb_build_object` is `variadic "any"`, so an
        -- untyped bind in the key position gives PostgreSQL nothing to resolve and it refuses the whole
        -- statement with `IndeterminateDatatypeError: could not determine data type of parameter $1`.
        -- Measured 2026-08-07 against production: this failed on the first `--apply` while the dry run,
        -- which opens no write transaction, passed. It cannot be caught by the unit tests either -- they
        -- answer `AsyncSession.execute` from a recording stub, so no bind ever reaches a type resolver.
        -- psycopg2 hides it as well, because it substitutes literals client-side; only asyncpg, which
        -- sends a real parameter, tells the truth. See jobs/AGENTS.md on real-database coverage.
        CAST(:marker_key AS text),
        CAST(marked.marker AS jsonb) || jsonb_build_object('reconciled_at', now())
    )
FROM jsonb_to_recordset(CAST(:marked AS jsonb)) AS marked(shard_key text, marker text)
WHERE item.job_run_id = CAST(:job_run_id AS uuid)
  AND item.shard_key = marked.shard_key
  AND item.status IN ('queued', 'retry_wait', 'deferred')
  AND item.lease_owner IS NULL
RETURNING item.shard_key AS shard_key
