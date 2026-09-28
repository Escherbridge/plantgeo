-- resolve_lane_incident
-- Purpose: close one open soft-failure incident and free its fingerprint for reuse by a FUTURE,
--          unrelated episode (spec Sec 4.9.3, design Sec 3.2A/3.4). `upsert_lane_incident.sql`'s
--          ON CONFLICT branch can only ever mean "the same still-open episode again" because this
--          statement renames the row away the moment it resolves -- `lane_hold:<lane>` gains the
--          suffix (colon, the word resolved, colon, the row id), a value nothing ever upserts against
--          again, so the base fingerprint is immediately free for the NEXT episode to open under.
-- Loaded by: agri_data_service.execution.lane_incidents
-- Params: fingerprint (text) -- the OPEN row's current fingerprint, before renaming.
--         now (timestamptz) -- the resolution instant.
--         detail_patch (text holding JSON) -- merged (jsonb `||`, right side wins per key) onto the
--                                             row's existing detail, so a resolution can record e.g.
--                                             released_by/resolution_reason without discarding the
--                                             probes/announcements history already accumulated there.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too. The rename suffix below is
-- concatenated from three separate literals (a lone colon, the word resolved, a lone colon) for the
-- same reason: a single literal holding a colon directly before a word reads as a phantom bind
-- parameter, and backslash-escaping it does NOT help here -- SQLAlchemy only strips the backslash
-- when the escaped word is not itself followed by a colon, so the backslash would reach PostgreSQL
-- and (standard_conforming_strings on) land verbatim in the renamed fingerprint.
--
-- What this returns: one row (the incident's id, its NEW fingerprint and resolved_at) when an open
-- row by that fingerprint existed and was resolved by this call; no row when nothing was open under
-- that fingerprint -- already resolved, or never opened -- which the caller reads as "nothing to do"
-- rather than a failure, the same idempotent-no-row convention `insert_run_supersession_incident.sql`
-- uses for its own conflict path.
--
-- How this query works, clause by clause:
--
--   fingerprint = fingerprint || ':' || 'resolved' || ':' || CAST(id AS varchar)
--     Appends the row's OWN id, so two different open incidents that happen to resolve in the same
--     transaction can never collide on the renamed value even if `now()` is identical for both.
--
--   detail = detail || CAST(:detail_patch AS jsonb)
--     The `||` jsonb concatenation operator is a shallow, top-level merge: keys named in
--     detail_patch overwrite the same key already in detail, and every key not named survives
--     untouched -- so this call can add `released_by`/`resolution_reason` without a caller ever
--     reading the row back first to preserve the rest of it.
--
--   WHERE fingerprint = CAST(:fingerprint AS varchar) AND status <> 'resolved'
--     status <> 'resolved' rather than `= 'open'` (the same generosity `select_open_incidents.sql`
--     argues for) admits a future `acknowledged` status through the same resolve path without an
--     edit here; matching on the CURRENT fingerprint means a retried call after a prior success
--     matches nothing (the row already carries its renamed fingerprint) and returns no row, which is
--     exactly the idempotent "already resolved" signal the caller wants.
--
--   RETURNING id, fingerprint, resolved_at
--     The renamed fingerprint is handed back so a caller that logs the resolution can cite the exact
--     value the row now carries, not the one it was opened under.
UPDATE agri.job_incident
SET status = 'resolved',
    resolved_at = CAST(:now AS timestamptz),
    fingerprint = fingerprint || ':' || 'resolved' || ':' || CAST(id AS varchar),
    detail = detail || CAST(:detail_patch AS jsonb)
WHERE fingerprint = CAST(:fingerprint AS varchar)
  AND status <> 'resolved'
RETURNING id, fingerprint, resolved_at
