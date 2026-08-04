"""Optional PostgreSQL execution test for the SQL forecasting functions."""

import hashlib
import os
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

from agri_data_service.routes.health import (
    _FORECAST_ROLE_CONTRACT_SQL,
    _READINESS_SQL,
    _RECEIVER_ROLE_BOUNDARY_SQL,
)

OBSERVATION_COUNT = 45
ROLLING_WINDOW = 5
HORIZON_STEPS = 10
TRAINING_POINT_COUNT = 30


def _expect_database_error(cursor: psycopg2.extensions.cursor, statement: str, parameters: tuple = ()) -> None:
    cursor.execute("SAVEPOINT expected_database_error")
    with pytest.raises(psycopg2.Error):
        cursor.execute(statement, parameters)
    cursor.execute("ROLLBACK TO SAVEPOINT expected_database_error")


def test_forecast_readiness_sql_reports_missing_role_gate_without_error(agri_db_dsn: str) -> None:
    connection = psycopg2.connect(agri_db_dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(_READINESS_SQL)
            columns = [column.name for column in cursor.description]
            readiness = dict(zip(columns, cursor.fetchone(), strict=True))

            assert readiness["forecast_roles_ready"] is False
            cursor.execute(_FORECAST_ROLE_CONTRACT_SQL)
            assert cursor.fetchone()[0] is False
            cursor.execute(_RECEIVER_ROLE_BOUNDARY_SQL)
            assert cursor.fetchone()[0] is False
    finally:
        connection.close()


def test_postgresql_forecasting_functions_in_rolled_back_fixture(  # noqa: PLR0915
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    connection = agri_db_connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT (SELECT count(*) FROM agri.signal_observation), "
            "       (SELECT count(*) FROM agri.drought_polygon_snapshot)"
        )
        historical_counts = cursor.fetchone()

        suffix = hashlib.sha256(os.urandom(16)).hexdigest()[:12]
        checksum = hashlib.sha256(suffix.encode()).hexdigest()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        as_of = start + timedelta(days=60)

        cursor.execute(
            """
            INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
            VALUES (%s, 'Forecast SQL test', 'PlantGeo test', 'rolled-back validation', 'test-only', 'test-only')
            RETURNING id
            """,
            (f"forecast-sql-{suffix}",),
        )
        data_source_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.source_release(
                data_source_id, source_version, retrieved_at, data_available_at,
                observed_from, observed_to, payload_checksum, schema_version,
                transform_version, license_snapshot, validation_state, validated_at
            )
            VALUES (%s, 'fixture-v1', %s, %s, %s, %s, %s, 'fixture-v1',
                    'fixture-transform-v1', 'test-only', 'valid', %s)
            RETURNING id
            """,
            (data_source_id, as_of, as_of, start, start + timedelta(days=44), checksum, as_of),
        )
        source_release_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state)
            VALUES (%s, %s, %s, 'draft')
            RETURNING id
            """,
            (f"forecast-sql-{suffix}", as_of, hashlib.sha256((suffix + "-set").encode()).hexdigest()),
        )
        release_set_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO agri.release_set_item(release_set_id, source_release_id) VALUES (%s, %s)",
            (release_set_id, source_release_id),
        )
        cursor.execute(
            "UPDATE agri.release_set SET state = 'validated', validated_at = %s WHERE id = %s",
            (as_of, release_set_id),
        )
        cursor.execute(
            """
            INSERT INTO agri.forecast_series(
                series_key, source_variant_key, input_adapter, data_source_id,
                source_transform_version, entity_type, entity_key, metric_name,
                metric_unit, representation_kind, spatial_support_kind,
                source_temporal_support, output_temporal_support
            )
            VALUES (%s, 'fixture-v1', 'forecast_observation', %s,
                    'fixture-transform-v1', 'test_object', 'object-1', 'linear_metric',
                    'unit', 'raw_native', 'point_sample', interval '1 day', interval '1 day')
            RETURNING id
            """,
            (f"fixture-series-{suffix}", data_source_id),
        )
        series_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_entity_state(
                series_id, source_release_id, state_key, valid_from, valid_to,
                data_available_at, state_checksum, state_payload
            )
            VALUES (%s, %s, 'fixture-state-v1', %s, %s, %s, %s, '{"phase":"observed"}'::jsonb)
            RETURNING id
            """,
            (
                series_id,
                source_release_id,
                start,
                start + timedelta(days=OBSERVATION_COUNT),
                as_of,
                hashlib.sha256((suffix + "-state").encode()).hexdigest(),
            ),
        )
        entity_state_id = cursor.fetchone()[0]

        observation_rows = []
        for day in range(OBSERVATION_COUNT):
            observed_at = start + timedelta(days=day)
            value = 5.0 + 2.0 * day
            row_checksum = hashlib.sha256(f"{suffix}:{day}:{value}".encode()).hexdigest()
            observation_rows.append(
                (
                    series_id,
                    entity_state_id,
                    source_release_id,
                    observed_at,
                    as_of,
                    value,
                    f"event-{day}",
                    row_checksum,
                )
            )
        cursor.executemany(
            """
            INSERT INTO agri.forecast_observation(
                series_id, entity_state_id, source_release_id, observed_at, data_available_at,
                metric_value, source_event_key, observation_checksum
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            observation_rows,
        )

        cursor.execute(
            "SELECT count(*) FROM agri.forecast_timeseries_base(%s, %s) WHERE series_id = %s",
            (release_set_id, as_of, series_id),
        )
        assert cursor.fetchone()[0] == OBSERVATION_COUNT

        cursor.execute(
            "SELECT agri.forecast_percentile(%s, %s, %s, %s, %s, 0.5)",
            (series_id, release_set_id, as_of, start, start + timedelta(days=45)),
        )
        assert cursor.fetchone()[0] == pytest.approx(49.0)

        cursor.execute(
            """
            SELECT sample_count, rolling_mean, rolling_p50
            FROM agri.forecast_rolling_stats(%s, %s, %s, 5)
            ORDER BY observed_at DESC LIMIT 1
            """,
            (series_id, release_set_id, as_of),
        )
        sample_count, rolling_mean, rolling_p50 = cursor.fetchone()
        assert sample_count == ROLLING_WINDOW
        assert rolling_mean == pytest.approx(89.0)
        assert rolling_p50 == pytest.approx(89.0)

        cutoff = start + timedelta(days=29)
        cursor.execute(
            """
            SELECT horizon_step, forecast_value, training_point_count, eligible, gate_reason
            FROM agri.forecast_linear_regression(%s, %s, %s, %s, 10, interval '1 day', 30)
            ORDER BY horizon_step
            """,
            (series_id, release_set_id, as_of, cutoff),
        )
        regression = cursor.fetchall()
        assert len(regression) == HORIZON_STEPS
        assert regression[0][1] == pytest.approx(65.0)
        assert regression[-1][1] == pytest.approx(83.0)
        assert all(row[2:] == (TRAINING_POINT_COUNT, True, "passed") for row in regression)

        cursor.execute(
            """
            SELECT training_point_count, backtest_point_count, mae, rmse, bias,
                   coverage_fraction, eligible, gate_reason
            FROM agri.forecast_linear_backtest(%s, %s, %s, %s, 10, interval '1 day', 30)
            """,
            (series_id, release_set_id, as_of, cutoff),
        )
        backtest = cursor.fetchone()
        assert backtest[:2] == (TRAINING_POINT_COUNT, HORIZON_STEPS)
        assert backtest[2] == pytest.approx(0.0, abs=1e-9)
        assert backtest[3] == pytest.approx(0.0, abs=1e-9)
        assert backtest[4] == pytest.approx(0.0, abs=1e-9)
        assert backtest[5:] == (1.0, True, "passed")

        release_checksum = hashlib.sha256((suffix + "-set").encode()).hexdigest()
        feature_checksum = hashlib.sha256((suffix + "-features").encode()).hexdigest()
        code_checksum = hashlib.sha256((suffix + "-code").encode()).hexdigest()
        parameter_checksum = hashlib.sha256((suffix + "-parameters").encode()).hexdigest()
        metrics_checksum = hashlib.sha256((suffix + "-metrics").encode()).hexdigest()
        cursor.execute(
            """
            INSERT INTO agri.job_definition(name, version, handler)
            VALUES (%s, 'v1', 'forecast-test') RETURNING id
            """,
            (f"forecast-test-{suffix}",),
        )
        job_definition_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.job_run(
                job_definition_id, release_set_id, logical_run_key, scheduled_for,
                status, total_work_items, succeeded_work_items, completed_at
            )
            VALUES (%s, %s, %s, %s, 'succeeded', 1, 1, %s) RETURNING id
            """,
            (job_definition_id, release_set_id, f"forecast-test-{suffix}", as_of, as_of),
        )
        job_run_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_feature_snapshot(
                snapshot_key, job_run_id, release_set_id, input_release_checksum,
                feature_recipe_version, feature_code_checksum, feature_checksum,
                training_window_start, training_window_end, row_count
            )
            VALUES (%s, %s, %s, %s, 'fixture-v1', %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                f"feature-snapshot-{suffix}",
                job_run_id,
                release_set_id,
                release_checksum,
                code_checksum,
                feature_checksum,
                start,
                cutoff,
                TRAINING_POINT_COUNT,
            ),
        )
        snapshot_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_feature_snapshot(
                snapshot_key, job_run_id, release_set_id, input_release_checksum,
                feature_recipe_version, feature_code_checksum, feature_checksum,
                training_window_start, training_window_end, row_count
            )
            VALUES (%s, %s, %s, %s, 'fixture-v1', %s, %s, %s, %s, 1)
            RETURNING id
            """,
            (
                f"invalid-feature-snapshot-{suffix}",
                job_run_id,
                release_set_id,
                hashlib.sha256((suffix + "-wrong-release").encode()).hexdigest(),
                code_checksum,
                feature_checksum,
                start,
                cutoff,
            ),
        )
        invalid_snapshot_id = cursor.fetchone()[0]
        _expect_database_error(
            cursor,
            "SELECT agri.validate_forecast_feature_snapshot(%s)",
            (invalid_snapshot_id,),
        )
        cursor.execute("SELECT (agri.validate_forecast_feature_snapshot(%s)).status", (snapshot_id,))
        assert cursor.fetchone()[0] == "validated"
        cursor.execute(
            """
            SELECT
                agri.forecast_quantiles_valid(ARRAY[0.1, 0.5, 0.9]::float8[]),
                agri.forecast_quantiles_valid(ARRAY[0.1, NULL, 0.5]::float8[]),
                agri.forecast_quantiles_valid(ARRAY[0.1, 0.5, 0.5]::float8[])
            """
        )
        assert cursor.fetchone() == (True, False, False)

        cursor.execute(
            """
            INSERT INTO agri.forecast_model(
                model_key, model_version, model_kind, model_purpose,
                algorithm, model_code_checksum
            )
            VALUES (%s, 'v1', 'sql_linear', 'metric_forecast', 'regr_slope', %s)
            RETURNING id
            """,
            (f"sql-linear-{suffix}", code_checksum),
        )
        model_id = cursor.fetchone()[0]
        model_artifact_checksum = hashlib.sha256((suffix + "-ml-model").encode()).hexdigest()
        validation_checksum = hashlib.sha256((suffix + "-ml-validation").encode()).hexdigest()
        training_code_checksum = hashlib.sha256((suffix + "-training-code").encode()).hexdigest()
        cursor.execute(
            """
            INSERT INTO agri.artifact(kind, uri, checksum_sha256, size_bytes)
            VALUES ('forecast_model', %s, %s, 0) RETURNING id
            """,
            (f"test://forecast-model/{suffix}", model_artifact_checksum),
        )
        model_artifact_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_model(
                model_key, model_version, model_kind, model_purpose,
                algorithm, model_code_checksum, artifact_id
            )
            VALUES (%s, 'v1', 'ml', 'metric_forecast', 'test-only-local-ml', %s, %s)
            RETURNING id
            """,
            (f"local-ml-{suffix}", code_checksum, model_artifact_id),
        )
        ml_model_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.job_output(
                job_run_id, artifact_id, output_key, kind, state, checksum_sha256,
                row_count, metadata_json, validated_at
            )
            VALUES (%s, %s, %s, 'model_training', 'validated', %s, 1,
                    jsonb_build_object('validation_checksum', %s), %s)
            RETURNING id
            """,
            (
                job_run_id,
                model_artifact_id,
                f"ml-training-{suffix}",
                model_artifact_checksum,
                validation_checksum,
                as_of,
            ),
        )
        training_output_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_training_run(
                training_key, model_id, job_run_id, job_output_id, feature_snapshot_id,
                input_release_checksum, feature_checksum, training_code_checksum
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            (
                f"ml-training-{suffix}",
                ml_model_id,
                job_run_id,
                training_output_id,
                snapshot_id,
                release_checksum,
                feature_checksum,
                training_code_checksum,
            ),
        )
        training_run_id = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT (agri.validate_forecast_training_run(%s, %s, %s, '{}'::jsonb)).status
            """,
            (training_run_id, model_artifact_checksum, validation_checksum),
        )
        assert cursor.fetchone()[0] == "validated"
        cursor.execute(
            """
            INSERT INTO agri.forecast_quality_policy(
                policy_key, min_training_points, min_backtest_points,
                min_coverage_fraction, max_mae, max_rmse, min_skill_score
            )
            VALUES (%s, %s, %s, 1, 0.001, 0.001, 0.9) RETURNING id
            """,
            (f"strict-linear-{suffix}", TRAINING_POINT_COUNT, HORIZON_STEPS),
        )
        policy_id = cursor.fetchone()[0]
        issue_time = as_of
        run_valid_from = issue_time + timedelta(days=1)
        run_valid_to = issue_time + timedelta(days=HORIZON_STEPS + 1)
        cursor.execute(
            """
            INSERT INTO agri.forecast_run(
                run_key, job_run_id, feature_snapshot_id, model_id, quality_policy_id,
                forecast_method, issue_time, valid_from, valid_to, horizon_steps,
                step_interval, input_release_checksum, feature_checksum,
                model_checksum, parameter_checksum
            )
            VALUES (%s, %s, %s, %s, %s, 'sql_linear', %s, %s, %s, %s,
                    interval '1 day', %s, %s, %s, %s) RETURNING id
            """,
            (
                f"forecast-run-{suffix}",
                job_run_id,
                snapshot_id,
                model_id,
                policy_id,
                issue_time,
                run_valid_from,
                run_valid_to,
                HORIZON_STEPS,
                release_checksum,
                feature_checksum,
                code_checksum,
                parameter_checksum,
            ),
        )
        forecast_run_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.job_output(
                job_run_id, output_key, kind, state, checksum_sha256,
                row_count, validated_at
            )
            VALUES (%s, %s, 'forecast_backtest', 'validated', %s, 1, %s)
            RETURNING id
            """,
            (job_run_id, f"backtest-{suffix}", metrics_checksum, as_of),
        )
        backtest_output_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_backtest_metric(
                forecast_run_id, job_output_id, series_id, cutoff_time,
                training_point_count, backtest_point_count, mae, rmse,
                naive_rmse, skill_score, bias, mape, coverage_fraction, metrics_checksum
            )
            VALUES (%s, %s, %s, %s, %s, %s, 0, 0, 10, 1, 0, 0, 1, %s)
            """,
            (
                forecast_run_id,
                backtest_output_id,
                series_id,
                cutoff,
                TRAINING_POINT_COUNT,
                HORIZON_STEPS,
                metrics_checksum,
            ),
        )
        cursor.execute("SELECT (agri.validate_forecast_run(%s)).status", (forecast_run_id,))
        assert cursor.fetchone()[0] == "validated"

        cursor.execute(
            """
            SELECT encode(digest(string_agg(
                concat_ws('|',
                    to_char(value.valid_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    value.horizon_step::text, value.point_value::text,
                    value.p10_value::text, value.p50_value::text, value.p90_value::text,
                    value.quantile_values::text
                ), E'\\n' ORDER BY value.horizon_step), 'sha256'), 'hex')
            FROM (
                SELECT
                    step AS horizon_step,
                    %s::timestamptz + interval '1 day' * step AS valid_time,
                    (100 + step)::float8 AS point_value,
                    (99 + step)::float8 AS p10_value,
                    (100 + step)::float8 AS p50_value,
                    (101 + step)::float8 AS p90_value,
                    jsonb_build_object(
                        '0.1', (99 + step)::float8,
                        '0.5', (100 + step)::float8,
                        '0.9', (101 + step)::float8
                    ) AS quantile_values
                FROM generate_series(1, %s) AS step
            ) AS value
            """,
            (issue_time, HORIZON_STEPS),
        )
        receipt_checksum = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.job_output(
                job_run_id, output_key, kind, state, checksum_sha256,
                row_count, validated_at
            )
            VALUES (%s, %s, 'forecast_values', 'validated', %s, %s, %s)
            RETURNING id
            """,
            (job_run_id, f"forecast-values-{suffix}", receipt_checksum, HORIZON_STEPS, as_of),
        )
        receipt_output_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_receipt(
                forecast_run_id, job_output_id, series_id, issue_time,
                valid_from, valid_to, expected_value_count, quantile_levels
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, ARRAY[0.1, 0.5, 0.9]::float8[])
            RETURNING id
            """,
            (
                forecast_run_id,
                receipt_output_id,
                series_id,
                issue_time,
                run_valid_from,
                run_valid_to,
                HORIZON_STEPS,
            ),
        )
        receipt_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_value(
                forecast_receipt_id, valid_time, horizon_step,
                point_value, p10_value, p50_value, p90_value, quantile_values
            )
            SELECT
                %s, %s::timestamptz + interval '1 day' * step, step,
                (100 + step)::float8, (99 + step)::float8,
                (100 + step)::float8, (101 + step)::float8,
                jsonb_build_object(
                    '0.1', (99 + step)::float8,
                    '0.5', (100 + step)::float8,
                    '0.9', (101 + step)::float8
                )
            FROM generate_series(1, %s) AS step
            """,
            (receipt_id, issue_time, HORIZON_STEPS),
        )
        # `forecast_receipt_finalized_verify` called `agri.finalize_forecast_receipt`, which
        # raised on a NULL expected checksum; 20260803_0018 dropped both and made
        # `ck_forecast_receipt_finalized_evidence` NULL-safe so it carries the invariant alone.
        # Before that repair the CHECK evaluated to NULL under a NULL receipt_checksum -- and a
        # CHECK rejects only FALSE -- so a digest-less receipt could be finalized and
        # `v_forecast_series_serving`, which trusts `status = 'finalized'`, would emit it.
        _expect_database_error(
            cursor,
            """
            UPDATE agri.forecast_receipt SET status = 'finalized', finalized_at = %s
            WHERE id = %s
            """,
            (as_of, receipt_id),
        )

        # What the CHECK does still reject: a finalized receipt whose digest is not SHA-256.
        _expect_database_error(
            cursor,
            """
            UPDATE agri.forecast_receipt
            SET status = 'finalized', receipt_checksum = 'not-a-digest', finalized_at = %s
            WHERE id = %s
            """,
            (as_of, receipt_id),
        )
        cursor.execute(
            """
            UPDATE agri.forecast_receipt
            SET status = 'finalized', receipt_checksum = %s, finalized_at = %s
            WHERE id = %s
            RETURNING status
            """,
            (receipt_checksum, as_of, receipt_id),
        )
        assert cursor.fetchone()[0] == "finalized"

        publication_checksum = hashlib.sha256(receipt_checksum.encode()).hexdigest()
        cursor.execute(
            """
            INSERT INTO agri.job_output(
                job_run_id, output_key, kind, state, checksum_sha256,
                row_count, validated_at
            )
            VALUES (%s, %s, 'forecast_publication', 'validated', %s, 1, %s)
            RETURNING id
            """,
            (job_run_id, f"publication-{suffix}", publication_checksum, as_of),
        )
        publication_output_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_publication(
                publication_key, job_run_id, job_output_id, release_set_id, scope_key
            )
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (
                f"forecast-publication-{suffix}",
                job_run_id,
                publication_output_id,
                release_set_id,
                f"fixture-scope-{suffix}",
            ),
        )
        publication_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_publication_item(publication_id, forecast_receipt_id)
            VALUES (%s, %s)
            """,
            (publication_id, receipt_id),
        )
        cursor.execute(
            "SELECT (agri.publish_forecast_publication(%s, %s)).state",
            (publication_id, publication_checksum),
        )
        assert cursor.fetchone()[0] == "published"
        cursor.execute(
            """
            INSERT INTO agri.publication_pointer(
                product, scope_key, job_output_id, release_set_id,
                revision, published_by
            )
            VALUES ('forecast_series', %s, %s, %s, 1, 'forecast-sql-test')
            """,
            (f"fixture-scope-{suffix}", publication_output_id, release_set_id),
        )
        cursor.execute(
            "SELECT count(*) FROM agri.v_forecast_series_serving WHERE forecast_receipt_id = %s",
            (receipt_id,),
        )
        assert cursor.fetchone()[0] == HORIZON_STEPS
        cursor.execute("SELECT agri.refresh_forecast_ml_daily_serving()")
        cursor.execute("SELECT count(*) FROM agri.mv_forecast_ml_daily_serving")
        assert cursor.fetchone()[0] == 0

        cursor.execute(
            "SELECT (SELECT count(*) FROM agri.signal_observation), "
            "       (SELECT count(*) FROM agri.drought_polygon_snapshot)"
        )
        assert cursor.fetchone() == historical_counts
