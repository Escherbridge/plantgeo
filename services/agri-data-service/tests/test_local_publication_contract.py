"""Pure route-policy tests for local publication governance."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from agri_data_service.config import settings
from agri_data_service.execution.contracts import (
    CommitRunRequest,
    ExpectedOutput,
    LocalCheckpoint,
    LocalOutput,
    PublicationManifest,
    RunValidationReport,
    StageRunRequest,
    ValidationCheck,
    deterministic_run_identity,
    output_manifest_checksum,
)
from agri_data_service.models.jobs import JobRunState
from agri_data_service.models.provenance import ReleaseSetState
from agri_data_service.routes.local_publication import (
    _abort,
    _inline_artifact_insert,
    _publication_event_key,
    _PublicationAbortError,
    _release_is_publishable,
    _run_is_publishable,
    _stage_limits_error,
)

CONFLICT_STATUS = 409
MAX_EVENT_KEY_LENGTH = 255


def _publication_manifest(*, artifact_bytes: int = 5, report_bytes: int = 4) -> PublicationManifest:
    release_set_id = uuid.UUID("d591552b-f82f-4a83-a318-f33cf1ceee05")
    release_set_manifest_checksum = "a" * 64
    scheduled_for = datetime(2026, 7, 20, 6, tzinfo=UTC)
    expected_outputs = [
        ExpectedOutput(
            output_key="part-0001",
            kind="test_result_shard",
            covered_shards=["cell-0001"],
            covered_partitions=["cell-0001"],
        )
    ]
    logical_key, run_id, run_plan_checksum = deterministic_run_identity(
        job_name="danger-forecast",
        job_version="1",
        scheduled_for=scheduled_for,
        release_set_id=release_set_id,
        release_set_manifest_checksum=release_set_manifest_checksum,
        recipe_version="features-v1",
        model_version="baseline-v1",
        partitions=["cell-0001"],
        expected_shards=["cell-0001"],
        expected_outputs=expected_outputs,
    )
    output = LocalOutput(
        output_key="part-0001",
        kind="test_result_shard",
        relative_path="outputs/part-0001.jsonl",
        media_type="application/x-ndjson",
        checksum_sha256="0" * 64,
        size_bytes=artifact_bytes,
        row_count=1,
        covered_shards=["cell-0001"],
        covered_partitions=["cell-0001"],
        validation_report_relative_path="validation/part-0001.json",
        validation_report_sha256="1" * 64,
        validation_report_size_bytes=report_bytes,
        validated_at=datetime(2026, 7, 20, 7, tzinfo=UTC),
    )
    return PublicationManifest(
        run_id=run_id,
        logical_run_key=logical_key,
        run_plan_checksum=run_plan_checksum,
        job_name="danger-forecast",
        job_version="1",
        scheduled_for=scheduled_for,
        release_set_id=release_set_id,
        release_set_manifest_checksum=release_set_manifest_checksum,
        recipe_version="features-v1",
        model_version="baseline-v1",
        partitions=["cell-0001"],
        expected_shards=["cell-0001"],
        expected_outputs=expected_outputs,
        checkpoints=[
            LocalCheckpoint(
                shard_key="cell-0001",
                sequence=1,
                progress_fraction=1,
                cursor_checksum="c" * 64,
                relative_path="checkpoints/cell-0001.json",
                written_at=datetime(2026, 7, 20, 6, 59, tzinfo=UTC),
            )
        ],
        outputs=[output],
        run_validation_report=RunValidationReport(
            status="passed",
            run_id=run_id,
            logical_run_key=logical_key,
            run_plan_checksum=run_plan_checksum,
            release_set_id=release_set_id,
            release_set_manifest_checksum=release_set_manifest_checksum,
            output_manifest_checksum=output_manifest_checksum([output]),
            validator="test-run-validator-v2",
            validated_at=datetime(2026, 7, 20, 7, 1, tzinfo=UTC),
            checks=[
                ValidationCheck(
                    name="complete-plan",
                    status="passed",
                    summary="Every frozen output and shard is complete.",
                )
            ],
        ),
    )


def test_stage_rejects_aggregate_bytes_before_database_work(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _publication_manifest(artifact_bytes=6, report_bytes=7)
    monkeypatch.setattr(settings, "local_publish_max_artifact_bytes", 10)
    monkeypatch.setattr(settings, "local_publish_max_validation_bytes", 10)
    monkeypatch.setattr(settings, "local_publish_max_run_artifact_bytes", 5)
    monkeypatch.setattr(settings, "local_publish_max_run_validation_bytes", 10)
    assert _stage_limits_error(manifest) == "aggregate_artifact_quota_exceeded"

    monkeypatch.setattr(settings, "local_publish_max_run_artifact_bytes", 10)
    monkeypatch.setattr(settings, "local_publish_max_run_validation_bytes", 6)
    assert _stage_limits_error(manifest) == "aggregate_validation_quota_exceeded"


def test_commit_policy_rejects_cancelled_runs_and_retired_releases() -> None:
    assert _run_is_publishable(JobRunState.RUNNING)
    assert _run_is_publishable(JobRunState.SUCCEEDED)
    assert not _run_is_publishable(JobRunState.CANCELLED)
    assert not _run_is_publishable(JobRunState.DEAD_LETTER)
    assert _release_is_publishable(ReleaseSetState.VALIDATED)
    assert _release_is_publishable(ReleaseSetState.PUBLISHED)
    assert not _release_is_publishable(ReleaseSetState.RETIRED)


def test_target_is_staged_and_commit_accepts_no_caller_identity_or_target() -> None:
    manifest = _publication_manifest()
    stage = StageRunRequest(
        manifest=manifest,
        product="danger_forecast_artifacts",
        scope_key="region-a",
    )
    assert stage.product == "danger_forecast_artifacts"
    with pytest.raises(ValueError, match="Extra inputs"):
        CommitRunRequest.model_validate(
            {
                "manifest_checksum": manifest.checksum(),
                "release_set_manifest_checksum": manifest.release_set_manifest_checksum,
                "product": "caller-controlled",
                "scope_key": "caller-controlled",
                "published_by": "caller-controlled",
            }
        )


def test_publication_transition_event_key_includes_revision() -> None:
    pointer_id = uuid.uuid4()
    run_id = uuid.uuid4()
    arguments = {
        "run_id": run_id,
        "product": "danger_forecast_artifacts",
        "scope_key": "region-a",
        "manifest_checksum": "0" * 64,
    }
    first = _publication_event_key(pointer_id, 1, **arguments)
    second = _publication_event_key(pointer_id, 2, **arguments)
    assert first != second
    assert len(first) <= MAX_EVENT_KEY_LENGTH


def test_artifact_insert_is_an_idempotent_postgresql_upsert() -> None:
    statement = _inline_artifact_insert(
        kind="test",
        uri="db://agri/artifact/sha256/" + "0" * 64,
        media_type="application/octet-stream",
        checksum_sha256="0" * 64,
        size_bytes=1,
        storage_class="database_inline",
        metadata_json={},
        content_bytes=b"x",
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_artifact_uri_checksum DO NOTHING" in sql
    assert "RETURNING agri.artifact.id" in sql


def test_transaction_abort_carries_a_non_reflective_error_contract() -> None:
    with pytest.raises(_PublicationAbortError) as caught:
        _abort("release_set_not_publishable", CONFLICT_STATUS)
    assert caught.value.code == "release_set_not_publishable"
    assert caught.value.status == CONFLICT_STATUS
    assert caught.value.detail == {}


def test_foundation_migration_freezes_validated_release_membership() -> None:
    migration = (Path(__file__).parents[1] / "alembic" / "versions" / "20260719_0001_agri_foundation.py").read_text(
        encoding="utf-8"
    )
    assert "release_set_identity_freeze" in migration
    assert "NEW.state IS DISTINCT FROM OLD.state" in migration
    assert "release_set_membership_draft_only" in migration
    assert "BEFORE INSERT OR UPDATE OR DELETE ON agri.release_set_item" in migration
    assert "FOR UPDATE" in migration
