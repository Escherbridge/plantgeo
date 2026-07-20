"""Validated contracts shared by local runners and the publication API."""

import hashlib
import json
import math
import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LOCAL_RUN_NAMESPACE = uuid.UUID("7fb70867-ee78-5279-8fcc-b87fd0c24c64")
OUTPUT_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
SHA256_PATTERN = r"^[a-f0-9]{64}$"
ASCII_CONTROL_BOUNDARY = 32
ASCII_DELETE = 127
MAX_PLAN_KEY_LENGTH = 500
SENSITIVE_FIELD_NAMES = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "bearer_token",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session_token",
    "set_cookie",
    "token",
    "x_api_key",
}
SENSITIVE_FIELD_SUFFIXES = (
    "_api_key",
    "_apikey",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_token",
)
MAX_SOURCE_GEOJSON_BYTES = 5_000_000
MAX_SOURCE_GEOJSON_FEATURES = 5_000
GEOJSON_COORDINATE_DIMENSIONS = 2
GEOJSON_MIN_LONGITUDE = -180
GEOJSON_MAX_LONGITUDE = 180
GEOJSON_MIN_LATITUDE = -90
GEOJSON_MAX_LATITUDE = 90
MAX_GEOJSON_STORAGE_NODES = 50_000
MAX_GEOJSON_STORAGE_DEPTH = 32


class ContractModel(BaseModel):
    """Strict base for persisted and remote contracts."""

    model_config = ConfigDict(extra="forbid")


class LocalRunState(StrEnum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    VALIDATED = "validated"
    PUBLISHING = "publishing"
    PUBLISHED = "published"


class PublicationPhase(StrEnum):
    PENDING = "pending"
    STAGING = "staging"
    UPLOADING = "uploading"
    COMMITTING = "committing"
    FAILED = "failed"
    PUBLISHED = "published"


class ValidationCheck(ContractModel):
    """One explicit validation assertion for an output."""

    name: str = Field(min_length=1, max_length=200)
    status: Literal["passed"]
    summary: str = Field(min_length=1, max_length=2000)


class ValidationReport(ContractModel):
    """Required evidence that an external algorithm validated an output."""

    schema_version: Literal[2] = 2
    status: Literal["passed"]
    output_key: str = Field(pattern=OUTPUT_KEY_PATTERN)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    artifact_size_bytes: int = Field(ge=0)
    artifact_row_count: int | None = Field(default=None, ge=0)
    run_plan_checksum: str = Field(pattern=SHA256_PATTERN)
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    validator: str = Field(min_length=1, max_length=255)
    validated_at: datetime
    checks: list[ValidationCheck] = Field(min_length=1, max_length=200)
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)

    @field_validator("validated_at")
    @classmethod
    def require_aware_validation_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "validated_at")

    @field_validator("metrics")
    @classmethod
    def reject_sensitive_metrics(
        cls, value: dict[str, int | float | str | bool | None]
    ) -> dict[str, int | float | str | bool | None]:
        reject_sensitive_fields(value)
        return value


class LocalCheckpoint(ContractModel):
    """Latest append-only checkpoint for one local shard."""

    shard_key: str = Field(min_length=1, max_length=500)
    sequence: int = Field(ge=1)
    progress_fraction: float = Field(ge=0, le=1)
    cursor_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    relative_path: str = Field(min_length=1, max_length=1_000)
    written_at: datetime

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("written_at")
    @classmethod
    def require_aware_written_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "written_at")


