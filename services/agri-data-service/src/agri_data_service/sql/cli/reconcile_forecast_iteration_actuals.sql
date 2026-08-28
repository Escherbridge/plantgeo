-- Purpose: attach the governed actual observations to one already-finalized forecast iteration, by
--          invoking the shipped reconciliation procedure, and report how many were attached.
-- Loaded by: agri_data_service.interface.cli
-- Params: iteration_id (uuid) -- the finalized iteration to reconcile; actual_release_set_id (uuid)
--         -- the governed release set the actuals must come from; as_of_time (timestamptz) -- the
--         actual-availability boundary, which bounds which observations already existed.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one value -- how many actual observations the procedure attached. The caller
-- reads it with scalar_one() and prints it as inserted_count.
--
-- What reconciliation is: a forecast is written before the days it predicts have happened. Later,
-- once real observations for those days exist in a governed release, they are matched back onto the
-- forecast's horizon steps so predicted and observed sit side by side and error can be measured.
-- The forecast values themselves are never touched -- they are sealed evidence; only the actual
-- alongside each one is filled in.
--
-- How this query works, clause by clause:
--
--   CALL agri.reconcile_forecast_iteration_actuals(...)
--     CALL invokes a stored procedure -- a routine living in the database that both reads and
--     writes. All the matching happens server-side inside the caller's transaction, so a partial
--     reconciliation cannot survive a crash.
--
--   the leading 0
--     The procedure's first parameter is its INOUT slot -- where it puts the count of actuals it
--     attached. A literal 0 seeds it; whatever the procedure leaves there is what comes back. It is
--     written as a bare 0 rather than a typed NULL because an integer literal already resolves the
--     overload unambiguously.
--
--   CAST(:iteration_id AS uuid) and its kin
--     Each bound value is cast to the exact type the procedure's signature declares. A bind
--     parameter reaches the server without a type of its own, and letting PostgreSQL guess risks
--     failing to resolve the procedure -- or resolving a different one.
CALL agri.reconcile_forecast_iteration_actuals(
    0,
    CAST(:iteration_id AS uuid),
    CAST(:actual_release_set_id AS uuid),
    CAST(:as_of_time AS timestamptz)
)
