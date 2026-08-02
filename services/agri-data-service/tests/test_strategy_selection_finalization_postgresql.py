"""Positive-path proof for the strategy-selection hard gate (finding: no such test existed).

``finalize_strategy_selection_receipt`` has several tests that prove it *refuses* --
missing lineage, an inverted cutoff, an absent quality-passed hindcast (see
``test_strategy_selection_gates_postgresql.py`` and
``test_forecast_quality_gate_postgresql.py``) -- but none that reach a real
``finalize`` success. This module builds one complete, validated lineage --
outcome definition, strategy, label episodes (treatment + control),
``finalize_strategy_label_release``, a validated ``forecast_training_run``
(``validate_forecast_training_run``), an approved selection policy, a finalized
``evaluation_only`` forecast iteration, and a finalized ``quality_passed``
hindcast bound to the same series -- and asserts ``finalize_strategy_selection_receipt``
actually succeeds. A second test flips only the hindcast's quality outcome to
``quality_passed = false`` and asserts the exact quality-gate refusal message,
so both directions of the hard gate are proven end to end rather than assumed.
"""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

TRAINING_POINT_COUNT = 30
HORIZON_STEPS = 10
CUTOFF_DAY = TRAINING_POINT_COUNT - 1
MINIMUM_TRAINING_POINTS = 10
CALIBRATION_BACK_DAYS = HORIZON_STEPS
FIXTURE_START = datetime(2026, 1, 1, tzinfo=UTC)
LABEL_START = datetime(2025, 1, 1, tzinfo=UTC)
SUBJECT_POLYGON_WKT = "POLYGON((-116.3 43.5, -116.1 43.5, -116.1 43.7, -116.3 43.7, -116.3 43.5))"
EVIDENCE_POINT_WKT = "POINT(-116.2 43.6)"
OUTSIDE_BAND_OFFSET = 3.0


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _pin_checksum_rendering_gucs(cursor: psycopg2.extensions.cursor) -> None:
    """Match the GUCs the checksum functions themselves pin (see 0011/0014 AGENTS notes).

    Every checksum this fixture pre-computes client-side (episode/taxonomy/feature
    checksums) must render timestamps, intervals, and floats identically to
    ``agri.strategy_label_episode_checksum`` and friends, which pin these GUCs for
    the duration of their own call. Pinning them once for the whole transaction
    keeps the pre-computed and server-recomputed digests byte-identical.
    """
    cursor.execute(
        """
        SET LOCAL "TimeZone" TO 'UTC';
        SET LOCAL "DateStyle" TO 'ISO, MDY';
        SET LOCAL "IntervalStyle" TO 'postgres';
        SET LOCAL extra_float_digits TO 1;
        """
    )


@dataclass(frozen=True)
class SeriesFixture:
    """Shared linear time series backing both the hindcast and the forecast iteration."""

    series_id: uuid.UUID
    release_set_id: uuid.UUID
    source_release_id: uuid.UUID
    entity_state_id: uuid.UUID
    cutoff: datetime


