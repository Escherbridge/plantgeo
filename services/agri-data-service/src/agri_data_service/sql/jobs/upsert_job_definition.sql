-- upsert_job_definition
-- Purpose: register one declared lane by (name, version) -- inserting it the first time and
--          bringing it up to date every time after -- and return the row the runtime will execute.
-- Loaded by: agri_data_service.jobs.worker
-- Params: name (text) -- the lane's name, e.g. a backfill lane for one upstream source.
--         version (text) -- the lane's version string; (name, version) together are the identity.
--         handler (text) -- the token that selects the Python handler this lane runs.
--         queue_name (text), schedule (text), schedule_timezone (text), enabled (bool)
--         concurrency_key (text, nullable) -- what this lane must not run concurrently with.
--         max_attempts (int) -- the per-shard failure budget new shards inherit.
--         lease_seconds (int) -- how long a claim on this lane's shards is good for.
--         time_budget_seconds (int) -- how long one cron tick of this lane may work.
--         retry_policy (text holding JSON), parameters (text holding JSON) -- both canonical JSON.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: exactly one row -- the definition as it now stands in the ledger, whether
-- this call created it or updated it. The caller reads its own configuration back out of that row
-- rather than trusting the values it sent, so the runtime always executes what the database
-- actually holds.
--
-- An "upsert" is insert-or-update in a single statement. It matters here because lane definitions
-- are declared in code and re-applied on every deployment: the first deploy must create the row,
-- every later one must reconcile it, and neither may fail because of the other.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_definition (...) VALUES (...)
--     The optimistic path -- assume the lane is new and insert it.
--
--   CAST(retry_policy AS jsonb), CAST(parameters AS jsonb)
--     Both arrive as JSON text and both columns are jsonb, PostgreSQL's parsed JSON type. The CASTs
--     pin the parameters' types -- a bare bound parameter in a VALUES list gives the planner no
--     column context to infer from -- and parsing means malformed JSON is refused by the database
--     rather than stored as unusable text.
--
--   ON CONFLICT (name, version) DO UPDATE
--     What turns this into an upsert. If the insert would violate the unique constraint on
--     (name, version) -- i.e. this lane at this version is already registered -- PostgreSQL runs
--     the UPDATE below on the existing row instead of raising an error. The alternative shape,
--     SELECT-then-INSERT-or-UPDATE, is racy: two deploys landing together would both see no row and
--     both try to insert.
--
--   SET handler = EXCLUDED.handler, queue_name = EXCLUDED.queue_name, ...
--     EXCLUDED is a special alias for the row this statement TRIED to insert. So each assignment
--     reads "take the value I just supplied and put it on the existing row" -- reconciling the
--     stored definition with the declared one, field by field.
--
--   name and version are deliberately absent from the SET list
--     They are the conflict key. They already match by definition, and they identify the row.
--
--   updated_at = now()
--     Records when the definition was last reconciled. now() is the database's clock, so
--     definitions written by different containers stay comparable.
--
--   RETURNING id, name, version, handler, ...
--     RETURNING hands back columns from the row the statement actually wrote -- inserted or updated
--     -- in the same round trip. It is how the caller learns the database-generated id and reads
--     the authoritative configuration in one go; on this statement it always yields exactly one row.
INSERT INTO agri.job_definition (
    name, version, handler, queue_name, schedule, schedule_timezone, enabled,
    concurrency_key, max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
)
VALUES (
    :name, :version, :handler, :queue_name, :schedule, :schedule_timezone, :enabled,
    :concurrency_key, :max_attempts, :lease_seconds, :time_budget_seconds,
    CAST(:retry_policy AS jsonb), CAST(:parameters AS jsonb)
)
ON CONFLICT (name, version) DO UPDATE
SET handler = EXCLUDED.handler,
    queue_name = EXCLUDED.queue_name,
    schedule = EXCLUDED.schedule,
    schedule_timezone = EXCLUDED.schedule_timezone,
    enabled = EXCLUDED.enabled,
    concurrency_key = EXCLUDED.concurrency_key,
    max_attempts = EXCLUDED.max_attempts,
    lease_seconds = EXCLUDED.lease_seconds,
    time_budget_seconds = EXCLUDED.time_budget_seconds,
    retry_policy = EXCLUDED.retry_policy,
    parameters = EXCLUDED.parameters,
    updated_at = now()
RETURNING id, name, version, handler, queue_name, concurrency_key,
          max_attempts, lease_seconds, time_budget_seconds, retry_policy, parameters
