"""Validated, checkpointed publication of one bounded source release."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select

from agri_data_service.execution.contracts import (
    MAX_SOURCE_GEOJSON_BYTES,
    canonical_json_bytes,
    reject_credential_url,
    reject_sensitive_fields,
    validate_phase_one_geojson_payload,
)
from agri_data_service.models.provenance import (
    Artifact,
    DataSource,
    ReleaseSet,
    ReleaseSetItem,
    ReleaseSetState,
    ReleaseValidationState,
    SourceRelease,
    SourceReviewState,
)

if TYPE_CHECKING:
    import uuid
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


class SourceDefinition(BaseModel):
    """Governed identity for one source accepted by the warehouse."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")
    name: str = Field(min_length=1, max_length=255)
    owner: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=10_000)
    license_name: str = Field(min_length=1, max_length=255)
    citation: str = Field(min_length=1, max_length=10_000)
    base_url: str | None = Field(default=None, max_length=1000)
    license_url: str | None = Field(default=None, max_length=1000)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    reviewed_at: datetime
    reviewed_by: str = Field(min_length=1, max_length=255)

    @field_validator("base_url", "license_url")
    @classmethod
    def urls_must_not_include_credentials(cls, value: str | None) -> str | None:
        if value is not None:
            reject_credential_url(value)
        return value

    @field_validator("reviewed_at")
    @classmethod
    def reviewed_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value.astimezone(UTC)


class SourceReleasePlan(BaseModel):
    """Immutable source release metadata supplied with a local capture."""

    model_config = ConfigDict(extra="forbid")

    source_version: str = Field(min_length=1, max_length=255)
    schema_version: str = Field(min_length=1, max_length=100)
    data_available_at: datetime
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    query_parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("data_available_at", "observed_from", "observed_to")
    @classmethod
    def times_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("release timestamps must include a timezone")
        return value.astimezone(UTC)

    @field_validator("query_parameters")
    @classmethod
    def query_parameters_must_not_store_credentials(
        cls, value: dict[str, str | int | float | bool | None]
    ) -> dict[str, str | int | float | bool | None]:
        reject_sensitive_fields(value)
        return value

    @model_validator(mode="after")
    def observation_window_must_be_ordered(self) -> "SourceReleasePlan":
        if self.observed_from is not None and self.observed_to is not None and self.observed_to < self.observed_from:
            raise ValueError("observed_to must not precede observed_from")
        return self


class SourceIngestionPlan(BaseModel):
    """Reviewed metadata sidecar for a locally captured GeoJSON release."""

    model_config = ConfigDict(extra="forbid")

    source: SourceDefinition
    release: SourceReleasePlan
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("release_set_as_of")
    @classmethod
    def release_set_time_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("release_set_as_of must include a timezone")
        return value.astimezone(UTC)


class SourceIngestionCheckpoint(BaseModel):
    """Local durable state for a single source release publication attempt."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1, 2] = 2
    state: Literal["validated", "published", "blocked"]
    source_key: str
    source_version: str
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=0)
    updated_at: datetime
    plan_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    release_set_manifest_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    release_set_id: uuid.UUID | None = None
    source_release_id: uuid.UUID | None = None
    artifact_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def checkpoint_must_bind_its_identity(self) -> "SourceIngestionCheckpoint":
        if self.schema_version == 2 and (
            self.plan_checksum is None or self.release_set_manifest_checksum is None
        ):
            raise ValueError("schema version 2 checkpoints require plan and release-set checksums")
        publication_ids = (self.release_set_id, self.source_release_id, self.artifact_id)
        if self.state == "published" and any(value is None for value in publication_ids):
            raise ValueError("published checkpoints require warehouse identities")
        if self.state != "published" and any(value is not None for value in publication_ids):
            raise ValueError("only published checkpoints may contain warehouse identities")
        return self


class SourceIngestionResult(BaseModel):
    """Stable command result describing exactly what reached the warehouse."""

    source_id: uuid.UUID
    source_release_id: uuid.UUID
    artifact_id: uuid.UUID
    release_set_id: uuid.UUID
    payload_checksum: str
    payload_bytes: int
    idempotent: bool


def load_and_validate_geojson(path: Path) -> tuple[bytes, dict[str, int]]:
    """Read a bounded GeoJSON observation release and enforce its structural contract."""
    with path.open("rb") as payload_file:
        payload = payload_file.read(MAX_SOURCE_GEOJSON_BYTES + 1)
    return payload, validate_phase_one_geojson_payload(payload)


def checkpoint_path(root: Path, plan: SourceIngestionPlan, payload_checksum: str) -> Path:
    """Derive a collision-resistant local checkpoint path from immutable release identity."""
    identity = f"{source_ingestion_plan_checksum(plan)}:{payload_checksum}".encode()
    return root / "source-ingestion" / f"{hashlib.sha256(identity).hexdigest()}.json"


def write_checkpoint(path: Path, checkpoint: SourceIngestionCheckpoint) -> None:
    """Atomically persist a resumable state transition on the operator machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical_json_bytes(checkpoint.model_dump(mode="json")))
    os.replace(temporary, path)