class LocalOutput(ContractModel):
    """Immutable validated artifact produced by a local run."""

    output_key: str = Field(pattern=OUTPUT_KEY_PATTERN)
    kind: str = Field(min_length=1, max_length=100)
    relative_path: str = Field(min_length=1, max_length=1_000)
    media_type: str = Field(min_length=1, max_length=255)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    row_count: int | None = Field(default=None, ge=0)
    covered_shards: list[str] = Field(min_length=1, max_length=10_000)
    covered_partitions: list[str] = Field(min_length=1, max_length=10_000)
    validation_report_relative_path: str = Field(min_length=1, max_length=1_000)
    validation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    validation_report_size_bytes: int = Field(ge=0)
    validated_at: datetime

    @field_validator("relative_path", "validation_report_relative_path")
    @classmethod
    def validate_relative_paths(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("validated_at")
    @classmethod
    def require_aware_validation_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "validated_at")

    @field_validator("covered_shards", "covered_partitions")
    @classmethod
    def require_sorted_unique_coverage(cls, value: list[str], info: Any) -> list[str]:
        return _sorted_unique_keys(value, info.field_name)


class ExpectedOutput(ContractModel):
    """Output and coverage declared before a local computation begins."""

    output_key: str = Field(pattern=OUTPUT_KEY_PATTERN)
    kind: str = Field(min_length=1, max_length=100)
    covered_shards: list[str] = Field(min_length=1, max_length=10_000)
    covered_partitions: list[str] = Field(min_length=1, max_length=10_000)

    @field_validator("covered_shards", "covered_partitions")
    @classmethod
    def require_sorted_unique_coverage(cls, value: list[str], info: Any) -> list[str]:
        return _sorted_unique_keys(value, info.field_name)


class RunValidationReport(ContractModel):
    """Passing run-level evidence over the exact frozen output descriptor set."""

    schema_version: Literal[2] = 2
    status: Literal["passed"]
    run_id: uuid.UUID
    logical_run_key: str = Field(pattern=r"^local:v2:[a-f0-9]{64}$")
    run_plan_checksum: str = Field(pattern=SHA256_PATTERN)
    release_set_id: uuid.UUID
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    output_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    validator: str = Field(min_length=1, max_length=255)
    validated_at: datetime
    checks: list[ValidationCheck] = Field(min_length=1, max_length=200)
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)

    @field_validator("validated_at")
    @classmethod
    def require_aware_validation_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "validated_at")

    @field_validator("metrics")
    @classmethod
    def reject_sensitive_metrics(
        cls, value: dict[str, int | float | str | bool | None]
    ) -> dict[str, int | float | str | bool | None]:
        reject_sensitive_fields(value)
        return value


class PublicationProgress(ContractModel):
    """Local durable cursor for retryable API publication."""

    phase: PublicationPhase = PublicationPhase.PENDING
    next_output_index: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    remote_revision: int | None = Field(default=None, ge=1)
    last_error: str | None = Field(default=None, max_length=2000)
    published_at: datetime | None = None
    product: str | None = Field(default=None, pattern=OUTPUT_KEY_PATTERN, max_length=150)
    scope_key: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("scope_key", mode="before")
    @classmethod
    def normalize_scope_key(cls, value: Any) -> Any:
        return _normalize_scope_key(value)

    @field_validator("published_at")
    @classmethod
    def require_aware_published_time(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value, "published_at") if value is not None else None

    @model_validator(mode="after")
    def validate_published_state(self) -> "PublicationProgress":
        target_fields = (self.product, self.scope_key)
        if self.phase == PublicationPhase.PENDING and any(target_fields):
            raise ValueError("pending publication cannot have a frozen target")
        if self.phase != PublicationPhase.PENDING and not all(target_fields):
            raise ValueError("active publication requires a frozen product and scope")
        if self.phase == PublicationPhase.PUBLISHED:
            if self.remote_revision is None or self.published_at is None:
                raise ValueError("published progress requires a remote revision and timestamp")
        elif self.remote_revision is not None or self.published_at is not None:
            raise ValueError("remote publication fields require the published phase")
        return self


