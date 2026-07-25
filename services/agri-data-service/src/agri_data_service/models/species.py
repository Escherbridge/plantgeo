"""Species and companion relationship models."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, INT4RANGE, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin


class PollinatorValue(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RelationshipType(StrEnum):
    COMPANION = "companion"
    ANTAGONIST = "antagonist"
    NEUTRAL = "neutral"


class EvidenceReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class Species(Base, UUIDMixin, TimestampMixin):
    """A plant species with ecological attributes."""

    __tablename__ = "species"

    scientific_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    common_name: Mapped[str] = mapped_column(String(255), nullable=False)
    usda_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    family: Mapped[str | None] = mapped_column(String(100), nullable=True)
    growth_habit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    native_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    usda_zones = mapped_column(INT4RANGE, nullable=True)
    min_precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    light_requirement: Mapped[str | None] = mapped_column(String(50), nullable=True)
    drought_tolerance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    salt_tolerance: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nitrogen_fixer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pollinator_value: Mapped[PollinatorValue | None] = mapped_column(
        Enum(
            PollinatorValue,
            name="pollinator_value",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    edible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timber_value: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guild_roles: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    # Relationships
    companion_as_a: Mapped[list["CompanionRelationship"]] = relationship(
        foreign_keys="CompanionRelationship.species_a_id",
        back_populates="species_a",
        cascade="all, delete-orphan",
    )
    companion_as_b: Mapped[list["CompanionRelationship"]] = relationship(
        foreign_keys="CompanionRelationship.species_b_id",
        back_populates="species_b",
        cascade="all, delete-orphan",
    )


class CompanionRelationship(Base, UUIDMixin):
    """A relationship between two species (companion, antagonist, neutral)."""

    __tablename__ = "companion_relationships"

    species_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.species.id"), nullable=False, index=True
    )
    species_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.species.id"), nullable=False, index=True
    )
    relationship_type: Mapped[RelationshipType] = mapped_column(
        Enum(
            RelationshipType,
            name="companion_relationship_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
    )
    guild_function: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_citation: Mapped[str | None] = mapped_column(Text)
    evidence_source_url: Mapped[str | None] = mapped_column(String(1000))
    evidence_grade: Mapped[str | None] = mapped_column(String(50))
    applicability_context: Mapped[str | None] = mapped_column(Text)
    jurisdiction: Mapped[str | None] = mapped_column(String(255))
    review_state: Mapped[EvidenceReviewState] = mapped_column(
        Enum(
            EvidenceReviewState,
            name="companion_review_state",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        server_default="draft",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))

    species_a: Mapped[Species] = relationship(foreign_keys=[species_a_id], back_populates="companion_as_a")
    species_b: Mapped[Species] = relationship(foreign_keys=[species_b_id], back_populates="companion_as_b")

    __table_args__ = (
        UniqueConstraint("species_a_id", "species_b_id", name="uq_companion_pair"),
        CheckConstraint("species_a_id <> species_b_id", name="companion_pair_not_self"),
        CheckConstraint(
            "review_state <> 'approved' OR (reviewed_at IS NOT NULL AND evidence_citation IS NOT NULL)",
            name="approved_companion_has_evidence",
        ),
    )
