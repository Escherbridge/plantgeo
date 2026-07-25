"""Resolution-aware geospatial subjects, source features, and evidence inputs."""

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, UUIDMixin

SPATIAL_SUPPORT_VALUES = (
    "'point_sample', 'line_sample', 'native_grid_cell', 'model_grid_cell', 'native_polygon', "
    "'area_aggregate', 'structure_footprint', 'parcel_boundary', "
    "'administrative_boundary', 'unknown'"
)
INFERENCE_SCALE_VALUES = "'structure', 'parcel', 'neighborhood', 'city', 'landscape', 'regional'"


class NormalizedSourceFeature(Base, UUIDMixin):
    """Immutable provider feature normalized to WGS84 and exact source bytes."""

    __tablename__ = "normalized_source_feature"

    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.artifact.id"), nullable=False)
    feature_key: Mapped[str] = mapped_column(String(500), nullable=False)
    feature_kind: Mapped[str] = mapped_column(String(100), nullable=False)
    geometry = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    geometry_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean)
    value_unit: Mapped[str | None] = mapped_column(String(64))
    attributes_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    spatial_support_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    native_resolution_m: Mapped[float | None] = mapped_column(Float)
    native_scale: Mapped[str] = mapped_column(String(120), nullable=False)
    maximum_inference_scale: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_basis: Mapped[str] = mapped_column(String(255), nullable=False)
    method_key: Mapped[str] = mapped_column(String(120), nullable=False)
    method_version: Mapped[str] = mapped_column(String(100), nullable=False)
    feature_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    is_life_safety_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("id", "source_release_id", name="uq_normalized_source_feature_id_release"),
        UniqueConstraint(
            "id",
            "source_release_id",
            "artifact_id",
            name="uq_normalized_source_feature_id_release_artifact",
        ),
        UniqueConstraint(
            "source_release_id",
            "feature_key",
            "method_key",
            "method_version",
            name="uq_normalized_source_feature_release_identity",
        ),
        CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name="ordered_observation_window",
        ),
        CheckConstraint("native_resolution_m IS NULL OR native_resolution_m > 0", name="positive_native_resolution"),
        CheckConstraint(f"spatial_support_kind IN ({SPATIAL_SUPPORT_VALUES})", name="known_spatial_support_kind"),
        CheckConstraint(
            f"maximum_inference_scale IN ({INFERENCE_SCALE_VALUES})",
            name="known_maximum_inference_scale",
        ),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="valid_confidence"),
        CheckConstraint(
            "ST_SRID(geometry) = 4326 AND ST_NDims(geometry) = 2 AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name="valid_wgs84_geometry",
        ),
        CheckConstraint(
            "GeometryType(geometry) IN "
            "('POINT', 'MULTIPOINT', 'LINESTRING', 'MULTILINESTRING', 'POLYGON', 'MULTIPOLYGON')",
            name="known_geometry_type",
        ),
        CheckConstraint("geometry_checksum ~ '^[0-9a-f]{64}$'", name="geometry_checksum_sha256"),
        CheckConstraint("feature_checksum ~ '^[0-9a-f]{64}$'", name="feature_checksum_sha256"),
        CheckConstraint("is_life_safety_validated = false", name="not_life_safety_validated"),
        Index("ix_normalized_source_feature_geometry", "geometry", postgresql_using="gist"),
        Index(
            "ix_normalized_source_feature_release_kind",
            "source_release_id",
            "feature_kind",
        ),
    )