def _build_shared_series(
    cursor: psycopg2.extensions.cursor, suffix: str, *, actual_offset: float = 0.0
) -> SeriesFixture:
    """Build one governed, license-approved series with 40 days of exact-linear history.

    ``actual_offset`` shifts every post-cutoff (horizon) observation away from the
    exact linear trend, without touching the training-window observations the
    forecast iteration relies on, so it can be used to make the hindcast's
    interval-coverage gate fail deliberately without breaking the iteration.
    """
    start = FIXTURE_START
    cutoff = start + timedelta(days=CUTOFF_DAY)
    cursor.execute(
        """
        INSERT INTO agri.data_source(
            key, name, owner, purpose, license_name, license_url, citation,
            review_state, reviewed_at, reviewed_by, created_at, updated_at
        )
        VALUES (%s, 'Selection lineage fixture', 'PlantGeo test',
                'rolled-back validation', 'CC0-1.0',
                'https://creativecommons.org/publicdomain/zero/1.0/', 'test fixture',
                'approved', %s, 'PlantGeo test', %s, %s)
        RETURNING id
        """,
        (f"selection-lineage-{suffix}", start, start, start),
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
                'fixture-transform-v1', 'CC0-1.0', 'valid', %s)
        RETURNING id
        """,
        (
            data_source_id,
            start,
            start,
            start,
            start + timedelta(days=TRAINING_POINT_COUNT + HORIZON_STEPS),
            _digest(f"{suffix}-payload"),
            start,
        ),
    )
    source_release_id = cursor.fetchone()[0]
    release_checksum = _digest(f"{suffix}-set")
    cursor.execute(
        """
        INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state, created_at)
        VALUES (%s, %s, %s, 'draft', %s) RETURNING id
        """,
        (f"selection-lineage-{suffix}", start, release_checksum, start),
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
        VALUES (%s, 'fixture-v1', 'forecast_observation', %s,
                'fixture-transform-v1', 'test_object', 'object-1', 'linear_metric',
                'unit', 'raw_native', 'point_sample', interval '1 day', interval '1 day',
                '{"fixture":true}'::jsonb, %s)
        RETURNING id
        """,
        (f"selection-lineage-series-{suffix}", data_source_id, start),
    )
    series_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_entity_state(
            series_id, source_release_id, state_key, valid_from, valid_to,
            data_available_at, state_checksum
        )
        VALUES (%s, %s, 'fixture-state-v1', %s, %s, %s, %s)
        RETURNING id
        """,
        (
            series_id,
            source_release_id,
            start,
            start + timedelta(days=TRAINING_POINT_COUNT + HORIZON_STEPS + 1),
            start,
            _digest(f"{suffix}-state"),
        ),
    )
    entity_state_id = cursor.fetchone()[0]

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
                start + timedelta(days=day, hours=1),
                5.0 + 2.0 * day + (actual_offset if day > CUTOFF_DAY else 0.0),
                f"event-{day}",
                _digest(f"{suffix}:{day}"),
            )
            for day in range(TRAINING_POINT_COUNT + HORIZON_STEPS)
        ],
    )
    return SeriesFixture(
        series_id=series_id,
        release_set_id=release_set_id,
        source_release_id=source_release_id,
        entity_state_id=entity_state_id,
        cutoff=cutoff,
    )


def _build_hindcast(
    cursor: psycopg2.extensions.cursor,
    series: SeriesFixture,
    suffix: str,
    *,
    min_coverage_fraction: float = 1.0,
    min_interval_coverage_fraction: float = 0.8,
) -> tuple[uuid.UUID, datetime, bool]:
    """Build and finalize one sql_linear hindcast bound to the shared series.

    Returns the finalized hindcast's ``(id, recorded_at, quality_passed)``. A caller
    that built the series with a nonzero ``actual_offset`` will see every horizon
    actual fall outside its residual band, driving ``interval_coverage_fraction``
    to ``0.0`` -- below any positive ``min_interval_coverage_fraction`` -- so
    ``quality_passed`` can be made to fail deliberately without missing any actuals.
    """
    code_checksum = _digest(f"{suffix}-hindcast-code")
    feature_checksum = _digest(f"{suffix}-hindcast-features")
    parameter_checksum = _digest(f"{suffix}-hindcast-parameters")
    metrics_checksum = _digest(f"{suffix}-hindcast-metrics")
    as_of = series.cutoff + timedelta(days=HORIZON_STEPS + 5)

    cursor.execute(
        """
        INSERT INTO agri.job_definition(name, version, handler)
        VALUES (%s, 'v1', 'selection-lineage-hindcast') RETURNING id
        """,
        (f"selection-lineage-hindcast-{suffix}",),
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
        (
            job_definition_id,
            series.release_set_id,
            f"selection-lineage-hindcast-{suffix}",
            FIXTURE_START,
            FIXTURE_START,
        ),
    )
    job_run_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_feature_snapshot(
            snapshot_key, job_run_id, release_set_id, input_release_checksum,
            feature_recipe_version, feature_code_checksum, feature_checksum,
            training_window_start, training_window_end, row_count
        )
        SELECT %s, %s, %s, release_set.manifest_checksum, 'fixture-v1', %s, %s, %s, %s, %s
        FROM agri.release_set AS release_set WHERE release_set.id = %s
        RETURNING id
        """,
        (
            f"selection-lineage-hindcast-snapshot-{suffix}",
            job_run_id,
            series.release_set_id,
            code_checksum,
            feature_checksum,
            FIXTURE_START,
            series.cutoff,
            TRAINING_POINT_COUNT,
            series.release_set_id,
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
        (f"selection-lineage-hindcast-model-{suffix}", code_checksum),
    )
    model_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_quality_policy(
            policy_key, min_training_points, min_backtest_points,
            min_coverage_fraction, min_interval_coverage_fraction
        )
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (
            f"selection-lineage-hindcast-policy-{suffix}",
            TRAINING_POINT_COUNT,
            HORIZON_STEPS,
            min_coverage_fraction,
            min_interval_coverage_fraction,
        ),
    )
    quality_policy_id = cursor.fetchone()[0]

    cursor.execute("SELECT manifest_checksum FROM agri.release_set WHERE id = %s", (series.release_set_id,))
    release_checksum = cursor.fetchone()[0]
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
            f"selection-lineage-hindcast-run-{suffix}",
            job_run_id,
            snapshot_id,
            model_id,
            quality_policy_id,
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
        (job_run_id, f"selection-lineage-hindcast-backtest-{suffix}", metrics_checksum, as_of),
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
            series.series_id,
            series.cutoff,
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
            f"selection-lineage-hindcast-{suffix}",
            forecast_run_id,
            series.series_id,
            series.release_set_id,
            series.cutoff,
            series.cutoff - timedelta(days=CALIBRATION_BACK_DAYS),
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            TRAINING_POINT_COUNT,
            HORIZON_STEPS,
            release_checksum,
            code_checksum,
            parameter_checksum,
        ),
    )
    hindcast_run_id = cursor.fetchone()[0]
    cursor.execute(
        """
        WITH regression AS (
            SELECT * FROM agri.forecast_linear_regression(%s, %s, %s, %s, %s, interval '1 day', %s)
        ), bands AS (
            SELECT * FROM agri.forecast_linear_residual_bands(%s, %s, %s, %s, %s, interval '1 day', %s)
        ), naive AS (
            SELECT metric_value
            FROM agri.forecast_timeseries_base(%s, %s)
            WHERE series_id = %s AND observed_at <= %s
            ORDER BY observed_at DESC LIMIT 1
        )
        INSERT INTO agri.forecast_hindcast_value(
            hindcast_run_id, valid_time, horizon_step, point_value,
            p10_value, p50_value, p90_value, naive_value, actual_value,
            actual_source_release_id, actual_observation_checksum, actual_data_available_at
        )
        SELECT
            %s, regression.valid_time, regression.horizon_step, regression.forecast_value,
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
            series.series_id,
            series.release_set_id,
            as_of,
            series.cutoff,
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            series.series_id,
            series.release_set_id,
            as_of,
            series.cutoff - timedelta(days=CALIBRATION_BACK_DAYS),
            HORIZON_STEPS,
            MINIMUM_TRAINING_POINTS,
            series.release_set_id,
            as_of,
            series.series_id,
            series.cutoff,
            hindcast_run_id,
            series.release_set_id,
            as_of,
            series.series_id,
        ),
    )
    assert cursor.rowcount == HORIZON_STEPS
    cursor.execute("SELECT agri.forecast_hindcast_receipt_checksum(%s)", (hindcast_run_id,))
    receipt_checksum = cursor.fetchone()[0]
    cursor.execute(
        "SELECT quality_passed, recorded_at FROM agri.finalize_forecast_hindcast_run(%s, %s)",
        (hindcast_run_id, receipt_checksum),
    )
    quality_passed, recorded_at = cursor.fetchone()
    return hindcast_run_id, recorded_at, quality_passed


def _build_iteration(
    cursor: psycopg2.extensions.cursor, series: SeriesFixture, suffix: str
) -> tuple[uuid.UUID, datetime]:
    """Materialize and finalize an evaluation-only forecast iteration on the shared series."""
    cursor.execute("SELECT clock_timestamp()")
    as_of_time = cursor.fetchone()[0]
    cursor.execute(
        """
        CALL agri.materialize_forecast_iteration(
            NULL, %s, %s, %s, %s, %s, NULL, 30, 100, 42, 'strict', NULL, NULL
        )
        """,
        (
            f"selection-lineage-iteration-{suffix}",
            series.series_id,
            series.release_set_id,
            as_of_time,
            series.cutoff,
        ),
    )
    iteration_id = cursor.fetchone()[0]
    cursor.execute("SELECT status, as_of_time FROM agri.forecast_iteration WHERE id = %s", (iteration_id,))
    status, iteration_as_of = cursor.fetchone()
    assert status == "finalized"
    return iteration_id, iteration_as_of


@dataclass(frozen=True)
class LabelLineage:
    """Identities from a fully validated strategy-label release with one treatment/control pair."""

    label_release_id: uuid.UUID
    label_release_set_id: uuid.UUID
    label_receipt_checksum: str
    treatment_subject_id: uuid.UUID
    training_window_end: datetime
    as_of_time: datetime


def _jsonb_text_checksum(cursor: psycopg2.extensions.cursor, payload: str) -> str:
    """The checksum PostgreSQL will later recompute from ``payload::jsonb::text``."""
    cursor.execute("SELECT encode(digest((%s::jsonb)::text, 'sha256'), 'hex')", (payload,))
    return cursor.fetchone()[0]


def _build_label_lineage(cursor: psycopg2.extensions.cursor, suffix: str) -> LabelLineage:  # noqa: PLR0915
    """Build one approved outcome, one approved treatment strategy, and a validated label release."""
    start = LABEL_START
    cursor.execute(
        """
        INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
        VALUES (%s, 'Selection label fixture', 'PlantGeo test', 'rolled-back validation',
                'test-only', 'test-only')
        RETURNING id
        """,
        (f"selection-label-{suffix}",),
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
            start,
            start,
            start - timedelta(days=10),
            start + timedelta(days=75),
            _digest(f"{suffix}-label-payload"),
            start,
        ),
    )
    source_release_id = cursor.fetchone()[0]
    release_checksum = _digest(f"{suffix}-label-set")
    cursor.execute(
        """
        INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state)
        VALUES (%s, %s, %s, 'draft') RETURNING id
        """,
        (f"selection-label-{suffix}", start, release_checksum),
    )
    label_release_set_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO agri.release_set_item(release_set_id, source_release_id) VALUES (%s, %s)",
        (label_release_set_id, source_release_id),
    )
    cursor.execute(
        "UPDATE agri.release_set SET state = 'validated', validated_at = %s WHERE id = %s",
        (start, label_release_set_id),
    )

    cursor.execute(
        """
        INSERT INTO agri.strategy_outcome_definition(
            definition_key, definition_version, metric_name, metric_unit, benefit_direction,
            smallest_meaningful_effect, baseline_window, outcome_window,
            aggregation_method, transform_method
        )
        VALUES (%s, 'v1', 'yield_delta', 'pct', 'increase', 0,
                interval '30 days', interval '30 days', 'mean', 'identity')
        RETURNING id
        """,
        (f"selection-label-outcome-{suffix}",),
    )
    outcome_definition_id = cursor.fetchone()[0]
    cursor.execute(
        """
        UPDATE agri.strategy_outcome_definition
           SET review_state = 'approved', reviewed_at = %s, reviewed_by = 'PlantGeo test'
         WHERE id = %s
         RETURNING definition_checksum
        """,
        (start, outcome_definition_id),
    )
    outcome_definition_checksum = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO agri.strategies(
            name, slug, category, authority, practice_code, description,
            water_requirement, labor_intensity, carbon_seq_potential, biodiversity_impact,
            review_state, reviewed_at, reviewed_by, evidence_citation, evidence_source_url,
            jurisdiction, limitations
        )
        VALUES (%s, %s, 'test-category', 'test-authority', %s, 'fixture strategy',
                'low', 'low', 'low', 'low', 'approved', %s, 'PlantGeo test',
                'test fixture citation', 'https://example.test/evidence', 'US', 'none')
        RETURNING id
        """,
        (f"Selection lineage strategy {suffix}", f"selection-lineage-strategy-{suffix}", suffix, start),
    )
    strategy_id = cursor.fetchone()[0]

    def _build_subject(arm: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        arm_suffix = f"{suffix}-{arm}"
        cursor.execute(
            """
            INSERT INTO agri.artifact(source_release_id, kind, uri, checksum_sha256, size_bytes)
            VALUES (%s, 'test', %s, %s, 0) RETURNING id
            """,
            (source_release_id, f"test://selection-subject/{arm_suffix}", _digest(f"{arm_suffix}-subject-artifact")),
        )
        subject_artifact_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.normalized_source_feature(
                source_release_id, artifact_id, feature_key, feature_kind, geometry,
                geometry_checksum, data_available_at, spatial_support_kind, native_scale,
                maximum_inference_scale, confidence_basis, method_key, method_version,
                feature_checksum
            )
            VALUES (%s, %s, %s, 'test_geometry', ST_GeomFromText(%s, 4326), %s, %s,
                    'administrative_boundary', 'test fixture', 'city', 'unassessed',
                    'fixture', 'v1', %s)
            RETURNING id
            """,
            (
                source_release_id,
                subject_artifact_id,
                f"selection-lineage-feature-{arm_suffix}",
                SUBJECT_POLYGON_WKT,
                _digest(f"{arm_suffix}-geometry"),
                start,
                _digest(f"{arm_suffix}-feature"),
            ),
        )
        source_feature_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.analysis_subject(
                subject_key, subject_version, subject_kind, source_release_id,
                artifact_id, source_feature_id, display_name, country_code,
                geometry, geometry_checksum, spatial_support_kind, native_scale,
                maximum_inference_scale, confidence_basis, method_key, method_version
            )
            VALUES (%s, 'v1', 'city', %s, %s, %s, %s, 'US',
                    ST_GeomFromText(%s, 4326), %s, 'administrative_boundary',
                    'test fixture', 'city', 'unassessed', 'fixture', 'v1')
            RETURNING id
            """,
            (
                f"selection-lineage-subject-{arm_suffix}",
                source_release_id,
                subject_artifact_id,
                source_feature_id,
                f"Selection lineage {arm} city",
                SUBJECT_POLYGON_WKT,
                _digest(f"{arm_suffix}-geometry"),
            ),
        )
        analysis_subject_id = cursor.fetchone()[0]
        return analysis_subject_id, subject_artifact_id, source_feature_id

    treatment_subject_id, _, treatment_feature_id = _build_subject("treatment")
    control_subject_id, _, control_feature_id = _build_subject("control")

    def _insert_evidence(  # noqa: PLR0913
        *,
        arm: str,
        role: str,
        subject_id: uuid.UUID,
        source_feature_id: uuid.UUID,
        numeric_value: float,
        observed_from: datetime,
        observed_to: datetime,
        data_available_at: datetime,
    ) -> uuid.UUID:
        cursor.execute(
            """
            INSERT INTO agri.intervention_evidence_input(
                release_set_id, analysis_subject_id, evidence_kind, metric_name, numeric_value,
                value_unit, evidence_geometry, observed_from, observed_to, data_available_at,
                spatial_support_kind, native_scale, maximum_inference_scale, confidence_basis,
                method_key, method_version, evidence_checksum
            )
            VALUES (%s, %s, 'observed_fact', 'yield_delta', %s, 'pct',
                    ST_GeomFromText(%s, 4326), %s, %s, %s,
                    'point_sample', 'test fixture', 'city', 'unassessed', 'fixture', 'v1', %s)
            RETURNING id
            """,
            (
                label_release_set_id,
                subject_id,
                numeric_value,
                EVIDENCE_POINT_WKT,
                observed_from,
                observed_to,
                data_available_at,
                _digest(f"{suffix}-{arm}-{role}-evidence"),
            ),
        )
        evidence_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.intervention_evidence_lineage(
                evidence_input_id, source_release_id, source_feature_id, lineage_role, input_order
            )
            VALUES (%s, %s, %s, 'direct_observation', 0)
            """,
            (evidence_id, source_release_id, source_feature_id),
        )
        return evidence_id

    baseline_start = start
    baseline_end = start + timedelta(days=30)
    assigned_at = baseline_end
    intervention_start = assigned_at
    intervention_end = intervention_start + timedelta(days=7)
    outcome_start = intervention_end
    outcome_end = outcome_start + timedelta(days=30)
    episode_data_available_at = outcome_end + timedelta(days=1)
    label_as_of_time = episode_data_available_at + timedelta(days=2)

    feature_schema_payload = json.dumps(["soil_moisture"])
    feature_schema_checksum = _jsonb_text_checksum(cursor, feature_schema_payload)
    taxonomy_payload = json.dumps([{"strategy_id": str(strategy_id)}])
    taxonomy_checksum = _jsonb_text_checksum(cursor, taxonomy_payload)
    extraction_plan_checksum = _digest(f"{suffix}-extraction-plan")
    extraction_code_checksum = _digest(f"{suffix}-extraction-code")

    cursor.execute(
        """
        INSERT INTO agri.strategy_label_release(
            release_key, release_set_id, outcome_definition_id, as_of_time,
            strategy_taxonomy_snapshot, strategy_taxonomy_checksum,
            feature_schema, feature_schema_checksum,
            extraction_plan_checksum, extraction_code_checksum,
            spatial_block_scheme, row_count, treated_count, control_count,
            strategy_count, spatial_block_count
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s,
                'fixture-blocks-v1', 2, 1, 1, 1, 1)
        RETURNING id
        """,
        (
            f"selection-label-release-{suffix}",
            label_release_set_id,
            outcome_definition_id,
            label_as_of_time,
            taxonomy_payload,
            taxonomy_checksum,
            feature_schema_payload,
            feature_schema_checksum,
            extraction_plan_checksum,
            extraction_code_checksum,
        ),
    )
    label_release_id = cursor.fetchone()[0]

    def _insert_episode(  # noqa: PLR0913
        *,
        arm: str,
        arm_kind: str,
        arm_strategy_id: uuid.UUID | None,
        subject_id: uuid.UUID,
        source_feature_id: uuid.UUID,
        baseline_value: float,
        outcome_value: float,
    ) -> None:
        baseline_evidence_id = _insert_evidence(
            arm=arm,
            role="baseline",
            subject_id=subject_id,
            source_feature_id=source_feature_id,
            numeric_value=baseline_value,
            observed_from=baseline_start - timedelta(days=5),
            observed_to=baseline_end + timedelta(days=5),
            data_available_at=baseline_end + timedelta(days=1),
        )
        outcome_evidence_id = _insert_evidence(
            arm=arm,
            role="outcome",
            subject_id=subject_id,
            source_feature_id=source_feature_id,
            numeric_value=outcome_value,
            observed_from=outcome_start - timedelta(days=5),
            observed_to=outcome_end + timedelta(days=3),
            data_available_at=outcome_end + timedelta(days=1),
        )
        cursor.execute(
            "SELECT evidence_checksum FROM agri.intervention_evidence_input WHERE id = %s",
            (baseline_evidence_id,),
        )
        baseline_evidence_checksum = cursor.fetchone()[0]
        cursor.execute(
            "SELECT evidence_checksum FROM agri.intervention_evidence_input WHERE id = %s",
            (outcome_evidence_id,),
        )
        outcome_evidence_checksum = cursor.fetchone()[0]

        covariate_payload = json.dumps([1.0 if arm_kind == "treatment" else 0.5])
        covariate_checksum = _jsonb_text_checksum(cursor, covariate_payload)
        target_value = outcome_value - baseline_value

        cursor.execute(
            """
            SELECT encode(digest(concat_ws('|',
                'strategy_label_episode_v1',
                %(episode_key)s, %(label_release_id)s::text, %(analysis_subject_id)s::text,
                coalesce(%(strategy_id)s::text, ''), %(arm_kind)s, %(cohort_key)s,
                %(assigned_at)s::text, %(intervention_start)s::text, %(intervention_end)s::text,
                %(baseline_start)s::text, %(baseline_end)s::text,
                %(outcome_start)s::text, %(outcome_end)s::text,
                %(baseline_evidence_input_id)s::text, %(baseline_evidence_checksum)s,
                %(outcome_evidence_input_id)s::text, %(outcome_evidence_checksum)s,
                %(target_value)s::double precision::text, %(target_unit)s, %(assignment_mechanism)s,
                coalesce(%(known_assignment_probability)s::text, ''),
                %(spatial_block_key)s, (%(covariate_snapshot)s::jsonb)::text, %(covariate_checksum)s,
                %(covariates_available_at)s::text, %(data_available_at)s::text,
                %(label_taxonomy_checksum)s, %(label_feature_schema_checksum)s,
                %(label_extraction_plan_checksum)s, %(label_extraction_code_checksum)s,
                %(outcome_definition_checksum)s
            ), 'sha256'), 'hex')
            """,
            {
                "episode_key": f"selection-lineage-episode-{suffix}-{arm}",
                "label_release_id": str(label_release_id),
                "analysis_subject_id": str(subject_id),
                "strategy_id": str(arm_strategy_id) if arm_strategy_id else None,
                "arm_kind": arm_kind,
                "cohort_key": f"cohort-{suffix}-{arm}",
                "assigned_at": assigned_at,
                "intervention_start": intervention_start,
                "intervention_end": intervention_end,
                "baseline_start": baseline_start,
                "baseline_end": baseline_end,
                "outcome_start": outcome_start,
                "outcome_end": outcome_end,
                "baseline_evidence_input_id": str(baseline_evidence_id),
                "baseline_evidence_checksum": baseline_evidence_checksum,
                "outcome_evidence_input_id": str(outcome_evidence_id),
                "outcome_evidence_checksum": outcome_evidence_checksum,
                "target_value": target_value,
                "target_unit": "pct",
                "assignment_mechanism": "randomized",
                "known_assignment_probability": None,
                "spatial_block_key": "block-a",
                "covariate_snapshot": covariate_payload,
                "covariate_checksum": covariate_checksum,
                "covariates_available_at": baseline_start,
                "data_available_at": episode_data_available_at,
                "label_taxonomy_checksum": taxonomy_checksum,
                "label_feature_schema_checksum": feature_schema_checksum,
                "label_extraction_plan_checksum": extraction_plan_checksum,
                "label_extraction_code_checksum": extraction_code_checksum,
                "outcome_definition_checksum": outcome_definition_checksum,
            },
        )
        episode_checksum = cursor.fetchone()[0]

        cursor.execute(
            """
            INSERT INTO agri.strategy_label_episode(
                episode_key, label_release_id, analysis_subject_id, strategy_id, arm_kind,
                cohort_key, assigned_at, intervention_start, intervention_end,
                baseline_start, baseline_end, outcome_start, outcome_end,
                baseline_evidence_input_id, outcome_evidence_input_id,
                target_value, target_unit, assignment_mechanism, spatial_block_key,
                covariate_snapshot, covariate_checksum, covariates_available_at,
                data_available_at, episode_checksum
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, 'randomized', %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                f"selection-lineage-episode-{suffix}-{arm}",
                label_release_id,
                subject_id,
                arm_strategy_id,
                arm_kind,
                f"cohort-{suffix}-{arm}",
                assigned_at,
                intervention_start,
                intervention_end,
                baseline_start,
                baseline_end,
                outcome_start,
                outcome_end,
                baseline_evidence_id,
                outcome_evidence_id,
                target_value,
                "pct",
                "block-a",
                covariate_payload,
                covariate_checksum,
                baseline_start,
                episode_data_available_at,
                episode_checksum,
            ),
        )

    _insert_episode(
        arm="treatment",
        arm_kind="treatment",
        arm_strategy_id=strategy_id,
        subject_id=treatment_subject_id,
        source_feature_id=treatment_feature_id,
        baseline_value=10.0,
        outcome_value=15.0,
    )
    _insert_episode(
        arm="control",
        arm_kind="control",
        arm_strategy_id=None,
        subject_id=control_subject_id,
        source_feature_id=control_feature_id,
        baseline_value=10.0,
        outcome_value=10.0,
    )

    cursor.execute("SELECT agri.strategy_label_release_checksum(%s)", (label_release_id,))
    label_expected_checksum = cursor.fetchone()[0]
    cursor.execute(
        "SELECT status, receipt_checksum FROM agri.finalize_strategy_label_release(%s, %s)",
        (label_release_id, label_expected_checksum),
    )
    status, receipt_checksum = cursor.fetchone()
    assert status == "validated"

    return LabelLineage(
        label_release_id=label_release_id,
        label_release_set_id=label_release_set_id,
        label_receipt_checksum=receipt_checksum,
        treatment_subject_id=treatment_subject_id,
        training_window_end=label_as_of_time,
        as_of_time=label_as_of_time,
    )


