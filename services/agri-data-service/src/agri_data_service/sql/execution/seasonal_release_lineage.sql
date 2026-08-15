-- Purpose: the frozen source-release identity behind an export: one row per release that
--          contributed a value, with its licence snapshot and validation state, so the
--          export manifest records what was frozen rather than asserting it.
-- Loaded by: agri_data_service.execution.seasonal_evaluation_export
-- Params: cell_keys (text[]), window_start/window_end (timestamptz, half-open on observed_at)
SELECT
    data_source.key AS source_key,
    release.id AS source_release_id,
    release.source_version AS source_version,
    release.transform_version AS transform_version,
    release.payload_checksum AS payload_checksum,
    release.validation_state AS validation_state,
    release.schema_version AS schema_version,
    encode(digest(release.license_snapshot, 'sha256'), 'hex') AS license_snapshot_checksum,
    release.retrieved_at AS retrieved_at,
    release.data_available_at AS release_data_available_at,
    count(*) AS contributed_row_count,
    min((observation.observed_at AT TIME ZONE 'UTC')::date) AS first_observed_date,
    max((observation.observed_at AT TIME ZONE 'UTC')::date) AS last_observed_date,
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
    data_source.key,
    release.id,
    release.source_version,
    release.transform_version,
    release.payload_checksum,
    release.validation_state,
    release.schema_version,
    release.license_snapshot,
    release.retrieved_at,
    release.data_available_at
ORDER BY
    data_source.key,
    release.source_version,
    release.id
