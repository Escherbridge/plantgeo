"""Semantic, bounded promotion archives for phase-one source lineage."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
import shutil
import tempfile
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agri_data_service.execution.contracts import (
    SHA256_PATTERN,
    canonical_json_bytes,
    reject_credential_url,
    reject_sensitive_fields,
    validate_phase_one_geojson_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

PROMOTION_ARCHIVE_SCHEMA_VERSION: Literal[1] = 1
PROMOTION_NAMESPACE = uuid.UUID("c61eb4a1-f009-5d72-9e74-6f17a5614d3d")
REQUIRED_EXTENSION_NAMES = frozenset({"postgis", "timescaledb", "vector", "pgcrypto"})
MAX_INSTALLED_EXTENSION_VERSION_LENGTH = 255
SHA256_HEX_LENGTH = 64
ARCHIVE_FILE_NAMES = {
    "data_sources": "data-sources.json",
    "source_releases": "source-releases.json",
    "artifacts": "artifacts.json",
    "release_sets": "release-sets.json",
    "release_set_items": "release-set-items.json",
}
MANIFEST_FILE_NAME = "manifest.json"
MAX_ARCHIVE_RECORDS = 10_000
MAX_INLINE_ARTIFACT_BYTES = 5_000_000
MAX_TOTAL_INLINE_ARTIFACT_BYTES = 100_000_000
MAX_METADATA_ARCHIVE_FILE_BYTES = 10_000_000
MAX_ARTIFACTS_ARCHIVE_FILE_BYTES = 4 * ((MAX_TOTAL_INLINE_ARTIFACT_BYTES + 2) // 3) + MAX_METADATA_ARCHIVE_FILE_BYTES
MAX_MANIFEST_BYTES = 1_000_000


class PromotionError(ValueError):
    """A semantic promotion archive is incomplete, unsafe, or incompatible."""


class PromotionModel(BaseModel):
    """Strict persisted promotion contract base."""

    model_config = ConfigDict(extra="forbid")


class PromotionSourceMetadata(PromotionModel):
    """Non-secret source environment evidence bound into a bundle."""

    source_label: str = Field(min_length=1, max_length=255)
    source_service_id: str | None = Field(default=None, min_length=1, max_length=255)
    postgres_major: int = Field(ge=16, le=18)
    alembic_revision: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    extension_versions: dict[str, str]

    @field_validator("source_label", "source_service_id")
    @classmethod
    def require_opaque_identity(cls, value: str | None) -> str | None:
        return _require_opaque_identity(value)

    @field_validator("extension_versions")
    @classmethod
    def require_required_extensions(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != REQUIRED_EXTENSION_NAMES:
            raise ValueError("extension_versions must contain exactly the required extension names")
        if any(
            not isinstance(version, str)
            or not version.strip()
            or len(version.strip()) > MAX_INSTALLED_EXTENSION_VERSION_LENGTH
            for version in value.values()
        ):
            raise ValueError("extension_versions must contain nonblank installed versions of at most 255 characters")
        return {name: value[name].strip() for name in sorted(value)}


class DataSourceRecord(PromotionModel):
    """Portable row form of a governed data source."""

    id: uuid.UUID
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")
    name: str = Field(min_length=1, max_length=255)
    owner: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=10_000)
    base_url: str | None = Field(default=None, max_length=1000)
    license_name: str = Field(min_length=1, max_length=255)
    license_url: str | None = Field(default=None, max_length=1000)
    citation: str = Field(min_length=1, max_length=10_000)
    refresh_policy: dict[str, Any] = Field(default_factory=dict)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    allowed_client_exposure: bool = False
    review_state: Literal["draft", "reviewed", "approved", "rejected", "retired"]
    review_due_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = Field(default=None, max_length=255)
    is_active: bool
    configuration: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @field_validator("base_url", "license_url")
    @classmethod
    def reject_credential_urls(cls, value: str | None) -> str | None:
        if value is not None:
            reject_credential_url(value)
        return value

    @field_validator("refresh_policy", "configuration")
    @classmethod
    def require_safe_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_safe_json(value)
        return value

    @field_validator("review_due_at", "reviewed_at", "created_at", "updated_at")
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def require_approved_review_evidence(self) -> DataSourceRecord:
        if self.review_state == "approved" and (self.reviewed_at is None or not self.reviewed_by):
            raise ValueError("approved data sources require reviewer evidence")
        return self


class SourceReleaseRecord(PromotionModel):
    """Portable row form of a validated source release."""

    id: uuid.UUID
    data_source_id: uuid.UUID
    source_version: str = Field(min_length=1, max_length=255)
    retrieved_at: datetime
    data_available_at: datetime
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    payload_checksum: str = Field(pattern=SHA256_PATTERN)
    payload_bytes: int = Field(ge=0)
    schema_version: str = Field(min_length=1, max_length=100)
    transform_version: str = Field(default="source-native", min_length=1, max_length=100)
    license_snapshot: str = Field(min_length=1, max_length=10_000)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    validation_state: Literal["valid"]
    validated_at: datetime
    supersedes_release_id: uuid.UUID | None = None
    retraction_reason: str | None = Field(default=None, max_length=10_000)
    created_at: datetime

    @field_validator(
        "retrieved_at",
        "data_available_at",
        "observed_from",
        "observed_to",
        "validated_at",
        "created_at",
    )
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value) if value is not None else None

    @field_validator("query_parameters", "quality_summary")
    @classmethod
    def require_safe_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_safe_json(value)
        return value

    @model_validator(mode="after")
    def require_ordered_observation_window(self) -> SourceReleaseRecord:
        if self.observed_from is not None and self.observed_to is not None and self.observed_to < self.observed_from:
            raise ValueError("observed_to must not precede observed_from")
        return self


class ArtifactRecord(PromotionModel):
    """Portable row form of a source artifact with verified inline bytes."""

    id: uuid.UUID
    source_release_id: uuid.UUID
    kind: str = Field(min_length=1, max_length=100)
    uri: str = Field(min_length=1, max_length=2000)
    media_type: str | None = Field(default=None, max_length=255)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    storage_class: str = Field(min_length=1, max_length=50)
    encryption_key_ref: str | None = Field(default=None, max_length=500)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    content_base64: str | None = None
    created_at: datetime

    @field_validator("uri")
    @classmethod
    def reject_credential_uri(cls, value: str) -> str:
        reject_credential_url(value)
        return value

    @field_validator("metadata_json")
    @classmethod
    def require_safe_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _require_safe_json(value)
        return value

    @field_validator("content_base64")
    @classmethod
    def normalize_inline_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        content = _decode_inline_content(value)
        if len(content) > MAX_INLINE_ARTIFACT_BYTES:
            raise ValueError("inline artifact exceeds the phase-one byte limit")
        return base64.b64encode(content).decode("ascii")

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def verify_content_receipt(self) -> ArtifactRecord:
        content = decode_artifact_content(self)
        if self.storage_class == "database_inline" and content is None:
            raise ValueError("database_inline artifacts require content_base64")
        if content is not None:
            if len(content) != self.size_bytes:
                raise ValueError("inline artifact size does not match its declared receipt")
            if hashlib.sha256(content).hexdigest() != self.checksum_sha256:
                raise ValueError("inline artifact checksum does not match its declared receipt")
        return self


class ReleaseSetRecord(PromotionModel):
    """V1 only permits already validated source release sets."""

    id: uuid.UUID
    logical_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    as_of_time: datetime
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    state: Literal["validated"]
    description: str | None = Field(default=None, max_length=10_000)
    validated_at: datetime
    created_at: datetime

    @field_validator("as_of_time", "validated_at", "created_at")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class ReleaseSetItemRecord(PromotionModel):
    """One source-release membership row in a validated release set."""

    release_set_id: uuid.UUID
    source_release_id: uuid.UUID
    source_role: Literal["input"]
    added_at: datetime

    @field_validator("added_at")
    @classmethod
    def require_aware_added_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class PromotionArchive(PromotionModel):
    """The narrow v1 lineage closure that may cross a promotion boundary."""

    source: PromotionSourceMetadata
    data_sources: list[DataSourceRecord] = Field(min_length=1, max_length=MAX_ARCHIVE_RECORDS)
    source_releases: list[SourceReleaseRecord] = Field(min_length=1, max_length=MAX_ARCHIVE_RECORDS)
    artifacts: list[ArtifactRecord] = Field(min_length=1, max_length=MAX_ARCHIVE_RECORDS)
    release_sets: list[ReleaseSetRecord] = Field(min_length=1, max_length=MAX_ARCHIVE_RECORDS)
    release_set_items: list[ReleaseSetItemRecord] = Field(min_length=1, max_length=MAX_ARCHIVE_RECORDS)

    @model_validator(mode="after")
    def validate_v1_lineage_closure(self) -> PromotionArchive:  # noqa: PLR0912, PLR0915
        total_records = sum(
            len(rows)
            for rows in (
                self.data_sources,
                self.source_releases,
                self.artifacts,
                self.release_sets,
                self.release_set_items,
            )
        )
        if total_records > MAX_ARCHIVE_RECORDS:
            raise ValueError("promotion archive exceeds the phase-one record limit")

        data_sources_by_id: dict[uuid.UUID, DataSourceRecord] = _index_unique(
            self.data_sources,
            lambda item: item.id,
            "data source id",
        )
        data_sources_by_key: dict[str, DataSourceRecord] = _index_unique(
            self.data_sources,
            lambda item: item.key,
            "data source key",
        )
        if len(data_sources_by_id) != len(data_sources_by_key):
            raise ValueError("data sources must have one unique id and key")

        releases_by_id: dict[uuid.UUID, SourceReleaseRecord] = _index_unique(
            self.source_releases,
            lambda item: item.id,
            "source release id",
        )
        for release in self.source_releases:
            if release.data_source_id not in data_sources_by_id:
                raise ValueError("source release references a data source outside the archive")
            if release.supersedes_release_id is not None and release.supersedes_release_id not in releases_by_id:
                raise ValueError("source release supersession must be closed inside the archive")

        release_sets_by_id: dict[uuid.UUID, ReleaseSetRecord] = _index_unique(
            self.release_sets,
            lambda item: item.id,
            "release set id",
        )
        release_sets_by_key: dict[str, ReleaseSetRecord] = _index_unique(
            self.release_sets,
            lambda item: item.logical_key,
            "release set logical key",
        )
        release_sets_by_checksum: dict[str, ReleaseSetRecord] = _index_unique(
            self.release_sets,
            lambda item: item.manifest_checksum,
            "release set manifest checksum",
        )
        if len(release_sets_by_id) != len(release_sets_by_key) or len(release_sets_by_id) != len(
            release_sets_by_checksum
        ):
            raise ValueError("release sets must have one unique id, logical key, and manifest checksum")

        items_by_set: dict[uuid.UUID, list[ReleaseSetItemRecord]] = {key: [] for key in release_sets_by_id}
        seen_item_keys: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for item in self.release_set_items:
            item_key = (item.release_set_id, item.source_release_id)
            if item_key in seen_item_keys:
                raise ValueError("release set membership rows must be unique")
            seen_item_keys.add(item_key)
            if item.release_set_id not in release_sets_by_id or item.source_release_id not in releases_by_id:
                raise ValueError("release set membership references rows outside the archive")
            items_by_set[item.release_set_id].append(item)
        if any(len(items) != 1 for items in items_by_set.values()):
            raise ValueError("v1 promotion supports exactly one input source release per release set")

        closure = _source_release_closure(items_by_set, releases_by_id)
        if closure != set(releases_by_id):
            raise ValueError("archive contains a source release outside the selected lineage closure")
        required_data_sources = {release.data_source_id for release in releases_by_id.values()}
        if required_data_sources != set(data_sources_by_id):
            raise ValueError("archive contains a data source outside the selected lineage closure")

        artifacts_by_id: dict[uuid.UUID, ArtifactRecord] = _index_unique(
            self.artifacts,
            lambda item: item.id,
            "artifact id",
        )
        artifacts_by_identity: dict[tuple[str, str], ArtifactRecord] = _index_unique(
            self.artifacts,
            lambda item: (item.uri, item.checksum_sha256),
            "artifact uri and checksum",
        )
        if len(artifacts_by_id) != len(artifacts_by_identity):
            raise ValueError("artifacts must have one unique id and immutable receipt")
        inline_bytes = 0
        source_artifact_by_release: dict[uuid.UUID, ArtifactRecord] = {}
        for artifact in self.artifacts:
            if artifact.source_release_id not in releases_by_id:
                raise ValueError("artifact references a source release outside the archive")
            content = decode_artifact_content(artifact)
            if content is not None:
                inline_bytes += len(content)
            if artifact.kind == "source_geojson":
                if artifact.source_release_id in source_artifact_by_release:
                    raise ValueError("v1 permits only one source GeoJSON artifact per source release")
                source_artifact_by_release[artifact.source_release_id] = artifact
        if inline_bytes > MAX_TOTAL_INLINE_ARTIFACT_BYTES:
            raise ValueError("promotion archive exceeds the aggregate inline artifact limit")

        for release_set in self.release_sets:
            item = items_by_set[release_set.id][0]
            source_release = releases_by_id[item.source_release_id]
            data_source = data_sources_by_id[source_release.data_source_id]
            expected_manifest = _phase_one_release_manifest(data_source, source_release)
            if release_set.manifest_checksum != expected_manifest:
                raise ValueError("release set manifest does not bind its phase-one source release")
            source_artifact = source_artifact_by_release.get(source_release.id)
            if source_artifact is None:
                raise ValueError("selected source release is missing its source GeoJSON artifact")
            _validate_phase_one_source_artifact(source_artifact, data_source, source_release)

        for release in self.source_releases:
            source_artifact = source_artifact_by_release.get(release.id)
            if source_artifact is None:
                raise ValueError("source release closure is missing its source GeoJSON artifact")
            _validate_phase_one_source_artifact(source_artifact, data_sources_by_id[release.data_source_id], release)

        self.data_sources = sorted(self.data_sources, key=lambda item: item.key)
        self.source_releases = _ordered_source_releases(self.source_releases)
        self.artifacts = sorted(self.artifacts, key=lambda item: (str(item.source_release_id), item.uri, str(item.id)))
        self.release_sets = sorted(self.release_sets, key=lambda item: item.logical_key)
        self.release_set_items = sorted(
            self.release_set_items,
            key=lambda item: (str(item.release_set_id), str(item.source_release_id)),
        )
        return self


class PromotionManifest(PromotionModel):
    """Hash-only receipt for the files that make up one archive directory."""

    schema_version: Literal[1] = PROMOTION_ARCHIVE_SCHEMA_VERSION
    bundle_id: uuid.UUID
    created_at: datetime
    source: PromotionSourceMetadata
    content_checksum: str = Field(pattern=SHA256_PATTERN)
    release_set_ids: list[uuid.UUID]
    row_counts: dict[str, int]
    file_hashes: dict[str, str]

    @field_validator("created_at")
    @classmethod
    def require_aware_created_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("release_set_ids")
    @classmethod
    def require_sorted_release_sets(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if not value or value != sorted(set(value), key=str):
            raise ValueError("release_set_ids must be nonempty, unique, and sorted")
        return value

    @model_validator(mode="after")
    def verify_manifest_shape(self) -> PromotionManifest:
        if self.bundle_id != uuid.uuid5(PROMOTION_NAMESPACE, self.content_checksum):
            raise ValueError("bundle_id does not bind the archive content checksum")
        if set(self.row_counts) != set(ARCHIVE_FILE_NAMES):
            raise ValueError("row_counts must cover every archive data file")
        if any(not isinstance(value, int) or value < 0 for value in self.row_counts.values()):
            raise ValueError("row_counts must be nonnegative integers")
        if set(self.file_hashes) != set(ARCHIVE_FILE_NAMES.values()):
            raise ValueError("file_hashes must cover every archive data file")
        if any(not _is_sha256(value) for value in self.file_hashes.values()):
            raise ValueError("file_hashes must contain SHA-256 digests")
        return self


class PromotionTargetPreflight(PromotionModel):
    """Reviewed private target evidence required before restore planning."""

    target_label: str = Field(min_length=1, max_length=255)
    target_service_id: str | None = Field(default=None, min_length=1, max_length=255)
    private_control_plane: Literal[True]
    postgres_major: int = Field(ge=16, le=18)
    alembic_revision: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    extension_versions: dict[str, str]

    @field_validator("target_label", "target_service_id")
    @classmethod
    def require_opaque_identity(cls, value: str | None) -> str | None:
        return _require_opaque_identity(value)

    @field_validator("extension_versions")
    @classmethod
    def require_required_extensions(cls, value: dict[str, str]) -> dict[str, str]:
        return PromotionSourceMetadata.require_required_extensions(value)


class ExistingReleaseSet(PromotionModel):
    """The target release-set state needed for idempotent semantic planning."""

    id: uuid.UUID
    logical_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    as_of_time: datetime
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    state: Literal["draft", "validated", "published", "retired"]
    description: str | None = Field(default=None, max_length=10_000)
    validated_at: datetime | None = None
    published_at: datetime | None = None
    created_at: datetime

    @field_validator("as_of_time", "validated_at", "published_at", "created_at")
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        return _require_aware_utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_state_times(self) -> ExistingReleaseSet:
        if self.state == "draft" and (self.validated_at is not None or self.published_at is not None):
            raise ValueError("draft release sets cannot have finalization timestamps")
        if self.state in {"validated", "published"} and self.validated_at is None:
            raise ValueError("validated release sets require a validation timestamp")
        if self.state == "published" and self.published_at is None:
            raise ValueError("published release sets require a publication timestamp")
        return self


class PromotionTargetSnapshot(PromotionModel):
    """A scoped target read model; unrelated target records need not be listed."""

    data_sources: list[DataSourceRecord] = Field(default_factory=list)
    source_releases: list[SourceReleaseRecord] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    release_sets: list[ExistingReleaseSet] = Field(default_factory=list)
    release_set_items: list[ReleaseSetItemRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_target_identities(self) -> PromotionTargetSnapshot:
        _index_unique(self.data_sources, lambda item: item.id, "target data source id")
        _index_unique(self.data_sources, lambda item: item.key, "target data source key")
        _index_unique(self.source_releases, lambda item: item.id, "target source release id")
        _index_unique(
            self.source_releases,
            lambda item: (
                item.data_source_id,
                item.source_version,
                item.payload_checksum,
                item.transform_version,
            ),
            "target source release identity",
        )
        _index_unique(self.artifacts, lambda item: item.id, "target artifact id")
        _index_unique(self.artifacts, lambda item: (item.uri, item.checksum_sha256), "target artifact identity")
        _index_unique(self.release_sets, lambda item: item.id, "target release set id")
        _index_unique(self.release_sets, lambda item: item.logical_key, "target release set logical key")
        _index_unique(
            self.release_sets,
            lambda item: item.manifest_checksum,
            "target release set manifest checksum",
        )
        _index_unique(
            self.release_set_items,
            lambda item: (item.release_set_id, item.source_release_id),
            "target release set membership",
        )
        return self


class RestoreStepKind(StrEnum):
    """Only semantic steps are available; no raw dump restore exists in v1."""

    ENSURE_DATA_SOURCE = "ensure_data_source"
    ENSURE_SOURCE_RELEASE = "ensure_source_release"
    ENSURE_ARTIFACT = "ensure_artifact"
    CREATE_RELEASE_SET_DRAFT = "create_release_set_draft"
    RESUME_RELEASE_SET_DRAFT = "resume_release_set_draft"
    ADD_RELEASE_SET_MEMBERSHIP = "add_release_set_membership"
    VALIDATE_RELEASE_SET = "validate_release_set"


class RestoreStep(PromotionModel):
    """One ordered, idempotent mutation required by a semantic restore."""

    kind: RestoreStepKind
    record_id: uuid.UUID | None = None
    release_set_id: uuid.UUID | None = None
    source_release_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_exact_step_identity(self) -> RestoreStep:
        base_steps = {
            RestoreStepKind.ENSURE_DATA_SOURCE,
            RestoreStepKind.ENSURE_SOURCE_RELEASE,
            RestoreStepKind.ENSURE_ARTIFACT,
        }
        draft_steps = {
            RestoreStepKind.CREATE_RELEASE_SET_DRAFT,
            RestoreStepKind.RESUME_RELEASE_SET_DRAFT,
            RestoreStepKind.VALIDATE_RELEASE_SET,
        }
        if self.kind in base_steps:
            if self.record_id is None or self.release_set_id is not None or self.source_release_id is not None:
                raise ValueError("base restore steps require only a record id")
        elif self.kind in draft_steps:
            if self.release_set_id is None or self.record_id is not None or self.source_release_id is not None:
                raise ValueError("release-set transition steps require only a release-set id")
        elif self.kind == RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP and (
            self.release_set_id is None or self.source_release_id is None or self.record_id is not None
        ):
            raise ValueError("membership steps require release-set and source-release ids")
        return self


class SemanticRestorePlan(PromotionModel):
    """A trigger-safe restore plan, generated only from a semantic archive."""

    transport: Literal["semantic_bundle"] = "semantic_bundle"
    archive_content_checksum: str = Field(pattern=SHA256_PATTERN)
    steps: list[RestoreStep]

    @model_validator(mode="after")
    def require_draft_before_membership_and_validation(self) -> SemanticRestorePlan:
        phase_by_release_set: dict[uuid.UUID, Literal["draft", "validated"]] = {}
        for step in self.steps:
            if step.kind in {
                RestoreStepKind.CREATE_RELEASE_SET_DRAFT,
                RestoreStepKind.RESUME_RELEASE_SET_DRAFT,
            }:
                assert step.release_set_id is not None
                if step.release_set_id in phase_by_release_set:
                    raise ValueError("release-set restore may enter draft exactly once")
                phase_by_release_set[step.release_set_id] = "draft"
            elif step.kind == RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP:
                assert step.release_set_id is not None
                if phase_by_release_set.get(step.release_set_id) != "draft":
                    raise ValueError("release-set membership must be restored while draft")
            elif step.kind == RestoreStepKind.VALIDATE_RELEASE_SET:
                assert step.release_set_id is not None
                if phase_by_release_set.get(step.release_set_id) != "draft":
                    raise ValueError("release set may validate only after entering draft")
                phase_by_release_set[step.release_set_id] = "validated"
        return self


def encode_artifact_content(content: bytes) -> str:
    """Encode bounded inline artifact bytes for a canonical JSON data file."""
    if len(content) > MAX_INLINE_ARTIFACT_BYTES:
        raise PromotionError("inline artifact exceeds the phase-one byte limit")
    return base64.b64encode(content).decode("ascii")


def decode_artifact_content(artifact: ArtifactRecord) -> bytes | None:
    """Decode an artifact only after its model has checked the receipt."""
    return _decode_inline_content(artifact.content_base64) if artifact.content_base64 is not None else None


def promotion_content_checksum(archive: PromotionArchive) -> str:
    """Hash the semantic source evidence and normalized data rows, not archive timestamps."""
    normalized = _normalize_archive(archive)
    return hashlib.sha256(canonical_json_bytes(_content_identity(normalized))).hexdigest()


def build_promotion_manifest(
    archive: PromotionArchive,
    *,
    file_hashes: dict[str, str],
    created_at: datetime,
) -> PromotionManifest:
    """Create a hash-only manifest after every data file has been written."""
    normalized = _normalize_archive(archive)
    checksum = promotion_content_checksum(normalized)
    return PromotionManifest(
        bundle_id=uuid.uuid5(PROMOTION_NAMESPACE, checksum),
        created_at=created_at,
        source=normalized.source,
        content_checksum=checksum,
        release_set_ids=sorted((item.id for item in normalized.release_sets), key=str),
        row_counts=_row_counts(normalized),
        file_hashes=file_hashes,
    )


def write_promotion_archive(
    destination: Path,
    archive: PromotionArchive,
    *,
    created_at: datetime | None = None,
) -> PromotionManifest:
    """Atomically write a closed, canonical archive directory without overwriting data."""
    normalized = _normalize_archive(archive)
    destination = destination.expanduser()
    if destination.exists():
        raise PromotionError("promotion archive destination already exists")
    if not destination.name:
        raise PromotionError("promotion archive destination must name a directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        file_hashes: dict[str, str] = {}
        for key, file_name in ARCHIVE_FILE_NAMES.items():
            content = canonical_json_bytes(_archive_rows(normalized)[key])
            if len(content) > _archive_file_max_bytes(key):
                raise PromotionError(f"promotion archive file exceeds its bounded size: {file_name}")
            _write_private_file(temporary / file_name, content)
            file_hashes[file_name] = hashlib.sha256(content).hexdigest()
        manifest = build_promotion_manifest(
            normalized,
            file_hashes=file_hashes,
            created_at=created_at or datetime.now(UTC),
        )
        manifest_content = canonical_json_bytes(manifest.model_dump(mode="json"))
        if len(manifest_content) > MAX_MANIFEST_BYTES:
            raise PromotionError("promotion manifest exceeds its bounded size")
        _write_private_file(temporary / MANIFEST_FILE_NAME, manifest_content)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def load_promotion_archive(source: Path) -> tuple[PromotionArchive, PromotionManifest]:
    """Load and verify every archive receipt before any restore planning can occur."""
    source = source.expanduser()
    if not source.is_dir():
        raise PromotionError("promotion archive must be a directory")
    expected_files = set(ARCHIVE_FILE_NAMES.values()) | {MANIFEST_FILE_NAME}
    actual_files = {child.name for child in source.iterdir()}
    if actual_files != expected_files:
        raise PromotionError("promotion archive contains missing or unexpected files")
    manifest_value = _load_json(source / MANIFEST_FILE_NAME, MAX_MANIFEST_BYTES)
    try:
        manifest = PromotionManifest.model_validate(manifest_value)
    except ValueError as exc:
        raise PromotionError("promotion manifest is invalid") from exc

    payload: dict[str, list[dict[str, Any]]] = {}
    for key, file_name in ARCHIVE_FILE_NAMES.items():
        content = _read_bounded_file(source / file_name, _archive_file_max_bytes(key))
        checksum = hashlib.sha256(content).hexdigest()
        if manifest.file_hashes[file_name] != checksum:
            raise PromotionError(f"archive file checksum mismatch: {file_name}")
        decoded = _load_json_bytes(content, file_name)
        if not isinstance(decoded, list):
            raise PromotionError(f"archive data file is not a JSON array: {file_name}")
        payload[key] = decoded

    try:
        archive = PromotionArchive.model_validate({"source": manifest.source, **payload})
    except ValueError as exc:
        raise PromotionError("promotion archive lineage is invalid") from exc
    if promotion_content_checksum(archive) != manifest.content_checksum:
        raise PromotionError("promotion archive content checksum mismatch")
    if manifest.release_set_ids != sorted((item.id for item in archive.release_sets), key=str):
        raise PromotionError("promotion manifest release-set receipt does not match archive content")
    if manifest.row_counts != _row_counts(archive):
        raise PromotionError("promotion manifest row counts do not match archive content")
    return archive, manifest


def validate_target_preflight(archive: PromotionArchive, target: PromotionTargetPreflight) -> None:
    """Require an already migrated private target before semantic restore planning."""
    normalized = _normalize_archive(archive)
    if target.postgres_major < normalized.source.postgres_major:
        raise PromotionError("target PostgreSQL major must not be older than the source archive")
    if target.alembic_revision != normalized.source.alembic_revision:
        raise PromotionError("target Alembic revision must match the source archive before restore")
    if set(target.extension_versions) != REQUIRED_EXTENSION_NAMES:
        raise PromotionError("target must expose all required installed extensions")


def plan_semantic_restore(
    archive: PromotionArchive,
    *,
    target_preflight: PromotionTargetPreflight,
    target: PromotionTargetSnapshot | None = None,
    transport: str = "semantic_bundle",
) -> SemanticRestorePlan:
    """Plan exact idempotent writes without a raw pg_restore or trigger bypass."""
    if transport != "semantic_bundle":
        raise PromotionError("blind pg_restore or trigger bypass is prohibited; use the semantic bundle adapter")
    normalized = _normalize_archive(archive)
    validate_target_preflight(normalized, target_preflight)
    target = target or PromotionTargetSnapshot()
    steps: list[RestoreStep] = []

    target_data_sources_by_id: dict[uuid.UUID, DataSourceRecord] = _index_unique(
        target.data_sources,
        lambda item: item.id,
        "target data source id",
    )
    target_data_sources_by_key: dict[str, DataSourceRecord] = _index_unique(
        target.data_sources,
        lambda item: item.key,
        "target data source key",
    )
    steps.extend(
        RestoreStep(kind=RestoreStepKind.ENSURE_DATA_SOURCE, record_id=data_source_record.id)
        for data_source_record in normalized.data_sources
        if not _target_record_matches(
            data_source_record,
            target_data_sources_by_id.get(data_source_record.id),
            target_data_sources_by_key.get(data_source_record.key),
            "data source",
        )
    )

    target_releases_by_id: dict[uuid.UUID, SourceReleaseRecord] = _index_unique(
        target.source_releases,
        lambda item: item.id,
        "target source release id",
    )
    target_releases_by_identity: dict[tuple[uuid.UUID, str, str, str], SourceReleaseRecord] = _index_unique(
        target.source_releases,
        lambda item: (
            item.data_source_id,
            item.source_version,
            item.payload_checksum,
            item.transform_version,
        ),
        "target source release identity",
    )
    steps.extend(
        RestoreStep(kind=RestoreStepKind.ENSURE_SOURCE_RELEASE, record_id=source_release_record.id)
        for source_release_record in normalized.source_releases
        if not _target_record_matches(
            source_release_record,
            target_releases_by_id.get(source_release_record.id),
            target_releases_by_identity.get(
                (
                    source_release_record.data_source_id,
                    source_release_record.source_version,
                    source_release_record.payload_checksum,
                    source_release_record.transform_version,
                )
            ),
            "source release",
        )
    )

    target_artifacts_by_id: dict[uuid.UUID, ArtifactRecord] = _index_unique(
        target.artifacts,
        lambda item: item.id,
        "target artifact id",
    )
    target_artifacts_by_identity: dict[tuple[str, str], ArtifactRecord] = _index_unique(
        target.artifacts,
        lambda item: (item.uri, item.checksum_sha256),
        "target artifact identity",
    )
    steps.extend(
        RestoreStep(kind=RestoreStepKind.ENSURE_ARTIFACT, record_id=artifact_record.id)
        for artifact_record in normalized.artifacts
        if not _target_record_matches(
            artifact_record,
            target_artifacts_by_id.get(artifact_record.id),
            target_artifacts_by_identity.get((artifact_record.uri, artifact_record.checksum_sha256)),
            "artifact",
        )
    )

    target_sets_by_id: dict[uuid.UUID, ExistingReleaseSet] = _index_unique(
        target.release_sets,
        lambda item: item.id,
        "target release set id",
    )
    target_sets_by_key: dict[str, ExistingReleaseSet] = _index_unique(
        target.release_sets,
        lambda item: item.logical_key,
        "target release set logical key",
    )
    target_sets_by_manifest: dict[str, ExistingReleaseSet] = _index_unique(
        target.release_sets,
        lambda item: item.manifest_checksum,
        "target release set manifest checksum",
    )
    target_items_by_set: dict[uuid.UUID, dict[tuple[uuid.UUID, uuid.UUID], ReleaseSetItemRecord]] = {}
    for target_release_set_item in target.release_set_items:
        target_items_by_set.setdefault(target_release_set_item.release_set_id, {})[
            (target_release_set_item.release_set_id, target_release_set_item.source_release_id)
        ] = target_release_set_item
    archive_items_by_set: dict[uuid.UUID, list[ReleaseSetItemRecord]] = {}
    for archive_release_set_item in normalized.release_set_items:
        archive_items_by_set.setdefault(archive_release_set_item.release_set_id, []).append(archive_release_set_item)

    for release_set_record in normalized.release_sets:
        existing_release_set = _target_release_set_match(
            release_set_record,
            target_sets_by_id.get(release_set_record.id),
            target_sets_by_key.get(release_set_record.logical_key),
            target_sets_by_manifest.get(release_set_record.manifest_checksum),
        )
        expected_release_set_items = {
            (item.release_set_id, item.source_release_id): item for item in archive_items_by_set[release_set_record.id]
        }
        if existing_release_set is None:
            steps.append(
                RestoreStep(kind=RestoreStepKind.CREATE_RELEASE_SET_DRAFT, release_set_id=release_set_record.id)
            )
            steps.extend(
                RestoreStep(
                    kind=RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP,
                    release_set_id=release_set_item.release_set_id,
                    source_release_id=release_set_item.source_release_id,
                )
                for release_set_item in expected_release_set_items.values()
            )
            steps.append(RestoreStep(kind=RestoreStepKind.VALIDATE_RELEASE_SET, release_set_id=release_set_record.id))
            continue

        if existing_release_set.state == "validated":
            if existing_release_set.validated_at != release_set_record.validated_at:
                raise PromotionError("validated target release set has different validation evidence")
            _require_exact_membership(
                expected_release_set_items,
                target_items_by_set.get(release_set_record.id, {}),
                release_set_id=release_set_record.id,
            )
            continue
        if existing_release_set.state != "draft":
            raise PromotionError("v1 refuses published or retired release sets during semantic restore")
        steps.append(RestoreStep(kind=RestoreStepKind.RESUME_RELEASE_SET_DRAFT, release_set_id=release_set_record.id))
        existing_release_set_items = target_items_by_set.get(release_set_record.id, {})
        _require_membership_subset(
            expected_release_set_items,
            existing_release_set_items,
            release_set_id=release_set_record.id,
        )
        for item_key, release_set_item in expected_release_set_items.items():
            if item_key not in existing_release_set_items:
                steps.append(
                    RestoreStep(
                        kind=RestoreStepKind.ADD_RELEASE_SET_MEMBERSHIP,
                        release_set_id=release_set_item.release_set_id,
                        source_release_id=release_set_item.source_release_id,
                    )
                )
        steps.append(RestoreStep(kind=RestoreStepKind.VALIDATE_RELEASE_SET, release_set_id=release_set_record.id))

    return SemanticRestorePlan(
        archive_content_checksum=promotion_content_checksum(normalized),
        steps=steps,
    )


def _normalize_archive(archive: PromotionArchive) -> PromotionArchive:
    return PromotionArchive.model_validate(archive.model_dump(mode="python"))


def _content_identity(archive: PromotionArchive) -> dict[str, Any]:
    return {
        "schema_version": PROMOTION_ARCHIVE_SCHEMA_VERSION,
        "source": archive.source.model_dump(mode="json"),
        **_archive_rows(archive),
    }


def _archive_rows(archive: PromotionArchive) -> dict[str, list[dict[str, Any]]]:
    return {
        "data_sources": [item.model_dump(mode="json") for item in archive.data_sources],
        "source_releases": [item.model_dump(mode="json") for item in archive.source_releases],
        "artifacts": [item.model_dump(mode="json") for item in archive.artifacts],
        "release_sets": [item.model_dump(mode="json") for item in archive.release_sets],
        "release_set_items": [item.model_dump(mode="json") for item in archive.release_set_items],
    }


def _row_counts(archive: PromotionArchive) -> dict[str, int]:
    return {
        "data_sources": len(archive.data_sources),
        "source_releases": len(archive.source_releases),
        "artifacts": len(archive.artifacts),
        "release_sets": len(archive.release_sets),
        "release_set_items": len(archive.release_set_items),
    }


def _archive_file_max_bytes(key: str) -> int:
    if key == "artifacts":
        return MAX_ARTIFACTS_ARCHIVE_FILE_BYTES
    return MAX_METADATA_ARCHIVE_FILE_BYTES


def _target_record_matches(
    expected: DataSourceRecord | SourceReleaseRecord | ArtifactRecord,
    by_id: DataSourceRecord | SourceReleaseRecord | ArtifactRecord | None,
    by_identity: DataSourceRecord | SourceReleaseRecord | ArtifactRecord | None,
    label: str,
) -> bool:
    candidates = {id(candidate): candidate for candidate in (by_id, by_identity) if candidate is not None}
    if not candidates:
        return False
    if len(candidates) != 1:
        raise PromotionError(f"target {label} id and immutable identity resolve to different records")
    candidate = next(iter(candidates.values()))
    if candidate.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise PromotionError(f"target {label} conflicts with immutable archive content")
    return True


def _target_release_set_match(
    expected: ReleaseSetRecord,
    by_id: ExistingReleaseSet | None,
    by_key: ExistingReleaseSet | None,
    by_manifest: ExistingReleaseSet | None,
) -> ExistingReleaseSet | None:
    candidates = {id(candidate): candidate for candidate in (by_id, by_key, by_manifest) if candidate is not None}
    if not candidates:
        return None
    if len(candidates) != 1:
        raise PromotionError("target release-set id and immutable identities resolve to different records")
    candidate = next(iter(candidates.values()))
    if (
        candidate.id != expected.id
        or candidate.logical_key != expected.logical_key
        or candidate.as_of_time != expected.as_of_time
        or candidate.manifest_checksum != expected.manifest_checksum
        or candidate.description != expected.description
        or candidate.created_at != expected.created_at
    ):
        raise PromotionError("target release set conflicts with immutable archive identity")
    return candidate


def _require_exact_membership(
    expected: dict[tuple[uuid.UUID, uuid.UUID], ReleaseSetItemRecord],
    actual: dict[tuple[uuid.UUID, uuid.UUID], ReleaseSetItemRecord],
    *,
    release_set_id: uuid.UUID,
) -> None:
    if set(expected) != set(actual) or any(actual[key] != value for key, value in expected.items()):
        raise PromotionError(f"validated target release set {release_set_id} has different immutable membership")


def _require_membership_subset(
    expected: dict[tuple[uuid.UUID, uuid.UUID], ReleaseSetItemRecord],
    actual: dict[tuple[uuid.UUID, uuid.UUID], ReleaseSetItemRecord],
    *,
    release_set_id: uuid.UUID,
) -> None:
    if not set(actual).issubset(expected) or any(expected[key] != value for key, value in actual.items()):
        raise PromotionError(f"draft target release set {release_set_id} contains conflicting membership")


def _index_unique[T, K](records: list[T], key: Callable[[T], K], label: str) -> dict[K, T]:
    indexed: dict[K, T] = {}
    for record in records:
        record_key = key(record)
        if record_key in indexed:
            raise ValueError(f"duplicate {label}")
        indexed[record_key] = record
    return indexed


def _source_release_closure(
    items_by_set: dict[uuid.UUID, list[ReleaseSetItemRecord]],
    releases_by_id: dict[uuid.UUID, SourceReleaseRecord],
) -> set[uuid.UUID]:
    required = {items[0].source_release_id for items in items_by_set.values()}
    closure: set[uuid.UUID] = set()
    visiting: set[uuid.UUID] = set()

    def visit(release_id: uuid.UUID) -> None:
        if release_id in closure:
            return
        if release_id in visiting:
            raise ValueError("source release supersession cycle is not promotable")
        visiting.add(release_id)
        parent = releases_by_id[release_id].supersedes_release_id
        if parent is not None:
            visit(parent)
        visiting.remove(release_id)
        closure.add(release_id)

    for release_id in required:
        visit(release_id)
    return closure


def _ordered_source_releases(records: list[SourceReleaseRecord]) -> list[SourceReleaseRecord]:
    by_id = {item.id: item for item in records}
    children: dict[uuid.UUID, list[uuid.UUID]] = {item.id: [] for item in records}
    in_degree: dict[uuid.UUID, int] = {item.id: 0 for item in records}
    for item in records:
        if item.supersedes_release_id is not None:
            children[item.supersedes_release_id].append(item.id)
            in_degree[item.id] += 1
    ready = sorted((item_id for item_id, degree in in_degree.items() if degree == 0), key=str)
    ordered: list[SourceReleaseRecord] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for child in sorted(children[current], key=str):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                ready.append(child)
        ready.sort(key=str)
    if len(ordered) != len(records):
        raise ValueError("source release supersession cycle is not promotable")
    return ordered


def _phase_one_release_manifest(data_source: DataSourceRecord, release: SourceReleaseRecord) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "source_key": data_source.key,
                "source_version": release.source_version,
                "payload_checksum": release.payload_checksum,
                "schema_version": release.schema_version,
            }
        )
    ).hexdigest()


def _validate_phase_one_source_artifact(
    artifact: ArtifactRecord,
    data_source: DataSourceRecord,
    release: SourceReleaseRecord,
) -> None:
    expected_uri = f"warehouse://source-releases/{data_source.key}/{release.source_version}/{release.payload_checksum}"
    content = decode_artifact_content(artifact)
    if (
        artifact.kind != "source_geojson"
        or artifact.uri != expected_uri
        or artifact.media_type != "application/geo+json"
        or artifact.storage_class != "database_inline"
        or artifact.checksum_sha256 != release.payload_checksum
        or artifact.size_bytes != release.payload_bytes
        or artifact.metadata_json != release.quality_summary
        or content is None
    ):
        raise ValueError("source GeoJSON artifact does not match its governed source release")
    validate_phase_one_geojson_payload(content)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _require_opaque_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or "://" in normalized or "@" in normalized:
        raise ValueError("source identity must be an opaque label, not a DSN")
    return normalized


def _require_safe_json(value: Any) -> None:
    reject_sensitive_fields(value)

    def visit(current: Any) -> None:
        if current is None or isinstance(current, str | bool | int):
            return
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("JSON metadata cannot contain non-finite numbers")
            return
        if isinstance(current, list):
            for nested in current:
                visit(nested)
            return
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise ValueError("JSON metadata object keys must be strings")
            for nested in current.values():
                visit(nested)
            return
        raise ValueError("metadata must contain JSON-compatible values")

    visit(value)


def _decode_inline_content(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("content_base64 must be strict standard Base64") from exc


def _is_sha256(value: str) -> bool:
    return len(value) == SHA256_HEX_LENGTH and all(character in "0123456789abcdef" for character in value)


def _write_private_file(path: Path, content: bytes) -> None:
    with path.open("xb") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    with suppress(OSError):
        path.chmod(0o600)
        # Windows ACL policy remains operator controlled; no broader mode is requested here.


def _read_bounded_file(path: Path, max_bytes: int) -> bytes:
    if not path.is_file():
        raise PromotionError(f"archive file is missing: {path.name}")
    size = path.stat().st_size
    if size < 0 or size > max_bytes:
        raise PromotionError(f"archive file exceeds its bounded size: {path.name}")
    with path.open("rb") as file:
        content = file.read(max_bytes + 1)
    if len(content) != size or len(content) > max_bytes:
        raise PromotionError(f"archive file changed while being read: {path.name}")
    return content


def _load_json(path: Path, max_bytes: int) -> Any:
    return _load_json_bytes(_read_bounded_file(path, max_bytes), path.name)


def _load_json_bytes(content: bytes, label: str) -> Any:
    try:
        return json.loads(content, parse_constant=_reject_non_finite_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PromotionError(f"archive JSON is invalid: {label}") from exc


def _reject_non_finite_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON numbers are not permitted")
