"""ORM mappings for immutable, release-pinned historical environmental data."""

import uuid
from datetime import date, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, UUIDMixin


class SpatialCell(Base, UUIDMixin):
    """Stable analysis cell with a native WGS84 polygon and centroid."""

    __tablename__ = "spatial_cell"

    cell_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    grid_name: Mapped[str] = mapped_column(String(100), nullable=False)
    resolution_m: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry = mapped_column(Geometry("POLYGON", srid=4326, spatial_index=False), nullable=False)
    centroid = mapped_column(Geometry("POINT", srid=4326, spatial_index=False), nullable=False)
    parent_cell_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.spatial_cell.id"))
    coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("resolution_m > 0", name="positive_resolution"),
        CheckConstraint("coverage_fraction > 0 AND coverage_fraction <= 1", name="valid_coverage"),
        Index("ix_spatial_cell_geometry", "geometry", postgresql_using="gist"),
        Index("ix_spatial_cell_centroid", "centroid", postgresql_using="gist"),
        Index("ix_spatial_cell_grid_resolution", "grid_name", "resolution_m"),
    )


class CellSourceCrosswalk(Base, UUIDMixin):
    """Immutable allocation of a native source feature onto an analysis cell."""

    __tablename__ = "cell_source_crosswalk"

    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    cell_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.spatial_cell.id"), nullable=False)
    native_feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    native_geometry = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    native_resolution_m: Mapped[int | None] = mapped_column(Integer)
    spatial_support_kind: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'unknown'"))
    mapping_method: Mapped[str] = mapped_column(String(100), nullable=False)
    coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "native_feature_key",
            "cell_id",
            name="uq_cell_source_crosswalk_release_feature_cell",
        ),
        CheckConstraint("native_resolution_m IS NULL OR native_resolution_m > 0", name="positive_native_resolution"),
        CheckConstraint(
            "spatial_support_kind IN "
            "('native_grid_cell', 'native_polygon', 'point_sample', 'area_aggregate', 'unknown')",
            name="known_spatial_support_kind",
        ),
        CheckConstraint("coverage_fraction > 0 AND coverage_fraction <= 1", name="valid_coverage"),
        Index("ix_cell_source_crosswalk_geometry", "native_geometry", postgresql_using="gist"),
        Index("ix_cell_source_crosswalk_release_cell", "source_release_id", "cell_id"),
    )


class SignalObservation(Base):
    """Append-only normalized signal fact tied to one immutable source release."""

    __tablename__ = "signal_observation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    cell_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.spatial_cell.id"), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_parameter: Mapped[str] = mapped_column(String(150), nullable=False)
    support_key: Mapped[str] = mapped_column(String(150), nullable=False, server_default=text("'surface'"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_value: Mapped[float | None] = mapped_column(Float)
    original_unit: Mapped[str | None] = mapped_column(String(64))
    normalized_value: Mapped[float | None] = mapped_column(Float)
    normalized_unit: Mapped[str | None] = mapped_column(String(64))
    quality_flag: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'accepted'"))
    coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1"))
    is_observed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "cell_id",
            "signal_name",
            "source_parameter",
            "support_key",
            "observed_at",
            name="uq_signal_observation_release_cell_signal_time",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from", name="ordered_valid_window"
        ),
        CheckConstraint("coverage_fraction >= 0 AND coverage_fraction <= 1", name="valid_coverage"),
        Index("ix_signal_observation_cell_time_signal", "cell_id", "observed_at", "signal_name"),
        Index("ix_signal_observation_release_time", "source_release_id", "observed_at"),
    )


class SignalCoverageAudit(Base, UUIDMixin):
    """Coverage evidence for one cell, source release, signal, and time window."""

    __tablename__ = "signal_coverage_audit"

    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    cell_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.spatial_cell.id"), nullable=False)
    signal_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_parameter: Mapped[str] = mapped_column(String(150), nullable=False)
    support_key: Mapped[str] = mapped_column(String(150), nullable=False, server_default=text("'surface'"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "cell_id",
            "signal_name",
            "source_parameter",
            "support_key",
            "window_start",
            "window_end",
            name="uq_signal_coverage_release_cell_signal_parameter_window",
        ),
        CheckConstraint("window_end >= window_start", name="ordered_window"),
        CheckConstraint(
            "expected_observation_count >= 0 AND received_observation_count >= 0", name="nonnegative_counts"
        ),
        CheckConstraint("received_observation_count <= expected_observation_count", name="received_within_expected"),
        CheckConstraint("status IN ('complete', 'partial', 'no_data', 'failed')", name="known_status"),
        CheckConstraint(
            "(status = 'complete' AND received_observation_count = expected_observation_count) OR "
            "(status = 'partial' AND received_observation_count > 0 "
            "AND received_observation_count < expected_observation_count) OR "
            "(status = 'no_data' AND received_observation_count = 0) OR "
            "(status = 'failed' AND received_observation_count < expected_observation_count)",
            name="status_matches_counts",
        ),
    )


class SourceCoverageAudit(Base, UUIDMixin):
    """Release-level coverage evidence for native vector or raster source captures."""

    __tablename__ = "source_coverage_audit"

    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(180), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_feature_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_feature_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "scope_key",
            "window_start",
            "window_end",
            name="uq_source_coverage_release_scope_window",
        ),
        CheckConstraint("window_end >= window_start", name="ordered_window"),
        CheckConstraint("expected_feature_count >= 0 AND received_feature_count >= 0", name="nonnegative_counts"),
        CheckConstraint("received_feature_count <= expected_feature_count", name="received_within_expected"),
        CheckConstraint("status IN ('complete', 'partial', 'no_data', 'failed')", name="known_status"),
        CheckConstraint(
            "(status = 'complete' AND received_feature_count = expected_feature_count) OR "
            "(status = 'partial' AND received_feature_count > 0 "
            "AND received_feature_count < expected_feature_count) OR "
            "(status = 'no_data' AND received_feature_count = 0) OR "
            "(status = 'failed' AND received_feature_count < expected_feature_count)",
            name="status_matches_counts",
        ),
        Index("ix_source_coverage_audit_release_window", "source_release_id", "window_start"),
    )


class DroughtPolygonSnapshot(Base):
    """Immutable USDM polygon feature from one reviewed weekly source release."""

    __tablename__ = "drought_polygon_snapshot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    issue_date: Mapped[date] = mapped_column(nullable=False)
    severity_class: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    impact_type: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'none'"))
    geometry = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=False)
    geometry_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id",
            "severity_class",
            "impact_type",
            "geometry_checksum",
            name="uq_drought_polygon_release_identity",
        ),
        CheckConstraint("severity_class >= 0 AND severity_class <= 4", name="valid_severity"),
        CheckConstraint("geometry_checksum ~ '^[0-9a-f]{64}$'", name="geometry_checksum_sha256"),
        Index("ix_drought_polygon_snapshot_geometry", "geometry", postgresql_using="gist"),
        Index("ix_drought_polygon_snapshot_issue_date", "issue_date"),
    )
