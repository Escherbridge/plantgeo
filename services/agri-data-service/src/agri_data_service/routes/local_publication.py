"""Authenticated local-run artifact publication endpoints."""

import base64
import hashlib
import json as json_parser
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any, NoReturn, cast

from sanic import Blueprint, Request, json
from sanic.response import HTTPResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from agri_data_service.config import settings
from agri_data_service.db.engine import receiver_writer_session
from agri_data_service.execution.contracts import (
    CommitRunRequest,
    LocalOutput,
    PublicationManifest,
    StageRunRequest,
    UploadOutputRequest,
    ValidationReport,
    canonical_json_bytes,
    validation_report_matches_output,
)
from agri_data_service.models.jobs import (
    JobDefinition,
    JobOutbox,
    JobOutput,
    JobRun,
    JobRunState,
    OutputState,
    PublicationPointer,
)
from agri_data_service.models.provenance import Artifact, ReleaseSet, ReleaseSetState

local_publication_bp = Blueprint("local_publication", url_prefix="/local-execution")
MANIFEST_OUTPUT_KEY = "__manifest__"
COMMIT_REQUEST_MAX_BYTES = 64_000


class _PublicationAbortError(Exception):
    def __init__(self, code: str, status: int, **detail: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


@local_publication_bp.post("/runs/stage")
async def stage_local_run(  # noqa: PLR0911, PLR0912, PLR0915
    request: Request,
) -> HTTPResponse:
    """Idempotently stage a frozen local run manifest."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    publication_actor = _publication_actor()
    if len(request.body) > settings.local_publish_max_manifest_bytes:
        return _error("manifest_too_large", 413)
    try:
        payload = StageRunRequest.model_validate(json_parser.loads(request.body))
    except (TypeError, ValueError):
        return _error("invalid_publication_manifest", 422)

    manifest = payload.manifest
    publication_target = {"product": payload.product, "scope_key": payload.scope_key}
    manifest_bytes = canonical_json_bytes(manifest.model_dump(mode="json"))
    if len(manifest_bytes) > settings.local_publish_max_manifest_bytes:
        return _error("manifest_too_large", 413)
    limits_error = _stage_limits_error(manifest)
    if limits_error:
        return _error(limits_error, 413)
    manifest_checksum = manifest.checksum()
    now = datetime.now(UTC)
    try:
        async with receiver_writer_session() as session, session.begin():
            release_set = await session.get(ReleaseSet, manifest.release_set_id)
            if release_set is None:
                _abort("unknown_release_set", 422)
            if not _release_is_publishable(release_set.state):
                _abort("unvalidated_release_set", 422)
            if release_set.manifest_checksum != manifest.release_set_manifest_checksum:
                _abort("release_set_manifest_checksum_mismatch", 409)

            definition = (
                await session.execute(
                    select(JobDefinition).where(
                        JobDefinition.name == manifest.job_name,
                        JobDefinition.version == manifest.job_version,
                    )
                )
            ).scalar_one_or_none()
            if definition is None:
                definition = JobDefinition(
                    name=manifest.job_name,
                    version=manifest.job_version,
                    handler=f"local.execution:{manifest.job_name}",
                    queue_name="local",
                    enabled=False,
                    parameters={
                        "execution_backend": "local",
                        "cloud_training": False,
                        "compute_location": "operator_workstation",
                        "server_compute_enabled": False,
                    },
                )
                session.add(definition)
                await session.flush()
            elif (
                definition.handler != f"local.execution:{manifest.job_name}"
                or definition.queue_name != "local"
                or definition.enabled
                or definition.parameters.get("execution_backend") != "local"
                or definition.parameters.get("cloud_training") is not False
                or definition.parameters.get("server_compute_enabled") is not False
            ):
                _abort("job_definition_conflict", 409)

            run = (
                await session.execute(
                    select(JobRun).where(JobRun.logical_run_key == manifest.logical_run_key).with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                run = JobRun(
                    id=manifest.run_id,
                    job_definition_id=definition.id,
                    release_set_id=manifest.release_set_id,
                    logical_run_key=manifest.logical_run_key,
                    scheduled_for=manifest.scheduled_for,
                    recipe_version=manifest.recipe_version,
                    model_version=manifest.model_version,
                    target_partitions=_frozen_run_plan(manifest, product=payload.product, scope_key=payload.scope_key),
                    status=JobRunState.RUNNING,
                    requested_by=publication_actor,
                    total_work_items=len(manifest.outputs),
                    started_at=now,
                )
                session.add(run)
                await session.flush()
            elif not _run_matches_manifest(
                run,
                definition.id,
                manifest,
                product=payload.product,
                scope_key=payload.scope_key,
                publication_actor=publication_actor,
            ):
                _abort("logical_run_key_conflict", 409)

            manifest_output = await _output_by_key(session, run.id, MANIFEST_OUTPUT_KEY)
            if manifest_output is not None:
                try:
                    stored_manifest = PublicationManifest.model_validate(manifest_output.metadata_json.get("manifest"))
                except (TypeError, ValueError):
                    _abort("stored_manifest_invalid", 409)
                if (
                    manifest_output.checksum_sha256 != manifest_checksum
                    or stored_manifest != manifest
                    or manifest_output.metadata_json.get("publication_target") != publication_target
                    or manifest_output.state
                    not in {
                        OutputState.VALIDATED,
                        OutputState.PUBLISHED,
                    }
                ):
                    _abort("manifest_changed_after_staging", 409)
            if manifest_output is None:
                if run.status != JobRunState.RUNNING:
                    _abort("local_run_not_stageable", 409)
                run.total_work_items = len(manifest.outputs)
                artifact = await _get_or_create_inline_artifact(
                    session,
                    kind="local_run_manifest",
                    media_type="application/json",
                    checksum=manifest_checksum,
                    content=manifest_bytes,
                    metadata={"run_id": str(run.id), "schema_version": manifest.schema_version},
                )
                session.add(
                    JobOutput(
                        job_run_id=run.id,
                        artifact_id=artifact.id,
                        output_key=MANIFEST_OUTPUT_KEY,
                        kind="local_run_manifest",
                        state=OutputState.VALIDATED,
                        uri=artifact.uri,
                        checksum_sha256=manifest_checksum,
                        size_bytes=len(manifest_bytes),
                        metadata_json={
                            "manifest": manifest.model_dump(mode="json"),
                            "publication_target": publication_target,
                            "promotion_state": "artifact_only",
                        },
                        validated_at=now,
                    )
                )
        return json(
            {
                "run_id": str(manifest.run_id),
                "manifest_checksum": manifest_checksum,
                "release_set_manifest_checksum": manifest.release_set_manifest_checksum,
                "output_count": len(manifest.outputs),
                "product": payload.product,
                "scope_key": payload.scope_key,
            },
            status=201,
        )
    except _PublicationAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("publication_stage_conflict", 409)


@local_publication_bp.put("/runs/<run_id:uuid>/outputs/<output_key:str>")
async def upload_local_output(  # noqa: PLR0911, PLR0912
    request: Request, run_id: uuid.UUID, output_key: str
) -> HTTPResponse:
    """Store one bounded, checksum-verified output idempotently."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if len(request.body) > settings.local_publish_max_upload_request_bytes:
        return _error("output_request_too_large", 413)
    try:
        payload = UploadOutputRequest.model_validate(json_parser.loads(request.body))
        if payload.descriptor.output_key != output_key:
            return _error("output_key_mismatch", 422)
        content = base64.b64decode(payload.content_base64, validate=True)
        report_bytes = base64.b64decode(payload.validation_report_base64, validate=True)
        report = ValidationReport.model_validate_json(report_bytes)
    except (TypeError, ValueError):
        return _error("invalid_output_payload", 422)
    descriptor = payload.descriptor
    if len(content) > settings.local_publish_max_artifact_bytes:
        return _error("artifact_too_large", 413)
    if len(report_bytes) > settings.local_publish_max_validation_bytes:
        return _error("validation_report_too_large", 413)
    if _sha256(content) != descriptor.checksum_sha256 or len(content) != descriptor.size_bytes:
        return _error("artifact_checksum_mismatch", 422)
    if (
        _sha256(report_bytes) != descriptor.validation_report_sha256
        or len(report_bytes) != descriptor.validation_report_size_bytes
    ):
        return _error("validation_checksum_mismatch", 422)
    if report.validated_at != descriptor.validated_at:
        return _error("validation_timestamp_mismatch", 422)

    try:
        async with receiver_writer_session() as session, session.begin():
            run = await session.get(JobRun, run_id, with_for_update=True)
            if run is None:
                _abort("unknown_local_run", 404)
            manifest_output = await _output_by_key(session, run_id, MANIFEST_OUTPUT_KEY)
            staged_manifest = _stored_manifest(manifest_output)
            expected = _expected_output(staged_manifest, output_key)
            if expected is None:
                _abort("undeclared_output", 422)
            if not _descriptor_matches(expected, descriptor):
                _abort("output_descriptor_mismatch", 409)
            if not validation_report_matches_output(
                report,
                descriptor,
                run_plan_checksum=staged_manifest.run_plan_checksum,
                release_set_manifest_checksum=(staged_manifest.release_set_manifest_checksum),
            ):
                _abort("validation_report_binding_mismatch", 409)
            existing = await _output_by_key(session, run_id, output_key)
            if existing and not _existing_output_matches(
                existing,
                descriptor,
                manifest=staged_manifest,
                supplied_report=report,
            ):
                _abort("output_key_conflict", 409)
            if existing:
                return json({"run_id": str(run_id), "output_key": output_key, "stored": True})
            if run.status != JobRunState.RUNNING:
                _abort("local_run_not_uploadable", 409)

            artifact = await _get_or_create_inline_artifact(
                session,
                kind=descriptor.kind,
                media_type=descriptor.media_type,
                checksum=descriptor.checksum_sha256,
                content=content,
                metadata={"run_id": str(run_id), "output_key": output_key},
            )
            session.add(
                JobOutput(
                    job_run_id=run_id,
                    artifact_id=artifact.id,
                    output_key=output_key,
                    kind=descriptor.kind,
                    state=OutputState.VALIDATED,
                    uri=artifact.uri,
                    checksum_sha256=descriptor.checksum_sha256,
                    row_count=descriptor.row_count,
                    size_bytes=descriptor.size_bytes,
                    metadata_json={
                        "descriptor": descriptor.model_dump(mode="json"),
                        "validation_report": report.model_dump(mode="json"),
                        "promotion_state": "artifact_only",
                    },
                    validated_at=descriptor.validated_at,
                )
            )
            await session.flush()
            run.succeeded_work_items = await _validated_output_count(session, run_id)
        return json({"run_id": str(run_id), "output_key": output_key, "stored": True}, status=201)
    except _PublicationAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("output_upload_conflict", 409)


@local_publication_bp.post("/runs/<run_id:uuid>/commit")
async def commit_local_run(  # noqa: PLR0911, PLR0912, PLR0915
    request: Request, run_id: uuid.UUID
) -> HTTPResponse:
    """Atomically publish a complete validated local artifact set."""
    unauthorized = _authorize(request)
    if unauthorized:
        return unauthorized
    if len(request.body) > COMMIT_REQUEST_MAX_BYTES:
        return _error("commit_request_too_large", 413)
    try:
        payload = CommitRunRequest.model_validate(json_parser.loads(request.body))
    except (TypeError, ValueError):
        return _error("invalid_commit_request", 422)
    now = datetime.now(UTC)
    try:
        async with receiver_writer_session() as session, session.begin():
            run = await session.get(JobRun, run_id, with_for_update=True)
            if run is None:
                _abort("unknown_local_run", 404)
            manifest_output = await _output_by_key(session, run_id, MANIFEST_OUTPUT_KEY)
            if (
                manifest_output is None
                or manifest_output.checksum_sha256 != payload.manifest_checksum
                or manifest_output.state not in {OutputState.VALIDATED, OutputState.PUBLISHED}
            ):
                _abort("manifest_checksum_mismatch", 409)
            staged_manifest = _stored_manifest(manifest_output)
            if payload.release_set_manifest_checksum != staged_manifest.release_set_manifest_checksum:
                _abort("release_set_manifest_checksum_mismatch", 409)
            product, scope_key = _stored_publication_target(manifest_output)
            publication_actor = _publication_actor()
            if run.requested_by != publication_actor:
                _abort("publication_credential_identity_changed", 409)
            if run.target_partitions != _frozen_run_plan(staged_manifest, product=product, scope_key=scope_key):
                _abort("stored_publication_target_invalid", 409)
            expected_outputs = staged_manifest.outputs
            outputs = (await session.execute(select(JobOutput).where(JobOutput.job_run_id == run_id))).scalars().all()
            by_key = {output.output_key: output for output in outputs}
            missing = [
                item.output_key
                for item in expected_outputs
                if item.output_key not in by_key
                or not _existing_output_matches(by_key[item.output_key], item, manifest=staged_manifest)
            ]
            if missing:
                _abort("incomplete_output_set", 409, missing=missing[:100])
            expected_keys = {item.output_key for item in expected_outputs}
            unexpected = sorted(set(by_key) - expected_keys - {MANIFEST_OUTPUT_KEY})
            if unexpected:
                _abort("unexpected_output_set", 409, unexpected=unexpected[:100])

            pointer = (
                await session.execute(
                    select(PublicationPointer)
                    .where(
                        PublicationPointer.product == product,
                        PublicationPointer.scope_key == scope_key,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if pointer and pointer.job_output_id == manifest_output.id:
                return json(
                    {
                        "run_id": str(run_id),
                        "publication_id": str(pointer.id),
                        "revision": pointer.revision,
                        "published": True,
                        "promotion_state": "artifact_only",
                    }
                )
            if not _run_is_publishable(run.status):
                _abort("local_run_not_publishable", 409)
            release_set = await session.get(ReleaseSet, run.release_set_id) if run.release_set_id is not None else None
            if release_set is None:
                _abort("unknown_release_set", 409)
            if not _release_is_publishable(release_set.state):
                _abort("release_set_not_publishable", 409)
            if release_set.manifest_checksum != staged_manifest.release_set_manifest_checksum:
                _abort("release_set_manifest_checksum_mismatch", 409)
            if pointer is not None:
                current_output = await session.get(JobOutput, pointer.job_output_id)
                current_run = (
                    await session.get(JobRun, current_output.job_run_id) if current_output is not None else None
                )
                if current_run is not None and current_run.scheduled_for > run.scheduled_for:
                    _abort("stale_publication", 409)

            manifest_output.state = OutputState.PUBLISHED
            for expected in expected_outputs:
                by_key[expected.output_key].state = OutputState.PUBLISHED
            run.status = JobRunState.SUCCEEDED
            run.succeeded_work_items = len(expected_outputs)
            run.failed_work_items = 0
            run.completed_at = now
            if pointer is None:
                pointer = PublicationPointer(
                    product=product,
                    scope_key=scope_key,
                    job_output_id=manifest_output.id,
                    release_set_id=run.release_set_id,
                    revision=1,
                    published_at=now,
                    published_by=publication_actor,
                )
                session.add(pointer)
            else:
                pointer.previous_job_output_id = pointer.job_output_id
                pointer.job_output_id = manifest_output.id
                pointer.release_set_id = run.release_set_id
                pointer.revision += 1
                pointer.published_at = now
                pointer.published_by = publication_actor
            await session.flush()
            event_key = _publication_event_key(
                pointer.id,
                pointer.revision,
                run_id=run_id,
                product=product,
                scope_key=scope_key,
                manifest_checksum=payload.manifest_checksum,
            )
            existing_event = (
                await session.execute(select(JobOutbox).where(JobOutbox.event_key == event_key))
            ).scalar_one_or_none()
            if existing_event is None:
                session.add(
                    JobOutbox(
                        event_key=event_key,
                        aggregate_type="job_run",
                        aggregate_id=run_id,
                        topic="publication.ready",
                        payload={
                            "publication_id": str(pointer.id),
                            "product": product,
                            "scope_key": scope_key,
                            "revision": pointer.revision,
                            "manifest_checksum": payload.manifest_checksum,
                            "release_set_manifest_checksum": (staged_manifest.release_set_manifest_checksum),
                            "promotion_state": "artifact_only",
                        },
                    )
                )
            revision = pointer.revision
            publication_id = pointer.id
        return json(
            {
                "run_id": str(run_id),
                "publication_id": str(publication_id),
                "revision": revision,
                "published": True,
                "promotion_state": "artifact_only",
            }
        )
    except _PublicationAbortError as exc:
        return _abort_response(exc)
    except (IntegrityError, ValueError):
        return _error("publication_commit_conflict", 409)


def _authorize(request: Request) -> HTTPResponse | None:
    configured = settings.local_publish_token
    if not settings.local_publication_receiver_enabled or configured is None or settings.local_publish_actor is None:
        return _error("local_publication_disabled", 503)
    authorization = request.headers.get("authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        return _error("unauthorized", 401)
    supplied = authorization[len(prefix) :]
    if not secrets.compare_digest(supplied, configured.get_secret_value()):
        return _error("unauthorized", 401)
    return None


def _publication_actor() -> str:
    actor = settings.local_publish_actor
    if actor is None:
        raise ValueError("local publication actor is not configured")
    return actor


def _run_matches_manifest(  # noqa: PLR0913
    run: JobRun,
    definition_id: uuid.UUID,
    manifest: PublicationManifest,
    *,
    product: str,
    scope_key: str,
    publication_actor: str,
) -> bool:
    return (
        run.id == manifest.run_id
        and run.job_definition_id == definition_id
        and run.release_set_id == manifest.release_set_id
        and run.scheduled_for == manifest.scheduled_for
        and run.recipe_version == manifest.recipe_version
        and run.model_version == manifest.model_version
        and run.requested_by == publication_actor
        and run.target_partitions == _frozen_run_plan(manifest, product=product, scope_key=scope_key)
    )


def _frozen_run_plan(
    manifest: PublicationManifest,
    *,
    product: str,
    scope_key: str,
) -> dict[str, Any]:
    return {
        "run_plan_checksum": manifest.run_plan_checksum,
        "release_set_manifest_checksum": manifest.release_set_manifest_checksum,
        "partitions": manifest.partitions,
        "expected_shards": manifest.expected_shards,
        "expected_outputs": [item.model_dump(mode="json") for item in manifest.expected_outputs],
        "publication_target": {"product": product, "scope_key": scope_key},
    }


def _existing_output_matches(
    existing: JobOutput,
    descriptor: LocalOutput,
    *,
    manifest: PublicationManifest,
    supplied_report: ValidationReport | None = None,
) -> bool:
    stored_descriptor = existing.metadata_json.get("descriptor")
    try:
        stored_report = ValidationReport.model_validate(existing.metadata_json.get("validation_report"))
    except (TypeError, ValueError):
        return False
    return (
        existing.checksum_sha256 == descriptor.checksum_sha256
        and existing.kind == descriptor.kind
        and existing.size_bytes == descriptor.size_bytes
        and existing.row_count == descriptor.row_count
        and existing.validated_at == descriptor.validated_at
        and existing.state in {OutputState.VALIDATED, OutputState.PUBLISHED}
        and stored_descriptor == descriptor.model_dump(mode="json")
        and (supplied_report is None or stored_report == supplied_report)
        and validation_report_matches_output(
            stored_report,
            descriptor,
            run_plan_checksum=manifest.run_plan_checksum,
            release_set_manifest_checksum=manifest.release_set_manifest_checksum,
        )
    )


def _stage_limits_error(manifest: PublicationManifest) -> str | None:
    if len(manifest.outputs) > settings.local_publish_max_outputs:
        return "too_many_outputs"
    if any(output.size_bytes > settings.local_publish_max_artifact_bytes for output in manifest.outputs):
        return "declared_artifact_too_large"
    if any(
        output.validation_report_size_bytes > settings.local_publish_max_validation_bytes for output in manifest.outputs
    ):
        return "declared_validation_report_too_large"
    if manifest.declared_artifact_bytes > settings.local_publish_max_run_artifact_bytes:
        return "aggregate_artifact_quota_exceeded"
    if manifest.declared_validation_bytes > settings.local_publish_max_run_validation_bytes:
        return "aggregate_validation_quota_exceeded"
    return None


def _run_is_publishable(status: JobRunState) -> bool:
    return status in {JobRunState.RUNNING, JobRunState.SUCCEEDED}


def _release_is_publishable(state: ReleaseSetState) -> bool:
    return state in {ReleaseSetState.VALIDATED, ReleaseSetState.PUBLISHED}


def _publication_event_key(  # noqa: PLR0913
    pointer_id: uuid.UUID,
    revision: int,
    *,
    run_id: uuid.UUID,
    product: str,
    scope_key: str,
    manifest_checksum: str,
) -> str:
    fingerprint = hashlib.sha256(
        (f"{pointer_id}:{revision}:{run_id}:{product}:{scope_key}:{manifest_checksum}").encode()
    ).hexdigest()
    return f"local-publication:{fingerprint}"


def _inline_artifact_insert(**values: Any) -> Any:
    return (
        pg_insert(Artifact)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_artifact_uri_checksum")
        .returning(Artifact.id)
    )


async def _get_or_create_inline_artifact(  # noqa: PLR0913
    session: Any,
    *,
    kind: str,
    media_type: str,
    checksum: str,
    content: bytes,
    metadata: dict[str, Any],
) -> Artifact:
    uri = f"db://agri/artifact/sha256/{checksum}"
    if _sha256(content) != checksum:
        raise ValueError("artifact content does not match its checksum")
    artifact_id = await session.scalar(
        _inline_artifact_insert(
            kind=kind,
            uri=uri,
            media_type=media_type,
            checksum_sha256=checksum,
            size_bytes=len(content),
            storage_class="database_inline",
            metadata_json=metadata,
            content_bytes=content,
        )
    )
    if artifact_id is not None:
        artifact = await session.get(Artifact, artifact_id)
    else:
        artifact = (
            await session.execute(
                select(Artifact).where(
                    Artifact.uri == uri,
                    Artifact.checksum_sha256 == checksum,
                )
            )
        ).scalar_one_or_none()
    if artifact is None:
        raise ValueError("content-addressed artifact was not visible after upsert")
    if artifact.content_bytes != content:
        raise ValueError("content-addressed artifact collision")
    return cast("Artifact", artifact)


async def _output_by_key(session: Any, run_id: uuid.UUID, output_key: str) -> JobOutput | None:
    query_result = await session.execute(
        select(JobOutput).where(
            JobOutput.job_run_id == run_id,
            JobOutput.output_key == output_key,
        )
    )
    return cast("JobOutput | None", query_result.scalar_one_or_none())


def _stored_manifest(manifest_output: JobOutput | None) -> PublicationManifest:
    if manifest_output is None:
        _abort("unstaged_local_run", 409)
    try:
        return PublicationManifest.model_validate(manifest_output.metadata_json.get("manifest"))
    except (TypeError, ValueError):
        _abort("stored_manifest_invalid", 409)


def _expected_output(manifest: PublicationManifest, output_key: str) -> LocalOutput | None:
    return next(
        (item for item in manifest.outputs if item.output_key == output_key),
        None,
    )


def _stored_publication_target(manifest_output: JobOutput) -> tuple[str, str]:
    value = manifest_output.metadata_json.get("publication_target")
    if not isinstance(value, dict):
        _abort("stored_publication_target_invalid", 409)
    try:
        target = StageRunRequest.model_validate({"manifest": manifest_output.metadata_json.get("manifest"), **value})
    except (TypeError, ValueError):
        _abort("stored_publication_target_invalid", 409)
    return target.product, target.scope_key


def _descriptor_matches(expected: LocalOutput, actual: LocalOutput) -> bool:
    return expected.model_dump(mode="json") == actual.model_dump(mode="json")


async def _validated_output_count(session: Any, run_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(JobOutput.id)).where(
            JobOutput.job_run_id == run_id,
            JobOutput.output_key != MANIFEST_OUTPUT_KEY,
            JobOutput.state.in_([OutputState.VALIDATED, OutputState.PUBLISHED]),
        )
    )
    return int(count or 0)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _abort(code: str, status: int, **detail: Any) -> NoReturn:
    raise _PublicationAbortError(code, status, **detail)


def _abort_response(exc: _PublicationAbortError) -> HTTPResponse:
    return json({"error": exc.code, **exc.detail}, status=exc.status)


def _error(code: str, status: int) -> HTTPResponse:
    return json({"error": code}, status=status)
