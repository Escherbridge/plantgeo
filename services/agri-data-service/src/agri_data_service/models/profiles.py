"""Environmental profile models: soil, climate, topography, water, land use."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin
from agri_data_service.models.location import Location


# --- Enums ---

class SoilSource(str, enum.Enum):
    SSURGO = "ssurgo"
    SOILGRIDS = "soilgrids"


class ClimateSource(str, enum.Enum):
    PRISM = "prism"
    NOAA = "noaa"
    NASA_POWER = "nasa_power"


class WaterSource(str, enum.Enum):
    USGS_NWIS = "usgs_nwis"
    NOAA_ATLAS14 = "noaa_atlas14"


class LandUseSource(str, enum.Enum):
    CDL = "cdl"
    NLCD = "nlcd"


# --- Models ---

class SoilProfile(Base, UUIDMixin):
    """Soil properties at a location from a specific data source."""

    __tablename__ = "soil_profiles"

    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False, index=True)
    source: Mapped[SoilSource] = mapped_column(Enum(SoilSource), nullable=False)
    soil_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    texture_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ph: Mapped[float | None] = mapped_column(Float, nullable=True)
    organic_matter_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cec: Mapped[float | None] = mapped_column(Float, nullable=True)
    bulk_density: Mapped[float | None] = mapped_column(Float, nullable=True)
    drainage_class: Mapped[str | None] = mapped_column(String(50), nullable=True)
    depth_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sand_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    silt_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    clay_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_water_capacity: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    location: Mapped[Location] = relationship(back_populates="soil_profiles")


class ClimateProfile(Base, UUIDMixin):
    """Climate data at a location from a specific data source."""

    __tablename__ = "climate_profiles"

    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False, index=True)
    source: Mapped[ClimateSource] = mapped_column(Enum(ClimateSource), nullable=False)
    annual_precip_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    growing_season_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    frost_free_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    koppen_zone: Mapped[str | None] = mapped_column(String(10), nullable=True)
    aridity_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    monthly_precip_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    monthly_temp_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    location: Mapped[Location] = relationship(back_populates="climate_profiles")


class TopographyProfile(Base, UUIDMixin):
    """Topographic data at a location."""

    __tablename__ = "topography_profiles"

    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False, index=True)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    aspect_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    curvature: Mapped[float | None] = mapped_column(Float, nullable=True)
    twi: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    location: Mapped[Location] = relationship(back_populates="topography_profiles")


class WaterProfile(Base, UUIDMixin):
    """Water/hydrology data at a location."""

    __tablename__ = "water_profiles"

    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False, index=True)
    source: Mapped[WaterSource] = mapped_column(Enum(WaterSource), nullable=False)
    nearest_stream_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    watershed_huc12: Mapped[str | None] = mapped_column(String(12), nullable=True)
    annual_runoff_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    flood_zone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    groundwater_depth_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    water_table_seasonal_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    location: Mapped[Location] = relationship(back_populates="water_profiles")


class LandUseSnapshot(Base, UUIDMixin):
    """Land use classification at a location for a given year."""

    __tablename__ = "land_use_snapshots"

    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id"), nullable=False, index=True)
    source: Mapped[LandUseSource] = mapped_column(Enum(LandUseSource), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(100), nullable=True)
    crop_history_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    location: Mapped[Location] = relationship(back_populates="land_use_snapshots")
