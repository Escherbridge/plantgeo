-- Purpose: one deterministic row per (cell, signal, support, UTC day) for the frozen
--          evaluation export. `uq_signal_observation_release_cell_signal_time` includes
--          source_release_id, so a re-ingest legitimately stores several admissible rows
--          for one cell-day; DISTINCT ON keeps the latest-available one with a stable
--          tie-break so the export is reproducible byte for byte.
-- Loaded by: agri_data_service.execution.seasonal_evaluation_export
-- Params: cell_keys (text[]), window_start/window_end (timestamptz, half-open on observed_at)
SELECT DISTINCT ON (cell.cell_key, observation.signal_name, observation.support_key, (observation.observed_at AT TIME ZONE 'UTC')::date)
    cell.cell_key AS cell_key,
    observation.signal_name AS signal_name,
    observation.support_key AS support_key,
    (observation.observed_at AT TIME ZONE 'UTC')::date AS observed_date,
    observation.normalized_value AS normalized_value,
    observation.normalized_unit AS normalized_unit,
    observation.is_observed AS is_observed,
    observation.quality_flag AS quality_flag,
    observation.data_available_at AS data_available_at,
    data_source.key AS source_key,
    release.id AS source_release_id,
    release.payload_checksum AS source_payload_checksum,
    release.source_version AS source_version,
    release.transform_version AS transform_version
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
ORDER BY
    cell.cell_key,
    observation.signal_name,
    observation.support_key,
    (observation.observed_at AT TIME ZONE 'UTC')::date,
    observation.data_available_at DESC,
    observation.id DESC
