-- Purpose: declare the job definition that the covariate wind training receipts hang from,
--          creating it only the first time and never rewriting a live one.
-- Loaded by: agri_data_service.execution.covariate_wind_persist
-- Params: name (text), version (text), handler (text), queue_name (text),
--         max_attempts (int), lease_seconds (int), time_budget_seconds (int),
--         parameters (jsonb as text)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- How this query works, clause by clause:
--
--   INSERT ... ON CONFLICT (name, version) DO NOTHING
--     ON CONFLICT is PostgreSQL's "what to do when this row would violate a unique
--     constraint". DO NOTHING means: if a definition with this name and version already
--     exists, skip the insert silently instead of raising. That is what makes this statement
--     safe to run on every training invocation -- the first run creates the definition and
--     every later run is a no-op. DO NOTHING is chosen over DO UPDATE deliberately: updating
--     would rewrite the recorded shape of a definition that already has runs hanging off it,
--     so an operator reading the row would no longer see what those runs were planned with.
--
--   RETURNING id
--     Hands back the primary key of the row this statement inserted. Note the consequence of
--     DO NOTHING: when the row already existed, nothing was inserted, so RETURNING yields NO
--     rows at all. That is expected, and the caller resolves the existing id with its own
--     one-line lookup rather than complicating this statement with a UNION.
--
--   CAST(... AS jsonb)
--     parameters is sent as text and cast here. The driver has no jsonb type of its own, so
--     without the cast PostgreSQL would be handed an untyped placeholder for a jsonb column
--     and refuse to infer it.
INSERT INTO agri.job_definition (
    name,
    version,
    handler,
    queue_name,
    enabled,
    max_attempts,
    lease_seconds,
    time_budget_seconds,
    parameters
)
VALUES (
    CAST(:name AS varchar),
    CAST(:version AS varchar),
    CAST(:handler AS varchar),
    CAST(:queue_name AS varchar),
    true,
    CAST(:max_attempts AS integer),
    CAST(:lease_seconds AS integer),
    CAST(:time_budget_seconds AS integer),
    CAST(:parameters AS jsonb)
)
ON CONFLICT (name, version) DO NOTHING
RETURNING id
