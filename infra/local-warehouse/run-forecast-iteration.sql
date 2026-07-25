\set ON_ERROR_STOP on
\pset pager off

\if :{?series_key}
\else
\set series_key 'nasa-power-ws2m-denver-point-v1'
\endif
\if :{?release_set_key}
\else
\set release_set_key 'nasa-power-na-sampling-20220430-20260430-asof-20260721'
\endif
\if :{?iteration_key}
\else
SELECT
    'nasa-power-ws2m-20260331-bootstrap-manual-'
    || to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYYMMDDHH24MISSMS')
    AS iteration_key
\gset
\endif
\if :{?as_of_time}
\else
\set as_of_time 'now'
\endif
\if :{?cutoff_time}
\else
\set cutoff_time '2026-03-31T00:00:00Z'
\endif
\if :{?history_start}
\else
\set history_start '2022-04-30T00:00:00Z'
\endif
\if :{?horizon_days}
\else
\set horizon_days 30
\endif
\if :{?simulation_count}
\else
\set simulation_count 1000
\endif
\if :{?simulation_seed}
\else
\set simulation_seed 42
\endif
\if :{?gap_policy}
\else
\set gap_policy 'strict'
\endif
\if :{?lower_bound}
\else
\set lower_bound 0
\endif

BEGIN;
SET LOCAL statement_timeout = '120s';

SELECT id::text AS series_id
FROM agri.forecast_series
WHERE series_key = :'series_key'
\gset

SELECT id::text AS release_set_id
FROM agri.release_set
WHERE logical_key = :'release_set_key'
  AND state IN ('validated', 'published')
\gset

CALL agri.materialize_forecast_iteration(
    NULL,
    :'iteration_key',
    :'series_id'::uuid,
    :'release_set_id'::uuid,
    :'as_of_time'::timestamptz,
    :'cutoff_time'::timestamptz,
    :'history_start'::timestamptz,
    :horizon_days,
    :simulation_count,
    :simulation_seed,
    :'gap_policy',
    :lower_bound,
    NULL
)
\gset

SELECT
    iteration.id,
    iteration.iteration_key,
    iteration.purpose,
    iteration.availability_mode,
    iteration.method,
    iteration.status,
    iteration.as_of_time,
    iteration.cutoff_time,
    iteration.horizon_days,
    iteration.simulation_count,
    iteration.training_day_count,
    iteration.increment_count,
    iteration.receipt_checksum,
    count(value.id) AS value_count
FROM agri.forecast_iteration AS iteration
JOIN agri.forecast_iteration_value AS value ON value.iteration_id = iteration.id
WHERE iteration.id = :'p_iteration_id'::uuid
GROUP BY iteration.id;

COMMIT;
