-- Purpose: write one iteration's whole forecast curve -- every horizon step's low, median and high
--          value -- in a single statement, each row carrying its own checksum.
-- Loaded by: agri_data_service.execution.vegetation_ndvi_plane
-- Params: iteration_id (uuid) -- the iteration these values belong to; parameter_checksum (text) --
--         the fingerprint of every simulation input, stamped onto each value; valid_times
--         (timestamptz[]) -- the day each forecast value is about; horizon_steps (integer[]) -- how
--         many days ahead of the cutoff each one is; low_values, median_values, high_values (double
--         precision[]) -- the simulated quantile band; increment_counts (integer[]) -- how many
--         historical increments fed each step's simulation.
--
-- The six array parameters are positionally parallel: element N of each one describes the same
-- horizon step.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: nothing. It writes the iteration's values; the caller already knows how many it
-- sent and recorded that expectation on the iteration header, so the sealing step can verify the
-- count independently.
--
-- How this query works, clause by clause:
--
--   INSERT INTO agri.forecast_iteration_value (...) SELECT ... FROM unnest(...)
--     The rows to insert come from a query rather than from literal VALUES, so a whole forecast curve
--     is written in one round trip instead of one statement per horizon step. There is no ON CONFLICT
--     clause here, deliberately: this statement only runs when the header insert reported that THIS
--     run created the iteration, so no values for it can already exist. A conflict here would be a
--     real inconsistency and should fail loudly rather than be silently skipped.
--
--   FROM unnest(CAST(...), CAST(...), ...) AS simulated(...)
--     unnest turns an array parameter into rows. Given several arrays at once it zips them side by
--     side, so row N holds element N of every array -- which is how six parallel arrays become one
--     row per horizon step. The AS simulated(...) part names the resulting table and its columns so
--     the SELECT above can refer to them.
--
--   CAST(valid_times AS timestamptz[]) and the five casts beside it
--     Casts that exist purely to pin each parameter's type. A bare bind parameter carries no type of
--     its own, and unnest accepts arrays of many element types, so without the cast the database
--     cannot tell what it was handed and refuses the statement. Naming each array's element type also
--     guarantees the values land as timestamps, integers and floating-point numbers rather than being
--     coerced from text later.
--
--   iteration_id and parameter_checksum repeated on every row
--     Two scalar parameters selected alongside the per-row columns, so each value row carries the
--     iteration it belongs to and the fingerprint of the configuration that produced it. Stamping the
--     checksum on every row rather than only on the header means an individual value can be traced to
--     its parameters even in isolation.
--
--   agri.forecast_iteration_value_checksum(...)
--     A shipped database function that folds one value row -- its time, step, three quantiles,
--     increment count and parameter checksum -- into a single fingerprint. It lives in the database
--     rather than in Python so every producer of these rows computes the checksum the same way, and
--     so the definition is versioned with the schema. Its final argument is written
--     CAST(parameter_checksum AS varchar) because the function is chosen by the types of its
--     arguments: an untyped parameter leaves the database unable to decide which function was meant,
--     and the cast names the intended one unambiguously.
INSERT INTO agri.forecast_iteration_value (
    iteration_id, valid_time, horizon_step, low_value, median_value, high_value,
    increment_count, parameter_checksum, value_checksum
)
SELECT
    :iteration_id,
    simulated.valid_time,
    simulated.horizon_step,
    simulated.low_value,
    simulated.median_value,
    simulated.high_value,
    simulated.increment_count,
    :parameter_checksum,
    agri.forecast_iteration_value_checksum(
        simulated.valid_time,
        simulated.horizon_step,
        simulated.low_value,
        simulated.median_value,
        simulated.high_value,
        simulated.increment_count,
        CAST(:parameter_checksum AS varchar)
    )
FROM unnest(
    CAST(:valid_times AS timestamptz[]),
    CAST(:horizon_steps AS integer[]),
    CAST(:low_values AS double precision[]),
    CAST(:median_values AS double precision[]),
    CAST(:high_values AS double precision[]),
    CAST(:increment_counts AS integer[])
) AS simulated(valid_time, horizon_step, low_value, median_value, high_value, increment_count)