class LocalRunManifest(ContractModel):
    """Durable local run identity, checkpoints, outputs, and publish cursor."""

    schema_version: Literal[2] = 2
    run_id: uuid.UUID
    logical_run_key: str = Field(pattern=r"^local:v2:[a-f0-9]{64}$")
    run_plan_checksum: str = Field(pattern=SHA256_PATTERN)
    job_name: str = Field(min_length=1, max_length=150)
    job_version: str = Field(min_length=1, max_length=100)
    scheduled_for: datetime
    release_set_id: uuid.UUID
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    recipe_version: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=100)
    partitions: list[str] = Field(min_length=1, max_length=10_000)
    expected_shards: list[str] = Field(min_length=1, max_length=10_000)
    expected_outputs: list[ExpectedOutput] = Field(min_length=1, max_length=1_000)
    state: LocalRunState = LocalRunState.INITIALIZED
    created_at: datetime
    updated_at: datetime
    checkpoints: list[LocalCheckpoint] = Field(default_factory=list)
    outputs: list[LocalOutput] = Field(default_factory=list)
    run_validation_report: RunValidationReport | None = None
    publication: PublicationProgress = Field(default_factory=PublicationProgress)

    @field_validator("scheduled_for", "created_at", "updated_at")
    @classmethod
    def require_aware_times(cls, value: datetime, info: Any) -> datetime:
        return _require_aware_utc(value, info.field_name)

    @field_validator("partitions", "expected_shards")
    @classmethod
    def normalize_plan_keys(cls, value: list[str], info: Any) -> list[str]:
        return _sorted_unique_keys(value, info.field_name)

    @model_validator(mode="after")
    def verify_identity(self) -> "LocalRunManifest":  # noqa: PLR0912
        if self.recipe_version is None and self.model_version is None:
            raise ValueError("a local run requires a recipe or model version")
        expected_keys = [output.output_key for output in self.expected_outputs]
        if expected_keys != sorted(expected_keys) or len(expected_keys) != len(set(expected_keys)):
            raise ValueError("expected outputs must be unique and sorted by output key")
        expected_shards = set(self.expected_shards)
        expected_partitions = set(self.partitions)
        if any(
            not set(output.covered_shards).issubset(expected_shards)
            or not set(output.covered_partitions).issubset(expected_partitions)
            for output in self.expected_outputs
        ):
            raise ValueError("expected output coverage must stay within the frozen run plan")
        covered_shards = {shard for output in self.expected_outputs for shard in output.covered_shards}
        if covered_shards != expected_shards:
            raise ValueError("expected outputs must cover every expected shard")
        covered_partitions = {partition for output in self.expected_outputs for partition in output.covered_partitions}
        if covered_partitions != expected_partitions:
            raise ValueError("expected outputs must cover every expected partition")
        output_keys = [output.output_key for output in self.outputs]
        if output_keys != sorted(output_keys) or len(output_keys) != len(set(output_keys)):
            raise ValueError("local output keys must be unique and sorted")
        expected_by_key = {output.output_key: output for output in self.expected_outputs}
        if any(
            output.output_key not in expected_by_key
            or output.kind != expected_by_key[output.output_key].kind
            or output.covered_shards != expected_by_key[output.output_key].covered_shards
            or output.covered_partitions != expected_by_key[output.output_key].covered_partitions
            for output in self.outputs
        ):
            raise ValueError("registered outputs must match the frozen run plan")
        checkpoint_shards = [checkpoint.shard_key for checkpoint in self.checkpoints]
        if checkpoint_shards != sorted(checkpoint_shards) or len(checkpoint_shards) != len(set(checkpoint_shards)):
            raise ValueError("only the sorted latest checkpoint for each shard belongs in the manifest")
        if not set(checkpoint_shards).issubset(expected_shards):
            raise ValueError("checkpoints must address only frozen expected shards")
        if self.publication.next_output_index > len(self.outputs):
            raise ValueError("publication cursor exceeds the frozen output set")
        if (
            self.state
            in {
                LocalRunState.VALIDATED,
                LocalRunState.PUBLISHING,
                LocalRunState.PUBLISHED,
            }
            and not self.outputs
        ):
            raise ValueError(f"{self.state} local runs require validated outputs")
        if self.state in {
            LocalRunState.VALIDATED,
            LocalRunState.PUBLISHING,
            LocalRunState.PUBLISHED,
        }:
            self._verify_complete_validation()
        elif self.run_validation_report is not None:
            raise ValueError("run validation evidence requires the validated state")
        active_phases = {
            PublicationPhase.STAGING,
            PublicationPhase.UPLOADING,
            PublicationPhase.COMMITTING,
            PublicationPhase.FAILED,
        }
        if self.state == LocalRunState.PUBLISHING and self.publication.phase not in active_phases:
            raise ValueError("publishing local runs require an active publication phase")
        if (self.state == LocalRunState.PUBLISHED) != (self.publication.phase == PublicationPhase.PUBLISHED):
            raise ValueError("local and publication completion states must agree")
        if self.state not in {LocalRunState.PUBLISHING, LocalRunState.PUBLISHED} and (
            self.publication.phase != PublicationPhase.PENDING
        ):
            raise ValueError("publication cannot start before the local run is frozen")
        expected_key, expected_id, expected_plan_checksum = deterministic_run_identity(
            job_name=self.job_name,
            job_version=self.job_version,
            scheduled_for=self.scheduled_for,
            release_set_id=self.release_set_id,
            release_set_manifest_checksum=self.release_set_manifest_checksum,
            recipe_version=self.recipe_version,
            model_version=self.model_version,
            partitions=self.partitions,
            expected_shards=self.expected_shards,
            expected_outputs=self.expected_outputs,
        )
        if (
            self.logical_run_key != expected_key
            or self.run_id != expected_id
            or self.run_plan_checksum != expected_plan_checksum
        ):
            raise ValueError("run identity does not match its immutable inputs")
        return self

    def _verify_complete_validation(self) -> None:
        expected_by_key = {item.output_key: item for item in self.expected_outputs}
        actual_by_key = {item.output_key: item for item in self.outputs}
        if set(actual_by_key) != set(expected_by_key):
            raise ValueError("validated run output set does not match the frozen run plan")
        for key, expected in expected_by_key.items():
            actual = actual_by_key[key]
            if (
                actual.kind != expected.kind
                or actual.covered_shards != expected.covered_shards
                or actual.covered_partitions != expected.covered_partitions
            ):
                raise ValueError(f"validated output {key!r} differs from its frozen plan")
        checkpoints = {item.shard_key: item for item in self.checkpoints}
        if set(checkpoints) != set(self.expected_shards) or any(
            checkpoint.progress_fraction != 1 for checkpoint in checkpoints.values()
        ):
            raise ValueError("validated runs require a complete checkpoint for every shard")
        report = self.run_validation_report
        if report is None or not _run_report_matches_manifest(report, self):
            raise ValueError("run-level validation report does not bind the frozen artifact set")

    def publication_manifest(self) -> "PublicationManifest":
        report = self.run_validation_report
        if report is None:
            raise ValueError("publication requires finalized run-level validation")
        return PublicationManifest(
            schema_version=self.schema_version,
            run_id=self.run_id,
            logical_run_key=self.logical_run_key,
            run_plan_checksum=self.run_plan_checksum,
            job_name=self.job_name,
            job_version=self.job_version,
            scheduled_for=self.scheduled_for,
            release_set_id=self.release_set_id,
            release_set_manifest_checksum=self.release_set_manifest_checksum,
            recipe_version=self.recipe_version,
            model_version=self.model_version,
            partitions=self.partitions,
            expected_shards=self.expected_shards,
            expected_outputs=self.expected_outputs,
            checkpoints=self.checkpoints,
            outputs=self.outputs,
            run_validation_report=report,
        )


