"""Durable receipt state for resumable typed historical promotion."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, UUIDMixin


class HistoricalPromotionState(enum.StrEnum):
    """Receiver state for one immutable historical-promotion manifest."""

    RECEIVING = "receiving"
    FINALIZED = "finalized"


class HistoricalPromotionBundle(Base, UUIDMixin):
    """One staged historical manifest and its target draft release set."""

    __tablename__ = "historical_promotion_bundle"

    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False, unique=True
    )
    state: Mapped[HistoricalPromotionState] = mapped_column(String(32), nullable=False, server_default="receiving")
    expected_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_by: Mapped[str] = mapped_column(String(255), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("manifest_checksum ~ '^[0-9a-f]{64}$'", name="historical_promotion_manifest_checksum_sha256"),
        CheckConstraint("state IN ('receiving', 'finalized')", name="historical_promotion_known_state"),
        CheckConstraint("expected_chunk_count > 0", name="historical_promotion_positive_chunk_count"),
        CheckConstraint("expected_record_count > 0", name="historical_promotion_positive_record_count"),
    )


class HistoricalPromotionChunkReceipt(Base):
    """Idempotent checksum receipt for one imported typed-record chunk."""

    __tablename__ = "historical_promotion_chunk_receipt"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.historical_promotion_bundle.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("sequence > 0", name="historical_chunk_positive_sequence"),
        CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="historical_chunk_checksum_sha256"),
        CheckConstraint("payload_bytes > 0", name="historical_chunk_positive_bytes"),
        CheckConstraint("record_count > 0", name="historical_chunk_positive_record_count"),
    )


class HistoricalPromotionDataSourceReceipt(Base):
    """Proof that one governed data-source record arrived in a promotion bundle."""

    __tablename__ = "historical_promotion_data_source_receipt"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.historical_promotion_bundle.id", ondelete="CASCADE"), primary_key=True
    )
    source_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HistoricalPromotionSourceReleaseReceipt(Base):
    """Proof that one exact root source-release record arrived in a promotion bundle."""

    __tablename__ = "historical_promotion_source_release_receipt"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.historical_promotion_bundle.id", ondelete="CASCADE"), primary_key=True
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), primary_key=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HistoricalPromotionArtifactReceipt(Base, UUIDMixin):
    """Declared raw-source payload awaiting or proving a separately streamed upload."""

    __tablename__ = "historical_promotion_artifact_receipt"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.historical_promotion_bundle.id", ondelete="CASCADE"), nullable=False
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    uri: Mapped[str] = mapped_column(String(2000), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(255))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.artifact.id"))
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("bundle_id", "source_release_id", name="uq_historical_artifact_receipt_bundle_release"),
        CheckConstraint("checksum_sha256 ~ '^[0-9a-f]{64}$'", name="historical_artifact_checksum_sha256"),
        CheckConstraint("size_bytes > 0", name="historical_artifact_positive_bytes"),
        Index("ix_historical_artifact_receipt_bundle_pending", "bundle_id", "uploaded_at"),
    )
