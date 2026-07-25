"""Optional two-phase proof that 0008 preserves a structural finalized v1 row."""

import hashlib
import os
from datetime import UTC, datetime

import psycopg2
import pytest

DATABASE_URL = os.getenv("FORECAST_V1_UPGRADE_DATABASE_URL")
PHASE = os.getenv("FORECAST_V1_UPGRADE_PHASE")
DATABASE_PREFIX = "plantgeo_forecast_test_upgrade_"
HINDCAST_ID = "00000000-0000-4000-8000-00000000000b"
MIN_SUPPORTED_SERVER_VERSION_NUM = 160000
MAX_SUPPORTED_SERVER_VERSION_NUM = 190000


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _connection() -> psycopg2.extensions.connection:
    assert DATABASE_URL is not None
    connection = psycopg2.connect(DATABASE_URL)
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_setting('server_version_num')::integer")
        database_name, server_version_num = cursor.fetchone()
    assert database_name.startswith(DATABASE_PREFIX)
    assert MIN_SUPPORTED_SERVER_VERSION_NUM <= server_version_num < MAX_SUPPORTED_SERVER_VERSION_NUM
    return connection


def _expected_v1_checksum(cursor: psycopg2.extensions.cursor) -> str:
    cursor.execute(
        """
        SELECT
            run.hindcast_key,
            run.simulated_cutoff_time,
            run.uncertainty_calibration_cutoff_time,
            run.availability_mode,
            run.input_release_checksum,
            run.model_checksum,
            run.parameter_checksum,
            value.value_checksum
        FROM agri.forecast_hindcast_run AS run
        INNER JOIN agri.forecast_hindcast_value AS value ON value.hindcast_run_id = run.id
        WHERE run.id = %s
        ORDER BY value.horizon_step
        """,
        (HINDCAST_ID,),
    )
    rows = cursor.fetchall()
    assert len(rows) == 1
    first = rows[0]
    payload = "|".join(
        (
            first[0],
            _canonical_datetime(first[1]),
            _canonical_datetime(first[2]),
            first[3],
            first[4],
            first[5],
            first[6],
            "\n".join(row[7] for row in rows),
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@pytest.mark.skipif(
    not DATABASE_URL or PHASE != "seed",
    reason="set the guarded v1 upgrade database and seed phase",
)
def test_seed_structural_finalized_v1_row_at_revision_0007() -> None:
    with _connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM public.alembic_version")
        assert cursor.fetchone()[0] == "20260722_0007"
        cursor.execute(
            """
            INSERT INTO agri.data_source(
                id, key, name, owner, purpose, license_name, citation
            ) VALUES (
                '00000000-0000-4000-8000-000000000001', 'v1-upgrade-source',
                'V1 upgrade source', 'PlantGeo test', 'Migration preservation proof',
                'test-only', 'test-only'
            );
            INSERT INTO agri.source_release(
                id, data_source_id, source_version, retrieved_at, data_available_at,
                payload_checksum, schema_version, license_snapshot
            ) VALUES (
                '00000000-0000-4000-8000-000000000002',
                '00000000-0000-4000-8000-000000000001', 'v1',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', repeat('2', 64),
                'v1', 'test-only'
            );
            INSERT INTO agri.release_set(
                id, logical_key, as_of_time, manifest_checksum
            ) VALUES (
                '00000000-0000-4000-8000-000000000003', 'v1-upgrade-release',
                '2026-01-01T00:00:00Z', repeat('3', 64)
            );
            INSERT INTO agri.job_definition(
                id, name, version, handler
            ) VALUES (
                '00000000-0000-4000-8000-000000000004', 'v1-upgrade-job', 'v1',
                'tests.v1_upgrade'
            );
            INSERT INTO agri.job_run(
                id, job_definition_id, release_set_id, logical_run_key, scheduled_for
            ) VALUES (
                '00000000-0000-4000-8000-000000000005',
                '00000000-0000-4000-8000-000000000004',
                '00000000-0000-4000-8000-000000000003', 'v1-upgrade-run',
                '2026-01-10T00:00:00Z'
            );
            INSERT INTO agri.forecast_series(
                id, series_key, source_variant_key, input_adapter, data_source_id,
                source_transform_version, entity_type, entity_key, metric_name,
                metric_unit, representation_kind, spatial_support_kind,
                source_temporal_support, output_temporal_support
            ) VALUES (
                '00000000-0000-4000-8000-000000000006', 'v1-upgrade-series',
                'v1-upgrade-variant', 'forecast_observation',
                '00000000-0000-4000-8000-000000000001', 'v1', 'test', 'test-1',
                'test_metric', 'unit', 'raw_native', 'point_sample', interval '1 day',
                interval '1 day'
            );
            INSERT INTO agri.forecast_feature_snapshot(
                id, snapshot_key, job_run_id, release_set_id, input_release_checksum,
                feature_recipe_version, feature_code_checksum, feature_checksum,
                training_window_start, training_window_end, row_count
            ) VALUES (
                '00000000-0000-4000-8000-000000000007', 'v1-upgrade-snapshot',
                '00000000-0000-4000-8000-000000000005',
                '00000000-0000-4000-8000-000000000003', repeat('3', 64), 'v1',
                repeat('4', 64), repeat('5', 64), '2026-01-01T00:00:00Z',
                '2026-01-09T00:00:00Z', 9
            );
            INSERT INTO agri.forecast_model(
                id, model_key, model_version, model_kind, algorithm, model_code_checksum
            ) VALUES (
                '00000000-0000-4000-8000-000000000008', 'v1-upgrade-model', 'v1',
                'sql_linear', 'sql_linear_regression', repeat('6', 64)
            );
            INSERT INTO agri.forecast_quality_policy(
                id, policy_key, min_training_points, min_backtest_points,
                min_coverage_fraction
            ) VALUES (
                '00000000-0000-4000-8000-000000000009', 'v1-upgrade-policy', 3, 1, 1
            );
            INSERT INTO agri.forecast_run(
                id, run_key, job_run_id, feature_snapshot_id, model_id,
                quality_policy_id, forecast_method, issue_time, valid_from, valid_to,
                horizon_steps, step_interval, input_release_checksum, feature_checksum,
                model_checksum, parameter_checksum
            ) VALUES (
                '00000000-0000-4000-8000-00000000000a', 'v1-upgrade-forecast',
                '00000000-0000-4000-8000-000000000005',
                '00000000-0000-4000-8000-000000000007',
                '00000000-0000-4000-8000-000000000008',
                '00000000-0000-4000-8000-000000000009', 'sql_linear',
                '2026-01-10T00:00:00Z', '2026-01-11T00:00:00Z',
                '2026-01-12T00:00:00Z', 1, interval '1 day', repeat('3', 64),
                repeat('5', 64), repeat('6', 64), repeat('7', 64)
            );
            INSERT INTO agri.forecast_hindcast_run(
                id, hindcast_key, forecast_run_id, series_id, release_set_id,
                simulated_cutoff_time, uncertainty_calibration_cutoff_time,
                horizon_steps, step_interval, minimum_training_points,
                training_point_count, expected_value_count, availability_mode,
                input_release_checksum, model_checksum, parameter_checksum
            ) VALUES (
                '00000000-0000-4000-8000-00000000000b', 'v1-upgrade-hindcast',
                '00000000-0000-4000-8000-00000000000a',
                '00000000-0000-4000-8000-000000000006',
                '00000000-0000-4000-8000-000000000003',
                '2026-01-10T00:00:00Z', '2026-01-09T00:00:00Z', 1,
                interval '1 day', 3, 3, 1, 'retrospective_pinned_release',
                repeat('3', 64), repeat('6', 64), repeat('7', 64)
            );
            INSERT INTO agri.forecast_hindcast_value(
                hindcast_run_id, valid_time, horizon_step, point_value, p10_value,
                p50_value, p90_value, naive_value, actual_value,
                actual_source_release_id, actual_observation_checksum,
                actual_data_available_at
            ) VALUES (
                '00000000-0000-4000-8000-00000000000b', '2026-01-11T00:00:00Z',
                1, 10, 9, 10, 11, 8, 10,
                '00000000-0000-4000-8000-000000000002', repeat('8', 64),
                '2026-01-12T00:00:00Z'
            )
            """
        )
        expected_checksum = _expected_v1_checksum(cursor)
        cursor.execute("SELECT agri.forecast_hindcast_receipt_checksum(%s)", (HINDCAST_ID,))
        assert cursor.fetchone()[0] == expected_checksum
        # This fixture tests migration backfill/checksum compatibility, not finalizer governance.
        cursor.execute("SET LOCAL session_replication_role = replica")
        cursor.execute(
            """
            UPDATE agri.forecast_hindcast_run
            SET status = 'finalized', quality_passed = true, mae = 0, rmse = 0,
                naive_rmse = 2, skill_score = 1, bias = 0, mape = 0,
                coverage_fraction = 1, interval_coverage_fraction = 1,
                receipt_checksum = %s, recorded_at = '2026-01-12T00:00:00Z',
                finalized_at = '2026-01-12T00:00:00Z'
            WHERE id = %s
            """,
            (expected_checksum, HINDCAST_ID),
        )
        assert cursor.rowcount == 1
        cursor.execute("SET LOCAL session_replication_role = origin")


@pytest.mark.skipif(
    not DATABASE_URL or PHASE != "verify",
    reason="set the guarded v1 upgrade database and verify phase",
)
def test_revision_0008_preserves_the_structural_finalized_v1_row() -> None:
    with _connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM public.alembic_version")
        assert cursor.fetchone()[0] == "20260722_0008"
        expected_checksum = _expected_v1_checksum(cursor)
        cursor.execute(
            """
            SELECT receipt_digest_version, receipt_checksum,
                   agri.forecast_hindcast_receipt_checksum(id)
            FROM agri.forecast_hindcast_run
            WHERE id = %s AND status = 'finalized'
            """,
            (HINDCAST_ID,),
        )
        digest_version, stored_checksum, recomputed_checksum = cursor.fetchone()
        assert digest_version == "hindcast_v1"
        assert stored_checksum == expected_checksum
        assert recomputed_checksum == expected_checksum
