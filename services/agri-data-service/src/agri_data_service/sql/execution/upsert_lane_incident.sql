-- upsert_lane_incident
-- Purpose: open, or bump the occurrence of, one soft-failure incident row -- a lane hold, an
--          incomplete streak, a plan failure, and every other kind `execution/lane_incidents.py`
--          names by its own fingerprint (spec Sec 4.9.3, design Sec 3.4). One statement covers
--          both "this condition has never been seen" and "this condition is still going on",
--          because the fingerprint is UNIQUE (`agri.job_incident.fingerprint`) and a resolved
--          episode is always RENAMED away by `resolve_lane_incident.sql` before this file's
--          fingerprint could be reused for a new episode -- so the ON CONFLICT branch below can
--          only ever mean "the same still-open episode happened again", never "reopen a closed one".
-- Loaded by: agri_data_service.execution.lane_incidents
-- Params: fingerprint (text), incident_type (text), severity (text: debug|info|warning|error|critical,
--         `models/jobs.py::EventSeverity`), job_run_id (uuid, nullable), job_work_item_id (uuid,
--         nullable), summary (text), now (timestamptz), detail (text holding JSON)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: exactly one row -- the incident's id, its fingerprint, its status, the
-- occurrence_count and last_seen_at THE LEDGER now holds (not merely the values this call sent),
-- and first_seen_at, so the caller can read back the true episode age without a second query.
--
-- How this query works, clause by clause:
--
--   VALUES (..., 1, CAST(:now AS timestamptz), CAST(:now AS timestamptz), ...)
--     A first sighting: occurrence_count starts at 1, and first_seen_at/last_seen_at both open on
--     the same instant -- the ordered_incident_seen_window CHECK constraint needs last_seen_at >=
--     first_seen_at, and one :now value trivially satisfies it whichever branch runs.
--
--   ON CONFLICT (fingerprint) DO UPDATE
--     uq_job_incident_fingerprint is the table's only unique constraint, so this is the one legal
--     conflict target. severity, job_run_id, job_work_item_id, summary and detail are overwritten
--     with THIS call's values on purpose: the caller (never this file) decides whether an escalation
--     applies -- `execution/lane_incidents.py::INCIDENT_SEVERITY` and the reconcile rules compute the
--     severity to pass in, so this statement never has to re-derive "is this worse than before" itself.
--
--   occurrence_count = agri.job_incident.occurrence_count + 1
--     Every upsert against an already-open row is one more sighting of the same condition.
--
--   last_seen_at = GREATEST(agri.job_incident.last_seen_at, CAST(:now AS timestamptz))
--     Never lets a call carrying an older clock reading move the ledger's last_seen_at backwards --
--     `test_upsert_never_moves_last_seen_backwards` pins exactly this, because a savepoint that rolls
--     back and retries, or two lanes racing the same fingerprint, could otherwise present their calls
--     out of wall-clock order.
--
--   status and first_seen_at are absent from the UPDATE SET list
--     status keeps whatever it already is (this file never resolves or reopens a row -- that is
--     resolve_lane_incident.sql's job alone), and first_seen_at keeps the FIRST instant this episode
--     was ever seen, which is exactly what "episode age" and the 72-hour chronic check both need.
INSERT INTO agri.job_incident (
    fingerprint, incident_type, severity, status, job_run_id, job_work_item_id, summary,
    occurrence_count, first_seen_at, last_seen_at, detail
)
VALUES (
    CAST(:fingerprint AS varchar),
    CAST(:incident_type AS varchar),
    CAST(:severity AS varchar),
    'open',
    CAST(:job_run_id AS uuid),
    CAST(:job_work_item_id AS uuid),
    CAST(:summary AS text),
    1,
    CAST(:now AS timestamptz),
    CAST(:now AS timestamptz),
    CAST(:detail AS jsonb)
)
ON CONFLICT (fingerprint) DO UPDATE SET
    severity = CAST(:severity AS varchar),
    job_run_id = CAST(:job_run_id AS uuid),
    job_work_item_id = CAST(:job_work_item_id AS uuid),
    summary = CAST(:summary AS text),
    occurrence_count = agri.job_incident.occurrence_count + 1,
    last_seen_at = GREATEST(agri.job_incident.last_seen_at, CAST(:now AS timestamptz)),
    detail = CAST(:detail AS jsonb)
RETURNING id, fingerprint, status, occurrence_count, first_seen_at, last_seen_at
