"""Add immutable resolution-aware geospatial intervention evidence.

Revision ID: 20260723_0009
Revises: 20260722_0008
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260723_0009"
down_revision = "20260722_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agri"
SPATIAL_SUPPORT_CHECK = (
    "spatial_support_kind IN ("
    "'point_sample', 'line_sample', 'native_grid_cell', 'model_grid_cell', 'native_polygon', "
    "'area_aggregate', 'structure_footprint', 'parcel_boundary', "
    "'administrative_boundary', 'unknown')"
)
INFERENCE_SCALE_CHECK = (
    "maximum_inference_scale IN ('structure', 'parcel', 'neighborhood', 'city', 'landscape', 'regional')"
)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
        primary_key=True,
    )


def _created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_artifact_inline_artifact_checksum_matches"),
        "artifact",
        "content_bytes IS NULL OR encode(public.digest(content_bytes, 'sha256'), 'hex') = checksum_sha256",
        schema=SCHEMA,
    )
    op.create_table(
        "normalized_source_feature",
        _uuid_pk(),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("feature_key", sa.String(length=500), nullable=False),
        sa.Column("feature_kind", sa.String(length=100), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("geometry_checksum", sa.String(length=64), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True)),
        sa.Column("observed_to", sa.DateTime(timezone=True)),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("numeric_value", sa.Float()),
        sa.Column("text_value", sa.Text()),
        sa.Column("boolean_value", sa.Boolean()),
        sa.Column("value_unit", sa.String(length=64)),
        sa.Column("attributes_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("spatial_support_kind", sa.String(length=32), nullable=False),
        sa.Column("native_resolution_m", sa.Float()),
        sa.Column("native_scale", sa.String(length=120), nullable=False),
        sa.Column("maximum_inference_scale", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("confidence_basis", sa.String(length=255), nullable=False),
        sa.Column("method_key", sa.String(length=120), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("feature_checksum", sa.String(length=64), nullable=False),
        sa.Column("is_life_safety_validated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["agri.artifact.id"]),
        sa.UniqueConstraint(
            "id",
            "source_release_id",
            name="uq_normalized_source_feature_id_release",
        ),
        sa.UniqueConstraint(
            "id",
            "source_release_id",
            "artifact_id",
            name="uq_normalized_source_feature_id_release_artifact",
        ),
        sa.UniqueConstraint(
            "source_release_id",
            "feature_key",
            "method_key",
            "method_version",
            name="uq_normalized_source_feature_release_identity",
        ),
        sa.CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name=op.f("ck_normalized_source_feature_ordered_observation_window"),
        ),
        sa.CheckConstraint(
            "native_resolution_m IS NULL OR native_resolution_m > 0",
            name=op.f("ck_normalized_source_feature_positive_native_resolution"),
        ),
        sa.CheckConstraint(
            SPATIAL_SUPPORT_CHECK,
            name=op.f("ck_normalized_source_feature_known_spatial_support_kind"),
        ),
        sa.CheckConstraint(
            INFERENCE_SCALE_CHECK,
            name=op.f("ck_normalized_source_feature_known_maximum_inference_scale"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_normalized_source_feature_valid_confidence"),
        ),
        sa.CheckConstraint(
            "ST_SRID(geometry) = 4326 AND ST_NDims(geometry) = 2 AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name=op.f("ck_normalized_source_feature_valid_wgs84_geometry"),
        ),
        sa.CheckConstraint(
            "GeometryType(geometry) IN "
            "('POINT', 'MULTIPOINT', 'LINESTRING', 'MULTILINESTRING', 'POLYGON', 'MULTIPOLYGON')",
            name=op.f("ck_normalized_source_feature_known_geometry_type"),
        ),
        sa.CheckConstraint(
            "geometry_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_normalized_source_feature_geometry_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "feature_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_normalized_source_feature_feature_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "is_life_safety_validated = false",
            name=op.f("ck_normalized_source_feature_not_life_safety_validated"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_normalized_source_feature_geometry",
        "normalized_source_feature",
        ["geometry"],
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_normalized_source_feature_release_kind",
        "normalized_source_feature",
        ["source_release_id", "feature_kind"],
        schema=SCHEMA,
    )

    op.create_table(
        "analysis_subject",
        _uuid_pk(),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("subject_version", sa.String(length=100), nullable=False),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("parent_subject_id", postgresql.UUID(as_uuid=True)),
        sa.Column("supersedes_subject_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_feature_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("geometry_checksum", sa.String(length=64), nullable=False),
        sa.Column("spatial_support_kind", sa.String(length=32), nullable=False),
        sa.Column("native_resolution_m", sa.Float()),
        sa.Column("native_scale", sa.String(length=120), nullable=False),
        sa.Column("maximum_inference_scale", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("confidence_basis", sa.String(length=255), nullable=False),
        sa.Column("method_key", sa.String(length=120), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("is_life_safety_validated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["parent_subject_id"], ["agri.analysis_subject.id"]),
        sa.ForeignKeyConstraint(["supersedes_subject_id"], ["agri.analysis_subject.id"]),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["agri.artifact.id"]),
        sa.ForeignKeyConstraint(
            ["source_feature_id", "source_release_id", "artifact_id"],
            [
                "agri.normalized_source_feature.id",
                "agri.normalized_source_feature.source_release_id",
                "agri.normalized_source_feature.artifact_id",
            ],
            name="fk_analysis_subject_feature_release_artifact",
        ),
        sa.UniqueConstraint(
            "subject_key",
            "subject_version",
            name="uq_analysis_subject_key_version",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('city', 'parcel', 'property')",
            name=op.f("ck_analysis_subject_known_subject_kind"),
        ),
        sa.CheckConstraint(
            "country_code IN ('US', 'CA', 'MX')",
            name=op.f("ck_analysis_subject_north_american_country"),
        ),
        sa.CheckConstraint(
            "parent_subject_id IS NULL OR parent_subject_id <> id",
            name=op.f("ck_analysis_subject_not_own_parent"),
        ),
        sa.CheckConstraint(
            "supersedes_subject_id IS NULL OR supersedes_subject_id <> id",
            name=op.f("ck_analysis_subject_not_own_predecessor"),
        ),
        sa.CheckConstraint(
            "native_resolution_m IS NULL OR native_resolution_m > 0",
            name=op.f("ck_analysis_subject_positive_native_resolution"),
        ),
        sa.CheckConstraint(
            SPATIAL_SUPPORT_CHECK,
            name=op.f("ck_analysis_subject_known_spatial_support_kind"),
        ),
        sa.CheckConstraint(
            INFERENCE_SCALE_CHECK,
            name=op.f("ck_analysis_subject_known_maximum_inference_scale"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_analysis_subject_valid_confidence"),
        ),
        sa.CheckConstraint(
            "ST_SRID(geometry) = 4326 AND ST_NDims(geometry) = 2 AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry)",
            name=op.f("ck_analysis_subject_valid_wgs84_geometry"),
        ),
        sa.CheckConstraint(
            "GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')",
            name=op.f("ck_analysis_subject_polygonal_geometry"),
        ),
        sa.CheckConstraint(
            "geometry_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_analysis_subject_geometry_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "is_life_safety_validated = false",
            name=op.f("ck_analysis_subject_not_life_safety_validated"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_analysis_subject_geometry",
        "analysis_subject",
        ["geometry"],
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_analysis_subject_kind_country",
        "analysis_subject",
        ["subject_kind", "country_code"],
        schema=SCHEMA,
    )

    op.create_table(
        "intervention_analysis_run",
        _uuid_pk(),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_key", sa.String(length=255), nullable=False),
        sa.Column("method_key", sa.String(length=120), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("analysis_plan_checksum", sa.String(length=64), nullable=False),
        sa.Column("analysis_code_checksum", sa.String(length=64), nullable=False),
        sa.Column("output_checksum", sa.String(length=64), nullable=False),
        sa.Column("validation_state", sa.String(length=32), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_count", sa.BigInteger(), nullable=False),
        sa.Column("is_life_safety_prediction", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.UniqueConstraint(
            "release_set_id",
            "run_key",
            name="uq_intervention_analysis_run_release_key",
        ),
        sa.CheckConstraint(
            "validation_state IN ('validated', 'rejected')",
            name=op.f("ck_intervention_analysis_run_known_validation_state"),
        ),
        sa.CheckConstraint(
            "(validation_state = 'validated' AND validated_at IS NOT NULL) OR "
            "(validation_state = 'rejected' AND validated_at IS NULL)",
            name=op.f("ck_intervention_analysis_run_validation_state_matches_timestamp"),
        ),
        sa.CheckConstraint(
            "validated_at IS NULL OR finalized_at >= validated_at",
            name=op.f("ck_intervention_analysis_run_ordered_validation_time"),
        ),
        sa.CheckConstraint(
            "row_count >= 0",
            name=op.f("ck_intervention_analysis_run_nonnegative_row_count"),
        ),
        sa.CheckConstraint(
            "analysis_plan_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intervention_analysis_run_analysis_plan_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "analysis_code_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intervention_analysis_run_analysis_code_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "output_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intervention_analysis_run_output_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "is_life_safety_prediction = false",
            name=op.f("ck_intervention_analysis_run_not_life_safety_prediction"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_intervention_analysis_run_release_method",
        "intervention_analysis_run",
        ["release_set_id", "method_key"],
        schema=SCHEMA,
    )

    op.create_table(
        "intervention_evidence_input",
        _uuid_pk(),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("metric_name", sa.String(length=150), nullable=False),
        sa.Column("numeric_value", sa.Float()),
        sa.Column("text_value", sa.Text()),
        sa.Column("boolean_value", sa.Boolean()),
        sa.Column("value_unit", sa.String(length=64)),
        sa.Column("gap_detail", sa.Text()),
        sa.Column(
            "evidence_geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("observed_from", sa.DateTime(timezone=True)),
        sa.Column("observed_to", sa.DateTime(timezone=True)),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("spatial_support_kind", sa.String(length=32), nullable=False),
        sa.Column("native_resolution_m", sa.Float()),
        sa.Column("native_scale", sa.String(length=120), nullable=False),
        sa.Column("maximum_inference_scale", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("confidence_basis", sa.String(length=255), nullable=False),
        sa.Column("method_key", sa.String(length=120), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("intervention_analysis_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("evidence_checksum", sa.String(length=64), nullable=False),
        sa.Column("is_life_safety_validated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.ForeignKeyConstraint(["analysis_subject_id"], ["agri.analysis_subject.id"]),
        sa.ForeignKeyConstraint(["intervention_analysis_run_id"], ["agri.intervention_analysis_run.id"]),
        sa.UniqueConstraint(
            "release_set_id",
            "analysis_subject_id",
            "evidence_checksum",
            name="uq_intervention_evidence_release_subject_checksum",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('observed_fact', 'model_derived_feature', 'known_gap')",
            name=op.f("ck_intervention_evidence_input_known_evidence_kind"),
        ),
        sa.CheckConstraint(
            "((evidence_kind IN ('observed_fact', 'model_derived_feature')) "
            "AND num_nonnulls(numeric_value, text_value, boolean_value) = 1 AND gap_detail IS NULL) OR "
            "(evidence_kind = 'known_gap' AND num_nonnulls(numeric_value, text_value, boolean_value) = 0 "
            "AND gap_detail IS NOT NULL)",
            name=op.f("ck_intervention_evidence_input_kind_matches_typed_value"),
        ),
        sa.CheckConstraint(
            "(evidence_kind = 'model_derived_feature' AND intervention_analysis_run_id IS NOT NULL) OR "
            "(evidence_kind <> 'model_derived_feature' AND intervention_analysis_run_id IS NULL)",
            name=op.f("ck_intervention_evidence_input_derived_feature_has_reproducible_run"),
        ),
        sa.CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name=op.f("ck_intervention_evidence_input_ordered_observation_window"),
        ),
        sa.CheckConstraint(
            "native_resolution_m IS NULL OR native_resolution_m > 0",
            name=op.f("ck_intervention_evidence_input_positive_native_resolution"),
        ),
        sa.CheckConstraint(
            SPATIAL_SUPPORT_CHECK,
            name=op.f("ck_intervention_evidence_input_known_spatial_support_kind"),
        ),
        sa.CheckConstraint(
            INFERENCE_SCALE_CHECK,
            name=op.f("ck_intervention_evidence_input_known_maximum_inference_scale"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_intervention_evidence_input_valid_confidence"),
        ),
        sa.CheckConstraint(
            "ST_SRID(evidence_geometry) = 4326 AND ST_NDims(evidence_geometry) = 2 "
            "AND NOT ST_IsEmpty(evidence_geometry) AND ST_IsValid(evidence_geometry)",
            name=op.f("ck_intervention_evidence_input_valid_wgs84_geometry"),
        ),
        sa.CheckConstraint(
            "GeometryType(evidence_geometry) IN "
            "('POINT', 'MULTIPOINT', 'LINESTRING', 'MULTILINESTRING', 'POLYGON', 'MULTIPOLYGON')",
            name=op.f("ck_intervention_evidence_input_known_geometry_type"),
        ),
        sa.CheckConstraint(
            "evidence_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intervention_evidence_input_evidence_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "is_life_safety_validated = false",
            name=op.f("ck_intervention_evidence_input_not_life_safety_validated"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_intervention_evidence_input_geometry",
        "intervention_evidence_input",
        ["evidence_geometry"],
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_intervention_evidence_input_subject_kind",
        "intervention_evidence_input",
        ["analysis_subject_id", "evidence_kind"],
        schema=SCHEMA,
    )

    op.create_table(
        "intervention_evidence_lineage",
        _uuid_pk(),
        sa.Column("evidence_input_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_feature_id", postgresql.UUID(as_uuid=True)),
        sa.Column("source_record_table", sa.String(length=64)),
        sa.Column("source_record_key", sa.String(length=255)),
        sa.Column("source_record_checksum", sa.String(length=64)),
        sa.Column("lineage_role", sa.String(length=32), nullable=False),
        sa.Column("input_order", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["evidence_input_id"], ["agri.intervention_evidence_input.id"]),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(
            ["source_feature_id", "source_release_id"],
            [
                "agri.normalized_source_feature.id",
                "agri.normalized_source_feature.source_release_id",
            ],
            name="fk_intervention_evidence_lineage_feature_release",
        ),
        sa.UniqueConstraint(
            "evidence_input_id",
            "input_order",
            name="uq_intervention_evidence_lineage_input_order",
        ),
        sa.CheckConstraint(
            "lineage_role IN ('direct_observation', 'derivation_input', 'coverage_basis', 'gap_basis')",
            name=op.f("ck_intervention_evidence_lineage_known_lineage_role"),
        ),
        sa.CheckConstraint(
            "(source_feature_id IS NOT NULL AND source_record_table IS NULL "
            "AND source_record_key IS NULL AND source_record_checksum IS NULL) OR "
            "(source_feature_id IS NULL AND source_record_table IS NOT NULL "
            "AND source_record_key IS NOT NULL AND source_record_checksum IS NOT NULL)",
            name=op.f("ck_intervention_evidence_lineage_feature_or_record_locator"),
        ),
        sa.CheckConstraint(
            "source_record_table IS NULL OR source_record_table IN "
            "('signal_observation', 'drought_polygon_snapshot', "
            "'signal_coverage_audit', 'source_coverage_audit')",
            name=op.f("ck_intervention_evidence_lineage_known_source_record_table"),
        ),
        sa.CheckConstraint(
            "source_record_checksum IS NULL OR source_record_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_intervention_evidence_lineage_source_record_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "input_order >= 0",
            name=op.f("ck_intervention_evidence_lineage_nonnegative_input_order"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_intervention_evidence_lineage_source_release",
        "intervention_evidence_lineage",
        ["source_release_id", "evidence_input_id"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION agri.enforce_normalized_feature_artifact_release()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            PERFORM artifact.id
            FROM agri.artifact AS artifact
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = artifact.source_release_id
            WHERE artifact.id = NEW.artifact_id
              AND artifact.source_release_id = NEW.source_release_id
              AND source_release.validation_state = 'valid'
            FOR SHARE OF artifact, source_release;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Normalized features require an artifact owned by the same source release';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_normalized_feature_artifact_release
        BEFORE INSERT ON agri.normalized_source_feature
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_normalized_feature_artifact_release();

        CREATE FUNCTION agri.reject_geospatial_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable; insert a versioned replacement instead', TG_TABLE_NAME;
        END
        $$;

        CREATE TRIGGER trg_normalized_source_feature_immutable
        BEFORE UPDATE OR DELETE ON agri.normalized_source_feature
        FOR EACH ROW EXECUTE FUNCTION agri.reject_geospatial_evidence_mutation();

        CREATE TRIGGER trg_analysis_subject_immutable
        BEFORE UPDATE OR DELETE ON agri.analysis_subject
        FOR EACH ROW EXECUTE FUNCTION agri.reject_geospatial_evidence_mutation();

        CREATE TRIGGER trg_intervention_analysis_run_immutable
        BEFORE UPDATE OR DELETE ON agri.intervention_analysis_run
        FOR EACH ROW EXECUTE FUNCTION agri.reject_geospatial_evidence_mutation();

        CREATE TRIGGER trg_intervention_evidence_input_immutable
        BEFORE UPDATE OR DELETE ON agri.intervention_evidence_input
        FOR EACH ROW EXECUTE FUNCTION agri.reject_geospatial_evidence_mutation();

        CREATE TRIGGER trg_intervention_evidence_lineage_immutable
        BEFORE UPDATE OR DELETE ON agri.intervention_evidence_lineage
        FOR EACH ROW EXECUTE FUNCTION agri.reject_geospatial_evidence_mutation();

        CREATE FUNCTION agri.protect_intervention_evidence_parents()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            is_referenced boolean := false;
            is_critical_change boolean := TG_OP = 'DELETE';
        BEGIN
            CASE TG_TABLE_NAME
                WHEN 'source_release' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.data_source_id,
                            NEW.source_version,
                            NEW.retrieved_at,
                            NEW.data_available_at,
                            NEW.observed_from,
                            NEW.observed_to,
                            NEW.payload_checksum,
                            NEW.payload_bytes,
                            NEW.schema_version,
                            NEW.transform_version,
                            NEW.license_snapshot,
                            NEW.query_parameters,
                            NEW.quality_summary,
                            NEW.validated_at,
                            NEW.supersedes_release_id
                        ) IS DISTINCT FROM ROW(
                            OLD.data_source_id,
                            OLD.source_version,
                            OLD.retrieved_at,
                            OLD.data_available_at,
                            OLD.observed_from,
                            OLD.observed_to,
                            OLD.payload_checksum,
                            OLD.payload_bytes,
                            OLD.schema_version,
                            OLD.transform_version,
                            OLD.license_snapshot,
                            OLD.query_parameters,
                            OLD.quality_summary,
                            OLD.validated_at,
                            OLD.supersedes_release_id
                        );
                    END IF;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.normalized_source_feature WHERE source_release_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.analysis_subject WHERE source_release_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_lineage WHERE source_release_id = OLD.id
                    ) INTO is_referenced;
                WHEN 'artifact' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := (to_jsonb(NEW) - 'created_at')
                            IS DISTINCT FROM (to_jsonb(OLD) - 'created_at');
                    END IF;
                    SELECT EXISTS (
                        SELECT 1
                        FROM agri.normalized_source_feature
                        WHERE artifact_id = OLD.id
                           OR source_release_id = OLD.source_release_id
                        UNION ALL
                        SELECT 1
                        FROM agri.analysis_subject
                        WHERE artifact_id = OLD.id
                           OR source_release_id = OLD.source_release_id
                    ) INTO is_referenced;
                WHEN 'release_set' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.logical_key,
                            NEW.as_of_time,
                            NEW.manifest_checksum,
                            NEW.description
                        ) IS DISTINCT FROM ROW(
                            OLD.logical_key,
                            OLD.as_of_time,
                            OLD.manifest_checksum,
                            OLD.description
                        );
                    END IF;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.intervention_analysis_run WHERE release_set_id = OLD.id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_input WHERE release_set_id = OLD.id
                    ) INTO is_referenced;
                WHEN 'release_set_item' THEN
                    IF TG_OP = 'UPDATE' THEN
                        is_critical_change := ROW(
                            NEW.release_set_id,
                            NEW.source_release_id,
                            NEW.source_role
                        ) IS DISTINCT FROM ROW(
                            OLD.release_set_id,
                            OLD.source_release_id,
                            OLD.source_role
                        );
                    END IF;
                    PERFORM 1
                    FROM agri.release_set
                    WHERE id = OLD.release_set_id
                    FOR UPDATE;
                    SELECT EXISTS (
                        SELECT 1 FROM agri.intervention_analysis_run WHERE release_set_id = OLD.release_set_id
                        UNION ALL
                        SELECT 1 FROM agri.intervention_evidence_input WHERE release_set_id = OLD.release_set_id
                    ) INTO is_referenced;
                ELSE
                    RAISE EXCEPTION 'Unsupported intervention evidence parent table';
            END CASE;
            IF TG_TABLE_NAME = 'source_release'
                AND is_referenced
                AND TG_OP = 'UPDATE'
            THEN
                IF ROW(NEW.validation_state, NEW.retraction_reason)
                        IS DISTINCT FROM ROW(OLD.validation_state, OLD.retraction_reason)
                    AND NOT (
                        OLD.validation_state = 'valid'
                        AND NEW.validation_state = 'retracted'
                        AND NEW.retraction_reason IS NOT NULL
                    )
                THEN
                    RAISE EXCEPTION 'Referenced source releases allow only valid-to-retracted lifecycle changes';
                END IF;
            END IF;
            IF is_referenced AND is_critical_change THEN
                RAISE EXCEPTION '% content is frozen by intervention evidence lineage', TG_TABLE_NAME;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_source_release_intervention_parent
        BEFORE UPDATE OR DELETE ON agri.source_release
        FOR EACH ROW EXECUTE FUNCTION agri.protect_intervention_evidence_parents();

        CREATE TRIGGER trg_artifact_intervention_parent
        BEFORE UPDATE OR DELETE ON agri.artifact
        FOR EACH ROW EXECUTE FUNCTION agri.protect_intervention_evidence_parents();

        CREATE TRIGGER trg_release_set_intervention_parent
        BEFORE UPDATE OR DELETE ON agri.release_set
        FOR EACH ROW EXECUTE FUNCTION agri.protect_intervention_evidence_parents();

        CREATE TRIGGER trg_release_set_item_intervention_parent
        BEFORE UPDATE OR DELETE ON agri.release_set_item
        FOR EACH ROW EXECUTE FUNCTION agri.protect_intervention_evidence_parents();
        """
    )

    op.execute(
        """
        CREATE FUNCTION agri.enforce_derived_evidence_run()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.evidence_kind = 'model_derived_feature' AND NOT EXISTS (
                SELECT 1
                FROM agri.intervention_analysis_run AS analysis_run
                WHERE analysis_run.id = NEW.intervention_analysis_run_id
                  AND analysis_run.release_set_id = NEW.release_set_id
                  AND analysis_run.validation_state = 'validated'
                  AND analysis_run.validated_at IS NOT NULL
                  AND analysis_run.method_key = NEW.method_key
                  AND analysis_run.method_version = NEW.method_version
            ) THEN
                RAISE EXCEPTION
                    'Derived evidence requires a validated release- and method-matched analysis receipt';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_derived_evidence_run
        BEFORE INSERT ON agri.intervention_evidence_input
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_derived_evidence_run();

        CREATE FUNCTION agri.enforce_intervention_analysis_release_set()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            PERFORM release_set.id
            FROM agri.release_set AS release_set
            WHERE release_set.id = NEW.release_set_id
              AND release_set.state IN ('validated', 'published')
            FOR SHARE OF release_set;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Intervention analysis receipts require a validated or published release set';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_intervention_analysis_release_set
        BEFORE INSERT ON agri.intervention_analysis_run
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_intervention_analysis_release_set();

        CREATE FUNCTION agri.enforce_intervention_lineage_release_membership()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            PERFORM release_set.id
            FROM agri.intervention_evidence_input AS evidence
            INNER JOIN agri.release_set AS release_set
                ON release_set.id = evidence.release_set_id
            WHERE evidence.id = NEW.evidence_input_id
            FOR SHARE OF release_set;
            PERFORM source_release.id
            FROM agri.source_release AS source_release
            WHERE source_release.id = NEW.source_release_id
            FOR SHARE OF source_release;
            PERFORM subject_release.id
            FROM agri.intervention_evidence_input AS evidence
            INNER JOIN agri.analysis_subject AS subject
                ON subject.id = evidence.analysis_subject_id
            INNER JOIN agri.source_release AS subject_release
                ON subject_release.id = subject.source_release_id
            WHERE evidence.id = NEW.evidence_input_id
            FOR SHARE OF subject_release;
            IF NOT EXISTS (
                SELECT 1
                FROM agri.intervention_evidence_input AS evidence
                INNER JOIN agri.release_set AS release_set
                    ON release_set.id = evidence.release_set_id
                INNER JOIN agri.analysis_subject AS subject
                    ON subject.id = evidence.analysis_subject_id
                INNER JOIN agri.release_set_item AS membership
                    ON membership.release_set_id = release_set.id
                   AND membership.source_release_id = NEW.source_release_id
                INNER JOIN agri.release_set_item AS subject_membership
                    ON subject_membership.release_set_id = release_set.id
                   AND subject_membership.source_release_id = subject.source_release_id
                INNER JOIN agri.source_release AS source_release
                    ON source_release.id = membership.source_release_id
                INNER JOIN agri.source_release AS subject_release
                    ON subject_release.id = subject_membership.source_release_id
                WHERE evidence.id = NEW.evidence_input_id
                  AND release_set.state IN ('validated', 'published')
                  AND source_release.validation_state = 'valid'
                  AND source_release.data_available_at <= release_set.as_of_time
                  AND subject_release.validation_state = 'valid'
                  AND subject_release.data_available_at <= release_set.as_of_time
            ) THEN
                RAISE EXCEPTION
                    'Evidence and subject lineage require valid releases in the validated or published release set';
            END IF;

            IF NEW.source_feature_id IS NULL THEN
                CASE NEW.source_record_table
                    WHEN 'signal_observation' THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM agri.signal_observation AS source_record
                            WHERE source_record.id::text = NEW.source_record_key
                              AND source_record.source_release_id = NEW.source_release_id
                              AND encode(
                                  public.digest(to_jsonb(source_record)::text, 'sha256'),
                                  'hex'
                              ) = NEW.source_record_checksum
                        ) THEN
                            RAISE EXCEPTION 'Signal-observation lineage locator or checksum does not match';
                        END IF;
                    WHEN 'drought_polygon_snapshot' THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM agri.drought_polygon_snapshot AS source_record
                            WHERE source_record.id::text = NEW.source_record_key
                              AND source_record.source_release_id = NEW.source_release_id
                              AND encode(
                                  public.digest(to_jsonb(source_record)::text, 'sha256'),
                                  'hex'
                              ) = NEW.source_record_checksum
                        ) THEN
                            RAISE EXCEPTION 'Drought-polygon lineage locator or checksum does not match';
                        END IF;
                    WHEN 'signal_coverage_audit' THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM agri.signal_coverage_audit AS source_record
                            WHERE source_record.id::text = NEW.source_record_key
                              AND source_record.source_release_id = NEW.source_release_id
                              AND encode(
                                  public.digest(to_jsonb(source_record)::text, 'sha256'),
                                  'hex'
                              ) = NEW.source_record_checksum
                        ) THEN
                            RAISE EXCEPTION 'Signal-coverage lineage locator or checksum does not match';
                        END IF;
                    WHEN 'source_coverage_audit' THEN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM agri.source_coverage_audit AS source_record
                            WHERE source_record.id::text = NEW.source_record_key
                              AND source_record.source_release_id = NEW.source_release_id
                              AND encode(
                                  public.digest(to_jsonb(source_record)::text, 'sha256'),
                                  'hex'
                              ) = NEW.source_record_checksum
                        ) THEN
                            RAISE EXCEPTION 'Source-coverage lineage locator or checksum does not match';
                        END IF;
                    ELSE
                        RAISE EXCEPTION 'Unknown evidence source-record table';
                END CASE;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER trg_intervention_lineage_release_membership
        BEFORE INSERT ON agri.intervention_evidence_lineage
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_intervention_lineage_release_membership();

        CREATE FUNCTION agri.require_intervention_evidence_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM agri.intervention_evidence_lineage AS lineage
                WHERE lineage.evidence_input_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'Every intervention evidence input requires relational source lineage';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE CONSTRAINT TRIGGER trg_intervention_evidence_requires_lineage
        AFTER INSERT ON agri.intervention_evidence_input
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION agri.require_intervention_evidence_lineage();

        REVOKE EXECUTE ON FUNCTION agri.reject_geospatial_evidence_mutation() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.protect_intervention_evidence_parents() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_normalized_feature_artifact_release() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_derived_evidence_run() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_intervention_analysis_release_set() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_intervention_lineage_release_membership() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.require_intervention_evidence_lineage() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Resolution-aware evidence is append-only; restore a verified backup into a fresh database."
    )
