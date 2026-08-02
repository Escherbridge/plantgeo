"""Optional PostgreSQL proof for the 0011 signal-evaluation objects (spec section 9)."""

import hashlib
import math
import os
from datetime import UTC, date, datetime, timedelta

import psycopg2
import pytest

FORECAST_HORIZON_DAYS = 30
FIXTURE_HISTORY_DAY_COUNT = 70
FIXTURE_OBSERVATION_COUNT = 100
SIMULATION_COUNT = 100
SIMULATION_SEED = 42
ACTUAL_OFFSET = 3.0
PERTURBED_HORIZON_STEP_COUNT = 15
EXPECTED_MEAN_ABSOLUTE_ERROR = 1.5
EXPECTED_ROOT_MEAN_SQUARED_ERROR = math.sqrt(4.5)
EXPECTED_INTERVAL_COVERAGE = 0.5
EVALUATION_COLUMNS = (
    "iteration_id",
    "iteration_key",
    "series_id",
    "release_set_id",
    "cutoff_time",
    "horizon_days",
    "forecast_available_at",
    "evaluated_count",
    "actual_count",
    "mean_absolute_error",
    "root_mean_squared_error",
    "interval_coverage",
    "receipt_checksum",
    "evaluation_checksum",
)
EVALUATION_COLUMN_TYPES = (
    "uuid",
    "character varying",
    "uuid",
    "uuid",
    "timestamp with time zone",
    "integer",
    "timestamp with time zone",
    "integer",
    "integer",
    "double precision",
    "double precision",
    "double precision",
    "text",
    "text",
)
DROUGHT_COLUMNS = (
    "cell_id",
    "observed_date",
    "severity_class",
    "is_imputed",
    "issue_date",
    "source_release_id",
    "data_available_at",
    "geometry_checksum",
)
DROUGHT_COLUMN_TYPES = (
    "uuid",
    "date",
    "integer",
    "boolean",
    "date",
    "uuid",
    "timestamp with time zone",
    "text",
)
_COVERING_MULTIPOLYGON_WKT = "MULTIPOLYGON(((-117.0 43.0, -115.0 43.0, -115.0 44.0, -117.0 44.0, -117.0 43.0)))"
SEVERITY_ISSUE_A = 2
SEVERITY_ISSUE_B_LOW = 3
SEVERITY_ISSUE_B_HIGH = 4
SEVERITY_ISSUE_C = 1


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _replicate_evaluation_checksum(
    cursor: psycopg2.extensions.cursor,
    row: dict[str, object],
    as_of_time: datetime,
) -> str:
    # spec section 9.1: checksum is checksummed output, must pin the same GUCs as the function
    cursor.execute("SET LOCAL TimeZone = 'UTC'")
    cursor.execute("SET LOCAL DateStyle = 'ISO, MDY'")
    cursor.execute("SET LOCAL extra_float_digits = 1")
    cursor.execute(
        """
        SELECT encode(digest(concat_ws('|',
            'forecast_iteration_evaluation_v1', %s::text,
            to_char(%s::timestamptz AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            %s::text, %s::text, %s::text, %s::text, %s::text
        ), 'sha256'), 'hex')
        """,
        (
            str(row["iteration_id"]),
            as_of_time,
            row["evaluated_count"],
            row["actual_count"],
            row["mean_absolute_error"],
            row["root_mean_squared_error"],
            row["interval_coverage"],
        ),
    )
    return cursor.fetchone()[0]


