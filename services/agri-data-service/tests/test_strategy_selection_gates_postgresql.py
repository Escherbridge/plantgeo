"""Corrected strategy-selection cutoff rule and the insert-time audit-flag contract."""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

FIXTURE_ISSUE_TIME = datetime(2026, 4, 1, tzinfo=UTC)
FIXTURE_DATA_CUTOFF = FIXTURE_ISSUE_TIME - timedelta(days=1)
FIXTURE_HISTORY_START = FIXTURE_ISSUE_TIME - timedelta(days=90)
ITERATION_HORIZON_DAYS = 30
ITERATION_SIMULATION_COUNT = 100
ITERATION_TRAINING_DAY_COUNT = 60
ITERATION_INCREMENT_COUNT = 59
SUBJECT_POLYGON_WKT = "POLYGON((-116.3 43.5, -116.1 43.5, -116.1 43.7, -116.3 43.7, -116.3 43.5))"


@dataclass(frozen=True)
class SelectionFixture:
    """A staged evaluation-only selection receipt and the iteration it declares."""

    selection_receipt_id: uuid.UUID
    forecast_iteration_id: uuid.UUID
    series_id: uuid.UUID


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _build_selection_fixture(
    cursor: psycopg2.extensions.cursor,
    *,
    iteration_cutoff_time: datetime,
) -> SelectionFixture:
    """Build a staged evaluation-only selection receipt bound to a staged iteration.

    Only the identities the corrected cutoff rule reads are real; the receipt is
    deliberately left in ``staging`` because the rule is a pure function of the
    receipt and its iteration.
    """
    suffix = _digest(os.urandom(16).hex())[:12]
    cursor.execute(
        """
        INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
        VALUES (%s, 'Selection gate test', 'PlantGeo test', 'rolled-back validation',
                'test-only', 'test-only')
        RETURNING id
        """,
        (f"selection-gate-{suffix}",),
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
            FIXTURE_HISTORY_START,
            FIXTURE_HISTORY_START,
            FIXTURE_HISTORY_START,
            FIXTURE_DATA_CUTOFF,
            _digest(f"{suffix}-payload"),
            FIXTURE_HISTORY_START,
        ),
    )
    source_release_id = cursor.fetchone()[0]
    release_checksum = _digest(f"{suffix}-set")
    cursor.execute(
        """
        INSERT INTO agri.release_set(logical_key, as_of_time, manifest_checksum, state)
        VALUES (%s, %s, %s, 'draft') RETURNING id
        """,
        (f"selection-gate-{suffix}", FIXTURE_HISTORY_START, release_checksum),
    )
    release_set_id = cursor.fetchone()[0]
    cursor.execute(
        "INSERT INTO agri.release_set_item(release_set_id, source_release_id) VALUES (%s, %s)",
        (release_set_id, source_release_id),
    )
    cursor.execute(
        "UPDATE agri.release_set SET state = 'validated', validated_at = %s WHERE id = %s",
        (FIXTURE_HISTORY_START, release_set_id),
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
        (f"selection-gate-series-{suffix}", data_source_id),
    )
    series_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_iteration(
            series_id, release_set_id, iteration_key, availability_mode,
            as_of_time, cutoff_time, history_start, horizon_days,
            simulation_count, simulation_seed, input_release_checksum,
            input_license_snapshots, contract_snapshot, contract_checksum,
            history_checksum, parameter_checksum, training_day_count,
            increment_count, expected_value_count
        )
        VALUES (%s, %s, %s, 'retrospective_pinned_release', %s, %s, %s, %s,
                %s, 0, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            series_id,
            release_set_id,
            f"selection-gate-iteration-{suffix}",
            FIXTURE_ISSUE_TIME,
            iteration_cutoff_time,
            FIXTURE_HISTORY_START,
            ITERATION_HORIZON_DAYS,
            ITERATION_SIMULATION_COUNT,
            release_checksum,
            json.dumps([{"license": "test-only"}]),
            json.dumps({"schema_version": "forecast_timeseries_contract_v1"}),
            _digest(f"{suffix}-contract"),
            _digest(f"{suffix}-history"),
            _digest(f"{suffix}-parameters"),
            ITERATION_TRAINING_DAY_COUNT,
            ITERATION_INCREMENT_COUNT,
            ITERATION_HORIZON_DAYS,
        ),
    )
    forecast_iteration_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO agri.artifact(source_release_id, kind, uri, checksum_sha256, size_bytes)
        VALUES (%s, 'test', %s, %s, 0) RETURNING id
        """,
        (source_release_id, f"test://selection-subject/{suffix}", _digest(f"{suffix}-subject-artifact")),
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
            f"selection-gate-feature-{suffix}",
            SUBJECT_POLYGON_WKT,
            _digest(f"{suffix}-geometry"),
            FIXTURE_HISTORY_START,
            _digest(f"{suffix}-feature"),
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
        VALUES (%s, 'v1', 'city', %s, %s, %s, 'Selection gate city', 'US',
                ST_GeomFromText(%s, 4326), %s, 'administrative_boundary',
                'test fixture', 'city', 'unassessed', 'fixture', 'v1')
        RETURNING id
        """,
        (
            f"selection-gate-subject-{suffix}",
            source_release_id,
            subject_artifact_id,
            source_feature_id,
            SUBJECT_POLYGON_WKT,
            _digest(f"{suffix}-geometry"),
        ),
    )
    analysis_subject_id = cursor.fetchone()[0]

    code_checksum = _digest(f"{suffix}-code")
    feature_checksum = _digest(f"{suffix}-features")
    cursor.execute(
        "INSERT INTO agri.job_definition(name, version, handler) VALUES (%s, 'v1', 'selection-gate') RETURNING id",
        (f"selection-gate-{suffix}",),
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
            release_set_id,
            f"selection-gate-{suffix}",
            FIXTURE_HISTORY_START,
            FIXTURE_HISTORY_START,
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
        VALUES (%s, %s, %s, %s, 'fixture-v1', %s, %s, %s, %s, 1) RETURNING id
        """,
        (
            f"selection-gate-snapshot-{suffix}",
            job_run_id,
            release_set_id,
            release_checksum,
            code_checksum,
            feature_checksum,
            FIXTURE_HISTORY_START,
            FIXTURE_DATA_CUTOFF,
        ),
    )
    feature_snapshot_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.artifact(kind, uri, checksum_sha256, size_bytes)
        VALUES ('forecast_model', %s, %s, 0) RETURNING id
        """,
        (f"test://selection-model/{suffix}", _digest(f"{suffix}-artifact")),
    )
    artifact_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.forecast_model(
            model_key, model_version, model_kind, model_purpose, algorithm,
            model_code_checksum, artifact_id
        )
        VALUES (%s, 'v1', 'ml', 'strategy_selection', 'test-only-local-ml', %s, %s) RETURNING id
        """,
        (f"selection-gate-model-{suffix}", code_checksum, artifact_id),
    )
    model_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.job_output(
            job_run_id, artifact_id, output_key, kind, state, checksum_sha256,
            row_count, metadata_json, validated_at
        )
        VALUES (%s, %s, %s, 'model_training', 'validated', %s, 1, '{}'::jsonb, %s)
        RETURNING id
        """,
        (
            job_run_id,
            artifact_id,
            f"selection-gate-training-{suffix}",
            _digest(f"{suffix}-artifact"),
            FIXTURE_HISTORY_START,
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
            f"selection-gate-training-{suffix}",
            model_id,
            job_run_id,
            training_output_id,
            feature_snapshot_id,
            release_checksum,
            feature_checksum,
            code_checksum,
        ),
    )
    training_run_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.strategy_selection_policy(
            policy_key, policy_version, min_treated_per_strategy, min_control_count,
            min_spatial_blocks, min_effective_sample_size, min_overlap_score,
            max_weighted_smd, min_coverage_fraction, max_data_age,
            min_conservative_value_gain, max_model_disagreement, max_ood_score,
            score_weights
        )
        VALUES (%s, 'v1', 12, 24, 4, 5, 0.9, 0.25, 0.9, interval '90 days',
                0.1, 0.35, 0.5, '{"conservative_benefit": 1}'::jsonb)
        RETURNING id
        """,
        (f"selection-gate-policy-{suffix}",),
    )
    selection_policy_id = cursor.fetchone()[0]
    cursor.execute(
        """
        INSERT INTO agri.strategy_selection_receipt(
            selection_key, analysis_subject_id, forecast_iteration_id,
            feature_snapshot_id, training_run_id, selection_policy_id,
            issue_time, applicability_start, applicability_end, data_cutoff,
            decision_state, abstention_reason, candidate_count
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'abstained', 'fixture', 0)
        RETURNING id
        """,
        (
            f"selection-gate-receipt-{suffix}",
            analysis_subject_id,
            forecast_iteration_id,
            feature_snapshot_id,
            training_run_id,
            selection_policy_id,
            FIXTURE_ISSUE_TIME,
            FIXTURE_ISSUE_TIME,
            FIXTURE_ISSUE_TIME + timedelta(days=ITERATION_HORIZON_DAYS),
            FIXTURE_DATA_CUTOFF,
        ),
    )
    selection_receipt_id = cursor.fetchone()[0]
    return SelectionFixture(
        selection_receipt_id=selection_receipt_id,
        forecast_iteration_id=forecast_iteration_id,
        series_id=series_id,
    )


def test_corrected_cutoff_rule_flags_only_late_iterations(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """The shared predicate the finalizer and migration 0014 both call uses ``<=``."""
    with agri_db_connection.cursor() as cursor:
        honest = _build_selection_fixture(cursor, iteration_cutoff_time=FIXTURE_DATA_CUTOFF)
        cursor.execute(
            "SELECT agri.strategy_selection_cutoff_violation(%s)",
            (honest.selection_receipt_id,),
        )
        assert cursor.fetchone()[0] is False

        earlier = _build_selection_fixture(
            cursor,
            iteration_cutoff_time=FIXTURE_DATA_CUTOFF - timedelta(days=7),
        )
        cursor.execute(
            "SELECT agri.strategy_selection_cutoff_violation(%s)",
            (earlier.selection_receipt_id,),
        )
        assert cursor.fetchone()[0] is False

        late = _build_selection_fixture(
            cursor,
            iteration_cutoff_time=FIXTURE_DATA_CUTOFF + timedelta(hours=1),
        )
        cursor.execute(
            "SELECT agri.strategy_selection_cutoff_violation(%s)",
            (late.selection_receipt_id,),
        )
        assert cursor.fetchone()[0] is True


def test_audit_flag_is_never_hand_set_at_insert(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A receipt enters clear and in staging; ``require_strategy_initial_state`` holds that line.

    The update half of this rule left with ``guard_strategy_selection_receipt_change``
    in revision 20260803_0018; only the insert-time contract is still enforced
    in the database.
    """
    with agri_db_connection.cursor() as cursor:
        fixture = _build_selection_fixture(cursor, iteration_cutoff_time=FIXTURE_DATA_CUTOFF)
        cursor.execute(
            "SELECT audit_state, audit_reason, audit_flagged_at FROM agri.strategy_selection_receipt WHERE id = %s",
            (fixture.selection_receipt_id,),
        )
        assert cursor.fetchone() == ("clear", None, None)

        cursor.execute("SAVEPOINT inserted_flag")
        with pytest.raises(psycopg2.Error, match="must be inserted in staging state"):
            cursor.execute(
                """
                INSERT INTO agri.strategy_selection_receipt(
                    selection_key, analysis_subject_id, forecast_iteration_id,
                    feature_snapshot_id, training_run_id, selection_policy_id,
                    issue_time, applicability_start, applicability_end, data_cutoff,
                    decision_state, abstention_reason, candidate_count,
                    audit_state, audit_reason, audit_flagged_at
                )
                SELECT
                    selection_key || '-flagged', analysis_subject_id, forecast_iteration_id,
                    feature_snapshot_id, training_run_id, selection_policy_id,
                    issue_time, applicability_start, applicability_end, data_cutoff,
                    decision_state, abstention_reason, candidate_count,
                    'cutoff_violation', 'hand set', now()
                FROM agri.strategy_selection_receipt WHERE id = %s
                """,
                (fixture.selection_receipt_id,),
            )
        cursor.execute("ROLLBACK TO SAVEPOINT inserted_flag")
