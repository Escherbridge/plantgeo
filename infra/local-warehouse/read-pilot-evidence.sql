\set ON_ERROR_STOP on
\pset pager off
\set pilot_release_key 'boise-hillside-hollow-open-v1'
\set forecast_scope_pattern 'nasa-power-ws2m-denver-point-v2:run:%'

BEGIN TRANSACTION READ ONLY;

\echo 'Pilot release and source versions'
SELECT
    release_set.id,
    release_set.logical_key,
    release_set.state,
    release_set.as_of_time,
    release_set.manifest_checksum,
    count(item.source_release_id) AS source_release_count
FROM agri.release_set
LEFT JOIN agri.release_set_item AS item ON item.release_set_id = release_set.id
WHERE release_set.logical_key = :'pilot_release_key'
GROUP BY release_set.id;

SELECT
    source.key AS source_key,
    release.source_version,
    release.retrieved_at,
    release.data_available_at,
    release.observed_from,
    release.observed_to,
    release.payload_bytes,
    release.payload_checksum,
    release.validation_state
FROM agri.release_set AS release_set
JOIN agri.release_set_item AS item ON item.release_set_id = release_set.id
JOIN agri.source_release AS release ON release.id = item.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
WHERE release_set.logical_key = :'pilot_release_key'
ORDER BY source.key, release.source_version;

\echo 'Raw artifact locations and checksums'
SELECT
    source.key AS source_key,
    artifact.kind,
    artifact.uri,
    artifact.media_type,
    artifact.size_bytes,
    artifact.storage_class,
    artifact.checksum_sha256,
    artifact.content_bytes IS NOT NULL AS content_is_inline
FROM agri.release_set AS release_set
JOIN agri.release_set_item AS item ON item.release_set_id = release_set.id
JOIN agri.source_release AS release ON release.id = item.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
JOIN agri.artifact AS artifact ON artifact.source_release_id = release.id
WHERE release_set.logical_key = :'pilot_release_key'
ORDER BY source.key, artifact.kind, artifact.uri;

\echo 'Normalized city/property subjects'
SELECT
    subject.id,
    subject.subject_key,
    subject.subject_version,
    subject.subject_kind,
    subject.display_name,
    subject.spatial_support_kind,
    subject.native_scale,
    subject.maximum_inference_scale,
    subject.confidence,
    subject.confidence_basis,
    GeometryType(subject.geometry) AS geometry_type,
    round(ST_Area(subject.geometry::geography)::numeric, 0) AS geodesic_area_m2,
    ST_AsText(ST_PointOnSurface(subject.geometry)) AS representative_point_wkt
FROM agri.analysis_subject AS subject
WHERE EXISTS (
    SELECT 1
    FROM agri.release_set AS release_set
    JOIN agri.release_set_item AS item ON item.release_set_id = release_set.id
    WHERE release_set.logical_key = :'pilot_release_key'
      AND item.source_release_id = subject.source_release_id
)
ORDER BY subject.subject_kind, subject.display_name;

\echo 'Facts, derived features, and known gaps'
SELECT
    subject.display_name,
    evidence.evidence_kind,
    evidence.metric_name,
    CASE
        WHEN evidence.evidence_kind = 'known_gap' THEN evidence.gap_detail
        ELSE coalesce(
            evidence.numeric_value::text,
            evidence.text_value,
            evidence.boolean_value::text
        )
    END AS value_or_gap,
    evidence.value_unit,
    evidence.spatial_support_kind,
    evidence.native_resolution_m,
    evidence.native_scale,
    evidence.maximum_inference_scale,
    evidence.confidence,
    evidence.confidence_basis,
    evidence.data_available_at,
    evidence.evidence_checksum,
    evidence.is_life_safety_validated
FROM agri.release_set AS release_set
JOIN agri.intervention_evidence_input AS evidence
    ON evidence.release_set_id = release_set.id
JOIN agri.analysis_subject AS subject ON subject.id = evidence.analysis_subject_id
WHERE release_set.logical_key = :'pilot_release_key'
ORDER BY subject.display_name, evidence.evidence_kind, evidence.metric_name;

\echo 'Evidence lineage'
SELECT
    subject.display_name,
    evidence.metric_name,
    lineage.input_order,
    lineage.lineage_role,
    source.key AS source_key,
    release.source_version,
    feature.feature_key,
    lineage.source_record_table,
    lineage.source_record_key
FROM agri.release_set AS release_set
JOIN agri.intervention_evidence_input AS evidence
    ON evidence.release_set_id = release_set.id