class PublicationManifest(ContractModel):
    """Frozen local run content accepted by the operational API."""

    schema_version: Literal[2] = 2
    run_id: uuid.UUID
    logical_run_key: str = Field(pattern=r"^local:v2:[a-f0-9]{64}$")
    run_plan_checksum: str = Field(pattern=SHA256_PATTERN)
    job_name: str = Field(min_length=1, max_length=150)
    job_version: str = Field(min_length=1, max_length=100)
    scheduled_for: datetime
    release_set_id: uuid.UUID
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    recipe_version: str | None = Field(default=None, max_length=100)
    model_version: str | None = Field(default=None, max_length=100)
    partitions: list[str] = Field(min_length=1, max_length=10_000)
    expected_shards: list[str] = Field(min_length=1, max_length=10_000)
    expected_outputs: list[ExpectedOutput] = Field(min_length=1, max_length=1_000)
    checkpoints: list[LocalCheckpoint] = Field(default_factory=list)
    outputs: list[LocalOutput] = Field(min_length=1, max_length=1_000)
    run_validation_report: RunValidationReport

    @field_validator("partitions", "expected_shards")
    @classmethod
    def normalize_plan_keys(cls, value: list[str], info: Any) -> list[str]:
        return _sorted_unique_keys(value, info.field_name)

    @model_validator(mode="after")
    def validate_publication_contract(self) -> "PublicationManifest":
        if self.recipe_version is None and self.model_version is None:
            raise ValueError("a publication requires a recipe or model version")
        keys = [output.output_key for output in self.outputs]
        if len(keys) != len(set(keys)):
            raise ValueError("output keys must be unique")
        expected_output_keys = [output.output_key for output in self.expected_outputs]
        if expected_output_keys != sorted(expected_output_keys) or keys != expected_output_keys:
            raise ValueError("publication outputs must exactly match the sorted frozen plan")
        expected_key, expected_id, expected_plan_checksum = deterministic_run_identity(
            job_name=self.job_name,
            job_version=self.job_version,
            scheduled_for=self.scheduled_for,
            release_set_id=self.release_set_id,
            release_set_manifest_checksum=self.release_set_manifest_checksum,
            recipe_version=self.recipe_version,
            model_version=self.model_version,
            partitions=self.partitions,
            expected_shards=self.expected_shards,
            expected_outputs=self.expected_outputs,
        )
        if (
            self.logical_run_key != expected_key
            or self.run_id != expected_id
            or self.run_plan_checksum != expected_plan_checksum
        ):
            raise ValueError("publication identity does not match immutable inputs")
        local_view = LocalRunManifest(
            **self.model_dump(mode="python"),
            state=LocalRunState.VALIDATED,
            created_at=self.scheduled_for,
            updated_at=self.run_validation_report.validated_at,
        )
        local_view._verify_complete_validation()
        return self

    def checksum(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()

    @property
    def declared_artifact_bytes(self) -> int:
        return sum(output.size_bytes for output in self.outputs)

    @property
    def declared_validation_bytes(self) -> int:
        return sum(output.validation_report_size_bytes for output in self.outputs)


class StageRunRequest(ContractModel):
    manifest: PublicationManifest
    product: str = Field(pattern=OUTPUT_KEY_PATTERN, max_length=150)
    scope_key: str = Field(min_length=1, max_length=500)

    @field_validator("scope_key", mode="before")
    @classmethod
    def normalize_scope_key(cls, value: Any) -> Any:
        return _normalize_scope_key(value)


class UploadOutputRequest(ContractModel):
    descriptor: LocalOutput
    content_base64: str
    validation_report_base64: str


class CommitRunRequest(ContractModel):
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)


