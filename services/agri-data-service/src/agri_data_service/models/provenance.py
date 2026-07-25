"""Versioned source, artifact, and release-set lineage."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin


def _enum_type(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class SourceReviewState(enum.StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class ReleaseValidationState(enum.StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    QUARANTINED = "quarantined"
    RETRACTED = "retracted"


class ReleaseSetState(enum.StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"
    RETIRED = "retired"


class DataSource(Base, UUIDMixin, TimestampMixin):
    """Governed upstream source definition."""

    __tablename__ = "data_source"

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1000))
    license_name: Mapped[str] = mapped_column(String(255), nullable=False)
    license_url: Mapped[str | None] = mapped_column(String(1000))
    citation: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    allowed_client_exposure: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    review_state: Mapped[SourceReviewState] = mapped_column(
        _enum_type(SourceReviewState, "source_review_state"), nullable=False, server_default="draft"
    )
    review_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint("retention_days IS NULL OR retention_days > 0", name="positive_retention_days"),
        CheckConstraint(
            "review_state <> 'approved' OR reviewed_at IS NOT NULL",
            name="approved_source_has_review",
        ),
    )


class SourceRelease(Base, UUIDMixin):
    """Immutable version retrieved from a governed source."""

    __tablename__ = "source_release"

    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.data_source.id"), nullable=False
    )
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_bytes: Mapped[int | None] = mapped_column(BigInteger)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    transform_version: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'source-native'"))
    license_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    query_parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    validation_state: Mapped[ReleaseValidationState] = mapped_column(
        _enum_type(ReleaseValidationState, "release_validation_state"),
        nullable=False,
        server_default="pending",
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id")
    )
    retraction_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "data_source_id",
            "source_version",
            "payload_checksum",
            "transform_version",
            name="uq_source_release_identity",
        ),
        CheckConstraint("payload_bytes IS NULL OR payload_bytes >= 0", name="nonnegative_payload_bytes"),
        CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name="ordered_observation_window",
        ),
        CheckConstraint(
            "validation_state <> 'valid' OR validated_at IS NOT NULL",
            name="valid_release_has_validation_time",
        ),
        Index("ix_source_release_source_available", "data_source_id", "data_available_at"),
    )


class Artifact(Base, UUIDMixin):
    """Content-addressed immutable source or model artifact."""

    __tablename__ = "artifact"

    source_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id")
    )
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_class: Mapped[str] = mapped_column(String(50), nullable=False, server_default="standard")
    encryption_key_ref: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    content_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("uri", "checksum_sha256", name="uq_artifact_uri_checksum"),
        CheckConstraint("size_bytes >= 0", name="nonnegative_artifact_size"),
        CheckConstraint(
            "content_bytes IS NULL OR octet_length(content_bytes) = size_bytes",
            name="inline_artifact_size_matches",
        ),
        CheckConstraint(
            "storage_class <> 'database_inline' OR content_bytes IS NOT NULL",
            name="inline_artifact_has_content",
        ),
        CheckConstraint(
            "content_bytes IS NULL OR encode(public.digest(content_bytes, 'sha256'), 'hex') = checksum_sha256",
            name="inline_artifact_checksum_matches",
        ),
    )


class ReleaseSet(Base, UUIDMixin):
    """Immutable set of source releases pinned for a computation."""

    __tablename__ = "release_set"

    logical_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    state: Mapped[ReleaseSetState] = mapped_column(
        _enum_type(ReleaseSetState, "release_set_state"), nullable=False, server_default="draft"
    )
    description: Mapped[str | None] = mapped_column(Text)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "manifest_checksum ~ '^[0-9a-f]{64}$'",
            name="release_set_manifest_checksum_sha256",
        ),
        CheckConstraint(
            "state NOT IN ('validated', 'published') OR validated_at IS NOT NULL",
            name="validated_set_has_timestamp",
        ),
        CheckConstraint(
            "state <> 'published' OR published_at IS NOT NULL",
            name="published_set_has_timestamp",
        ),
    )


class ReleaseSetItem(Base):
    """Membership of an immutable release set."""

    __tablename__ = "release_set_item"

    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id", ondelete="CASCADE"), primary_key=True
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), primary_key=True
    )
    source_role: Mapped[str] = mapped_column(String(100), nullable=False, server_default="input")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