class AnalysisSubject(Base, UUIDMixin):
    """Immutable version of a stable city, parcel, or property analysis identity."""

    __tablename__ = "analysis_subject"

    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_version: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.analysis_subject.id")
    )
    supersedes_subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.analysis_subject.id")
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.artifact.id"), nullable=False)
    source_feature_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    geometry = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    geometry_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    spatial_support_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    native_resolution_m: Mapped[float | None] = mapped_column(Float)
    native_scale: Mapped[str] = mapped_column(String(120), nullable=False)
    maximum_inference_scale: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_basis: Mapped[str] = mapped_column(String(255), nullable=False)
    method_key: Mapped[str] = mapped_column(String(120), nullable=False)
    method_version: Mapped[str] = mapped_column(String(100), nullable=False)
    is_life_safety_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_feature_id", "source_release_id", "artifact_id"],
            [
                "agri.normalized_source_feature.id",
                "agri.normalized_source_feature.source_release_id",
                "agri.normalized_source_feature.artifact_id",
            ],
            name="fk_analysis_subject_feature_release_artifact",
        ),
        UniqueConstraint("subject_key", "subject_version", name="uq_analysis_subject_key_version"),
        CheckConstraint("subject_kind IN ('city', 'parcel', 'property')", name="known_subject_kind"),
        CheckConstraint("country_code IN ('US', 'CA', 'MX')", name="north_american_country"),
        CheckConstraint("parent_subject_id IS NULL OR parent_subject_id <> id", name="not_own_parent"),
        CheckConstraint("supersedes_subject_id IS NULL OR supersedes_subject_id <> id", name="not_own_predecessor"),
        CheckConstraint("native_resolution_m IS NULL OR native_resolution_m > 0", name="positive_native_resolution"),
        CheckConstraint(f"spatial_support_kind IN ({SPATIAL_SUPPORT_VALUES})", name="known_spatial_support_kind"),
        CheckConstraint(
            f"maximum_inference_scale IN ({INFERENCE_SCALE_VALUES})",
            name="known_maximum_inference_scale",
        ),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="valid_confidence"),
        CheckConstraint(
            "ST_SRID(geometry) = 4326 AND ST_NDims(geometry) = 2 AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name="valid_wgs84_geometry",
        ),
        CheckConstraint(
            "GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')",
            name="polygonal_geometry",
        ),
        CheckConstraint("geometry_checksum ~ '^[0-9a-f]{64}$'", name="geometry_checksum_sha256"),
        CheckConstraint("is_life_safety_validated = false", name="not_life_safety_validated"),
        Index("ix_analysis_subject_geometry", "geometry", postgresql_using="gist"),
        Index("ix_analysis_subject_kind_country", "subject_kind", "country_code"),
    )


class InterventionAnalysisRun(Base, UUIDMixin):
    """Immutable validated computation receipt for model-derived evidence."""

    __tablename__ = "intervention_analysis_run"

    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)
    method_key: Mapped[str] = mapped_column(String(120), nullable=False)
    method_version: Mapped[str] = mapped_column(String(100), nullable=False)
    analysis_plan_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    analysis_code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    output_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_state: Mapped[str] = mapped_column(String(32), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_life_safety_prediction: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("release_set_id", "run_key", name="uq_intervention_analysis_run_release_key"),
        CheckConstraint(
            "validation_state IN ('validated', 'rejected')",
            name="known_validation_state",
        ),
        CheckConstraint(
            "(validation_state = 'validated' AND validated_at IS NOT NULL) OR "
            "(validation_state = 'rejected' AND validated_at IS NULL)",
            name="validation_state_matches_timestamp",
        ),
        CheckConstraint(
            "validated_at IS NULL OR finalized_at >= validated_at",
            name="ordered_validation_time",
        ),
        CheckConstraint("row_count >= 0", name="nonnegative_row_count"),
        CheckConstraint(
            "analysis_plan_checksum ~ '^[0-9a-f]{64}$'",
            name="analysis_plan_checksum_sha256",
        ),
        CheckConstraint(
            "analysis_code_checksum ~ '^[0-9a-f]{64}$'",
            name="analysis_code_checksum_sha256",
        ),
        CheckConstraint("output_checksum ~ '^[0-9a-f]{64}$'", name="output_checksum_sha256"),
        CheckConstraint("is_life_safety_prediction = false", name="not_life_safety_prediction"),
        Index("ix_intervention_analysis_run_release_method", "release_set_id", "method_key"),
    )


