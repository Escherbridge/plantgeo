"""Hindcast gates that revision 20260801_0014 made able to fail, and its knowledge pin."""

import hashlib
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

TRAINING_POINT_COUNT = 30
HORIZON_STEPS = 10
CUTOFF_DAY = TRAINING_POINT_COUNT - 1
CALIBRATION_BACK_DAYS = HORIZON_STEPS
MINIMUM_TRAINING_POINTS = 10
FIXTURE_START = datetime(2026, 1, 1, tzinfo=UTC)
FIXTURE_AS_OF = FIXTURE_START + timedelta(days=60)
FULL_HORIZON_COVERAGE = 1.0
PARTIAL_HORIZON_COVERAGE = 0.8
PARTIAL_HORIZON_VALUE_COUNT = 8
OUTSIDE_BAND_OFFSET = 3.0


@dataclass(frozen=True)
class QualityThresholds:
    """Policy thresholds a fixture wants to exercise."""

    min_coverage_fraction: float = 1.0
    min_interval_coverage_fraction: float = 0.8
    max_mae: float | None = None
    max_rmse: float | None = None
    min_skill_score: float | None = None


@dataclass(frozen=True)
class HindcastFixture:
    """Identities a finalization test needs from a freshly built hindcast run."""

    series_id: uuid.UUID
    release_set_id: uuid.UUID
    source_release_id: uuid.UUID
    entity_state_id: uuid.UUID
    policy_id: uuid.UUID
    hindcast_run_id: uuid.UUID
    receipt_checksum: str
    cutoff: datetime


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _build_hindcast_fixture(
    cursor: psycopg2.extensions.cursor,
    *,
    missing_horizon_steps: Iterable[int] = (),
    actual_offset: float = 0.0,
    thresholds: QualityThresholds | None = None,
) -> HindcastFixture:
    """Build one staged sql_linear hindcast run over a strictly linear fixture series.

    ``missing_horizon_steps`` leaves those ideal horizon steps without any actual
    observation, which is what horizon completeness measures. ``actual_offset``
    shifts every horizon actual away from the (exact) linear forecast, which is
    what interval coverage measures.
    """
    effective_thresholds = thresholds or QualityThresholds()
    suffix = _digest(os.urandom(16).hex())[:12]
    start = FIXTURE_START
    as_of = FIXTURE_AS_OF
    cutoff = start + timedelta(days=CUTOFF_DAY)
    skipped = set(missing_horizon_steps)

    cursor.execute(
        """
        INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
        VALUES (%s, 'Quality gate test', 'PlantGeo test', 'rolled-back validation',
                'test-only', 'test-only')
        RETURNING id
        """,
        (f"quality-gate-{suffix}",),
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
        (
            data_source_id,
            as_of,
            as_of,
            start,
            start + timedelta(days=CUTOFF_DAY + HORIZON_STEPS),
            _digest(f"{suffix}-payload"),
            as_of,
        ),
    )
    source_release_id = cursor.fetchone()[0]
    release_checksum = _digest(f"{suffix}-set")
    cursor.execute(
        """
        INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state)
        VALUES (%s, %s, %s, 'draft') RETURNING id
        """,
        (f"quality-gate-{suffix}", as_of, release_checksum),
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
        (f"quality-gate-series-{suffix}", data_source_id),
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
            start + timedelta(days=CUTOFF_DAY + HORIZON_STEPS + 1),
            as_of,
            _digest(f"{suffix}-state"),
        ),
    )
    entity_state_id = cursor.fetchone()[0]

    observed_days = list(range(TRAINING_POINT_COUNT))
    observed_days += [CUTOFF_DAY + step for step in range(1, HORIZON_STEPS + 1) if step not in skipped]
    cursor.executemany(
        """
        INSERT INTO agri.forecast_observation(
            series_id, entity_state_id, source_release_id, observed_at, data_available_at,
            metric_value, source_event_key, observation_checksum
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            (
                series_id,
                entity_state_id,
                source_release_id,
                start + timedelta(days=day),
                as_of,
                5.0 + 2.0 * day + (actual_offset if day > CUTOFF_DAY else 0.0),
                f"event-{day}",
                _digest(f"{suffix}:{day}"),
            )
            for day in observed_days
        ],
    )

    code_checksum = _digest(f"{suffix}-code")
    feature_checksum = _digest(f"{suffix}-features")
    parameter_checksum = _digest(f"{suffix}-parameters")
    metrics_checksum = _digest(f"{suffix}-metrics")
    cursor.execute(
        "INSERT INTO agri.job_definition(name, version, handler) VALUES (%s, 'v1', 'quality-gate') RETURNING id",
        (f"quality-gate-{suffix}",),
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
        (job_definition_id, release_set_id, f"quality-gate-{suffix}", as_of, as_of),
    )
    job_run_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_feature_snapshot(
            snapshot_key, job_run_id, release_set_id, input_release_checksum,
            feature_recipe_version, feature_code_checksum, feature_checksum,
            training_window_start, training_window_end, row_count
        )
        VALUES (%s, %s, %s, %s, 'fixture-v1', %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            f"quality-gate-snapshot-{suffix}",
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
    cursor.execute("SELECT (agri.validate_forecast_feature_snapshot(%s)).status", (snapshot_id,))
    assert cursor.fetchone()[0] == "validated"
    cursor.execute(
        """
        INSERT INTO agri.forecast_model(
            model_key, model_version, model_kind, model_purpose, algorithm, model_code_checksum
        )
        VALUES (%s, 'v1', 'sql_linear', 'metric_forecast', 'regr_slope', %s) RETURNING id
        """,
        (f"quality-gate-model-{suffix}", code_checksum),
    )
    model_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_quality_policy(
            policy_key, min_training_points, min_backtest_points,
            min_coverage_fraction, min_interval_coverage_fraction,
            max_mae, max_rmse, min_skill_score
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            f"quality-gate-policy-{suffix}",
            TRAINING_POINT_COUNT,
            HORIZON_STEPS,
            effective_thresholds.min_coverage_fraction,
            effective_thresholds.min_interval_coverage_fraction,
            effective_thresholds.max_mae,
            effective_thresholds.max_rmse,
            effective_thresholds.min_skill_score,
        ),
    )
    policy_id = cursor.fetchone()[0]
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
            f"quality-gate-run-{suffix}",
            job_run_id,
            snapshot_id,
            model_id,
            policy_id,
            as_of,
            as_of + timedelta(days=1),
            as_of + timedelta(days=HORIZON_STEPS + 1),
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
            job_run_id, output_key, kind, state, checksum_sha256, row_count, validated_at
        )
        VALUES (%s, %s, 'forecast_backtest', 'validated', %s, 1, %s) RETURNING id
        """,
        (job_run_id, f"quality-gate-backtest-{suffix}", metrics_checksum, as_of),
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
    cursor.execute(
        """
        INSERT INTO agri.forecast_hindcast_run(
            hindcast_key, forecast_run_id, series_id, release_set_id,
            simulated_cutoff_time, uncertainty_calibration_cutoff_time,
            horizon_steps, step_interval, minimum_training_points,
            training_point_count, expected_value_count, availability_mode,
            input_release_checksum, model_checksum, parameter_checksum
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, interval '1 day', %s,
                %s, %s, 'retrospective_pinned_release', %s, %s, %s)
        RETURNING id
        """,
        (
            f"quality-gate-hindcast-{suffix}",
            forecast_run_id,
            series_id,
            release_set_id,
            cutoff,
            cutoff - timedelta(days=CALIBRATION_BACK_DAYS),
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            TRAINING_POINT_COUNT,
            HORIZON_STEPS - len(skipped),
            release_checksum,
            code_checksum,
            parameter_checksum,
        ),
    )
    hindcast_run_id = cursor.fetchone()[0]
    cursor.execute(
        """
        WITH regression AS (
            SELECT * FROM agri.forecast_linear_regression(
                %s, %s, %s, %s, %s, interval '1 day', %s
            )
        ), bands AS (
            SELECT * FROM agri.forecast_linear_residual_bands(
                %s, %s, %s, %s, %s, interval '1 day', %s
            )
        ), naive AS (
            SELECT metric_value
            FROM agri.forecast_timeseries_base(%s, %s)
            WHERE series_id = %s AND observed_at <= %s
            ORDER BY observed_at DESC LIMIT 1
        )
        INSERT INTO agri.forecast_hindcast_value(
            hindcast_run_id, valid_time, horizon_step, point_value,
            p10_value, p50_value, p90_value, naive_value, actual_value,
            actual_source_release_id, actual_observation_checksum,
            actual_data_available_at
        )
        SELECT
            %s, regression.valid_time, regression.horizon_step,
            regression.forecast_value,
            regression.forecast_value + bands.residual_p10,
            regression.forecast_value + bands.residual_p50,
            regression.forecast_value + bands.residual_p90,
            naive.metric_value, actual.metric_value, actual.source_release_id,
            actual.observation_checksum, source_release.data_available_at
        FROM regression
        CROSS JOIN bands
        CROSS JOIN naive
        JOIN agri.forecast_timeseries_base(%s, %s) AS actual
          ON actual.series_id = %s AND actual.observed_at = regression.valid_time
        JOIN agri.source_release AS source_release
          ON source_release.id = actual.source_release_id
        WHERE regression.eligible AND bands.eligible
        """,
        (
            series_id,
            release_set_id,
            as_of,
            cutoff,
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            series_id,
            release_set_id,
            as_of,
            cutoff - timedelta(days=CALIBRATION_BACK_DAYS),
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            release_set_id,
            as_of,
            series_id,
            cutoff,
            hindcast_run_id,
            release_set_id,
            as_of,
            series_id,
        ),
    )
    assert cursor.rowcount == HORIZON_STEPS - len(skipped)
    cursor.execute("SELECT agri.forecast_hindcast_receipt_checksum(%s)", (hindcast_run_id,))
    receipt_checksum = cursor.fetchone()[0]
    return HindcastFixture(
        series_id=series_id,
        release_set_id=release_set_id,
        source_release_id=source_release_id,
        entity_state_id=entity_state_id,
        policy_id=policy_id,
        hindcast_run_id=hindcast_run_id,
        receipt_checksum=receipt_checksum,
        cutoff=cutoff,
    )


def _finalize(cursor: psycopg2.extensions.cursor, fixture: HindcastFixture) -> tuple[bool, float, float]:
    cursor.execute(
        """
        SELECT quality_passed, coverage_fraction, interval_coverage_fraction
        FROM agri.finalize_forecast_hindcast_run(%s, %s)
        """,
        (fixture.hindcast_run_id, fixture.receipt_checksum),
    )
    quality_passed, coverage, interval_coverage = cursor.fetchone()
    return quality_passed, coverage, interval_coverage


def test_horizon_completeness_gate_can_fail(agri_db_connection: psycopg2.extensions.connection) -> None:
    """coverage_fraction is actuals-available / ideal horizon steps, and can be < 1."""
    with agri_db_connection.cursor() as cursor:
        strict = _build_hindcast_fixture(
            cursor,
            missing_horizon_steps=(9, 10),
            thresholds=QualityThresholds(min_coverage_fraction=1.0),
        )
        quality_passed, coverage, interval_coverage = _finalize(cursor, strict)
        assert coverage == pytest.approx(PARTIAL_HORIZON_COVERAGE)
        assert interval_coverage == pytest.approx(FULL_HORIZON_COVERAGE)
        assert quality_passed is False

        cursor.execute(
            "SELECT expected_value_count, horizon_steps FROM agri.forecast_hindcast_run WHERE id = %s",
            (strict.hindcast_run_id,),
        )
        assert cursor.fetchone() == (PARTIAL_HORIZON_VALUE_COUNT, HORIZON_STEPS)

        tolerant = _build_hindcast_fixture(
            cursor,
            missing_horizon_steps=(9, 10),
            thresholds=QualityThresholds(min_coverage_fraction=0.75),
        )
        quality_passed, coverage, _ = _finalize(cursor, tolerant)
        assert coverage == pytest.approx(PARTIAL_HORIZON_COVERAGE)
        assert quality_passed is True


def test_interval_coverage_gate_is_enforced(agri_db_connection: psycopg2.extensions.connection) -> None:
    """A band that never contains the actual fails min_interval_coverage_fraction."""
    with agri_db_connection.cursor() as cursor:
        fixture = _build_hindcast_fixture(
            cursor,
            actual_offset=OUTSIDE_BAND_OFFSET,
            thresholds=QualityThresholds(min_coverage_fraction=1.0, min_interval_coverage_fraction=0.8),
        )
        quality_passed, coverage, interval_coverage = _finalize(cursor, fixture)
        assert coverage == pytest.approx(FULL_HORIZON_COVERAGE)
        assert interval_coverage == pytest.approx(0.0)
        assert quality_passed is False

        tolerant = _build_hindcast_fixture(
            cursor,
            thresholds=QualityThresholds(min_coverage_fraction=1.0, min_interval_coverage_fraction=1.0),
        )
        quality_passed, coverage, interval_coverage = _finalize(cursor, tolerant)
        assert coverage == pytest.approx(FULL_HORIZON_COVERAGE)
        assert interval_coverage == pytest.approx(FULL_HORIZON_COVERAGE)
        assert quality_passed is True


def test_finalization_pins_the_knowledge_horizon_and_stays_reproducible(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """Actuals published after the first finalize never change a finalized receipt."""
    with agri_db_connection.cursor() as cursor:
        fixture = _build_hindcast_fixture(
            cursor,
            missing_horizon_steps=(9, 10),
            thresholds=QualityThresholds(min_coverage_fraction=0.75),
        )
        quality_passed, coverage, _ = _finalize(cursor, fixture)
        assert quality_passed is True
        assert coverage == pytest.approx(PARTIAL_HORIZON_COVERAGE)

        cursor.execute(
            "SELECT actual_knowledge_as_of FROM agri.forecast_hindcast_run WHERE id = %s",
            (fixture.hindcast_run_id,),
        )
        pinned_knowledge_as_of = cursor.fetchone()[0]
        assert pinned_knowledge_as_of is not None

        cursor.executemany(
            """
            INSERT INTO agri.forecast_observation(
                series_id, entity_state_id, source_release_id, observed_at,
                data_available_at, metric_value, source_event_key, observation_checksum
            )
            VALUES (%s, %s, %s, %s, clock_timestamp(), %s, %s, %s)
            """,
            [
                (
                    fixture.series_id,
                    fixture.entity_state_id,
                    fixture.source_release_id,
                    fixture.cutoff + timedelta(days=step),
                    5.0 + 2.0 * (CUTOFF_DAY + step),
                    f"late-event-{step}",
                    _digest(f"{fixture.hindcast_run_id}:late:{step}"),
                )
                for step in (9, 10)
            ],
        )
        cursor.execute(
            """
            SELECT
                count(*) FILTER (WHERE knowledge.observed_at IS NOT NULL),
                count(*) FILTER (WHERE latest.observed_at IS NOT NULL)
            FROM generate_series(1, %s) AS step(horizon_step)
            LEFT JOIN agri.forecast_timeseries_base(%s, %s) AS knowledge
                   ON knowledge.series_id = %s
                  AND knowledge.observed_at = %s + interval '1 day' * step.horizon_step
            LEFT JOIN agri.forecast_timeseries_base(%s, clock_timestamp()) AS latest
                   ON latest.series_id = %s
                  AND latest.observed_at = %s + interval '1 day' * step.horizon_step
            """,
            (
                HORIZON_STEPS,
                fixture.release_set_id,
                pinned_knowledge_as_of,
                fixture.series_id,
                fixture.cutoff,
                fixture.release_set_id,
                fixture.series_id,
                fixture.cutoff,
            ),
        )
        available_at_pin, available_now = cursor.fetchone()
        assert available_at_pin == PARTIAL_HORIZON_VALUE_COUNT
        assert available_now == HORIZON_STEPS

        quality_passed, coverage, _ = _finalize(cursor, fixture)
        assert quality_passed is True
        assert coverage == pytest.approx(PARTIAL_HORIZON_COVERAGE)


def test_finalizers_reject_a_null_expected_checksum(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A NULL checksum must not slip past the SHA-256 format gate."""
    unknown_id = str(uuid.uuid4())
    with agri_db_connection.cursor() as cursor:
        for statement, message in (
            ("SELECT agri.finalize_forecast_hindcast_run(%s, NULL)", "hindcast receipt checksum must be SHA-256"),
            ("SELECT agri.finalize_forecast_receipt(%s, NULL)", "receipt checksum must be SHA-256"),
            (
                "SELECT agri.finalize_strategy_label_release(%s, NULL)",
                "strategy label release checksum must be SHA-256",
            ),
            (
                "SELECT agri.finalize_strategy_selection_receipt(%s, NULL)",
                "strategy selection receipt checksum must be SHA-256",
            ),
        ):
            cursor.execute("SAVEPOINT null_checksum")
            with pytest.raises(psycopg2.Error, match=message):
                cursor.execute(statement, (unknown_id,))
            cursor.execute("ROLLBACK TO SAVEPOINT null_checksum")


def test_unknown_receipt_digest_version_cannot_enter_the_hindcast_plane(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """The digest-version set is closed at the table, the insert contract, and the guard."""
    with agri_db_connection.cursor() as cursor:
        fixture = _build_hindcast_fixture(cursor, thresholds=QualityThresholds(min_coverage_fraction=1.0))
        cursor.execute(
            """
            SELECT pg_get_constraintdef(constraint_row.oid)
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conname = 'ck_forecast_hindcast_run_receipt_digest_version'
              AND constraint_row.conrelid = 'agri.forecast_hindcast_run'::regclass
            """
        )
        definition = cursor.fetchone()[0]
        assert "hindcast_v1" in definition
        assert "hindcast_v2" in definition
        assert "hindcast_v3" in definition
        assert "hindcast_v4" not in definition

        cursor.execute("SAVEPOINT unknown_digest")
        with pytest.raises(psycopg2.Error, match="hindcast identity is immutable"):
            cursor.execute(
                """
                UPDATE agri.forecast_hindcast_run
                SET receipt_digest_version = 'hindcast_v9'
                WHERE id = %s
                """,
                (fixture.hindcast_run_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT unknown_digest")

        for rejected_version in ("hindcast_v2", "hindcast_v9"):
            cursor.execute("SAVEPOINT rejected_digest")
            with pytest.raises(psycopg2.Error, match="require receipt digest version hindcast_v3"):
                cursor.execute(
                    """
                    INSERT INTO agri.forecast_hindcast_run(
                        hindcast_key, forecast_run_id, series_id, release_set_id,
                        simulated_cutoff_time, uncertainty_calibration_cutoff_time,
                        horizon_steps, step_interval, minimum_training_points,
                        training_point_count, expected_value_count, availability_mode,
                        input_release_checksum, model_checksum, parameter_checksum,
                        receipt_digest_version
                    )
                    SELECT
                        hindcast_key || %s, forecast_run_id, series_id, release_set_id,
                        simulated_cutoff_time - interval '1 day',
                        uncertainty_calibration_cutoff_time - interval '1 day',
                        horizon_steps, step_interval, minimum_training_points,
                        training_point_count, expected_value_count, availability_mode,
                        input_release_checksum, model_checksum, parameter_checksum,
                        %s
                    FROM agri.forecast_hindcast_run WHERE id = %s
                    """,
                    (f"-{rejected_version}", rejected_version, fixture.hindcast_run_id),
                )
            cursor.execute("ROLLBACK TO SAVEPOINT rejected_digest")