def test_forecast_iteration_evaluation_arithmetic_leakage_and_column_contract(  # noqa: PLR0915
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    connection = agri_db_connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regprocedure('agri.forecast_iteration_evaluation(uuid,timestamptz)')::text")
        (procedure_name,) = cursor.fetchone()
        assert procedure_name is not None

        suffix = _checksum(os.urandom(16).hex())[:12]
        start = datetime(2026, 1, 1, tzinfo=UTC)
        cutoff = start + timedelta(days=FIXTURE_HISTORY_DAY_COUNT - 1)

        cursor.execute(
            """
            INSERT INTO agri.data_source(
                key, name, owner, purpose, license_name, license_url, citation,
                review_state, reviewed_at, reviewed_by, created_at, updated_at
            )
            VALUES (
                %s, 'Signal evaluation fixture', 'PlantGeo test',
                'rolled-back signal evaluation validation',
                'CC0-1.0', 'https://creativecommons.org/publicdomain/zero/1.0/',
                'test fixture', 'approved', %s, 'PlantGeo test', %s, %s
            )
            RETURNING id
            """,
            (f"signal-eval-{suffix}", start, start, start),
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
                start + timedelta(days=FIXTURE_OBSERVATION_COUNT),
                _checksum(f"{suffix}:release"),
                start,
                start,
            ),
        )
        source_release_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state, created_at)
            VALUES (%s, %s, %s, 'draft', %s)
            RETURNING id
            """,
            (f"signal-eval-{suffix}", start, _checksum(f"{suffix}:release-set"), start),
        )
        release_set_id = cursor.fetchone()[0]
        cursor.execute(
            "INSERT INTO agri.release_set_item(release_set_id, source_release_id) VALUES (%s, %s)",
            (release_set_id, source_release_id),
        )
        cursor.execute(
            "UPDATE agri.release_set SET state = 'validated', validated_at = %s WHERE id = %s",
            (start, release_set_id),
        )
        cursor.execute(
            """
            INSERT INTO agri.forecast_series(
                series_key, source_variant_key, input_adapter, data_source_id,
                source_transform_version, entity_type, entity_key, metric_name,
                metric_unit, representation_kind, spatial_support_kind,
                source_temporal_support, output_temporal_support, metadata_json, created_at
            )
            VALUES (
                %s, 'fixture-v1', 'forecast_observation', %s,
                'fixture-transform-v1', 'test_object', 'object-1', 'daily_metric', 'unit',
                'raw_native', 'point_sample', interval '1 day', interval '1 day',
                '{"fixture":true}'::jsonb, %s
            )
            RETURNING id
            """,
            (f"signal-eval-series-{suffix}", data_source_id, start),
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
                start + timedelta(days=FIXTURE_OBSERVATION_COUNT),
                start,
                _checksum(f"{suffix}:state"),
                start,
            ),
        )
        state_id = cursor.fetchone()[0]

        # days 0..69 train the forecast; days 70..84 (h1-15) continue the trend exactly;
        # days 85..99 (h16-30) are offset by ACTUAL_OFFSET so MAE/RMSE/coverage are non-degenerate
        observations = []
        for day in range(FIXTURE_OBSERVATION_COUNT):
            observed_at = start + timedelta(days=day)
            metric_value = 10.0 + 2.0 * day
            horizon_step = day - FIXTURE_HISTORY_DAY_COUNT + 1
            if horizon_step > PERTURBED_HORIZON_STEP_COUNT:
                metric_value += ACTUAL_OFFSET
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
                series_id, entity_state_id, source_release_id, observed_at, data_available_at,
                metric_value, source_event_key, observation_checksum, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            observations,
        )

        cursor.execute("SELECT clock_timestamp()")
        as_of_before_finalize = cursor.fetchone()[0]

        iteration_key = f"signal-eval-iteration-{suffix}"
        staging_parameter_checksum = _checksum(f"{suffix}:staging-parameter")
        cursor.execute(
            """
            CALL agri.materialize_forecast_iteration(
                NULL, %s, %s, %s, %s, %s, NULL, %s, %s, %s, 'strict', 0, NULL
            )
            """,
            (
                iteration_key,
                series_id,
                release_set_id,
                as_of_before_finalize,
                cutoff,
                FORECAST_HORIZON_DAYS,
                SIMULATION_COUNT,
                SIMULATION_SEED,
            ),
        )
        iteration_id = cursor.fetchone()[0]
        cursor.execute("SELECT recorded_at FROM agri.forecast_iteration WHERE id = %s", (iteration_id,))
        forecast_available_at = cursor.fetchone()[0]
        assert forecast_available_at > as_of_before_finalize

        # a raw staging sibling on the same series: never reachable, since the source view is
        # already restricted to status = 'finalized' (spec section 9.1)
        cursor.execute(
            """
            INSERT INTO agri.forecast_iteration(
                iteration_key, series_id, release_set_id, availability_mode,
                as_of_time, cutoff_time, history_start, horizon_days,
                simulation_count, simulation_seed, gap_policy, lower_bound,
                input_release_checksum, input_license_snapshots, contract_snapshot,
                contract_checksum, history_checksum, parameter_checksum,
                training_day_count, increment_count, expected_value_count
            )
            VALUES (
                %s, %s, %s, 'retrospective_pinned_release',
                %s, %s, %s, %s,
                %s, %s, 'strict', 0,
                %s, '["CC0-1.0"]'::jsonb, '{}'::jsonb,
                %s, %s, %s,
                %s, %s, %s
            )
            RETURNING id
            """,
            (
                f"signal-eval-staging-{suffix}",
                series_id,
                release_set_id,
                as_of_before_finalize,
                cutoff,
                start,
                FORECAST_HORIZON_DAYS,
                SIMULATION_COUNT,
                SIMULATION_SEED,
                _checksum(f"{suffix}:staging-release"),
                _checksum(f"{suffix}:staging-contract"),
                _checksum(f"{suffix}:staging-history"),
                staging_parameter_checksum,
                FIXTURE_HISTORY_DAY_COUNT,
                FIXTURE_HISTORY_DAY_COUNT - 1,
                FORECAST_HORIZON_DAYS,
            ),
        )
        staging_iteration_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.forecast_iteration_value(
                iteration_id, valid_time, horizon_step, low_value, median_value,
                high_value, increment_count, parameter_checksum
            )
            VALUES (%s, %s, 1, 999.0, 999.0, 999.0, %s, %s)
            """,
            (
                staging_iteration_id,
                cutoff + timedelta(days=1),
                FIXTURE_HISTORY_DAY_COUNT - 1,
                staging_parameter_checksum,
            ),
        )

        cursor.execute("SELECT clock_timestamp()")
        as_of_between_finalize_and_reconcile = cursor.fetchone()[0]

        cursor.execute("SELECT clock_timestamp()")
        reconcile_as_of = cursor.fetchone()[0]
        cursor.execute(
            "CALL agri.reconcile_forecast_iteration_actuals(0, %s, %s, %s)",
            (iteration_id, release_set_id, reconcile_as_of),
        )
        assert cursor.fetchone()[0] == FORECAST_HORIZON_DAYS

        cursor.execute(
            """
            SELECT actual.recorded_at
            FROM agri.forecast_iteration_actual AS actual
            INNER JOIN agri.forecast_iteration_value AS value ON value.id = actual.iteration_value_id
            WHERE value.iteration_id = %s
            LIMIT 1
            """,
            (iteration_id,),
        )
        actual_recorded_at_sample = cursor.fetchone()[0]
        cursor.execute("SELECT clock_timestamp()")
        as_of_full = cursor.fetchone()[0]
        assert as_of_between_finalize_and_reconcile < actual_recorded_at_sample < as_of_full

        # full case: evaluated + reconciled, non-degenerate arithmetic, exact column contract
        cursor.execute(
            "SELECT * FROM agri.forecast_iteration_evaluation(%s, %s) ORDER BY iteration_id",
            (series_id, as_of_full),
        )
        full_rows = cursor.fetchall()
        full_columns = [column.name for column in cursor.description]
        assert full_columns == list(EVALUATION_COLUMNS)
        assert len(full_rows) == 1
        row = dict(zip(full_columns, full_rows[0], strict=True))
        assert row["iteration_id"] != staging_iteration_id  # staging never surfaces

        assert row["iteration_id"] == iteration_id
        assert row["iteration_key"] == iteration_key
        assert row["series_id"] == series_id
        assert row["release_set_id"] == release_set_id
        assert row["cutoff_time"] == cutoff
        assert row["horizon_days"] == FORECAST_HORIZON_DAYS
        assert row["forecast_available_at"] == forecast_available_at
        assert row["evaluated_count"] == FORECAST_HORIZON_DAYS
        assert row["actual_count"] == FORECAST_HORIZON_DAYS
        assert row["mean_absolute_error"] == pytest.approx(EXPECTED_MEAN_ABSOLUTE_ERROR)
        assert row["root_mean_squared_error"] == pytest.approx(EXPECTED_ROOT_MEAN_SQUARED_ERROR)
        assert row["interval_coverage"] == pytest.approx(EXPECTED_INTERVAL_COVERAGE)

        cursor.execute("SELECT receipt_checksum FROM agri.forecast_iteration WHERE id = %s", (iteration_id,))
        assert row["receipt_checksum"] == cursor.fetchone()[0]
        assert row["evaluation_checksum"] == _replicate_evaluation_checksum(cursor, row, as_of_full)

        cursor.execute(
            """
            SELECT
                pg_typeof(iteration_id)::text, pg_typeof(iteration_key)::text,
                pg_typeof(series_id)::text, pg_typeof(release_set_id)::text,
                pg_typeof(cutoff_time)::text, pg_typeof(horizon_days)::text,
                pg_typeof(forecast_available_at)::text, pg_typeof(evaluated_count)::text,
                pg_typeof(actual_count)::text, pg_typeof(mean_absolute_error)::text,
                pg_typeof(root_mean_squared_error)::text, pg_typeof(interval_coverage)::text,
                pg_typeof(receipt_checksum)::text, pg_typeof(evaluation_checksum)::text
            FROM agri.forecast_iteration_evaluation(%s, %s)
            """,
            (series_id, as_of_full),
        )
        assert cursor.fetchone() == EVALUATION_COLUMN_TYPES

        # LEAKAGE: as-of between finalize and reconcile -> evaluated but no actuals yet
        cursor.execute(
            "SELECT * FROM agri.forecast_iteration_evaluation(%s, %s) ORDER BY iteration_id",
            (series_id, as_of_between_finalize_and_reconcile),
        )
        between_rows = cursor.fetchall()
        between_columns = [column.name for column in cursor.description]
        assert len(between_rows) == 1
        between_row = dict(zip(between_columns, between_rows[0], strict=True))
        assert between_row["iteration_id"] == iteration_id
        assert between_row["evaluated_count"] == FORECAST_HORIZON_DAYS
        assert between_row["actual_count"] == 0
        assert between_row["mean_absolute_error"] is None
        assert between_row["root_mean_squared_error"] is None
        assert between_row["interval_coverage"] is None
        assert between_row["evaluation_checksum"] == _replicate_evaluation_checksum(
            cursor, between_row, as_of_between_finalize_and_reconcile
        )

        # LEAKAGE: as-of before finalize -> the iteration is omitted entirely, not zero-filled
        cursor.execute(
            "SELECT * FROM agri.forecast_iteration_evaluation(%s, %s)",
            (series_id, as_of_before_finalize),
        )
        assert cursor.fetchall() == []


