"""Focused local execution and retryable publication contract tests."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from agri_data_service.config import Settings
from agri_data_service.execution.contracts import (
    ExpectedOutput,
    LocalOutput,
    LocalRunManifest,
    LocalRunState,
    PublicationPhase,
    deterministic_run_identity,
    output_manifest_checksum,
)
from agri_data_service.execution.local_store import LocalRunStore
from agri_data_service.execution.publisher import BoundedPublisher, PublicationError

EXPECTED_REQUEST_BYTES = 26
EXPECTED_REMOTE_REVISION = 7
EXPECTED_SECOND_SEQUENCE = 2
EXPECTED_OUTPUT_COUNT = 2
RELEASE_SET_ID = uuid.UUID("d591552b-f82f-4a83-a318-f33cf1ceee05")
RELEASE_SET_CHECKSUM = "a" * 64


def _expected_outputs(keys: tuple[str, ...] = ("part-0001", "part-0002")) -> list[ExpectedOutput]:
    if len(keys) == 1:
        return [
            ExpectedOutput(
                output_key=keys[0],
                kind="test_result_shard",
                covered_shards=["cell-0001", "cell-0002"],
                covered_partitions=["cell-0001", "cell-0002"],
            )
        ]
    return [
        ExpectedOutput(
            output_key=key,
            kind="test_result_shard",
            covered_shards=[f"cell-{index:04d}"],
            covered_partitions=[f"cell-{index:04d}"],
        )
        for index, key in enumerate(keys, start=1)
    ]


def _initialize(
    store: LocalRunStore,
    *,
    keys: tuple[str, ...] = ("part-0001", "part-0002"),
) -> LocalRunManifest:
    return store.initialize(
        job_name="danger-forecast",
        job_version="1",
        scheduled_for=datetime(2026, 7, 20, 6, tzinfo=UTC),
        release_set_id=RELEASE_SET_ID,
        release_set_manifest_checksum=RELEASE_SET_CHECKSUM,
        recipe_version="features-v1",
        model_version="baseline-v1",
        partitions=["cell-0001", "cell-0002"],
        expected_shards=["cell-0001", "cell-0002"],
        expected_outputs=_expected_outputs(keys),
    )


def _write_validated_output(
    store: LocalRunStore,
    run_id: uuid.UUID,
    *,
    key: str,
    content: bytes,
    artifact_sha256: str | None = None,
) -> None:
    manifest = store.load(run_id)
    run_directory = store.run_directory(run_id)
    output_path = run_directory / "outputs" / f"{key}.jsonl"
    report_path = run_directory / "validation" / f"{key}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "passed",
                "output_key": key,
                "artifact_sha256": artifact_sha256 or hashlib.sha256(content).hexdigest(),
                "artifact_size_bytes": len(content),
                "artifact_row_count": 1,
                "run_plan_checksum": manifest.run_plan_checksum,
                "release_set_manifest_checksum": manifest.release_set_manifest_checksum,
                "validator": "test-validator-v2",
                "validated_at": "2026-07-20T07:00:00Z",
                "checks": [
                    {
                        "name": "schema",
                        "status": "passed",
                        "summary": "Fixture matches the declared test schema.",
                    }
                ],
                "metrics": {"rows": 1},
            }
        ),
        encoding="utf-8",
    )
    store.register_output(
        run_id,
        output_key=key,
        kind="test_result_shard",
        artifact_path=output_path,
        validation_report_path=report_path,
        media_type="application/x-ndjson",
        row_count=1,
    )


def _complete_shards(store: LocalRunStore, run_id: uuid.UUID) -> None:
    for shard_key in store.load(run_id).expected_shards:
        store.checkpoint(
            run_id,
            shard_key=shard_key,
            cursor={"complete": True},
            progress_fraction=1,
        )


def _finalize(store: LocalRunStore, run_id: uuid.UUID) -> LocalRunManifest:
    manifest = store.load(run_id)
    validated_at = (datetime.now(UTC) + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    report_path = store.run_directory(run_id) / "validation" / "run.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": "passed",
                "run_id": str(run_id),
                "logical_run_key": manifest.logical_run_key,
                "run_plan_checksum": manifest.run_plan_checksum,
                "release_set_id": str(manifest.release_set_id),
                "release_set_manifest_checksum": manifest.release_set_manifest_checksum,
                "output_manifest_checksum": output_manifest_checksum(manifest.outputs),
                "validator": "run-validator-v2",
                "validated_at": validated_at,
                "checks": [
                    {
                        "name": "complete-plan",
                        "status": "passed",
                        "summary": "All frozen shards, outputs, and partitions are covered.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return store.finalize_validation(
        run_id,
        run_validation_report_path=report_path,
    )


def _prepare_validated_run(
    store: LocalRunStore,
    *,
    keys: tuple[str, ...] = ("part-0001", "part-0002"),
    content: bytes | None = None,
) -> LocalRunManifest:
    manifest = _initialize(store, keys=keys)
    _complete_shards(store, manifest.run_id)
    for index, key in enumerate(keys, start=1):
        _write_validated_output(
            store,
            manifest.run_id,
            key=key,
            content=content if content is not None else f'{{"value":{index}}}\n'.encode(),
        )
    return _finalize(store, manifest.run_id)


def test_run_identity_is_stable_across_timezone_representation() -> None:
    instant_utc = datetime(2026, 7, 20, 6, tzinfo=UTC)
    instant_offset = datetime(2026, 7, 19, 23, tzinfo=timezone(timedelta(hours=-7)))
    arguments = {
        "job_name": "danger-forecast",
        "job_version": "1",
        "release_set_id": RELEASE_SET_ID,
        "release_set_manifest_checksum": RELEASE_SET_CHECKSUM,
        "recipe_version": "features-v1",
        "model_version": "baseline-v1",
        "expected_shards": ["cell-0001", "cell-0002"],
        "expected_outputs": _expected_outputs(),
    }
    first = deterministic_run_identity(**arguments, scheduled_for=instant_utc, partitions=["b", "a"])
    second = deterministic_run_identity(**arguments, scheduled_for=instant_offset, partitions=["a", "b"])
    assert first == second
    assert first[0].startswith("local:v2:")


def test_identity_changes_with_release_manifest_or_expected_plan() -> None:
    arguments = {
        "job_name": "danger-forecast",
        "job_version": "1",
        "scheduled_for": datetime(2026, 7, 20, 6, tzinfo=UTC),
        "release_set_id": RELEASE_SET_ID,
        "recipe_version": "features-v1",
        "model_version": "baseline-v1",
        "partitions": ["cell-0001", "cell-0002"],
        "expected_shards": ["cell-0001", "cell-0002"],
        "expected_outputs": _expected_outputs(),
    }
    original = deterministic_run_identity(**arguments, release_set_manifest_checksum=RELEASE_SET_CHECKSUM)
    changed = deterministic_run_identity(**arguments, release_set_manifest_checksum="b" * 64)
    assert original != changed


def test_phase_one_configuration_rejects_cloud_dispatch() -> None:
    configured = Settings(_env_file=None)
    assert configured.execution_backend == "local"
    assert configured.celery_dispatch_enabled is False
    assert configured.cloud_training_enabled is False
    with pytest.raises(ValueError, match="disabled for phase one"):
        Settings(_env_file=None, celery_dispatch_enabled=True)
    with pytest.raises(ValueError, match="at least 32"):
        Settings(_env_file=None, local_publish_token="too-short")
    with pytest.raises(ValueError, match="diverse"):
        Settings(_env_file=None, local_publish_token="a" * 32)
    with pytest.raises(ValueError, match="aggregate artifact quota"):
        Settings(
            _env_file=None,
            local_publish_max_artifact_bytes=100,
            local_publish_max_run_artifact_bytes=99,
        )


def test_upload_request_limit_accounts_for_both_base64_payloads() -> None:
    configured = Settings(
        _env_file=None,
        local_publish_max_artifact_bytes=5,
        local_publish_max_validation_bytes=4,
        local_publish_max_run_artifact_bytes=5,
        local_publish_max_run_validation_bytes=4,
        local_publish_request_overhead_bytes=10,
    )
    assert configured.local_publish_max_upload_request_bytes == EXPECTED_REQUEST_BYTES


def test_checkpoint_sequence_resumes_after_interruption(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _initialize(store)
    assert _initialize(store).run_id == manifest.run_id

    first = store.checkpoint(
        manifest.run_id,
        shard_key="cell-0001",
        cursor={"next_day": 8},
        progress_fraction=0.25,
    )
    store.mark_interrupted(manifest.run_id)
    second = store.checkpoint(
        manifest.run_id,
        shard_key="cell-0001",
        cursor={"next_day": 15},
        progress_fraction=0.5,
    )

    assert first.sequence == 1
    assert second.sequence == EXPECTED_SECOND_SEQUENCE
    assert store.resume_cursor(manifest.run_id, shard_key="cell-0001")["cursor"] == {"next_day": 15}
    with pytest.raises(ValueError, match="backwards"):
        store.checkpoint(
            manifest.run_id,
            shard_key="cell-0001",
            cursor={"next_day": 10},
            progress_fraction=0.4,
        )
    with pytest.raises(ValueError, match="frozen run plan"):
        store.checkpoint(
            manifest.run_id,
            shard_key="undeclared",
            cursor={"next_day": 1},
            progress_fraction=0.1,
        )
    with pytest.raises(ValueError, match="sensitive field"):
        store.checkpoint(
            manifest.run_id,
            shard_key="cell-0002",
            cursor={"authorization": "Bearer should-not-persist"},
            progress_fraction=0.1,
        )


def test_finalize_rejects_partial_completion_and_artifact_binding_drift(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _initialize(store)
    _complete_shards(store, manifest.run_id)
    _write_validated_output(store, manifest.run_id, key="part-0001", content=b'{"value":1}\n')
    with pytest.raises(ValueError, match=r"exact output set|frozen run plan"):
        _finalize(store, manifest.run_id)

    second_store = LocalRunStore(tmp_path / "binding")
    second = _initialize(second_store, keys=("part-0001",))
    with pytest.raises(ValueError, match="exact frozen output"):
        _write_validated_output(
            second_store,
            second.run_id,
            key="part-0001",
            content=b'{"value":1}\n',
            artifact_sha256="0" * 64,
        )


def test_manifest_directory_identity_and_paths_are_fail_closed(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    first = _initialize(store)
    second = store.initialize(
        job_name="danger-forecast",
        job_version="1",
        scheduled_for=datetime(2026, 7, 21, 6, tzinfo=UTC),
        release_set_id=first.release_set_id,
        release_set_manifest_checksum=first.release_set_manifest_checksum,
        recipe_version="features-v1",
        model_version="baseline-v1",
        partitions=["cell-0001", "cell-0002"],
        expected_shards=["cell-0001", "cell-0002"],
        expected_outputs=_expected_outputs(),
    )
    store.manifest_path(second.run_id).write_bytes(store.manifest_path(first.run_id).read_bytes())
    with pytest.raises(ValueError, match="run directory"):
        store.load(second.run_id)

    with pytest.raises(ValueError, match="run directory"):
        store.resolve_run_path(first.run_id, str(tmp_path.parent / "outside.bin"))

    output_data = {
        "output_key": "part-0001",
        "kind": "test",
        "relative_path": "C:/Windows/win.ini",
        "media_type": "application/octet-stream",
        "checksum_sha256": "0" * 64,
        "size_bytes": 1,
        "covered_shards": ["cell-0001"],
        "covered_partitions": ["cell-0001"],
        "validation_report_relative_path": "validation/report.json",
        "validation_report_sha256": "1" * 64,
        "validation_report_size_bytes": 1,
        "validated_at": "2026-07-20T07:00:00Z",
    }
    with pytest.raises(ValueError, match=r"POSIX-relative|run directory"):
        LocalOutput.model_validate(output_data)


def test_validation_finalize_freezes_registered_outputs(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _prepare_validated_run(store, keys=("part-0001",))
    assert manifest.state == LocalRunState.VALIDATED
    with pytest.raises(ValueError, match="validation has finalized"):
        store.checkpoint(
            manifest.run_id,
            shard_key="cell-0001",
            cursor={"complete": True},
            progress_fraction=1,
        )


def test_publication_retry_target_is_immutable(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _prepare_validated_run(store, keys=("part-0001",))
    store.begin_publication(
        manifest.run_id,
        product="danger_forecast_artifacts",
        scope_key="region-a",
    )
    store.update_publication(
        manifest.run_id,
        phase=PublicationPhase.FAILED,
        last_error="simulated interruption",
    )

    with pytest.raises(ValueError, match="frozen retry target"):
        store.begin_publication(
            manifest.run_id,
            product="danger_forecast_artifacts",
            scope_key="region-b",
        )


def test_publisher_retries_and_persists_output_cursor(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _prepare_validated_run(store)
    calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls[path] = calls.get(path, 0) + 1
        if path.endswith("/outputs/part-0001") and calls[path] == 1:
            return httpx.Response(503, json={"error": "temporary"})
        if path.endswith("/commit"):
            body = json.loads(request.content)
            assert set(body) == {"manifest_checksum", "release_set_manifest_checksum"}
            return httpx.Response(
                200,
                json={"revision": EXPECTED_REMOTE_REVISION, "published": True},
            )
        if path.endswith("/stage"):
            body = json.loads(request.content)
            assert body["product"] == "danger_forecast_artifacts"
            assert body["scope_key"] == "test-region"
        return httpx.Response(201, json={"stored": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    publisher = BoundedPublisher(
        base_url="https://publish.example.test/api/v1/local-execution",
        token="test-only-token-0123456789abcdef",
        max_artifact_bytes=1024,
        max_validation_bytes=1024,
        max_outputs=10,
        max_run_artifact_bytes=10_000,
        max_run_validation_bytes=10_000,
        retry_attempts=2,
        retry_base_seconds=0,
        client=client,
        sleep=lambda _seconds: None,
    )
    result = publisher.publish(
        store,
        manifest.run_id,
        product="danger_forecast_artifacts",
        scope_key="test-region",
    )

    saved = store.load(manifest.run_id)
    assert result["revision"] == EXPECTED_REMOTE_REVISION
    assert calls[f"/api/v1/local-execution/runs/{manifest.run_id}/outputs/part-0001"] == EXPECTED_SECOND_SEQUENCE
    assert saved.state == LocalRunState.PUBLISHED
    assert saved.publication.next_output_index == EXPECTED_OUTPUT_COUNT
    assert saved.publication.remote_revision == EXPECTED_REMOTE_REVISION
    assert saved.publication.product == "danger_forecast_artifacts"
    assert saved.publication.scope_key == "test-region"
    client.close()


def test_publisher_rejects_unsharded_oversize_artifact(tmp_path: Path) -> None:
    store = LocalRunStore(tmp_path)
    manifest = _prepare_validated_run(store, keys=("oversize",), content=b"x" * 20)
    client = httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(201, json={})))
    publisher = BoundedPublisher(
        base_url="https://publish.example.test/api/v1/local-execution",
        token="test-only-token-0123456789abcdef",
        max_artifact_bytes=10,
        max_validation_bytes=1024,
        max_outputs=10,
        max_run_artifact_bytes=100,
        max_run_validation_bytes=10_000,
        retry_attempts=1,
        retry_base_seconds=0,
        client=client,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(PublicationError, match="deterministic bounded shards"):
        publisher.publish(
            store,
            manifest.run_id,
            product="danger_forecast_artifacts",
            scope_key="test-region",
        )
    client.close()