def _build_training(
    cursor: psycopg2.extensions.cursor, suffix: str, label: LabelLineage
) -> tuple[uuid.UUID, uuid.UUID]:
    """Build a validated strategy-selection ``forecast_training_run`` bound to ``label``."""
    feature_checksum = _digest(f"{suffix}-strategy-features")
    code_checksum = _digest(f"{suffix}-strategy-model-code")
    model_artifact_checksum = _digest(f"{suffix}-strategy-model-artifact")
    validation_checksum = _digest(f"{suffix}-strategy-validation")

    cursor.execute(
        """
        INSERT INTO agri.job_definition(name, version, handler)
        VALUES (%s, 'v1', 'selection-lineage-training') RETURNING id
        """,
        (f"selection-lineage-training-{suffix}",),
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
        (
            job_definition_id,
            label.label_release_set_id,
            f"selection-lineage-training-{suffix}",
            label.training_window_end,
            label.training_window_end,
        ),
    )
    job_run_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO agri.artifact(kind, uri, checksum_sha256, size_bytes)
        VALUES ('forecast_feature', %s, %s, 0) RETURNING id
        """,
        (f"test://selection-lineage-feature/{suffix}", feature_checksum),
    )
    feature_artifact_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.job_output(
            job_run_id, artifact_id, output_key, kind, state, checksum_sha256, row_count, validated_at
        )
        VALUES (%s, %s, %s, 'strategy_feature', 'validated', %s, 2, %s) RETURNING id
        """,
        (
            job_run_id,
            feature_artifact_id,
            f"selection-lineage-feature-output-{suffix}",
            feature_checksum,
            label.training_window_end,
        ),
    )
    feature_output_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_feature_snapshot(
            snapshot_key, job_run_id, release_set_id, input_release_checksum,
            feature_recipe_version, feature_code_checksum, feature_checksum,
            training_window_start, training_window_end, row_count, job_output_id
        )
        SELECT %s, %s, %s, release_set.manifest_checksum, 'fixture-v1', %s, %s, %s, %s, 2, %s
        FROM agri.release_set AS release_set WHERE release_set.id = %s
        RETURNING id
        """,
        (
            f"selection-lineage-training-snapshot-{suffix}",
            job_run_id,
            label.label_release_set_id,
            code_checksum,
            feature_checksum,
            LABEL_START,
            label.training_window_end,
            feature_output_id,
            label.label_release_set_id,
        ),
    )
    feature_snapshot_id = cursor.fetchone()[0]
    cursor.execute("SELECT (agri.validate_forecast_feature_snapshot(%s)).status", (feature_snapshot_id,))
    assert cursor.fetchone()[0] == "validated"

    cursor.execute(
        """
        INSERT INTO agri.artifact(kind, uri, checksum_sha256, size_bytes)
        VALUES ('forecast_model', %s, %s, 0) RETURNING id
        """,
        (f"test://selection-lineage-model/{suffix}", model_artifact_checksum),
    )
    model_artifact_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_model(
            model_key, model_version, model_kind, model_purpose, algorithm,
            model_code_checksum, artifact_id
        )
        VALUES (%s, 'v1', 'ml', 'strategy_selection', 'test-only-local-ml', %s, %s) RETURNING id
        """,
        (f"selection-lineage-model-{suffix}", code_checksum, model_artifact_id),
    )
    model_id = cursor.fetchone()[0]

    cursor.execute("SELECT agri.strategy_label_bundle_checksum(%s)", (label.label_release_id,))
    label_bundle_checksum = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.job_output(
            job_run_id, artifact_id, output_key, kind, state, checksum_sha256,
            row_count, metadata_json, validated_at
        )
        VALUES (%s, %s, %s, 'model_training', 'validated', %s, 1, %s::jsonb, %s)
        RETURNING id
        """,
        (
            job_run_id,
            model_artifact_id,
            f"selection-lineage-training-output-{suffix}",
            model_artifact_checksum,
            json.dumps(
                {
                    "validation_checksum": validation_checksum,
                    "strategy_label_checksum": label.label_receipt_checksum,
                    "label_bundle_checksum": label_bundle_checksum,
                }
            ),
            label.training_window_end,
        ),
    )
    training_output_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO agri.forecast_training_run(
            training_key, model_id, job_run_id, job_output_id, feature_snapshot_id,
            input_release_checksum, feature_checksum, training_code_checksum,
            strategy_label_release_id, strategy_label_checksum
        )
        SELECT %s, %s, %s, %s, %s, release_set.manifest_checksum, %s, %s, %s, %s
        FROM agri.release_set AS release_set WHERE release_set.id = %s
        RETURNING id
        """,
        (
            f"selection-lineage-training-{suffix}",
            model_id,
            job_run_id,
            training_output_id,
            feature_snapshot_id,
            feature_checksum,
            code_checksum,
            label.label_release_id,
            label.label_receipt_checksum,
            label.label_release_set_id,
        ),
    )
    training_run_id = cursor.fetchone()[0]
    cursor.execute(
        "SELECT status FROM agri.validate_forecast_training_run(%s, %s, %s, '{}'::jsonb)",
        (training_run_id, model_artifact_checksum, validation_checksum),
    )
    assert cursor.fetchone()[0] == "validated"
    return training_run_id, feature_snapshot_id


