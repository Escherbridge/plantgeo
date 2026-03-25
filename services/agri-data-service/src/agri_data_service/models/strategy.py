"""Regenerative agriculture strategy model."""

import enum

from sqlalchemy import Enum, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin


class WaterRequirement(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LaborIntensity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImpactLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class Strategy(Base, UUIDMixin, TimestampMixin):
    """A regenerative agriculture strategy with suitability rules."""

    __tablename__ = "strategies"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
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
    water_requirement: Mapped[WaterRequirement] = mapped_column(Enum(WaterRequirement), nullable=False)
    labor_intensity: Mapped[LaborIntensity] = mapped_column(Enum(LaborIntensity), nullable=False)
    time_to_yield_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    carbon_seq_potential: Mapped[ImpactLevel] = mapped_column(Enum(ImpactLevel), nullable=False)
    biodiversity_impact: Mapped[ImpactLevel] = mapped_column(Enum(ImpactLevel), nullable=False)