class InterventionEvidenceInput(Base, UUIDMixin):
    """Typed fact, derived feature, or known gap for one pinned analysis subject."""

    __tablename__ = "intervention_evidence_input"

    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    analysis_subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.analysis_subject.id"), nullable=False
    )
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(150), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean)
    value_unit: Mapped[str | None] = mapped_column(String(64))
    gap_detail: Mapped[str | None] = mapped_column(Text)
    evidence_geometry = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    observed_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spatial_support_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    native_resolution_m: Mapped[float | None] = mapped_column(Float)
    native_scale: Mapped[str] = mapped_column(String(120), nullable=False)
    maximum_inference_scale: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    confidence_basis: Mapped[str] = mapped_column(String(255), nullable=False)
    method_key: Mapped[str] = mapped_column(String(120), nullable=False)
    method_version: Mapped[str] = mapped_column(String(100), nullable=False)
    intervention_analysis_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.intervention_analysis_run.id")
    )
    evidence_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    is_life_safety_validated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "release_set_id",
            "analysis_subject_id",
            "evidence_checksum",
            name="uq_intervention_evidence_release_subject_checksum",
        ),
        CheckConstraint(
            "evidence_kind IN ('observed_fact', 'model_derived_feature', 'known_gap')",
            name="known_evidence_kind",
        ),
        CheckConstraint(
            "((evidence_kind IN ('observed_fact', 'model_derived_feature')) "
            "AND num_nonnulls(numeric_value, text_value, boolean_value) = 1 AND gap_detail IS NULL) OR "
            "(evidence_kind = 'known_gap' AND num_nonnulls(numeric_value, text_value, boolean_value) = 0 "
            "AND gap_detail IS NOT NULL)",
            name="kind_matches_typed_value",
        ),
        CheckConstraint(
            "(evidence_kind = 'model_derived_feature' AND intervention_analysis_run_id IS NOT NULL) OR "
            "(evidence_kind <> 'model_derived_feature' AND intervention_analysis_run_id IS NULL)",
            name="derived_feature_has_reproducible_run",
        ),
        CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name="ordered_observation_window",
        ),
        CheckConstraint("native_resolution_m IS NULL OR native_resolution_m > 0", name="positive_native_resolution"),
        CheckConstraint(f"spatial_support_kind IN ({SPATIAL_SUPPORT_VALUES})", name="known_spatial_support_kind"),
        CheckConstraint(
            f"maximum_inference_scale IN ({INFERENCE_SCALE_VALUES})",
            name="known_maximum_inference_scale",
        ),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="valid_confidence"),
        CheckConstraint(
            "ST_SRID(evidence_geometry) = 4326 AND ST_NDims(evidence_geometry) = 2 "
            "AND NOT ST_IsEmpty(evidence_geometry) AND ST_IsValid(evidence_geometry)",
            name="valid_wgs84_geometry",
        ),
        CheckConstraint(
            "GeometryType(evidence_geometry) IN "
            "('POINT', 'MULTIPOINT', 'LINESTRING', 'MULTILINESTRING', 'POLYGON', 'MULTIPOLYGON')",
            name="known_geometry_type",
        ),
        CheckConstraint("evidence_checksum ~ '^[0-9a-f]{64}$'", name="evidence_checksum_sha256"),
        CheckConstraint("is_life_safety_validated = false", name="not_life_safety_validated"),
        Index("ix_intervention_evidence_input_geometry", "evidence_geometry", postgresql_using="gist"),
        Index(
            "ix_intervention_evidence_input_subject_kind",
            "analysis_subject_id",
            "evidence_kind",
        ),
    )


class InterventionEvidenceLineage(Base, UUIDMixin):
    """Relational source-release and feature lineage for one evidence input."""

    __tablename__ = "intervention_evidence_lineage"

    evidence_input_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.intervention_evidence_input.id"), nullable=False
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    source_feature_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_record_table: Mapped[str | None] = mapped_column(String(64))
    source_record_key: Mapped[str | None] = mapped_column(String(255))
    source_record_checksum: Mapped[str | None] = mapped_column(String(64))
    lineage_role: Mapped[str] = mapped_column(String(32), nullable=False)
    input_order: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["source_feature_id", "source_release_id"],
            ["agri.normalized_source_feature.id", "agri.normalized_source_feature.source_release_id"],
            name="fk_intervention_evidence_lineage_feature_release",
        ),
        UniqueConstraint(
            "evidence_input_id",
            "input_order",
            name="uq_intervention_evidence_lineage_input_order",
        ),
        CheckConstraint(
            "lineage_role IN ('direct_observation', 'derivation_input', 'coverage_basis', 'gap_basis')",
            name="known_lineage_role",
        ),
        CheckConstraint(
            "(source_feature_id IS NOT NULL AND source_record_table IS NULL "
            "AND source_record_key IS NULL AND source_record_checksum IS NULL) OR "
            "(source_feature_id IS NULL AND source_record_table IS NOT NULL "
            "AND source_record_key IS NOT NULL AND source_record_checksum IS NOT NULL)",
            name="feature_or_record_locator",
        ),
        CheckConstraint(
            "source_record_table IS NULL OR source_record_table IN "
            "('signal_observation', 'drought_polygon_snapshot', "
            "'signal_coverage_audit', 'source_coverage_audit')",
            name="known_source_record_table",
        ),
        CheckConstraint(
            "source_record_checksum IS NULL OR source_record_checksum ~ '^[0-9a-f]{64}$'",
            name="source_record_checksum_sha256",
        ),
        CheckConstraint("input_order >= 0", name="nonnegative_input_order"),
        Index(
            "ix_intervention_evidence_lineage_source_release",
            "source_release_id",
            "evidence_input_id",
        ),
    )
