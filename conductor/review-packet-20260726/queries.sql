-- Read-only PlantGeo warehouse review queries.
-- Run after BEGIN READ ONLY; see warehouse-access.md.

-- 1. Schema/migration evidence and available governed relations.
SELECT version_num AS schema_head FROM alembic_version;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'agri'
ORDER BY table_name;

-- 2. Retained GHISACONUS source, release, and artifacts.
SELECT source.key AS source_key, release.id AS source_release_id,
       release.source_version, release.validation_state, release.retrieved_at,
       release.data_available_at
FROM agri.data_source AS source
JOIN agri.source_release AS release ON release.data_source_id = source.id
WHERE source.key = 'kaggle-ghisaconus-mirror';

SELECT artifact.kind, artifact.media_type, artifact.size_bytes,
       artifact.checksum_sha256, artifact.storage_class
FROM agri.artifact AS artifact
JOIN agri.source_release AS release ON release.id = artifact.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE source.key = 'kaggle-ghisaconus-mirror'
ORDER BY artifact.kind;

-- 3. Signal coverage by governed signal; avoids raw-row dumping.
SELECT observation.signal_name, observation.source_parameter,
       observation.normalized_unit, COUNT(*) AS observations,
       MIN(observation.observed_at) AS first_observed_at,
       MAX(observation.observed_at) AS last_observed_at,
       MIN(observation.data_available_at) AS first_available_at,
       MAX(observation.data_available_at) AS last_available_at,
       COUNT(*) FILTER (WHERE observation.normalized_value IS NULL) AS null_values
FROM agri.signal_observation AS observation
GROUP BY observation.signal_name, observation.source_parameter, observation.normalized_unit
ORDER BY observations DESC, observation.signal_name;

-- 4. Seven-day hindcast outcome lineage by origin/horizon.
SELECT simulated_cutoff_time, horizon_step, COUNT(*) AS outcome_rows,
       MIN(actual_data_available_at) AS first_actual_available_at,
       MAX(actual_data_available_at) AS last_actual_available_at
FROM agri.v_forecast_hindcast_outcome
GROUP BY simulated_cutoff_time, horizon_step
ORDER BY simulated_cutoff_time, horizon_step;

-- 5. Input-availability diagnostic: source releases in each hindcast release set
-- versus the simulated cutoff. `actual_data_available_at` in query 4 is outcome
-- lineage and deliberately is not used for input eligibility.
SELECT hindcast.simulated_cutoff_time,
       release_set.logical_key AS input_release_set,
       COUNT(DISTINCT release.id) AS input_releases,
       COUNT(DISTINCT release.id) FILTER (
         WHERE release.data_available_at <= hindcast.simulated_cutoff_time
       ) AS eligible_input_releases,
       COUNT(DISTINCT release.id) FILTER (
         WHERE release.data_available_at > hindcast.simulated_cutoff_time
       ) AS later_available_input_releases,
       MIN(release.data_available_at) AS first_input_available_at,
       MAX(release.data_available_at) AS last_input_available_at
FROM agri.forecast_hindcast_run AS hindcast
JOIN agri.release_set AS release_set ON release_set.id = hindcast.release_set_id
JOIN agri.release_set_item AS item ON item.release_set_id = release_set.id
JOIN agri.source_release AS release ON release.id = item.source_release_id
GROUP BY hindcast.simulated_cutoff_time, release_set.logical_key
ORDER BY hindcast.simulated_cutoff_time;

-- 6. Read-only release-set lineage summary.
SELECT release_set.logical_key, release_set.state,
       release_set.manifest_checksum, COUNT(member.source_release_id) AS member_releases
FROM agri.release_set AS release_set
LEFT JOIN agri.release_set_item AS member ON member.release_set_id = release_set.id
GROUP BY release_set.logical_key, release_set.state, release_set.manifest_checksum
ORDER BY release_set.logical_key;
