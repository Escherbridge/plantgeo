-- Purpose: append the governed actual observations to one already-finalized forecast iteration, by
--          invoking the shipped reconciliation procedure.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: iteration_id (uuid) -- the finalized iteration to reconcile; release_set_id (uuid) -- the
--         governed set the actuals must come from; as_of_time (timestamptz) -- the moment the
--         reconciliation is performed as of, which bounds which actuals were already available.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: one value -- how many actuals the procedure attached. The caller sums that
-- across iterations and treats a missing value as zero.
--
-- What reconciliation is: a forecast is written before the days it predicts have happened. Later,
-- once real observations for those days exist in a governed release, they are matched back onto the
-- forecast's horizon steps so that predicted and observed sit side by side and error can be
-- measured. The forecast values themselves are never touched -- they are sealed evidence; only the
-- actual alongside each one is filled in.
--
-- How this query works, clause by clause:
--
--   CALL agri.reconcile_forecast_iteration_actuals(...)
--     CALL invokes a stored procedure, which is not the same thing as running a query. A procedure is
--     a named unit of work that lives in the database and can write rows and control its own
--     transaction; it is not something a SELECT can wrap. The matching rule -- which observation
--     answers which horizon step, and which observations are allowed to count -- therefore lives in
--     one shipped, schema-versioned definition rather than being restated by each caller. That the
--     rule is applied identically everywhere is the point of calling it rather than reimplementing it.
--
--   CAST(NULL AS integer)  (the first argument)
--     The procedure's first parameter is an output slot: the procedure writes the number of
--     reconciled actuals back into it, and that is the value the caller reads. It is passed as an
--     explicitly typed NULL because there is nothing to send in -- only somewhere to receive. The cast
--     is required rather than cosmetic: a bare NULL has no type at all, and a procedure is selected by
--     the types of its arguments, so an untyped NULL leaves the database unable to tell which
--     procedure was meant.
--
--   CAST(iteration_id AS uuid), CAST(release_set_id AS uuid), CAST(as_of_time AS timestamptz)
--     Casts that exist purely to pin each parameter's type, for the same reason: a bare bind parameter
--     carries no type of its own, and the procedure is resolved by its argument types. Naming them
--     makes the call unambiguous.
--
--   one call per iteration
--     The procedure reconciles a single iteration, so the caller loops. That is deliberate: each
--     iteration's reconciliation is independent, and a per-iteration call keeps the count attributable
--     and one failure from silently affecting another iteration's result.
CALL agri.reconcile_forecast_iteration_actuals(
    CAST(NULL AS integer),
    CAST(:iteration_id AS uuid),
    CAST(:release_set_id AS uuid),
    CAST(:as_of_time AS timestamptz)
)
