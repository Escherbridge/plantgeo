-- insert_run_supersession_incident
-- Purpose: record, once, that an operator superseded one failed or partial executor checkpoint run
--          with evidence, so the scheduler may open the bucket after it. Nothing on the run or its
--          dead-lettered work item changes; the failure stays exactly as the ledger recorded it.
-- Loaded by: agri_data_service.execution.job_run_supersession
-- Params: fingerprint (text) -- the executor's supersession prefix followed by the run id; UNIQUE on
--                               the table, which is what makes the second recording a no-op.
--         incident_type (text) -- the executor's supersession vocabulary word.
--         job_run_id (uuid) -- the failed or partial checkpoint run being superseded.
--         job_work_item_id (uuid, nullable) -- its first dead-lettered work item, for traceability.
--         summary (text) -- the operator's evidence note, verbatim.
--         owner (text) -- who recorded the supersession.
--         acknowledged_by (text) -- the same operator, bound under its own name because a parameter
--                                   that appears twice in one statement must not rely on type deduction.
--         detail (text holding JSON) -- the lane, bucket, run status, next bucket and the dead letters
--                                       the receipt names, so the row explains itself without a join.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row (the new incident's id and resolution time) when this call recorded the
-- supersession, and NO row when the run was already superseded. The caller reads "no row" as
-- "already recorded", never as a failure, so a retried command is idempotent.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.job_incident (...) VALUES (...)
--     One incident row, opened and resolved in the same statement. The table is the ledger's
--     deduplicated operational-alert lifecycle; a dead-lettered checkpoint is the alert and the
--     operator's evidence is its resolution, so status is written as 'resolved' with resolved_at,
--     acknowledged_at and acknowledged_by all set -- the two CHECK constraints on those pairs are
--     IMMEDIATE, so they must travel together in one statement.
--
--   CAST(... AS varchar), CAST(... AS uuid), CAST(... AS text), CAST(... AS jsonb)
--     Every parameter is cast explicitly so PostgreSQL never has to deduce a type for it; the enum-like
--     severity and status columns are varchar with CHECK constraints, and the literals 'warning' and
--     'resolved' satisfy those checks.
--
--   occurrence_count 1, first_seen_at now(), last_seen_at now()
--     A supersession is seen exactly once; the ordered-seen-window CHECK needs both timestamps equal
--     or ascending, and one now() satisfies it.
--
--   ON CONFLICT (fingerprint) DO NOTHING
--     The fingerprint is UNIQUE, so a second recording for the same run inserts nothing and raises
--     nothing. That is what lets the planner read the marker as a boolean and the operator retry.
--
--   RETURNING id, resolved_at
--     Hands back the identity of the row this call created, or nothing on the conflict path.
INSERT INTO agri.job_incident (
    fingerprint, incident_type, severity, status, job_run_id, job_work_item_id, summary,
    occurrence_count, first_seen_at, last_seen_at, owner, acknowledged_at, acknowledged_by,
    resolved_at, detail
)
VALUES (
    CAST(:fingerprint AS varchar),
    CAST(:incident_type AS varchar),
    'warning',
    'resolved',
    CAST(:job_run_id AS uuid),
    CAST(:job_work_item_id AS uuid),
    CAST(:summary AS text),
    1,
    now(),
    now(),
    CAST(:owner AS varchar),
    now(),
    CAST(:acknowledged_by AS varchar),
    now(),
    CAST(:detail AS jsonb)
)
ON CONFLICT (fingerprint) DO NOTHING
RETURNING id, resolved_at