def _build_policy(cursor: psycopg2.extensions.cursor, suffix: str) -> uuid.UUID:
    cursor.execute(
        """
        INSERT INTO agri.strategy_selection_policy(
            policy_key, policy_version, min_treated_per_strategy, min_control_count,
            min_spatial_blocks, min_effective_sample_size, min_overlap_score,
            max_weighted_smd, min_coverage_fraction, max_data_age,
            min_conservative_value_gain, max_model_disagreement, max_ood_score,
            score_weights
        )
        VALUES (%s, 'v1', 1, 1, 1, 1, 0.5, 1.0, 0.5, interval '9999 days',
                0, 1, 1, '{"conservative_benefit": 1}'::jsonb)
        RETURNING id
        """,
        (f"selection-lineage-policy-{suffix}",),
    )
    policy_id = cursor.fetchone()[0]
    cursor.execute(
        """
        UPDATE agri.strategy_selection_policy
           SET review_state = 'approved', reviewed_at = %s, reviewed_by = 'PlantGeo test'
         WHERE id = %s
        """,
        (LABEL_START, policy_id),
    )
    return policy_id


def _build_receipt(  # noqa: PLR0913
    cursor: psycopg2.extensions.cursor,
    suffix: str,
    *,
    label: LabelLineage,
    training_run_id: uuid.UUID,
    feature_snapshot_id: uuid.UUID,
    policy_id: uuid.UUID,
    iteration_id: uuid.UUID,
    iteration_as_of: datetime,
    data_cutoff: datetime,
) -> tuple[uuid.UUID, str]:
    cursor.execute(
        """
        INSERT INTO agri.strategy_selection_receipt(
            selection_key, analysis_subject_id, forecast_iteration_id,
            feature_snapshot_id, training_run_id, selection_policy_id,
            issue_time, applicability_start, applicability_end, data_cutoff,
            decision_state, abstention_reason, candidate_count
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'abstained', %s, 0)
        RETURNING id
        """,
        (
            f"selection-lineage-receipt-{suffix}",
            label.treatment_subject_id,
            iteration_id,
            feature_snapshot_id,
            training_run_id,
            policy_id,
            iteration_as_of,
            iteration_as_of,
            iteration_as_of + timedelta(days=30),
            data_cutoff,
            "fixture: proving the strategy-selection hard-gate finalize path",
        ),
    )
    receipt_id = cursor.fetchone()[0]
    cursor.execute("SELECT agri.strategy_selection_receipt_checksum(%s)", (receipt_id,))
    expected_checksum = cursor.fetchone()[0]
    return receipt_id, expected_checksum


