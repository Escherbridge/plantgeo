-- insert_job_work_items
-- Purpose: fan a run out into its shards -- any number of them, in ONE statement -- skipping any
--          shard the run already holds, so re-planning a run is a no-op rather than a duplication.
-- Loaded by: agri_data_service.jobs.worker
-- Params: job_run_id (uuid) -- the run these shards belong to.
--         max_attempts (int) -- the failure budget every new shard inherits from the lane.
--         items (text holding a JSON array) -- the whole fan-out as one JSON document, each element
--         carrying shard_key, kind, payload, priority and available_at.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per shard actually created. Shards that already existed produce no
-- row, so the count of rows is exactly "how many windows this call added", which is what the caller
-- reports.
--
-- ONE STATEMENT FOR ANY NUMBER OF SHARDS, and the reason is concrete. A per-shard INSERT would be
-- one network round trip each against the Railway proxy, and the ingest session opens exactly ONE
-- connection, so a thousand-shard fan-out would be a thousand serial round trips.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_work_item (...) SELECT ...
--     An INSERT fed by a SELECT rather than by a VALUES list. That is what lets the number of rows
--     be data instead of being baked into the statement text -- the same SQL serves a one-shard
--     fan-out and a ten-thousand-shard one.
--
--   FROM jsonb_to_recordset(CAST(items AS jsonb))
--        AS item(shard_key text, kind text, payload text, priority integer, available_at timestamptz)
--     jsonb_to_recordset takes a JSON ARRAY OF OBJECTS and expands it into a table -- one row per
--     element, one column per named field. The column list after AS is mandatory: JSON carries no
--     schema, so PostgreSQL has to be told what shape to read each object as, and it also does the
--     type conversion (the timestamps and integers arrive as JSON text and land as real
--     timestamptz and integer values). The CAST to jsonb pins the parameter's type and parses the
--     document, so a malformed fan-out is refused up front.
--
--   SELECT job_run_id, item.shard_key, ...
--     Mixes one constant per row -- the run id and the attempt budget, the same for every shard --
--     with the per-shard columns coming out of the expanded JSON.
--
--   CAST(item.payload AS jsonb)
--     Each shard's payload is nested JSON that was rendered to a string when the outer array was
--     built, so it is read back as text and re-parsed here into the jsonb column.
--
--   the availability fallback, written out twice in the SELECT below
--     This clause heading paraphrases that expression rather than quoting it. The whole file is the
--     statement text -- comments included -- and a unit test counts how many times that exact
--     expression appears in this statement, expecting exactly the two occurrences in the SELECT.
--     Quoting it here would make the count three and fail the test. Describe it; do not paste it.
--     COALESCE returns its first non-NULL argument, so a shard that named no availability time is
--     available immediately. The same expression feeds two columns, which is deliberate:
--     available_at is the shard's earliest legal start, and next_attempt_at is seeded to match.
--     That seeding is a CONVENIENCE, NOT a correctness requirement -- claim_work_item spells the
--     unset case out as "next_attempt_at IS NULL OR next_attempt_at <= now()", so an unseeded row
--     is claimable either way. It matters only to a query written WITHOUT that disjunction, such as
--     an operator's ad-hoc completeness check or a future claim variant, where a NULL would make
--     the comparison UNKNOWN and hide the row.
--     Nothing here rides on an index, either: ix_job_work_item_claim is
--     (status, next_attempt_at, available_at, priority), which leads with neither job_run_id (the
--     claim filters on it) nor priority descending (the claim sorts by it), so the claim is a
--     filter-and-sort at any seeding. Harmless at this scale; do not quote it as load-bearing.
--
--   ON CONFLICT (job_run_id, shard_key) DO NOTHING
--     The idempotency. (run, shard_key) is unique, so a shard already planned for this run simply
--     does not get inserted again -- no error, no overwrite, no duplicate window. DO NOTHING rather
--     than DO UPDATE because an existing shard may be mid-flight, and its live state must never be
--     reset by a re-plan.
--
--   RETURNING id
--     RETURNING hands back one row per row the statement actually inserted, in the same round trip.
--     Skipped conflicts return nothing, which is precisely what makes the row count equal to the
--     number of newly added shards.
INSERT INTO agri.job_work_item (
    job_run_id, shard_key, kind, payload, priority, available_at, next_attempt_at, max_attempts
)
SELECT :job_run_id,
       item.shard_key,
       item.kind,
       CAST(item.payload AS jsonb),
       item.priority,
       COALESCE(item.available_at, now()),
       COALESCE(item.available_at, now()),
       :max_attempts
FROM jsonb_to_recordset(CAST(:items AS jsonb))
     AS item(shard_key text, kind text, payload text, priority integer, available_at timestamptz)
ON CONFLICT (job_run_id, shard_key) DO NOTHING
RETURNING id
