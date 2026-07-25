"""Regenerative agriculture strategy model."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin


class WaterRequirement(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LaborIntensity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImpactLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class StrategyReviewState(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    REJECTED = "rejected"


class Strategy(Base, UUIDMixin, TimestampMixin):
    """Governed practice definition with optional reviewed claim fields."""

    __tablename__ = "strategies"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    authority: Mapped[str] = mapped_column(String(100), nullable=False)
    practice_code: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    # Suitability ranges
    min_precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    suitable_soil_types: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    suitable_drainage: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    max_slope_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_organic_matter_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Characteristics
    water_requirement: Mapped[WaterRequirement | None] = mapped_column(
        Enum(
            WaterRequirement,
            name="water_requirement",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    labor_intensity: Mapped[LaborIntensity | None] = mapped_column(
        Enum(
            LaborIntensity,
            name="labor_intensity",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    time_to_yield_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbon_seq_potential: Mapped[ImpactLevel | None] = mapped_column(
        Enum(
            ImpactLevel,
            name="strategy_carbon_impact_level",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    biodiversity_impact: Mapped[ImpactLevel | None] = mapped_column(
        Enum(
            ImpactLevel,
            name="strategy_biodiversity_impact_level",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=True,
    )
    evidence_citation: Mapped[str | None] = mapped_column(Text)
    evidence_source_url: Mapped[str | None] = mapped_column(String(1000))
    jurisdiction: Mapped[str | None] = mapped_column(String(255))
    limitations: Mapped[str | None] = mapped_column(Text)
    review_state: Mapped[StrategyReviewState] = mapped_column(
        Enum(
            StrategyReviewState,
            name="strategy_review_state",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda members: [member.value for member in members],
        ),
        nullable=False,
        server_default="draft",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("authority", "practice_code", name="uq_strategy_authority_practice"),
        CheckConstraint(
            "review_state <> 'approved' OR "
            "(reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND evidence_citation IS NOT NULL AND evidence_source_url IS NOT NULL "
            "AND jurisdiction IS NOT NULL AND limitations IS NOT NULL)",
            name="approved_strategy_has_evidence",
        ),
    )
