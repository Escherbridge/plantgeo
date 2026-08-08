-- Purpose: read back one just-materialized forecast iteration's header row together with how many
--          forecast values were written under it, so the CLI can print a receipt.
-- Loaded by: agri_data_service.cli
-- Params: iteration_id (uuid) -- the iteration to summarize, as returned by
--         materialize_forecast_iteration.sql.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon-prefixed word
-- inside a comment would mint a bind parameter that no caller supplies.
--
-- What this returns: exactly one row, or none. The caller uses .one(), so no row is an error --
-- which is the intent: an iteration that exists but has zero values is not a successful run, and
-- the INNER JOIN below is what makes that case return nothing rather than a row saying "zero".
--
-- How this query works, clause by clause:
--
--   FROM agri.forecast_iteration AS iteration
--     The header row: one row per iteration, recording what was simulated and under which
--     parameters. Every column selected from it is echoed straight into the CLI's JSON receipt.
--
--   INNER JOIN agri.forecast_iteration_value AS value ON value.iteration_id = iteration.id
--     The per-horizon-step forecast values, one row each. INNER (rather than LEFT) is load-bearing:
--     it drops the iteration entirely when it has no values, which turns "the run wrote nothing"
--     into a loud missing-row failure instead of a quiet value_count of 0.
--
--   count(value.id)::integer AS value_count
--     How many forecast values the join found. count() returns bigint, a 64-bit integer; the ::
--     cast narrows it to a plain integer so the driver hands Python an int rather than a wider type
--     the JSON layer would then have to reason about.
--
--   WHERE iteration.id = :iteration_id
--     Restricts to the one iteration asked about. Bound, never interpolated.
--
--   GROUP BY iteration.id
--     Required because the SELECT mixes plain columns with an aggregate. Grouping by the
--     iteration's primary key is the case where PostgreSQL allows the other iteration.* columns to
--     be selected without listing each one in the GROUP BY: they are functionally dependent on the
--     key, so there is exactly one possible value for each per group.
SELECT
    iteration.id,
    iteration.iteration_key,
    iteration.status,
    iteration.method,
    iteration.purpose,
    iteration.availability_mode,
    iteration.series_id,
    iteration.release_set_id,
    iteration.cutoff_time,
    iteration.horizon_days,
    iteration.simulation_count,
    iteration.simulation_seed,
    iteration.gap_policy,
    iteration.training_day_count,
    iteration.increment_count,
    iteration.receipt_checksum,
    iteration.recorded_at,
    count(value.id)::integer AS value_count
FROM agri.forecast_iteration AS iteration
INNER JOIN agri.forecast_iteration_value AS value
    ON value.iteration_id = iteration.id
WHERE iteration.id = :iteration_id
GROUP BY iteration.id
