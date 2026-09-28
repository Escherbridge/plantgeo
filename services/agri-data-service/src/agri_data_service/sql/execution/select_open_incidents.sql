-- Purpose: every open incident, of every kind -- section 3 of `agri-service ops jobs-usage-report`
--          (spec Sec 4.9.2, design Sec 2.6). A minimal, generic read: `execution/lane_incidents.py`
--          (o2b-incidents, GL-5) lands AFTER this GL and owns the richer helper queries
--          (`select_lane_incidents.sql` folds in recently-resolved `lane_hold` episodes too); this
--          statement exists only so the usage report's third section has something to read before
--          that module exists, and stays generic on purpose so it keeps working once it does.
-- Loaded by: agri_data_service.execution.usage_report
-- Params: (none)
--
-- What this returns: one row per NON-resolved `agri.job_incident` row, regardless of
-- `incident_type` -- lane holds, executor-lane-control audits, run-supersession conflicts, and
-- whatever kind GL-5 and later slices add, because "every kind" (spec Sec 4.9.2) means this
-- statement must never need to learn a new incident_type's name to keep showing it.
--
-- How this query works, clause by clause:
--
--   WHERE incident.status <> 'resolved'
--     `agri.job_incident.status` is `open`, `acknowledged` or `resolved` (`models/jobs.py::
--     IncidentState`); excluding only `resolved` -- rather than matching `IN ('open',
--     'acknowledged')` -- means a future status this file's author never anticipated still shows up
--     as open rather than silently vanishing from the report.
--
--   detail ->> 'state' / ->> 'rung' / ->> 'exit_class'
--     Soft-failure incidents (GL-5 onward) carry their hold-ladder position inside the free-form
--     `detail` jsonb column; read here as plain text so an incident kind that carries none of these
--     keys simply reports NULL instead of failing the query.
--
--   ORDER BY incident.first_seen_at
--     Oldest-open-first, so the longest-running unresolved condition is the first thing an operator
--     reads.
SELECT
    incident.id,
    incident.fingerprint,
    incident.incident_type,
    incident.severity,
    incident.status,
    incident.summary,
    incident.occurrence_count,
    incident.first_seen_at,
    incident.last_seen_at,
    incident.cooldown_until,
    incident.owner,
    incident.acknowledged_at,
    incident.acknowledged_by,
    incident.detail ->> 'state' AS state,
    incident.detail ->> 'rung' AS rung,
    incident.detail ->> 'exit_class' AS exit_class,
    incident.detail ->> 'chain_first_seen_at' AS chain_first_seen_at
FROM agri.job_incident AS incident
WHERE incident.status <> 'resolved'
ORDER BY incident.first_seen_at