def load_checkpoint(path: Path) -> SourceIngestionCheckpoint:
    """Read a checkpoint without attempting network or warehouse work."""
    return SourceIngestionCheckpoint.model_validate_json(path.read_bytes())


def release_set_manifest(plan: SourceIngestionPlan, payload_checksum: str) -> str:
    """Hash the immutable identity that a local computation will later pin."""
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "source_key": plan.source.key,
                "source_version": plan.release.source_version,
                "payload_checksum": payload_checksum,
                "schema_version": plan.release.schema_version,
            }
        )
    ).hexdigest()


def source_ingestion_plan_checksum(plan: SourceIngestionPlan) -> str:
    """Fingerprint the complete reviewed sidecar that a checkpoint may resume."""
    return hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()


async def publish_source_release(
    session: AsyncSession,
    plan: SourceIngestionPlan,
    payload: bytes,
    quality_summary: dict[str, int],
) -> SourceIngestionResult:
    """Idempotently persist governed provenance, the raw release, and a validated release set."""
    payload_checksum = hashlib.sha256(payload).hexdigest()
    await _acquire_source_ingestion_locks(session, plan, payload_checksum)
    source = (
        await session.execute(select(DataSource).where(DataSource.key == plan.source.key))
    ).scalar_one_or_none()
    idempotent = source is not None
    if source is None:
        source = DataSource(
            key=plan.source.key,
            name=plan.source.name,
            owner=plan.source.owner,
            purpose=plan.source.purpose,
            base_url=plan.source.base_url,
            license_name=plan.source.license_name,
            license_url=plan.source.license_url,
            citation=plan.source.citation,
            retention_days=plan.source.retention_days,
            allowed_client_exposure=False,
            review_state=SourceReviewState.APPROVED,
            reviewed_at=plan.source.reviewed_at,
            reviewed_by=plan.source.reviewed_by,
            configuration={"ingestion_boundary": "local_capture_then_warehouse_publication"},
        )
        session.add(source)
        await session.flush()
    elif source.review_state != SourceReviewState.APPROVED or not source.is_active:
        raise ValueError("source is not approved and active for publication")
    elif any(
        getattr(source, field) != getattr(plan.source, field)
        for field in (
            "name",
            "owner",
            "purpose",
            "base_url",
            "license_name",
            "license_url",
            "citation",
            "retention_days",
            "reviewed_at",
            "reviewed_by",
        )
    ):
        raise ValueError("source key is already governed by different source metadata")

    release = (
        await session.execute(
            select(SourceRelease)
            .where(
                SourceRelease.data_source_id == source.id,
                SourceRelease.source_version == plan.release.source_version,
                SourceRelease.payload_checksum == payload_checksum,
            )
        )
    ).scalar_one_or_none()
    if release is None:
        release = SourceRelease(
            data_source_id=source.id,
            source_version=plan.release.source_version,
            retrieved_at=datetime.now(UTC),
            data_available_at=plan.release.data_available_at,
            observed_from=plan.release.observed_from,
            observed_to=plan.release.observed_to,
            payload_checksum=payload_checksum,
            payload_bytes=len(payload),
            schema_version=plan.release.schema_version,
            license_snapshot=plan.source.license_name,
            query_parameters=plan.release.query_parameters,
            quality_summary=quality_summary,
            validation_state=ReleaseValidationState.VALID,
            validated_at=datetime.now(UTC),
        )
        session.add(release)
        await session.flush()
        idempotent = False
    elif any(
        getattr(release, field) != value
        for field, value in {
            "data_available_at": plan.release.data_available_at,
            "observed_from": plan.release.observed_from,
            "observed_to": plan.release.observed_to,
            "payload_bytes": len(payload),
            "schema_version": plan.release.schema_version,
            "license_snapshot": plan.source.license_name,
            "query_parameters": plan.release.query_parameters,
            "quality_summary": quality_summary,
            "validation_state": ReleaseValidationState.VALID,
        }.items()
    ):
        raise ValueError("source release identity is already governed by different release metadata")

    artifact_uri = f"warehouse://source-releases/{plan.source.key}/{plan.release.source_version}/{payload_checksum}"
    artifact = (
        await session.execute(
            select(Artifact).where(Artifact.uri == artifact_uri, Artifact.checksum_sha256 == payload_checksum)
        )
    ).scalar_one_or_none()
    if artifact is None:
        artifact = Artifact(
            source_release_id=release.id,
            kind="source_geojson",
            uri=artifact_uri,
            media_type="application/geo+json",
            checksum_sha256=payload_checksum,
            size_bytes=len(payload),
            storage_class="database_inline",
            metadata_json=quality_summary,
            content_bytes=payload,
        )
        session.add(artifact)
        await session.flush()
        idempotent = False
    elif (
        artifact.source_release_id != release.id
        or artifact.kind != "source_geojson"
        or artifact.media_type != "application/geo+json"
        or artifact.size_bytes != len(payload)
        or artifact.storage_class != "database_inline"
        or artifact.metadata_json != quality_summary
        or artifact.content_bytes != payload
    ):
        raise ValueError("artifact identity is already governed by different immutable content")

    manifest_checksum = release_set_manifest(plan, payload_checksum)
    release_sets = (
        await session.execute(
            select(ReleaseSet)
            .where(
                or_(
                    ReleaseSet.logical_key == plan.release_set_key,
                    ReleaseSet.manifest_checksum == manifest_checksum,
                )
            )
        )
    ).scalars().all()
    if len(release_sets) > 1:
        raise ValueError("release set key and manifest checksum identify different existing release sets")
    release_set = release_sets[0] if release_sets else None
    if release_set is None:
        release_set = ReleaseSet(
            logical_key=plan.release_set_key,
            as_of_time=plan.release_set_as_of,
            manifest_checksum=manifest_checksum,
            state=ReleaseSetState.DRAFT,
            description=plan.description,
        )
        session.add(release_set)
        await session.flush()
        session.add(ReleaseSetItem(release_set_id=release_set.id, source_release_id=release.id))
        await session.flush()
        release_set.state = ReleaseSetState.VALIDATED
        release_set.validated_at = datetime.now(UTC)
        idempotent = False
    elif release_set.logical_key != plan.release_set_key:
        raise ValueError("release set content is already governed by a different logical key")
    elif not is_finalized_release_set_state(release_set.state):
        raise ValueError("release_set_key is not finalized and cannot satisfy an idempotent source ingestion")
    elif release_set.manifest_checksum != manifest_checksum:
        raise ValueError("release_set_key already identifies different immutable source content")
    elif release_set.as_of_time != plan.release_set_as_of or release_set.description != plan.description:
        raise ValueError("release_set_key is already governed by different release metadata")
    else:
        membership = await session.get(
            ReleaseSetItem,
            {"release_set_id": release_set.id, "source_release_id": release.id},
        )
        if membership is None:
            raise ValueError("release_set_key is missing its required source release membership")

    return SourceIngestionResult(
        source_id=source.id,
        source_release_id=release.id,
        artifact_id=artifact.id,
        release_set_id=release_set.id,
        payload_checksum=payload_checksum,
        payload_bytes=len(payload),
        idempotent=idempotent,
    )


def is_finalized_release_set_state(state: ReleaseSetState) -> bool:
    """Allow source-ingest retries only against immutable, finalized release sets."""
    return state in {ReleaseSetState.VALIDATED, ReleaseSetState.PUBLISHED}


async def _acquire_source_ingestion_locks(
    session: AsyncSession,
    plan: SourceIngestionPlan,
    payload_checksum: str,
) -> None:
    """Serialize one release and release-set key without granting broad table updates."""
    lock_keys = (
        f"source-release:{plan.source.key}:{plan.release.source_version}:{payload_checksum}",
        f"release-set:{plan.release_set_key}",
    )
    for lock_key in lock_keys:
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