def deterministic_run_identity(  # noqa: PLR0913
    *,
    job_name: str,
    job_version: str,
    scheduled_for: datetime,
    release_set_id: uuid.UUID,
    release_set_manifest_checksum: str,
    recipe_version: str | None,
    model_version: str | None,
    partitions: list[str],
    expected_shards: list[str],
    expected_outputs: list[ExpectedOutput],
) -> tuple[str, uuid.UUID, str]:
    """Derive an idempotency key and UUID from immutable run inputs."""
    scheduled_utc = _require_aware_utc(scheduled_for, "scheduled_for")
    normalized_partitions = sorted(set(partitions))
    identity = {
        "schema_version": 2,
        "job_name": job_name,
        "job_version": job_version,
        "scheduled_for": scheduled_utc.isoformat().replace("+00:00", "Z"),
        "release_set_id": str(release_set_id),
        "release_set_manifest_checksum": release_set_manifest_checksum,
        "recipe_version": recipe_version,
        "model_version": model_version,
        "partitions": normalized_partitions,
        "expected_shards": sorted(set(expected_shards)),
        "expected_outputs": [
            output.model_dump(mode="json") for output in sorted(expected_outputs, key=lambda item: item.output_key)
        ],
    }
    digest = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return f"local:v2:{digest}", uuid.uuid5(LOCAL_RUN_NAMESPACE, digest), digest


