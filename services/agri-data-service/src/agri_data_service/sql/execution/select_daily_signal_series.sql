-- Purpose: one governed value per (signal, UTC day) for a cell, so the label mapper can derive
--          site climate terms (annual precipitation, mean temperature, frost-free days,
--          a Hargreaves aridity proxy) without pulling every revision into memory.
-- Loaded by: agri_data_service.execution.recommendation_lane
-- Params: cell_id (uuid), signal_names (text[]), window_start/window_end (date, inclusive
--         start and exclusive end of the UTC day range), as_of_time (timestamptz),
--         row_limit (int: hard cap, the caller sizes it from the window it asked for)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md.
--
-- Clause by clause:
--
--   DISTINCT ON (signal_name, UTC day)
--     uq_signal_observation_release_cell_signal_time includes source_release_id, so one day
--     legitimately holds several admissible rows (a re-ingest, a revision). DISTINCT ON keeps
--     exactly one per (signal, day) -- the latest-available one under the ORDER BY below --
--     so a rolling window counts distinct DAYS and a duplicated day cannot double-count.
--
--   data_available_at <= :as_of_time
--     The availability gate. It is the server-recorded arrival time of the row, never a
--     simulated cutoff, so this read is time-honest with respect to the caller's as-of.
--
--   observed_at >= :window_start AND observed_at < :window_end
--     Bounds the scan on ix_signal_observation_cell_time_signal. The caller passes the
--     lookback-widened window; nothing here scans the whole 46M-row table.
--
--   support_key / quality_flag filters
--     Match agri.covariate_daily_features exactly, so a climate term derived here and a
--     covariate value read there are computed off the same admissible row set.
SELECT
    (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_date,
    observation.signal_name,
    observation.normalized_value,
    observation.is_observed,
    observation.source_release_id,
    observation.data_available_at
FROM (
    SELECT DISTINCT ON (signal.signal_name, (signal.observed_at AT TIME ZONE 'UTC')::date)
        signal.signal_name,
        signal.observed_at,
        signal.normalized_value,
        signal.is_observed,
        signal.source_release_id,
        signal.data_available_at,
        signal.id
    FROM agri.signal_observation AS signal
    WHERE signal.cell_id = CAST(:cell_id AS uuid)
      AND signal.signal_name = ANY (CAST(:signal_names AS text[]))
      AND signal.data_available_at <= CAST(:as_of_time AS timestamptz)
      AND signal.support_key = 'surface'
      AND signal.quality_flag = 'accepted'
      AND signal.observed_at >= CAST(:window_start AS date)::timestamptz
      AND signal.observed_at < CAST(:window_end AS date)::timestamptz
    ORDER BY
        signal.signal_name,
        (signal.observed_at AT TIME ZONE 'UTC')::date,
        signal.data_available_at DESC,
        signal.observed_at DESC,
        signal.source_release_id DESC,
        signal.id DESC
) AS observation
ORDER BY observed_date, signal_name
LIMIT CAST(:row_limit AS integer)
