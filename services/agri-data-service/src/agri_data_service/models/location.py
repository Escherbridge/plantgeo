"""Location model with PostGIS geometry."""

import uuid
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import Index, String, Float
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from agri_data_service.models.profiles import (
        ClimateProfile,
        LandUseSnapshot,
        SoilProfile,
        TopographyProfile,
        WaterProfile,
    )


class Location(Base, UUIDMixin, TimestampMixin):
    """A geographic location with associated environmental profiles."""

    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    geometry = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    bounding_box = mapped_column(Geometry("POLYGON", srid=4326), nullable=True)
    usda_zone: Mapped[str | None] = mapped_column(String(10), nullable=True)
    epa_ecoregion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    soil_profiles: Mapped[list["SoilProfile"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    climate_profiles: Mapped[list["ClimateProfile"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    topography_profiles: Mapped[list["TopographyProfile"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    water_profiles: Mapped[list["WaterProfile"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    land_use_snapshots: Mapped[list["LandUseSnapshot"]] = relationship(back_populates="location", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_locations_geometry", "geometry", postgresql_using="gist"),
    )