def test_finalize_strategy_selection_succeeds_with_a_full_validated_lineage(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A complete lineage with a passing backing hindcast finalizes successfully."""
    with agri_db_connection.cursor() as cursor:
        _pin_checksum_rendering_gucs(cursor)
        suffix = _digest(os.urandom(16).hex())[:12]
        series = _build_shared_series(cursor, suffix)
        _hindcast_id, recorded_at, quality_passed = _build_hindcast(cursor, series, suffix)
        assert quality_passed is True
        iteration_id, iteration_as_of = _build_iteration(cursor, series, suffix)
        assert recorded_at <= iteration_as_of

        label = _build_label_lineage(cursor, suffix)
        training_run_id, feature_snapshot_id = _build_training(cursor, suffix, label)
        policy_id = _build_policy(cursor, suffix)
        receipt_id, expected_checksum = _build_receipt(
            cursor,
            suffix,
            label=label,
            training_run_id=training_run_id,
            feature_snapshot_id=feature_snapshot_id,
            policy_id=policy_id,
            iteration_id=iteration_id,
            iteration_as_of=iteration_as_of,
            data_cutoff=series.cutoff,
        )

        cursor.execute(
            "SELECT status, receipt_checksum FROM agri.finalize_strategy_selection_receipt(%s, %s)",
            (receipt_id, expected_checksum),
        )
        status, receipt_checksum = cursor.fetchone()
        assert status == "finalized"
        assert receipt_checksum == expected_checksum


def test_finalize_strategy_selection_refuses_when_the_backing_hindcast_failed_quality(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """The same lineage, but the backing hindcast's quality gate failed, must refuse to finalize."""
    with agri_db_connection.cursor() as cursor:
        _pin_checksum_rendering_gucs(cursor)
        suffix = _digest(os.urandom(16).hex())[:12]
        series = _build_shared_series(cursor, suffix, actual_offset=OUTSIDE_BAND_OFFSET)
        _hindcast_id, _recorded_at, quality_passed = _build_hindcast(cursor, series, suffix)
        assert quality_passed is False
        iteration_id, iteration_as_of = _build_iteration(cursor, series, suffix)

        label = _build_label_lineage(cursor, suffix)
        training_run_id, feature_snapshot_id = _build_training(cursor, suffix, label)
        policy_id = _build_policy(cursor, suffix)
        receipt_id, expected_checksum = _build_receipt(
            cursor,
            suffix,
            label=label,
            training_run_id=training_run_id,
            feature_snapshot_id=feature_snapshot_id,
            policy_id=policy_id,
            iteration_id=iteration_id,
            iteration_as_of=iteration_as_of,
            data_cutoff=series.cutoff,
        )

        with pytest.raises(
            psycopg2.Error,
            match="strategy selection requires a finalized quality-passed hindcast for its backing series",
        ):
            cursor.execute(
                "SELECT agri.finalize_strategy_selection_receipt(%s, %s)",
                (receipt_id, expected_checksum),
            )
