"""Optional PostgreSQL proof for deterministic forecast iterations and realizations."""

import hashlib
import os
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

DATABASE_URL = os.getenv("FORECAST_ITERATION_TEST_DATABASE_URL")
DISPOSABLE_PREFIXES = ("plantgeo_forecast_test", "plantgeo_geospatial_test_")
FORECAST_HORIZON_DAYS = 30
FIXTURE_OBSERVATION_COUNT = 100
MIN_SUPPORTED_POSTGRES_VERSION = 160000
MAX_SUPPORTED_POSTGRES_VERSION = 190000


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set FORECAST_ITERATION_TEST_DATABASE_URL to a migrated disposable PlantGeo database",
)
def test_deterministic_iteration_materialization_and_actual_reconciliation() -> None:  # noqa: PLR0915
    assert DATABASE_URL is not None
    connection = psycopg2.connect(DATABASE_URL)
    connection.autocommit = False

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_setting('server_version_num')::integer, "
                "bool_and(procedure_name IS NOT NULL) "
                "FROM (VALUES "
                "(to_regprocedure('agri.materialize_forecast_iteration("
                "uuid,character varying,uuid,uuid,timestamp with time zone,"
                "timestamp with time zone,timestamp with time zone,integer,integer,"
                "bigint,character varying,double precision,double precision)')::text), "
                "(to_regprocedure('agri.reconcile_forecast_iteration_actuals("
                "integer,uuid,uuid,timestamp with time zone)')::text)"
                ") AS required_procedure(procedure_name)"
            )
            database_name, server_version_num, schema_contract_present = cursor.fetchone()
            assert database_name.startswith(DISPOSABLE_PREFIXES)
            assert MIN_SUPPORTED_POSTGRES_VERSION <= server_version_num < MAX_SUPPORTED_POSTGRES_VERSION
            assert schema_contract_present

            suffix = _checksum(os.urandom(16).hex())[:12]
            start = datetime(2026, 1, 1, tzinfo=UTC)
            cutoff = start + timedelta(days=69)

            cursor.execute(
                """
                INSERT INTO agri.data_source(
                    key, name, owner, purpose, license_name, license_url, citation,
                    review_state, reviewed_at, reviewed_by, created_at, updated_at
                )
                VALUES (
                    %s, 'Iteration SQL fixture', 'PlantGeo test',
                    'rolled-back deterministic forecast validation',
                    'CC0-1.0', 'https://creativecommons.org/publicdomain/zero/1.0/',
                    'test fixture', 'approved', %s, 'PlantGeo test', %s, %s
                )
                RETURNING id
                """,
                (f"forecast-iteration-{suffix}", start, start, start),
            )
            data_source_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO agri.source_release(
                    data_source_id, source_version, retrieved_at, data_available_at,
                    observed_from, observed_to, payload_checksum, schema_version,
                    transform_version, license_snapshot, validation_state, validated_at,
                    created_at
                )
                VALUES (
                    %s, 'fixture-v1', %s, %s, %s, %s, %s, 'fixture-v1',
                    'fixture-transform-v1', 'CC0-1.0', 'valid', %s, %s
                )
                RETURNING id
                """,
                (
                    data_source_id,
                    start,
                    start,
                    start,
                    start + timedelta(days=99),
                    _checksum(f"{suffix}:release"),
                    start,
                    start,
                ),
            )
            source_release_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO agri.release_set(
                    logical_key, as_of_time, manifest_checksum, state, created_at
                )
                VALUES (%s, %s, %s, 'draft', %s)
                RETURNING id
                """,
                (
                    f"forecast-iteration-{suffix}",
                    start,
                    _checksum(f"{suffix}:release-set"),
                    start,
                ),
            )
            release_set_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO agri.release_set_item(release_set_id, source_release_id)
                VALUES (%s, %s)
                """,
                (release_set_id, source_release_id),
            )
            cursor.execute(
                """
                UPDATE agri.release_set
                   SET state = 'validated', validated_at = %s
                 WHERE id = %s
                """,
                (start, release_set_id),
            )
            cursor.execute(
                """
                INSERT INTO agri.forecast_series(
                    series_key, source_variant_key, input_adapter, data_source_id,
                    source_transform_version, entity_type, entity_key, metric_name,
                    metric_unit, representation_kind, spatial_support_kind,
                    source_temporal_support, output_temporal_support, metadata_json
                    , created_at
                )
                VALUES (
                    %s, 'fixture-v1', 'forecast_observation', %s,
                    'fixture-transform-v1', 'test_object', 'object-1',
                    'daily_metric', 'unit', 'raw_native', 'point_sample',
                    interval '1 day', interval '1 day', '{"fixture":true}'::jsonb,
                    %s
                )
                RETURNING id
                """,
                (f"forecast-iteration-series-{suffix}", data_source_id, start),
            )
            series_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO agri.forecast_entity_state(
                    series_id, source_release_id, state_key, valid_from, valid_to,
                    data_available_at, state_checksum, created_at
                )
                VALUES (%s, %s, 'fixture-state-v1', %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    series_id,
                    source_release_id,
                    start,
                    start + timedelta(days=100),
                    start,
                    _checksum(f"{suffix}:state"),
                    start,
                ),
            )
            state_id = cursor.fetchone()[0]

            observations = []
            for day in range(FIXTURE_OBSERVATION_COUNT):
                observed_at = start + timedelta(days=day)
                metric_value = 10.0 + 2.0 * day
                observations.append(
                    (
                        series_id,
                        state_id,
                        source_release_id,
                        observed_at,
                        observed_at + timedelta(hours=1),
                        metric_value,
                        f"event-{day}",
                        _checksum(f"{suffix}:{day}:{metric_value}"),
                        observed_at + timedelta(hours=1),
                    )
                )
            cursor.executemany(
                """
                INSERT INTO agri.forecast_observation(
                    series_id, entity_state_id, source_release_id, observed_at,
                    data_available_at, metric_value, source_event_key,
                    observation_checksum, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                observations,
            )

            cursor.execute("SELECT clock_timestamp()")
            forecast_as_of = cursor.fetchone()[0]
            cursor.execute(
                """
                SELECT count(*)
                FROM agri.forecast_timeseries_contract(%s, %s)
                WHERE series_id = %s
                """,
                (release_set_id, forecast_as_of, series_id),
            )
            assert cursor.fetchone()[0] == FIXTURE_OBSERVATION_COUNT

            iteration_key = f"bootstrap-30d-{suffix}"
            cursor.execute(
                """
                CALL agri.materialize_forecast_iteration(
                    NULL, %s, %s, %s, %s, %s, NULL, %s, 100, 42, 'strict', 0, NULL
                )
                """,
                (
                    iteration_key,
                    series_id,
                    release_set_id,
                    forecast_as_of,
                    cutoff,
                    FORECAST_HORIZON_DAYS,
                ),
            )
            iteration_id = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT
                    iteration.status,
                    iteration.purpose,
                    iteration.availability_mode,
                    iteration.horizon_days,
                    iteration.increment_count,
                    count(value.id),
                    min(value.low_value),
                    min(value.median_value),
                    min(value.high_value),
                    max(value.median_value),
                    bool_and(value.low_value = value.median_value),
                    bool_and(value.median_value = value.high_value)
                FROM agri.forecast_iteration AS iteration
                INNER JOIN agri.forecast_iteration_value AS value
                    ON value.iteration_id = iteration.id
                WHERE iteration.id = %s
                GROUP BY iteration.id
                """,
                (iteration_id,),
            )
            result = cursor.fetchone()
            assert result[:6] == (
                "finalized",
                "evaluation_only",
                "retrospective_pinned_release",
                30,
                69,
                30,
            )
            assert result[6] == pytest.approx(150.0)
            assert result[7] == pytest.approx(150.0)
            assert result[8] == pytest.approx(150.0)
            assert result[9] == pytest.approx(208.0)
            assert result[10:] == (True, True)

            cursor.execute("SET LOCAL DateStyle = 'German, DMY'")
            cursor.execute("SET LOCAL IntervalStyle = 'sql_standard'")
            cursor.execute("SET LOCAL extra_float_digits = 0")
            cursor.execute("SET LOCAL TimeZone = 'America/Denver'")
            cursor.execute(
                """
                CALL agri.materialize_forecast_iteration(
                    NULL, %s, %s, %s, %s, %s, NULL, 30, 100, 42, 'strict', 0, NULL
                )
                """,
                (
                    iteration_key,
                    series_id,
                    release_set_id,
                    forecast_as_of,
                    cutoff,
                ),
            )
            assert cursor.fetchone()[0] == iteration_id
            cursor.execute("SET LOCAL DateStyle TO DEFAULT")
            cursor.execute("SET LOCAL IntervalStyle TO DEFAULT")
            cursor.execute("SET LOCAL extra_float_digits TO DEFAULT")
            cursor.execute("SET LOCAL TimeZone TO DEFAULT")
            cursor.execute(
                "SELECT count(*) FROM agri.forecast_iteration WHERE iteration_key = %s",
                (iteration_key,),
            )
            assert cursor.fetchone()[0] == 1

            cursor.execute(
                """
                INSERT INTO agri.forecast_observation(
                    series_id, entity_state_id, source_release_id, observed_at,
                    data_available_at, metric_value, source_event_key,
                    observation_checksum, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 9999, %s, %s, %s)
                """,
                (
                    series_id,
                    state_id,
                    source_release_id,
                    start + timedelta(days=1, hours=12),
                    start,
                    "late-backdated-event",
                    _checksum(f"{suffix}:late-backdated"),
                    start,
                ),
            )
            cursor.execute(
                """
                SELECT observation.created_at, ledger.recorded_at
                FROM agri.forecast_observation AS observation
                JOIN agri.forecast_input_recorded_at AS ledger
                  ON ledger.input_kind = 'source_release'
                 AND ledger.input_key = observation.source_release_id::text
                WHERE observation.source_event_key = 'late-backdated-event'
                """
            )
            declared_created_at, server_recorded_at = cursor.fetchone()
            assert declared_created_at < forecast_as_of < server_recorded_at
            cursor.execute(
                """
                SELECT count(*)
                FROM agri.forecast_timeseries_contract(%s, %s)
                WHERE series_id = %s
                """,
                (release_set_id, forecast_as_of, series_id),
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute("SELECT date_trunc('day', clock_timestamp()) AT TIME ZONE 'UTC'")
            today_start = cursor.fetchone()[0].replace(tzinfo=UTC)
            cursor.execute(
                """
                INSERT INTO agri.forecast_entity_state(
                    series_id, source_release_id, state_key, valid_from, valid_to,
                    data_available_at, state_checksum, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    series_id,
                    source_release_id,
                    f"partial-day-state-{suffix}",
                    today_start - timedelta(days=6),
                    today_start + timedelta(days=1),
                    today_start - timedelta(days=6),
                    _checksum(f"{suffix}:partial-day-state"),
                    start,
                ),
            )
            recent_state_id = cursor.fetchone()[0]
            recent_observations = []
            for day_offset in range(-6, 1):
                observed_at = today_start + timedelta(days=day_offset)
                metric_value = 300.0 + day_offset
                recent_observations.append(
                    (
                        series_id,
                        recent_state_id,
                        source_release_id,
                        observed_at,
                        observed_at,
                        metric_value,
                        f"partial-day-{day_offset}-{suffix}",
                        _checksum(f"{suffix}:partial:{day_offset}:{metric_value}"),
                        start,
                    )
                )
            cursor.executemany(
                """
                INSERT INTO agri.forecast_observation(
                    series_id, entity_state_id, source_release_id, observed_at,
                    data_available_at, metric_value, source_event_key,
                    observation_checksum, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                recent_observations,
            )
            cursor.execute("SELECT clock_timestamp()")
            partial_day_as_of = cursor.fetchone()[0]
            cursor.execute(
                """
                CALL agri.materialize_forecast_iteration(
                    NULL, %s, %s, %s, %s, %s, %s, 1, 100, 7, 'strict', 0, NULL
                )
                """,
                (
                    f"partial-day-{suffix}",
                    series_id,
                    release_set_id,
                    partial_day_as_of,
                    today_start - timedelta(days=1),
                    today_start - timedelta(days=6),
                ),
            )
            partial_iteration_id = cursor.fetchone()[0]
            cursor.execute(
                "CALL agri.reconcile_forecast_iteration_actuals(0, %s, %s, %s)",
                (partial_iteration_id, release_set_id, partial_day_as_of),
            )
            assert cursor.fetchone()[0] == 0

            cursor.execute("SELECT clock_timestamp()")
            reconcile_as_of = cursor.fetchone()[0]
            cursor.execute(
                "CALL agri.reconcile_forecast_iteration_actuals(0, %s, %s, %s)",
                (iteration_id, release_set_id, reconcile_as_of),
            )
            assert cursor.fetchone()[0] == FORECAST_HORIZON_DAYS
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")

            cursor.execute(
                """
                SELECT
                    count(*),
                    min(actual.actual_value),
                    max(actual.actual_value),
                    bool_and(actual.actual_value = value.median_value),
                    bool_and(actual.actual_checksum ~ '^[0-9a-f]{64}$'),
                    bool_and(
                        actual.actual_digest_version = 'forecast_iteration_actual_v2'
                    ),
                    bool_and(input.license_snapshot = 'CC0-1.0')
                FROM agri.forecast_iteration_actual AS actual
                INNER JOIN agri.forecast_iteration_value AS value
                    ON value.id = actual.iteration_value_id
                INNER JOIN agri.forecast_iteration_actual_input AS input
                    ON input.actual_id = actual.id
                WHERE value.iteration_id = %s
                """,
                (iteration_id,),
            )
            assert cursor.fetchone() == (30, 150.0, 208.0, True, True, True, True)

            signal_as_of = datetime.now(tz=UTC) + timedelta(days=1)
            cursor.execute(
                """
                SELECT observed_at, metric_value
                FROM agri.forecast_iteration_signal_timeseries(
                    %s, 'residual_actual_minus_forecast', 1, %s
                )
                """,
                (series_id, signal_as_of),
            )
            signal = cursor.fetchone()
            assert signal[0] == cutoff + timedelta(days=1)
            assert signal[1] == pytest.approx(0.0)

            cursor.execute("SAVEPOINT immutable_iteration")
            with pytest.raises(psycopg2.Error):
                cursor.execute(
                    "UPDATE agri.forecast_iteration SET simulation_count = 101 WHERE id = %s",
                    (iteration_id,),
                )
            cursor.execute("ROLLBACK TO SAVEPOINT immutable_iteration")
    finally:
        connection.rollback()
        connection.close()
