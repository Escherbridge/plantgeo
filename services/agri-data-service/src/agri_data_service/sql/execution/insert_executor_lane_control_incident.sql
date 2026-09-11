-- Purpose: append an operator's pause/resume audit atomically with the definition update.
-- Loaded by: agri_data_service.execution.job_lane_control
-- Params: fingerprint (text), summary (text), owner (text), acknowledged_by (text), detail (JSON text).
-- One resolved informational incident records requested state and before/after definition identities.
-- Explicit casts bind driver parameters; resolution/acknowledgement timestamps satisfy table checks.
-- Run/work-item links are NULL: this incident cannot release a failed run or change its evidence.
-- The control UUID fingerprint deliberately differs from the scheduler's supersession fingerprint.
-- There is no conflict overwrite: duplicate control identity fails the whole transaction.
-- RETURNING gives the caller the durable audit identity once its enclosing commit succeeds.
INSERT INTO agri.job_incident (
    fingerprint, incident_type, severity, status, job_run_id, job_work_item_id,
    summary, occurrence_count, first_seen_at, last_seen_at, owner,
    acknowledged_at, acknowledged_by, resolved_at, detail
)
VALUES (
    CAST(:fingerprint AS varchar), 'executor_lane_control', 'info', 'resolved', NULL, NULL,
    CAST(:summary AS text), 1, now(), now(), CAST(:owner AS varchar),
    now(), CAST(:acknowledged_by AS varchar), now(), CAST(:detail AS jsonb)
)
RETURNING id
