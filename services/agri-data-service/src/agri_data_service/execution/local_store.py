"""Crash-safe local run manifests, checkpoints, and validated outputs."""

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from filelock import FileLock

from agri_data_service.execution.contracts import (
    ExpectedOutput,
    LocalCheckpoint,
    LocalOutput,
    LocalRunManifest,
    LocalRunState,
    PublicationPhase,
    RunValidationReport,
    ValidationReport,
    canonical_json_bytes,
    deterministic_run_identity,
    output_manifest_checksum,
    reject_sensitive_fields,
    sanitize_shard_key,
    validation_report_matches_output,
)

MANIFEST_FILENAME = "manifest.json"


class LocalRunStore:
    """Own local run state beneath one configured root."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def initialize(  # noqa: PLR0913
        self,
        *,
        job_name: str,
        job_version: str,
        scheduled_for: datetime,
        release_set_id: uuid.UUID,
        release_set_manifest_checksum: str,
        recipe_version: str | None = None,
        model_version: str | None = None,
        partitions: list[str],
        expected_shards: list[str],
        expected_outputs: list[ExpectedOutput],
    ) -> LocalRunManifest:
        normalized_partitions = sorted(set(partitions))
        normalized_shards = sorted(set(expected_shards))
        normalized_outputs = sorted(expected_outputs, key=lambda item: item.output_key)
        logical_run_key, run_id, run_plan_checksum = deterministic_run_identity(
            job_name=job_name,
            job_version=job_version,
            scheduled_for=scheduled_for,
            release_set_id=release_set_id,
            release_set_manifest_checksum=release_set_manifest_checksum,
            recipe_version=recipe_version,
            model_version=model_version,
            partitions=normalized_partitions,
            expected_shards=normalized_shards,
            expected_outputs=normalized_outputs,
        )
        run_directory = self.run_directory(run_id)
        run_directory.mkdir(parents=True, exist_ok=True)
        with self._lock(run_id):
            if self.manifest_path(run_id).exists():
                return self.load(run_id)
            now = datetime.now(UTC)
            manifest = LocalRunManifest(
                run_id=run_id,
                logical_run_key=logical_run_key,
                run_plan_checksum=run_plan_checksum,
                job_name=job_name,
                job_version=job_version,
                scheduled_for=scheduled_for,
                release_set_id=release_set_id,
                release_set_manifest_checksum=release_set_manifest_checksum,
                recipe_version=recipe_version,
                model_version=model_version,
                partitions=normalized_partitions,
                expected_shards=normalized_shards,
                expected_outputs=normalized_outputs,
                created_at=now,
                updated_at=now,
            )
            self._write_manifest(manifest)
            return manifest

    def load(self, run_id: uuid.UUID) -> LocalRunManifest:
        path = self.manifest_path(run_id)
        if not path.is_file():
            raise FileNotFoundError(f"local run {run_id} does not exist under {self.root}")
        manifest = LocalRunManifest.model_validate_json(path.read_bytes())
        if manifest.run_id != run_id:
            raise ValueError("local manifest identity does not match its run directory")
        return manifest

    def checkpoint(
        self,
        run_id: uuid.UUID,
        *,
        shard_key: str,
        cursor: dict[str, Any],
        progress_fraction: float,
    ) -> LocalCheckpoint:
        with self._lock(run_id):
            manifest = self.load(run_id)
            self._require_mutable(manifest, "checkpoint")
            if shard_key not in manifest.expected_shards:
                raise ValueError(f"shard {shard_key!r} is not in the frozen run plan")
            previous = next((item for item in manifest.checkpoints if item.shard_key == shard_key), None)
            sequence = previous.sequence + 1 if previous else 1
            if previous is not None and progress_fraction < previous.progress_fraction:
                raise ValueError("checkpoint progress cannot move backwards")
            written_at = datetime.now(UTC)
            reject_sensitive_fields(cursor)
            cursor_bytes = canonical_json_bytes(cursor)
            cursor_checksum = hashlib.sha256(cursor_bytes).hexdigest()
            relative_path = (
                Path("checkpoints") / sanitize_shard_key(shard_key) / f"{sequence:08d}-{cursor_checksum[:12]}.json"
            )
            envelope = {
                "schema_version": 1,
                "run_id": str(run_id),
                "shard_key": shard_key,
                "sequence": sequence,
                "progress_fraction": progress_fraction,
                "cursor_checksum": cursor_checksum,
                "cursor": cursor,
                "written_at": written_at.isoformat().replace("+00:00", "Z"),
            }
            self._atomic_write(
                self.run_directory(run_id) / relative_path,
                canonical_json_bytes(envelope),
            )
            checkpoint = LocalCheckpoint(
                shard_key=shard_key,
                sequence=sequence,
                progress_fraction=progress_fraction,
                cursor_checksum=cursor_checksum,
                relative_path=relative_path.as_posix(),
                written_at=written_at,
            )
            manifest.checkpoints = [item for item in manifest.checkpoints if item.shard_key != shard_key] + [checkpoint]
            manifest.checkpoints.sort(key=lambda item: item.shard_key)
            manifest.state = LocalRunState.RUNNING
            manifest.updated_at = written_at
            self._write_manifest(manifest)
            return checkpoint

    def mark_interrupted(self, run_id: uuid.UUID) -> LocalRunManifest:
        with self._lock(run_id):
            manifest = self.load(run_id)
            self._require_mutable(manifest, "mark interrupted")
            manifest.state = LocalRunState.INTERRUPTED
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
            return manifest

    def resume_cursor(self, run_id: uuid.UUID, *, shard_key: str) -> dict[str, Any]:
        """Load and verify the latest cursor for a shard."""
        with self._lock(run_id):
            manifest = self.load(run_id)
            checkpoint = next((item for item in manifest.checkpoints if item.shard_key == shard_key), None)
            if checkpoint is None:
                raise ValueError(f"no checkpoint exists for shard {shard_key!r}")
            checkpoint_path = self._path_within(
                self.run_directory(run_id),
                self.run_directory(run_id) / checkpoint.relative_path,
            )
            envelope_value = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(envelope_value, dict) or not isinstance(envelope_value.get("cursor"), dict):
                raise ValueError("checkpoint envelope is not a JSON object with a cursor")
            envelope = cast("dict[str, Any]", envelope_value)
            cursor = cast("dict[str, Any]", envelope["cursor"])
            if (
                envelope.get("run_id") != str(run_id)
                or envelope.get("shard_key") != shard_key
                or envelope.get("sequence") != checkpoint.sequence
                or hashlib.sha256(canonical_json_bytes(cursor)).hexdigest() != checkpoint.cursor_checksum
            ):
                raise ValueError("checkpoint identity or checksum is invalid")
            return envelope

    def register_output(  # noqa: PLR0913
        self,
        run_id: uuid.UUID,
        *,
        output_key: str,
        kind: str,
        artifact_path: Path,
        validation_report_path: Path,
        media_type: str = "application/octet-stream",
        row_count: int | None = None,
        max_validation_bytes: int = 256_000,
    ) -> LocalOutput:
        with self._lock(run_id):
            manifest = self.load(run_id)
            self._require_mutable(manifest, "register output")
            run_directory = self.run_directory(run_id)
            artifact = self._path_within(run_directory, artifact_path)
            report_path = self._path_within(run_directory, validation_report_path)
            if artifact == self.manifest_path(run_id) or not artifact.is_file():
                raise ValueError("artifact must be a file within the local run directory")
            if not report_path.is_file():
                raise ValueError("validation report must be a file within the local run directory")
            report_bytes = _read_bounded_stable_file(report_path, max_validation_bytes)
            report = ValidationReport.model_validate_json(report_bytes)
            artifact_checksum, artifact_size = hash_stable_file(artifact)
            report_checksum = hashlib.sha256(report_bytes).hexdigest()
            report_size = len(report_bytes)
            expected = next(
                (item for item in manifest.expected_outputs if item.output_key == output_key),
                None,
            )
            if expected is None:
                raise ValueError(f"output key {output_key!r} is not in the frozen run plan")
            if kind != expected.kind:
                raise ValueError(f"output kind for {output_key!r} differs from the frozen run plan")
            output = LocalOutput(
                output_key=output_key,
                kind=kind,
                relative_path=artifact.relative_to(run_directory).as_posix(),
                media_type=media_type,
                checksum_sha256=artifact_checksum,
                size_bytes=artifact_size,
                row_count=row_count,
                covered_shards=expected.covered_shards,
                covered_partitions=expected.covered_partitions,
                validation_report_relative_path=report_path.relative_to(run_directory).as_posix(),
                validation_report_sha256=report_checksum,
                validation_report_size_bytes=report_size,
                validated_at=report.validated_at,
            )
            if not validation_report_matches_output(
                report,
                output,
                run_plan_checksum=manifest.run_plan_checksum,
                release_set_manifest_checksum=manifest.release_set_manifest_checksum,
            ):
                raise ValueError("validation report does not bind the exact frozen output")
            existing = next((item for item in manifest.outputs if item.output_key == output_key), None)
            if existing and existing != output:
                raise ValueError(f"output key {output_key!r} is already bound to another artifact")
            if not existing:
                manifest.outputs.append(output)
                manifest.outputs.sort(key=lambda item: item.output_key)
            manifest.state = LocalRunState.RUNNING
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
            return output

    def finalize_validation(
        self,
        run_id: uuid.UUID,
        *,
        run_validation_report_path: Path,
        max_validation_bytes: int = 256_000,
    ) -> LocalRunManifest:
        """Freeze a complete run only after exact coverage and run-level validation."""
        with self._lock(run_id):
            manifest = self.load(run_id)
            self._require_mutable(manifest, "finalize validation")
            run_directory = self.run_directory(run_id)
            report_path = self._path_within(run_directory, run_validation_report_path)
            if not report_path.is_file():
                raise ValueError("run validation report must be inside the local run directory")
            report_bytes = _read_bounded_stable_file(report_path, max_validation_bytes)
            report = RunValidationReport.model_validate_json(report_bytes)

            for output in manifest.outputs:
                artifact_path = self.resolve_run_path(run_id, output.relative_path)
                checksum, size = hash_stable_file(artifact_path)
                if checksum != output.checksum_sha256 or size != output.size_bytes:
                    raise ValueError(f"artifact changed before finalization: {output.output_key}")
                output_report_path = self.resolve_run_path(run_id, output.validation_report_relative_path)
                output_report_bytes = _read_bounded_stable_file(output_report_path, max_validation_bytes)
                if (
                    hashlib.sha256(output_report_bytes).hexdigest() != output.validation_report_sha256
                    or len(output_report_bytes) != output.validation_report_size_bytes
                ):
                    raise ValueError(f"validation report changed before finalization: {output.output_key}")
                output_report = ValidationReport.model_validate_json(output_report_bytes)
                if not validation_report_matches_output(
                    output_report,
                    output,
                    run_plan_checksum=manifest.run_plan_checksum,
                    release_set_manifest_checksum=manifest.release_set_manifest_checksum,
                ):
                    raise ValueError(f"validation report no longer binds output: {output.output_key}")

            if report.output_manifest_checksum != output_manifest_checksum(manifest.outputs):
                raise ValueError("run validation report does not bind the exact output set")
            manifest.run_validation_report = report
            manifest.state = LocalRunState.VALIDATED
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
            return manifest

    def begin_publication(
        self,
        run_id: uuid.UUID,
        *,
        product: str,
        scope_key: str,
    ) -> LocalRunManifest:
        """Freeze the remote pointer target before the first upload attempt."""
        with self._lock(run_id):
            manifest = self.load(run_id)
            if manifest.state not in {LocalRunState.VALIDATED, LocalRunState.PUBLISHING}:
                raise ValueError("local run must contain validated outputs before publication")
            requested_target = (product, scope_key)
            existing_target = (
                manifest.publication.product,
                manifest.publication.scope_key,
            )
            if manifest.publication.phase == PublicationPhase.PENDING:
                manifest.publication.product = product
                manifest.publication.scope_key = scope_key
            elif existing_target != requested_target:
                raise ValueError("publication target differs from the frozen retry target")
            manifest.state = LocalRunState.PUBLISHING
            manifest.publication.phase = PublicationPhase.STAGING
            manifest.publication.last_error = None
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
            return manifest

    def update_publication(  # noqa: PLR0913
        self,
        run_id: uuid.UUID,
        *,
        phase: PublicationPhase,
        next_output_index: int | None = None,
        attempt_increment: int = 0,
        last_error: str | None = None,
        remote_revision: int | None = None,
    ) -> LocalRunManifest:
        with self._lock(run_id):
            manifest = self.load(run_id)
            if manifest.state == LocalRunState.PUBLISHED and phase != PublicationPhase.PUBLISHED:
                raise ValueError("a published local run is immutable")
            if phase not in {PublicationPhase.PENDING, PublicationPhase.FAILED}:
                manifest.state = (
                    LocalRunState.PUBLISHED if phase == PublicationPhase.PUBLISHED else LocalRunState.PUBLISHING
                )
            manifest.publication.phase = phase
            if next_output_index is not None:
                manifest.publication.next_output_index = next_output_index
            manifest.publication.attempt_count += attempt_increment
            manifest.publication.last_error = last_error
            manifest.publication.remote_revision = remote_revision
            if phase == PublicationPhase.PUBLISHED:
                manifest.publication.published_at = datetime.now(UTC)
            manifest.updated_at = datetime.now(UTC)
            self._write_manifest(manifest)
            return manifest

    def run_directory(self, run_id: uuid.UUID) -> Path:
        candidate = (self.root / str(run_id)).resolve()
        if self.root != candidate.parent:
            raise ValueError("run directory escaped the configured local execution root")
        return candidate

    def resolve_run_path(self, run_id: uuid.UUID, relative_path: str) -> Path:
        """Resolve one validated manifest path beneath its requested run directory."""
        run_directory = self.run_directory(run_id)
        return self._path_within(run_directory, run_directory / Path(relative_path))

    def manifest_path(self, run_id: uuid.UUID) -> Path:
        return self.run_directory(run_id) / MANIFEST_FILENAME

    def _lock(self, run_id: uuid.UUID) -> FileLock:
        return FileLock(self.run_directory(run_id) / ".manifest.lock", timeout=10)

    def _write_manifest(self, manifest: LocalRunManifest) -> None:
        validated = LocalRunManifest.model_validate(manifest.model_dump(mode="json"))
        self._atomic_write(
            self.manifest_path(validated.run_id),
            canonical_json_bytes(validated.model_dump(mode="json")),
        )

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as file:
                temporary_name = file.name
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, path)
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)

    @staticmethod
    def _path_within(run_directory: Path, candidate: Path) -> Path:
        resolved = candidate.expanduser().resolve()
        if resolved != run_directory and run_directory not in resolved.parents:
            raise ValueError("path must remain within the local run directory")
        return resolved

    @staticmethod
    def _require_mutable(manifest: LocalRunManifest, operation: str) -> None:
        if manifest.state in {
            LocalRunState.VALIDATED,
            LocalRunState.PUBLISHING,
            LocalRunState.PUBLISHED,
        }:
            raise ValueError(f"cannot {operation} after validation has finalized")


def hash_stable_file(path: Path) -> tuple[str, int]:
    """Hash a file and reject concurrent modification."""
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"artifact changed while hashing: {path}")
    return digest.hexdigest(), after.st_size


def _read_bounded_stable_file(path: Path, max_bytes: int) -> bytes:
    before = path.stat()
    with path.open("rb") as file:
        content = file.read(max_bytes + 1)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"file changed while reading: {path}")
    if len(content) > max_bytes or after.st_size > max_bytes:
        raise ValueError("validation report exceeds the configured publication bound")
    if len(content) != after.st_size:
        raise ValueError(f"file could not be read completely: {path}")
    return content
