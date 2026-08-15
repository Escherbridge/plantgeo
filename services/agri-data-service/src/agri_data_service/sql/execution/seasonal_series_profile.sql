-- Purpose: data-quality profile for the governed signal series of a bounded cell set:
--          cadence, duplicate releases per day, nulls, unobserved and flagged rows,
--          observation span and warehouse availability bounds.
-- Loaded by: agri_data_service.execution.seasonal_evidence_report
-- Params: cell_keys (text[]), window_start/window_end (timestamptz, inclusive of
--         window_start, exclusive of window_end, matched on observed_at)
SELECT
    cell.cell_key AS cell_key,
    data_source.key AS source_key,
    observation.signal_name AS signal_name,
    observation.support_key AS support_key,
    observation.normalized_unit AS normalized_unit,
    count(*) AS row_count,
    count(DISTINCT (observation.observed_at AT TIME ZONE 'UTC')::date) AS observed_day_count,
    count(DISTINCT observation.source_release_id) AS source_release_count,
    min((observation.observed_at AT TIME ZONE 'UTC')::date) AS first_observed_date,
    max((observation.observed_at AT TIME ZONE 'UTC')::date) AS last_observed_date,
    count(*) FILTER (WHERE observation.normalized_value IS NULL) AS null_value_count,
    count(*) FILTER (WHERE NOT observation.is_observed) AS unobserved_count,
    count(*) FILTER (WHERE observation.quality_flag <> 'accepted') AS flagged_count,
    min(observation.data_available_at) AS earliest_data_available_at,
    max(observation.data_available_at) AS latest_data_available_at
FROM agri.signal_observation AS observation
JOIN agri.spatial_cell AS cell
    ON cell.id = observation.cell_id
JOIN agri.source_release AS release
    ON release.id = observation.source_release_id
JOIN agri.data_source AS data_source
    ON data_source.id = release.data_source_id
WHERE cell.cell_key = ANY(:cell_keys)
  AND observation.observed_at >= :window_start
  AND observation.observed_at < :window_end
GROUP BY
    cell.cell_key,
    data_source.key,
    observation.signal_name,
    observation.support_key,
    observation.normalized_unit
ORDER BY
    cell.cell_key,
    observation.signal_name,
    data_source.key
