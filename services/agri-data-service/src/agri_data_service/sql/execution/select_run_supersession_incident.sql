-- select_run_supersession_incident
-- Purpose: read back the one recorded supersession of a checkpoint run, so a repeated recording reports
--          the evidence and operator the ledger actually holds rather than the ones just typed.
-- Loaded by: agri_data_service.execution.job_run_supersession
-- Params: fingerprint (text) -- the executor's supersession prefix followed by the run id; UNIQUE on
--                               the table, so this is an index probe returning at most one row.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: at most one row -- the incident's id, its evidence (summary), who recorded it
-- (owner) and when it was resolved. No rows means the run has never been superseded.
--
-- How this query works, clause by clause:
--
--   WHERE fingerprint = fingerprint
--     A lookup on uq_job_incident_fingerprint, so no ORDER BY or LIMIT is needed: uniqueness already
--     guarantees there is nothing to choose between.
SELECT id, summary, owner, resolved_at
FROM agri.job_incident
WHERE fingerprint = CAST(:fingerprint AS varchar)
