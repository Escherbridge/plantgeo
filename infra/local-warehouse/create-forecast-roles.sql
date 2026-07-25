\set ON_ERROR_STOP on

BEGIN;

DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'plantgeo_forecast_writer',
        'plantgeo_forecast_publisher',
        'plantgeo_forecast_reader',
        'plantgeo_forecast_mv_refresher'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            RAISE EXCEPTION '% already exists; inspect it before applying the reviewed role gate', role_name;
        END IF;
        EXECUTE format(
            'CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT',
            role_name
        );
    END LOOP;
END
$$;

REVOKE ALL PRIVILEGES ON DATABASE plantgeo FROM
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;
GRANT CONNECT ON DATABASE plantgeo TO
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;

REVOKE ALL PRIVILEGES ON SCHEMA agri FROM
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;
GRANT USAGE ON SCHEMA agri TO
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri FROM
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA agri FROM
    plantgeo_forecast_writer,
    plantgeo_forecast_publisher,
    plantgeo_forecast_reader,
    plantgeo_forecast_mv_refresher;

GRANT SELECT ON TABLE
    agri.data_source,
    agri.source_release,
    agri.release_set,
    agri.release_set_item,
    agri.spatial_cell,
    agri.cell_source_crosswalk,
    agri.signal_observation,
    agri.signal_coverage_audit,
    agri.source_coverage_audit,
    agri.drought_polygon_snapshot,
    agri.job_definition,
    agri.job_run,
    agri.job_output
TO plantgeo_forecast_writer;
GRANT SELECT, INSERT ON TABLE
    agri.forecast_series,
    agri.forecast_entity_state,
    agri.forecast_observation,
    agri.forecast_feature_snapshot,
    agri.forecast_model,
    agri.forecast_training_run,
    agri.forecast_quality_policy,
    agri.forecast_run,
    agri.forecast_backtest_metric,
    agri.forecast_receipt,
    agri.forecast_value,
    agri.forecast_hindcast_run,
    agri.forecast_hindcast_value
TO plantgeo_forecast_writer;
GRANT INSERT ON TABLE
    agri.job_definition,
    agri.job_run,
    agri.job_output
TO plantgeo_forecast_writer;
GRANT USAGE, SELECT ON SEQUENCE
    agri.forecast_observation_id_seq,
    agri.forecast_value_id_seq,
    agri.forecast_hindcast_value_id_seq
TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_quantiles_valid(double precision[])
TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.v_signal_timeseries_contract(timestamptz, uuid)
TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_timeseries_base(uuid, timestamptz)
TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_percentile(
    uuid, uuid, timestamptz, timestamptz, timestamptz, double precision
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_normalized_series(
    uuid, uuid, timestamptz, timestamptz, timestamptz, interval
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_rolling_stats(uuid, uuid, timestamptz, integer)
TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_linear_regression(
    uuid, uuid, timestamptz, timestamptz, integer, interval, integer
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_linear_backtest(
    uuid, uuid, timestamptz, timestamptz, integer, interval, integer
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_linear_residual_bands(
    uuid, uuid, timestamptz, timestamptz, integer, interval, integer
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_hindcast_value_checksum(
    timestamptz, integer, double precision, double precision, double precision,
    double precision, double precision, double precision, uuid, varchar
) TO plantgeo_forecast_writer;
GRANT EXECUTE ON FUNCTION agri.forecast_hindcast_receipt_checksum(uuid)
TO plantgeo_forecast_writer;

GRANT SELECT ON TABLE
    agri.artifact,
    agri.data_source,
    agri.source_release,
    agri.release_set,
    agri.release_set_item,
    agri.spatial_cell,
    agri.cell_source_crosswalk,
    agri.signal_observation,
    agri.signal_coverage_audit,
    agri.source_coverage_audit,
    agri.drought_polygon_snapshot,
    agri.job_definition,
    agri.job_run,
    agri.job_output,
    agri.forecast_series,
    agri.forecast_entity_state,
    agri.forecast_observation,
    agri.forecast_feature_snapshot,
    agri.forecast_model,
    agri.forecast_training_run,
    agri.forecast_quality_policy,
    agri.forecast_run,
    agri.forecast_backtest_metric,
    agri.forecast_receipt,
    agri.forecast_value,
    agri.forecast_hindcast_run,
    agri.forecast_hindcast_value,
    agri.forecast_publication,
    agri.forecast_publication_item,
    agri.publication_pointer
TO plantgeo_forecast_publisher;
GRANT INSERT ON TABLE
    agri.job_output,
    agri.forecast_publication,
    agri.forecast_publication_item,
    agri.publication_pointer
TO plantgeo_forecast_publisher;
GRANT UPDATE (state, validated_at) ON TABLE agri.job_output
TO plantgeo_forecast_publisher;
GRANT UPDATE (status, validation_metrics, validated_at)
ON TABLE agri.forecast_feature_snapshot TO plantgeo_forecast_publisher;
GRANT UPDATE (
    status, model_checksum, validation_checksum, validation_metrics,
    completed_at, validated_at
) ON TABLE agri.forecast_training_run TO plantgeo_forecast_publisher;
GRANT UPDATE (status, backtest_passed, validated_at)
ON TABLE agri.forecast_run TO plantgeo_forecast_publisher;
GRANT UPDATE (passed) ON TABLE agri.forecast_backtest_metric
TO plantgeo_forecast_publisher;
GRANT UPDATE (
    status, quality_passed, mae, rmse, naive_rmse, skill_score, bias, mape,
    coverage_fraction, interval_coverage_fraction, receipt_checksum,
    recorded_at, finalized_at
) ON TABLE agri.forecast_hindcast_run TO plantgeo_forecast_publisher;
GRANT UPDATE (status, receipt_checksum, finalized_at)
ON TABLE agri.forecast_receipt TO plantgeo_forecast_publisher;
GRANT UPDATE (state, manifest_checksum, published_at)
ON TABLE agri.forecast_publication TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.validate_forecast_feature_snapshot(uuid)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.validate_forecast_training_run(uuid, varchar, varchar, jsonb)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.validate_forecast_run(uuid)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.finalize_forecast_receipt(uuid, varchar)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.finalize_forecast_hindcast_run(uuid, varchar)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.forecast_hindcast_receipt_checksum(uuid)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.v_signal_timeseries_contract(timestamptz, uuid)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.forecast_timeseries_base(uuid, timestamptz)
TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.forecast_linear_regression(
    uuid, uuid, timestamptz, timestamptz, integer, interval, integer
) TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.forecast_linear_residual_bands(
    uuid, uuid, timestamptz, timestamptz, integer, interval, integer
) TO plantgeo_forecast_publisher;
GRANT EXECUTE ON FUNCTION agri.publish_forecast_publication(uuid, varchar)
TO plantgeo_forecast_publisher;

GRANT SELECT ON TABLE
    agri.v_forecast_series_serving,
    agri.v_forecast_hindcast_outcome,
    agri.mv_forecast_ml_daily_serving,
    agri.spatial_cell
TO plantgeo_forecast_reader;
GRANT EXECUTE ON FUNCTION agri.forecast_hindcast_signal_timeseries(
    uuid, uuid, varchar, integer, timestamptz
) TO plantgeo_forecast_reader;

GRANT SELECT ON TABLE agri.mv_forecast_ml_daily_serving
TO plantgeo_forecast_mv_refresher;
GRANT EXECUTE ON FUNCTION agri.refresh_forecast_ml_daily_serving()
TO plantgeo_forecast_mv_refresher;

COMMIT;

SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls,
       rolinherit
FROM pg_roles
WHERE rolname IN (
    'plantgeo_forecast_writer',
    'plantgeo_forecast_publisher',
    'plantgeo_forecast_reader',
    'plantgeo_forecast_mv_refresher'
)
ORDER BY rolname;