def test_drought_class_daily_series_resolution_imputation_and_leakage_gates(  # noqa: PLR0915
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    connection = agri_db_connection
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regprocedure('agri.drought_class_daily_series(uuid,timestamptz,timestamptz,timestamptz)')::text"
        )
        (procedure_name,) = cursor.fetchone()
        assert procedure_name is not None

        suffix = _checksum(os.urandom(16).hex())[:12]

        cursor.execute(
            """
            INSERT INTO agri.spatial_cell(cell_key, grid_name, resolution_m, geometry, centroid)
            VALUES (
                %s, 'signal-eval-test-grid', 10000,
                ST_GeomFromText(
                    'POLYGON((-116.15 43.55, -116.10 43.55, -116.10 43.60, -116.15 43.60, -116.15 43.55))',
                    4326
                ),
                ST_GeomFromText('POINT(-116.125 43.575)', 4326)
            )
            RETURNING id
            """,
            (f"signal-eval-cell-{suffix}",),
        )
        cell_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
            VALUES (%s, 'USDM fixture', 'PlantGeo test', 'rolled-back drought validation', 'test-only', 'test-only')
            RETURNING id
            """,
            (f"usdm-signal-eval-{suffix}",),
        )
        data_source_id = cursor.fetchone()[0]

        def _insert_source_release(label: str, data_available_at: datetime) -> str:
            cursor.execute(
                """
                INSERT INTO agri.source_release(
                    data_source_id, source_version, retrieved_at, data_available_at,
                    payload_checksum, schema_version, license_snapshot, validation_state, validated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'fixture-v1', 'test-only', 'valid', %s)
                RETURNING id
                """,
                (
                    data_source_id,
                    f"{label}-{suffix}",
                    data_available_at,
                    data_available_at,
                    _checksum(f"{suffix}:{label}"),
                    data_available_at,
                ),
            )
            return cursor.fetchone()[0]

        def _insert_polygon(
            label: str,
            source_release_id: str,
            issue_date: date,
            severity_class: int,
            data_available_at: datetime,
        ) -> None:
            cursor.execute(
                """
                INSERT INTO agri.drought_polygon_snapshot(
                    source_release_id, issue_date, severity_class, impact_type,
                    geometry, geometry_checksum, data_available_at
                )
                VALUES (%s, %s, %s, 'none', ST_GeomFromText(%s, 4326), %s, %s)
                """,
                (
                    source_release_id,
                    issue_date,
                    severity_class,
                    _COVERING_MULTIPOLYGON_WKT,
                    _checksum(f"{suffix}:{label}"),
                    data_available_at,
                ),
            )

        # weekly cadence: issue A (week 1), issue B (week 2, two tied polygons), a missed
        # week 3 issue (simulating a gap), then issue C published 5 days late (leakage probe)
        release_a_available = datetime(2026, 1, 2, tzinfo=UTC)
        release_b_available = datetime(2026, 1, 9, tzinfo=UTC)
        release_c_available = datetime(2026, 1, 27, tzinfo=UTC)
        release_a_id = _insert_source_release("issue-a", release_a_available)
        release_b_id = _insert_source_release("issue-b", release_b_available)
        release_c_id = _insert_source_release("issue-c", release_c_available)

        _insert_polygon("issue-a", release_a_id, date(2026, 1, 1), SEVERITY_ISSUE_A, release_a_available)
        _insert_polygon("issue-b-low", release_b_id, date(2026, 1, 8), SEVERITY_ISSUE_B_LOW, release_b_available)
        _insert_polygon("issue-b-high", release_b_id, date(2026, 1, 8), SEVERITY_ISSUE_B_HIGH, release_b_available)
        _insert_polygon("issue-c", release_c_id, date(2026, 1, 22), SEVERITY_ISSUE_C, release_c_available)

        window_start = datetime(2025, 12, 25, tzinfo=UTC)
        window_end = datetime(2026, 1, 28, tzinfo=UTC)
        as_of_full = datetime(2026, 1, 30, tzinfo=UTC)
        as_of_before_issue_c_available = datetime(2026, 1, 24, tzinfo=UTC)

        cursor.execute(
            "SELECT * FROM agri.drought_class_daily_series(%s, %s, %s, %s) ORDER BY observed_date",
            (cell_id, window_start, window_end, as_of_full),
        )
        full_rows = cursor.fetchall()
        full_columns = [column.name for column in cursor.description]
        assert full_columns == list(DROUGHT_COLUMNS)
        assert len(full_rows) == (window_end - window_start).days + 1
        full_by_date = {row[1]: dict(zip(full_columns, row, strict=True)) for row in full_rows}
        assert full_by_date[window_start.date()]["observed_date"] == window_start.date()
        assert full_by_date[window_end.date()]["observed_date"] == window_end.date()

        # before any admissible issue: NULL all the way, never a fabricated zero
        no_issue_row = full_by_date[date(2025, 12, 27)]
        assert no_issue_row["severity_class"] is None
        assert no_issue_row["is_imputed"] is True
        assert no_issue_row["issue_date"] is None
        assert no_issue_row["source_release_id"] is None
        assert no_issue_row["data_available_at"] is None
        assert no_issue_row["geometry_checksum"] is None

        # normal weekly hold-over under issue A: not imputed
        issue_a_row = full_by_date[date(2026, 1, 5)]
        assert issue_a_row["severity_class"] == SEVERITY_ISSUE_A
        assert issue_a_row["is_imputed"] is False
        assert issue_a_row["issue_date"] == date(2026, 1, 1)
        assert issue_a_row["source_release_id"] == release_a_id

        # tie on issue_date: MAX(severity_class) wins over the two Jan-8 polygons
        issue_c_day = date(2026, 1, 25)
        issue_b_row = full_by_date[date(2026, 1, 10)]
        assert issue_b_row["severity_class"] == SEVERITY_ISSUE_B_HIGH
        assert issue_b_row["is_imputed"] is False
        assert issue_b_row["issue_date"] == date(2026, 1, 8)
        assert issue_b_row["source_release_id"] == release_b_id

        # missed week-3 issue: issue B held over more than 7 days -> imputed
        held_over_row = full_by_date[date(2026, 1, 20)]
        assert held_over_row["severity_class"] == SEVERITY_ISSUE_B_HIGH
        assert held_over_row["is_imputed"] is True
        assert held_over_row["issue_date"] == date(2026, 1, 8)

        # issue C fully available: resolves on its own issue_date, not held over
        issue_c_row = full_by_date[issue_c_day]
        assert issue_c_row["severity_class"] == SEVERITY_ISSUE_C
        assert issue_c_row["is_imputed"] is False
        assert issue_c_row["issue_date"] == date(2026, 1, 22)
        assert issue_c_row["source_release_id"] == release_c_id
        assert issue_c_row["data_available_at"] == release_c_available

        # LEAKAGE: issue C not yet available -> the same day falls back to issue B, imputed
        cursor.execute(
            "SELECT * FROM agri.drought_class_daily_series(%s, %s, %s, %s) ORDER BY observed_date",
            (cell_id, window_start, window_end, as_of_before_issue_c_available),
        )
        leak_rows = cursor.fetchall()
        leak_columns = [column.name for column in cursor.description]
        leak_by_date = {row[1]: dict(zip(leak_columns, row, strict=True)) for row in leak_rows}
        leak_issue_c_day_row = leak_by_date[issue_c_day]
        assert leak_issue_c_day_row["severity_class"] == SEVERITY_ISSUE_B_HIGH
        assert leak_issue_c_day_row["issue_date"] == date(2026, 1, 8)
        assert leak_issue_c_day_row["source_release_id"] == release_b_id
        assert leak_issue_c_day_row["is_imputed"] is True

        cursor.execute(
            """
            SELECT
                pg_typeof(cell_id)::text, pg_typeof(observed_date)::text,
                pg_typeof(severity_class)::text, pg_typeof(is_imputed)::text,
                pg_typeof(issue_date)::text, pg_typeof(source_release_id)::text,
                pg_typeof(data_available_at)::text, pg_typeof(geometry_checksum)::text
            FROM agri.drought_class_daily_series(%s, %s, %s, %s)
            WHERE observed_date = %s
            """,
            (cell_id, window_start, window_end, as_of_full, date(2026, 1, 5)),
        )
        assert cursor.fetchone() == DROUGHT_COLUMN_TYPES


def test_drought_class_daily_series_cell_scoping_intersection_and_checksum_tie_break(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """Guard the three paths the 0016 CTE hoist touches: cell existence, ST_Intersects, checksum ties."""
    connection = agri_db_connection
    with connection.cursor() as cursor:
        suffix = _checksum(os.urandom(16).hex())[:12]

        cursor.execute(
            """
            INSERT INTO agri.spatial_cell(cell_key, grid_name, resolution_m, geometry, centroid)
            VALUES (
                %s, 'signal-eval-test-grid', 10000,
                ST_GeomFromText(
                    'POLYGON((-116.15 43.55, -116.10 43.55, -116.10 43.60, -116.15 43.60, -116.15 43.55))',
                    4326
                ),
                ST_GeomFromText('POINT(-116.125 43.575)', 4326)
            )
            RETURNING id
            """,
            (f"signal-eval-scope-cell-{suffix}",),
        )
        cell_id = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
            VALUES (%s, 'USDM scope fixture', 'PlantGeo test', 'rolled-back drought scoping', 'test-only', 'test-only')
            RETURNING id
            """,
            (f"usdm-scope-{suffix}",),
        )
        data_source_id = cursor.fetchone()[0]

        available_at = datetime(2026, 1, 2, tzinfo=UTC)
        cursor.execute(
            """
            INSERT INTO agri.source_release(
                data_source_id, source_version, retrieved_at, data_available_at,
                payload_checksum, schema_version, license_snapshot, validation_state, validated_at
            )
            VALUES (%s, %s, %s, %s, %s, 'fixture-v1', 'test-only', 'valid', %s)
            RETURNING id
            """,
            (
                data_source_id,
                f"scope-{suffix}",
                available_at,
                available_at,
                _checksum(f"{suffix}:scope"),
                available_at,
            ),
        )
        source_release_id = cursor.fetchone()[0]

        def _insert_polygon(geometry_wkt: str, severity_class: int, geometry_checksum: str) -> None:
            cursor.execute(
                """
                INSERT INTO agri.drought_polygon_snapshot(
                    source_release_id, issue_date, severity_class, impact_type,
                    geometry, geometry_checksum, data_available_at
                )
                VALUES (%s, %s, %s, 'none', ST_GeomFromText(%s, 4326), %s, %s)
                """,
                (source_release_id, date(2026, 1, 1), severity_class, geometry_wkt, geometry_checksum, available_at),
            )

        # two intersecting polygons tied on both issue_date and severity_class: the
        # lexicographically-least geometry_checksum must win (stable-order house rule)
        low_checksum = "0" * 63 + "1"
        high_checksum = "f" * 64
        _insert_polygon(_COVERING_MULTIPOLYGON_WKT, SEVERITY_ISSUE_A, high_checksum)
        _insert_polygon(_COVERING_MULTIPOLYGON_WKT, SEVERITY_ISSUE_A, low_checksum)
        # a strictly higher-severity polygon that does NOT intersect the cell must never win
        _insert_polygon(
            "MULTIPOLYGON(((-80.0 30.0, -79.0 30.0, -79.0 31.0, -80.0 31.0, -80.0 30.0)))",
            SEVERITY_ISSUE_B_HIGH,
            "a" * 64,
        )

        window_start = datetime(2026, 1, 1, tzinfo=UTC)
        window_end = datetime(2026, 1, 3, tzinfo=UTC)
        as_of = datetime(2026, 1, 10, tzinfo=UTC)

        cursor.execute(
            "SELECT severity_class, geometry_checksum FROM agri.drought_class_daily_series(%s, %s, %s, %s)",
            (cell_id, window_start, window_end, as_of),
        )
        rows = cursor.fetchall()
        assert len(rows) == (window_end - window_start).days + 1
        assert all(severity == SEVERITY_ISSUE_A for severity, _ in rows), (
            "a non-intersecting higher-severity polygon must not be selected"
        )
        assert all(checksum == low_checksum for _, checksum in rows), (
            "ties must break on the lexicographically-least geometry_checksum"
        )

        # an unknown cell has no geometry, so the series must be empty -- not a spine of
        # all-NULL days echoing the caller's own cell_id back as if it were real
        cursor.execute(
            "SELECT count(*) FROM agri.drought_class_daily_series(gen_random_uuid(), %s, %s, %s)",
            (window_start, window_end, as_of),
        )
        assert cursor.fetchone()[0] == 0
