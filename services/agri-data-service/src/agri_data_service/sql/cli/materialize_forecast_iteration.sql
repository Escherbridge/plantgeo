-- Purpose: run one complete forecast iteration inside the database by calling the shipped
--          procedure, and return the identifier of the iteration it opened or found.
-- Loaded by: agri_data_service.cli
-- Params: iteration_key (text) -- the deterministic idempotency key naming this evaluation;
--         series_id (uuid) -- the forecast series being simulated; release_set_id (uuid) -- the
--         governed release set the inputs are pinned to; as_of_time (timestamptz) -- the moment the
--         run stands at, which bounds what data counts as available; cutoff_time (timestamptz) --
--         the UTC day start that ends the training history; history_start (timestamptz, nullable)
--         -- the first day to train from, or NULL to start at the earliest governed observation;
--         horizon_days (int) -- how many days forward to forecast; simulation_count (int) -- how
--         many simulated paths to draw; seed (int) -- the random seed that makes the draw
--         reproducible; gap_policy (text) -- how missing days in the history are handled;
--         lower_bound, upper_bound (float, nullable) -- the metric's physical limits, or NULL for
--         unbounded.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one value -- the uuid of the forecast iteration. The caller reads it with
-- scalar_one() and immediately looks the iteration up with forecast_iteration_summary.sql, so a
-- missing value is a hard error rather than something to work around.
--
-- What a procedure call is doing here: CALL invokes a stored procedure -- a routine that lives in
-- the database and can both read and write. The entire simulation runs server-side, next to the
-- governed data, so no history is shipped to this process and back. That is deliberate: the
-- iteration's inputs, outputs and receipt are all written inside the one transaction the caller
-- opened around this statement, so a partial iteration cannot survive a crash.
--
-- How this query works, clause by clause:
--
--   CALL agri.materialize_forecast_iteration(...)
--     One statement, one call. The procedure is what enforces idempotency: called twice with the
--     same iteration_key it returns the existing iteration rather than writing a second one.
--
--   CAST(NULL AS uuid)
--     The procedure's first parameter is its INOUT slot -- the place it puts the resulting
--     iteration id. Passing an explicitly typed NULL says "no value going in, tell me the value
--     coming out". The cast is required because a bare NULL has no type PostgreSQL can use to pick
--     which overload of the procedure is meant.
--
--   CAST(:iteration_key AS varchar), CAST(:series_id AS uuid), ... and their kin
--     Every bound value is cast to the exact type the procedure's signature declares. A bind
--     parameter arrives at the server without a type of its own, so without these casts PostgreSQL
--     would have to guess -- and a wrong guess is not a subtle bug here, it is a failure to resolve
--     the procedure at all, or worse, resolving a different one. The three integer parameters
--     (horizon_days, simulation_count, seed) need no cast because an integer bind is already
--     unambiguous.
CALL agri.materialize_forecast_iteration(
    CAST(NULL AS uuid),
    CAST(:iteration_key AS varchar),
    CAST(:series_id AS uuid),
    CAST(:release_set_id AS uuid),
    CAST(:as_of_time AS timestamptz),
    CAST(:cutoff_time AS timestamptz),
    CAST(:history_start AS timestamptz),
    :horizon_days,
    :simulation_count,
    :seed,
    CAST(:gap_policy AS varchar),
    CAST(:lower_bound AS double precision),
    CAST(:upper_bound AS double precision)
)