def output_manifest_checksum(outputs: list[LocalOutput]) -> str:
    """Hash the exact sorted output descriptors without run-validation recursion."""
    payload = [output.model_dump(mode="json") for output in sorted(outputs, key=lambda item: item.output_key)]
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validation_report_matches_output(
    report: ValidationReport,
    output: LocalOutput,
    *,
    run_plan_checksum: str,
    release_set_manifest_checksum: str,
) -> bool:
    """Check that passing evidence binds the exact artifact and immutable inputs."""
    return (
        report.output_key == output.output_key
        and report.artifact_sha256 == output.checksum_sha256
        and report.artifact_size_bytes == output.size_bytes
        and report.artifact_row_count == output.row_count
        and report.run_plan_checksum == run_plan_checksum
        and report.release_set_manifest_checksum == release_set_manifest_checksum
        and report.validated_at == output.validated_at
    )


def _run_report_matches_manifest(report: RunValidationReport, manifest: LocalRunManifest) -> bool:
    evidence_times = [output.validated_at for output in manifest.outputs] + [
        checkpoint.written_at for checkpoint in manifest.checkpoints
    ]
    return (
        report.run_id == manifest.run_id
        and report.logical_run_key == manifest.logical_run_key
        and report.run_plan_checksum == manifest.run_plan_checksum
        and report.release_set_id == manifest.release_set_id
        and report.release_set_manifest_checksum == manifest.release_set_manifest_checksum
        and report.output_manifest_checksum == output_manifest_checksum(manifest.outputs)
        and (not evidence_times or report.validated_at >= max(evidence_times))
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a contract deterministically for checksums and run IDs."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _safe_relative_path(value: str) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or any(ord(character) < ASCII_CONTROL_BOUNDARY for character in value)
    ):
        raise ValueError("artifact paths must be non-empty POSIX-relative paths")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("artifact paths must remain within the run directory")
    return str(path)


def _sorted_unique_keys(value: list[str], field_name: str) -> list[str]:
    normalized = sorted({item.strip() for item in value if item.strip()})
    if len(normalized) != len(value) or normalized != value:
        raise ValueError(f"{field_name} must be non-empty, unique, and sorted")
    if any(len(item) > MAX_PLAN_KEY_LENGTH for item in normalized):
        raise ValueError(f"{field_name} entries cannot exceed {MAX_PLAN_KEY_LENGTH} characters")
    if any(
        ord(character) < ASCII_CONTROL_BOUNDARY or ord(character) == ASCII_DELETE
        for item in normalized
        for character in item
    ):
        raise ValueError(f"{field_name} entries cannot contain control characters")
    return normalized


