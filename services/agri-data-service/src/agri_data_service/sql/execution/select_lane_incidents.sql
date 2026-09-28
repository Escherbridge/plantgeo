-- select_lane_incidents
-- Purpose: the executor's ONE read per tick of everything `execution/lane_incidents.py::reconcile`
--          needs (spec Sec 4.9.3, design Sec 3.2A): every currently open (or, from G1, acknowledged)
--          incident of any kind, PLUS every `lane_hold` episode that resolved in the last 7 days --
--          because a hold reopening inside that window inherits its prior rung and
--          `chain_first_seen_at` (the "watch window" and episode-chaining rules), and after a row
--          resolves it is immediately RENAMED (`resolve_lane_incident.sql`) so nothing but this
--          widened read can find it again.
-- Loaded by: agri_data_service.execution.lane_incidents
-- Params: now (timestamptz) -- the tick's own clock reading; the 7-day window is measured back from it.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too.
--
-- What this returns: one row per matching `agri.job_incident`, oldest-first. `detail`'s hold-ladder
-- keys are extracted with `->>` (`state`, `rung`, `chain_first_seen_at`, `episodes_7d`), the same
-- convention `select_open_incidents.sql` already uses, rather than handed back as one jsonb blob:
-- every other kind of incident this file also returns carries none of these keys and reports them
-- NULL instead of failing the query, and the caller never has to decide how a raw jsonb column
-- arrived (driver-decoded object vs. text) before it can read a single field out of it.
--
-- How this query works, clause by clause:
--
--   WHERE incident.status <> 'resolved'
--     Every kind of open (or acknowledged) incident, not only `lane_hold` -- `lane_incomplete`,
--     `lane_report_missing`, `lane_plan_failed` and the rest all need their own streak read back
--     every tick too. `<> 'resolved'` rather than `IN ('open', 'acknowledged')` is the same
--     forward-generous predicate `select_open_incidents.sql` already argues for: a status this file's
--     author never anticipated still counts as "not yet resolved" instead of silently vanishing.
--
--   OR (incident.incident_type = 'lane_hold' AND incident.resolved_at >= CAST(:now AS timestamptz)
--       - interval '7 days')
--     `incident_type` survives the fingerprint rename `resolve_lane_incident.sql` performs, so this
--     is a stable way to find a RECENTLY-CLOSED hold even though its fingerprint no longer reads
--     `lane_hold:<lane>` -- `resolved_at` is NOT NULL exactly when status is resolved
--     (`resolved_incident_has_timestamp`), so no extra status check is needed inside this branch.
--     Bounded to `lane_hold` alone: no other incident kind chains episodes across a resolve, so
--     widening this branch to every kind would only cost the read a wider scan for no reconcile rule
--     that reads it.
--
--   ORDER BY incident.first_seen_at
--     Oldest-open-first, matching `select_open_incidents.sql`'s convention, so the longest-running
--     condition is always the first row a caller iterating this result sees.
SELECT
    incident.id,
    incident.fingerprint,
    incident.incident_type,
    incident.severity,
    incident.status,
    incident.job_run_id,
    incident.job_work_item_id,
    incident.summary,
    incident.occurrence_count,
    incident.first_seen_at,
    incident.last_seen_at,
    incident.cooldown_until,
    incident.resolved_at,
    incident.detail ->> 'state' AS state,
    incident.detail ->> 'rung' AS rung,
    incident.detail ->> 'chain_first_seen_at' AS chain_first_seen_at,
    incident.detail ->> 'episodes_7d' AS episodes_7d
FROM agri.job_incident AS incident
WHERE incident.status <> 'resolved'
   OR (
        incident.incident_type = 'lane_hold'
        AND incident.resolved_at >= CAST(:now AS timestamptz) - interval '7 days'
   )
ORDER BY incident.first_seen_at