JOIN agri.analysis_subject AS subject ON subject.id = evidence.analysis_subject_id
JOIN agri.intervention_evidence_lineage AS lineage
    ON lineage.evidence_input_id = evidence.id
JOIN agri.source_release AS release ON release.id = lineage.source_release_id
JOIN agri.data_source AS source ON source.id = release.data_source_id
LEFT JOIN agri.normalized_source_feature AS feature
    ON feature.id = lineage.source_feature_id
WHERE release_set.logical_key = :'pilot_release_key'
ORDER BY subject.display_name, evidence.metric_name, lineage.input_order;

\echo 'Latest evaluation-only v2 forecast candidate'
WITH latest AS (
    SELECT id
    FROM agri.forecast_run
    WHERE run_key LIKE :'forecast_scope_pattern'
    ORDER BY created_at DESC
    LIMIT 1
)
SELECT
    run.id,
    run.run_key,
    run.status,
    run.issue_time,
    run.quality_summary ->> 'gate_result' AS gate_result,
    run.quality_summary ->> 'evaluation_disposition' AS evaluation_disposition,
    run.quality_summary ->> 'historical_hindcast_pass_count' AS hindcast_pass_count,
    run.quality_summary ->> 'historical_hindcast_run_count' AS hindcast_run_count,
    run.quality_summary ->> 'historical_hindcast_pass_fraction' AS hindcast_pass_fraction,
    run.quality_summary ->> 'historical_hindcast_interval_coverage_fraction'
        AS interval_coverage_fraction,
    metric.mae,
    metric.rmse,
    metric.naive_rmse,
    metric.skill_score,
    metric.mape,
    metric.coverage_fraction,
    metric.passed AS terminal_holdout_passed
FROM latest
JOIN agri.forecast_run AS run ON run.id = latest.id
JOIN agri.forecast_backtest_metric AS metric ON metric.forecast_run_id = run.id;

WITH latest AS (
    SELECT id
    FROM agri.forecast_run
    WHERE run_key LIKE :'forecast_scope_pattern'
    ORDER BY created_at DESC
    LIMIT 1
)
SELECT
    count(DISTINCT hindcast.id) AS hindcast_runs,
    count(DISTINCT hindcast.id) FILTER (WHERE hindcast.quality_passed) AS passing_hindcasts,
    count(value.id) AS forecast_actual_values,
    avg(value.absolute_error) AS aggregate_mae,
    sqrt(avg(value.squared_error)) AS aggregate_rmse,
    sqrt(avg(power(value.naive_value - value.actual_value, 2))) AS aggregate_naive_rmse,
    avg(value.absolute_error / nullif(abs(value.actual_value), 0)) AS aggregate_mape,
    avg(CASE WHEN value.interval_covered THEN 1.0 ELSE 0.0 END) AS interval_coverage
FROM latest
JOIN agri.forecast_hindcast_run AS hindcast ON hindcast.forecast_run_id = latest.id
JOIN agri.forecast_hindcast_value AS value ON value.hindcast_run_id = hindcast.id;

\echo 'Fail-closed candidate publication audit and strategy surface presence'
WITH candidate_runs AS (
    SELECT id, job_run_id
    FROM agri.forecast_run
    WHERE run_key LIKE :'forecast_scope_pattern'
), candidate_receipts AS (
    SELECT receipt.id
    FROM agri.forecast_receipt AS receipt
    JOIN candidate_runs ON candidate_runs.id = receipt.forecast_run_id
), candidate_publications AS (
    SELECT publication.id
    FROM agri.forecast_publication AS publication
    JOIN candidate_runs ON candidate_runs.job_run_id = publication.job_run_id
)
SELECT
    (SELECT count(*) FROM candidate_runs) AS candidate_runs,
    (SELECT count(*) FROM candidate_receipts) AS forecast_receipts,
    (
        SELECT count(*)
        FROM agri.forecast_value AS value
        JOIN candidate_receipts ON candidate_receipts.id = value.forecast_receipt_id
    ) AS forecast_values,
    (
        SELECT count(*)
        FROM agri.forecast_publication_item AS item
        JOIN candidate_publications ON candidate_publications.id = item.publication_id
    ) AS publication_items,
    (SELECT count(*) FROM candidate_publications) AS forecast_publications,
    (
        SELECT count(*)
        FROM agri.publication_pointer
        WHERE product = 'forecast_series'
          AND scope_key = 'nasa-power-ws2m-denver-point-v2'
    ) AS publication_pointers,
    to_regclass('agri.strategy_selection') IS NOT NULL AS strategy_selection_table_exists,
    to_regclass('agri.recommendation') IS NOT NULL AS recommendation_table_exists;

COMMIT;
