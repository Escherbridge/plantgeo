-- Boise Trekker Rim NASA POWER WS2M forecast iterations: retrospective (fully
-- actualized, cutoff + horizon land exactly at the backfilled window end) and
-- current (cutoff at the latest fully-observed UTC day; no actuals yet since
-- its horizon extends past the backfilled window). Reconciles actuals against
-- the retrospective iteration only. Parameterized like run-forecast-iteration.sql.
-- Run as plantgeo_local_developer against the disposable database, e.g.:
--   psql ... -v as_of_time="'2026-07-25T18:00:00Z'" -f run-boise-trekker-rim-iterations.sql

\set ON_ERROR_STOP on
\pset pager off

\if :{?series_key}
\else
\set series_key 'nasa-power-ws2m-boise-trekker-rim-v1'
\endif
\if :{?as_of_time}
\else
\set as_of_time 'now'
\endif
\if :{?retrospective_iteration_key}
\else
\set retrospective_iteration_key 'boise-trekker-rim-ws2m-retrospective-20260623-30d'
\endif
\if :{?retrospective_cutoff_time}
\else
\set retrospective_cutoff_time '2026-06-23T00:00:00Z'
\endif
\if :{?current_iteration_key}
\else
\set current_iteration_key 'boise-trekker-rim-ws2m-current-20260723'
\endif
\if :{?current_cutoff_time}
\else
\set current_cutoff_time '2026-07-23T00:00:00Z'
\endif
\if :{?history_start}
\else
\set history_start '2022-07-23T00:00:00Z'
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

SELECT id::text AS series_id, metadata_json ->> 'release_set_id' AS release_set_id
FROM agri.forecast_series
WHERE series_key = :'series_key'
\gset

\echo 'Retrospective iteration (fully actualized window)'
CALL agri.materialize_forecast_iteration(
    NULL,
    :'retrospective_iteration_key',
    :'series_id'::uuid,
    :'release_set_id'::uuid,
    :'as_of_time'::timestamptz,
    :'retrospective_cutoff_time'::timestamptz,
    :'history_start'::timestamptz,
    :horizon_days,
    :simulation_count,
    :simulation_seed,
    :'gap_policy',
    :lower_bound,
    NULL
)
\gset retrospective_

SELECT
    iteration.id,
    iteration.iteration_key,
    iteration.status,
    iteration.availability_mode,
    iteration.receipt_checksum,
    count(value.id) AS value_count
FROM agri.forecast_iteration AS iteration
JOIN agri.forecast_iteration_value AS value ON value.iteration_id = iteration.id
WHERE iteration.id = :'retrospective_p_iteration_id'::uuid
GROUP BY iteration.id;

\echo 'Current iteration (no actuals yet; horizon extends past the backfilled window)'
CALL agri.materialize_forecast_iteration(
    NULL,
    :'current_iteration_key',
    :'series_id'::uuid,
    :'release_set_id'::uuid,
    :'as_of_time'::timestamptz,
    :'current_cutoff_time'::timestamptz,
    :'history_start'::timestamptz,
    :horizon_days,
    :simulation_count,
    :simulation_seed,
    :'gap_policy',
    :lower_bound,
    NULL
)
\gset current_

SELECT
    iteration.id,
    iteration.iteration_key,
    iteration.status,
    iteration.availability_mode,
    iteration.receipt_checksum,
    count(value.id) AS value_count
FROM agri.forecast_iteration AS iteration
JOIN agri.forecast_iteration_value AS value ON value.iteration_id = iteration.id
WHERE iteration.id = :'current_p_iteration_id'::uuid
GROUP BY iteration.id;

\echo 'Reconcile actuals against the retrospective iteration only'
CALL agri.reconcile_forecast_iteration_actuals(
    0,
    :'retrospective_p_iteration_id'::uuid,
    :'release_set_id'::uuid,
    :'as_of_time'::timestamptz
)
\gset reconcile_

-- Governed metrics only: agri.forecast_iteration_evaluation is the single
-- canonical, leakage-gated, checksummed definition of MAE/RMSE/coverage.
SELECT *
FROM agri.forecast_iteration_evaluation(:'series_id'::uuid, :'as_of_time'::timestamptz)
WHERE iteration_id = :'retrospective_p_iteration_id'::uuid;

COMMIT;
