-- write_slice_event
-- Purpose: write the ONE durable heartbeat row every cron tick leaves behind, with the run's queue
--          depth computed inside the same statement.
-- Loaded by: agri_data_service.jobs.worker
-- Params: job_run_id (uuid, nullable) -- the run this tick drove; NULL when there was no open run.
--         severity (text) -- 'warning' when the tick dead-lettered something, otherwise 'info'.
--         event_code (text) -- the vocabulary word that makes this row findable, always the
--         slice-finished code.
--         environment (text) -- which deployment this ran in; NOT NULL, and nothing else names it.
--         service (text) -- which service wrote the row; NOT NULL for the same reason.
--         duration_ms (bigint) -- how long the tick took.
--         progress (text holding JSON) -- the tick's operator-facing summary, verbatim.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: exactly one row, the new event's id -- ALWAYS, including on the tick that
-- found no work at all. That is the whole point, and the reason for the statement's odd shape.
--
-- WHY IT EXISTS. A tick that claims nothing otherwise writes nothing at all, which means a lane
-- whose windows are finished, a lane whose cron service was never created, and a lane whose
-- container crashes on startup are all IDENTICAL from SQL. With one row per tick,
-- max(occurred_at) filtered to this event code becomes a true per-lane heartbeat -- the single fact
-- the ledger previously could not produce. job_work_item.updated_at cannot stand in for it: that
-- column is ORM onupdate only and every runtime write here is a raw UPDATE, so it is frozen at
-- insert time.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_event (...) SELECT ...
--     An INSERT fed by a SELECT rather than a VALUES list, so the queue depth can be computed in
--     the same statement instead of fetched first. One statement, one round trip, one row.
--
--   an aggregate SELECT with NO GROUP BY
--     This is what makes the always-writes guarantee hold. An aggregate query with no GROUP BY
--     returns exactly ONE row even when its scan matches nothing -- with zeroes -- so the tick that
--     most needs to be seen, the one with no open run and therefore no matching work items, still
--     writes its heartbeat. A plain non-aggregate SELECT would have produced no rows and inserted
--     nothing.
--
--   CAST(job_run_id AS uuid), CAST(severity AS varchar), ... on every value
--     Every parameter is cast explicitly rather than left for the target column to type. An
--     INSERT ... SELECT gives PostgreSQL no column context for a bare parameter -- unlike a VALUES
--     list, where the target column supplies it -- so without these casts the statement is
--     ambiguous and is refused.
--
--   jsonb_build_object('outstanding_work_items', count(*) FILTER (...), ...)
--     jsonb_build_object assembles a JSON object from alternating key and value arguments, so the
--     four queue-depth figures land as one structured jsonb value rather than four columns the
--     table does not have.
--     FILTER restricts ONE aggregate to the rows matching its condition while the others in the
--     same expression still see every row, which is how all four counters come out of a single pass
--     over the work items instead of four separate queries.
--
--   FROM agri.job_work_item AS item WHERE item.job_run_id = CAST(job_run_id AS uuid)
--     The scan the counters aggregate over. When the parameter is NULL this matches nothing and,
--     per the no-GROUP-BY rule above, the counters all come back as zero on a single row.
--
--   severity and event_code as separate columns
--     Severity rather than a second event code, deliberately: an operator filtering on severity
--     gets the ticks that dead-lettered something without having to learn a second vocabulary word.
--
--   RETURNING id
--     RETURNING hands back the database-generated id in the same round trip. The table is
--     PARTITIONED BY RANGE on occurred_at, with a default partition covering the unbounded range,
--     so a row lands in today's hot partition when db/maintenance.py has created one and in the
--     default partition otherwise -- either way it is written, never lost.
INSERT INTO agri.job_event (
    job_run_id, severity, event_code, environment, service, duration_ms, progress, detail
)
SELECT CAST(:job_run_id AS uuid),
       CAST(:severity AS varchar),
       CAST(:event_code AS varchar),
       CAST(:environment AS varchar),
       CAST(:service AS varchar),
       CAST(:duration_ms AS bigint),
       CAST(:progress AS jsonb),
       jsonb_build_object(
           'outstanding_work_items', count(*) FILTER (WHERE item.status NOT IN ('succeeded', 'cancelled')),
           'claimable_work_items', count(*) FILTER (WHERE item.status IN ('queued', 'retry_wait', 'deferred')),
           'leased_work_items', count(*) FILTER (WHERE item.status IN ('leased', 'running')),
           'dead_lettered_work_items', count(*) FILTER (WHERE item.status = 'dead_letter')
       )
FROM agri.job_work_item AS item
WHERE item.job_run_id = CAST(:job_run_id AS uuid)
RETURNING id