def _normalize_scope_key(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError("scope key cannot be blank")
    if any(ord(character) < ASCII_CONTROL_BOUNDARY or ord(character) == ASCII_DELETE for character in normalized):
        raise ValueError("scope key cannot contain control characters")
    return normalized


def sanitize_shard_key(value: str) -> str:
    """Create a stable filename fragment without exposing a shard cursor."""
    prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")[:40] or "shard"
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{suffix}"


def reject_sensitive_fields(value: Any) -> None:
    """Reject obvious credentials before durable local or database storage."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if is_sensitive_field_name(key):
                raise ValueError(f"sensitive field {key!r} cannot be persisted")
            reject_sensitive_fields(nested)
    elif isinstance(value, list):
        for nested in value:
            reject_sensitive_fields(nested)
    elif isinstance(value, str):
        if value.lstrip().lower().startswith(("bearer ", "basic ")):
            raise ValueError("authorization values cannot be persisted")
        reject_credential_url(value)


def reject_credential_url(value: str) -> None:
    """Reject URLs that would persist userinfo or credential query parameters."""
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("URLs with credentials cannot be persisted")
    components = [parsed.query, parsed.fragment]
    if not parsed.scheme and not parsed.netloc and "=" in value:
        components.append(value)
    for component in components:
        for key, _ in parse_qsl(component, keep_blank_values=True):
            if is_sensitive_field_name(key):
                raise ValueError("URLs with credential query parameters cannot be persisted")


def validate_phase_one_geojson_payload(payload: bytes) -> dict[str, int]:
    """Validate the bounded point-GeoJSON custody contract before durable storage."""
    if len(payload) > MAX_SOURCE_GEOJSON_BYTES:
        raise ValueError(f"payload exceeds {MAX_SOURCE_GEOJSON_BYTES} bytes")
    try:
        value = json.loads(payload, parse_constant=_reject_non_finite_json_constant)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be UTF-8 JSON") from exc
    _enforce_geojson_storage_policy(value)
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ValueError("payload must be a GeoJSON FeatureCollection")
    features = value.get("features")
    if not isinstance(features, list) or len(features) > MAX_SOURCE_GEOJSON_FEATURES:
        raise ValueError(f"features must contain at most {MAX_SOURCE_GEOJSON_FEATURES} entries")
    points = 0
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("every payload member must be a GeoJSON Feature")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "Point":
            raise ValueError("only Point observations are accepted in this phase-one slice")
        coordinates = geometry.get("coordinates")
        if (
            not isinstance(coordinates, list)
            or len(coordinates) < GEOJSON_COORDINATE_DIMENSIONS
            or not all(_is_finite_coordinate(value) for value in coordinates[:GEOJSON_COORDINATE_DIMENSIONS])
            or not GEOJSON_MIN_LONGITUDE <= coordinates[0] <= GEOJSON_MAX_LONGITUDE
            or not GEOJSON_MIN_LATITUDE <= coordinates[1] <= GEOJSON_MAX_LATITUDE
        ):
            raise ValueError("each Point must have finite WGS84 coordinates")
        if not isinstance(feature.get("properties"), dict):
            raise ValueError("each feature must have an object properties member")
        points += 1
    return {"feature_count": len(features), "point_count": points}


def canonical_sensitive_field_name(value: object) -> str:
    """Normalize camelCase, punctuation, and whitespace before custody checks."""
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value))
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")


def is_sensitive_field_name(value: object) -> bool:
    """Recognize explicit credential names plus unambiguous credential suffixes."""
    normalized = canonical_sensitive_field_name(value)
    return normalized in SENSITIVE_FIELD_NAMES or normalized.endswith(SENSITIVE_FIELD_SUFFIXES)


def _reject_non_finite_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON values are not permitted")


def _is_finite_coordinate(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _enforce_geojson_storage_policy(value: object) -> None:
    """Reject sensitive or excessively nested source payloads before hashing or storage."""
    stack: list[tuple[object, int]] = [(value, 0)]
    visited_nodes = 0
    while stack:
        current, depth = stack.pop()
        visited_nodes += 1
        if visited_nodes > MAX_GEOJSON_STORAGE_NODES:
            raise ValueError("payload exceeds the GeoJSON metadata-node storage limit")
        if depth > MAX_GEOJSON_STORAGE_DEPTH:
            raise ValueError("payload exceeds the GeoJSON metadata nesting limit")
        if isinstance(current, dict):
            for key, nested in current.items():
                if is_sensitive_field_name(key):
                    raise ValueError("payload contains a sensitive field and cannot be persisted")
                stack.append((nested, depth + 1))
        elif isinstance(current, list):
            stack.extend((nested, depth + 1) for nested in current)
        elif isinstance(current, str):
            if current.lstrip().lower().startswith(("bearer ", "basic ")):
                raise ValueError("payload contains an authorization value and cannot be persisted")
            reject_credential_url(current)
