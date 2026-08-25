"""Create the Alembic-owned agri schema and durable execution foundation.

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import geoalchemy2
import pgvector.sqlalchemy
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260719_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agri"

_REQUIRED_EXTENSION_PREFLIGHT_SQL = """
DO $$
DECLARE
    missing_extensions text;
BEGIN
    SELECT string_agg(required.extname, ', ' ORDER BY required.extname)
    INTO missing_extensions
    FROM (
        VALUES
            ('postgis'::text),
            ('timescaledb'::text),
            ('vector'::text),
            ('pgcrypto'::text)
    ) AS required(extname)
    LEFT JOIN pg_extension installed ON installed.extname = required.extname
    WHERE installed.extname IS NULL;

    IF missing_extensions IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = format(
                'Agri foundation preflight failed: missing installed PostgreSQL extension(s): %s.',
                missing_extensions
            ),
            HINT = 'An operator must first confirm package availability and run the reviewed extension gate. '
                'This migration never creates extensions.';
    END IF;
END
$$;
"""


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


def _updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def _text_enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.execute(sa.text(_REQUIRED_EXTENSION_PREFLIGHT_SQL))
    op.execute(sa.text('CREATE SCHEMA IF NOT EXISTS "agri"'))

    op.create_table(
        "locations",
        _uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("POINT", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "bounding_box",
            geoalchemy2.Geometry("POLYGON", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
        ),
        sa.Column("usda_zone", sa.String(length=10)),
        sa.Column("epa_ecoregion", sa.String(length=100)),
        sa.Column("elevation_m", sa.Float()),
        _created_at(),
        _updated_at(),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_locations_geometry", "locations", ["geometry"], unique=False, schema=SCHEMA, postgresql_using="gist"
    )
    op.create_index(
        "ix_locations_bounding_box",
        "locations",
        ["bounding_box"],
        unique=False,
        schema=SCHEMA,
        postgresql_using="gist",
    )

    op.create_table(
        "strategies",
        _uuid_pk(),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("slug", sa.String(length=255), nullable=False, unique=True),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("authority", sa.String(length=100), nullable=False),
        sa.Column("practice_code", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("min_precip_mm", sa.Float()),
        sa.Column("max_precip_mm", sa.Float()),
        sa.Column("min_temp_c", sa.Float()),
        sa.Column("max_temp_c", sa.Float()),
        sa.Column("suitable_soil_types", postgresql.ARRAY(sa.String())),
        sa.Column("suitable_drainage", postgresql.ARRAY(sa.String())),
        sa.Column("max_slope_pct", sa.Float()),
        sa.Column("min_organic_matter_pct", sa.Float()),
        sa.Column("water_requirement", _text_enum("water_requirement", "low", "medium", "high")),
        sa.Column("labor_intensity", _text_enum("labor_intensity", "low", "medium", "high")),
        sa.Column("time_to_yield_years", sa.Float()),
        sa.Column(
            "carbon_seq_potential",
            _text_enum("strategy_carbon_impact_level", "low", "medium", "high", "very_high"),
        ),
        sa.Column(
            "biodiversity_impact",
            _text_enum("strategy_biodiversity_impact_level", "low", "medium", "high", "very_high"),
        ),
        sa.Column("evidence_citation", sa.Text()),
        sa.Column("evidence_source_url", sa.String(length=1000)),
        sa.Column("jurisdiction", sa.String(length=255)),
        sa.Column("limitations", sa.Text()),
        sa.Column(
            "review_state",
            _text_enum("strategy_review_state", "draft", "reviewed", "approved", "rejected"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("authority", "practice_code", name="uq_strategy_authority_practice"),
        sa.CheckConstraint(
            "review_state <> 'approved' OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND evidence_citation IS NOT NULL AND evidence_source_url IS NOT NULL "
            "AND jurisdiction IS NOT NULL AND limitations IS NOT NULL)",
            name=op.f("ck_strategies_approved_strategy_has_evidence"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "species",
        _uuid_pk(),
        sa.Column("scientific_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("common_name", sa.String(length=255), nullable=False),
        sa.Column("usda_symbol", sa.String(length=20)),
        sa.Column("family", sa.String(length=100)),
        sa.Column("growth_habit", sa.String(length=50)),
        sa.Column("native_status", sa.String(length=50)),
        sa.Column("usda_zones", postgresql.INT4RANGE()),
        sa.Column("min_precip_mm", sa.Float()),
        sa.Column("max_precip_mm", sa.Float()),
        sa.Column("min_ph", sa.Float()),
        sa.Column("max_ph", sa.Float()),
        sa.Column("light_requirement", sa.String(length=50)),
        sa.Column("drought_tolerance", sa.String(length=20)),
        sa.Column("salt_tolerance", sa.String(length=20)),
        sa.Column("nitrogen_fixer", sa.Boolean(), nullable=False),
        sa.Column(
            "pollinator_value",
            _text_enum("pollinator_value", "none", "low", "medium", "high"),
        ),
        sa.Column("edible", sa.Boolean(), nullable=False),
        sa.Column("timber_value", sa.Boolean(), nullable=False),
        sa.Column("guild_roles", postgresql.ARRAY(sa.String())),
        _created_at(),
        _updated_at(),
        schema=SCHEMA,
    )

    op.create_table(
        "companion_relationships",
        _uuid_pk(),
        sa.Column("species_a_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("species_b_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "relationship_type",
            _text_enum("companion_relationship_type", "companion", "antagonist", "neutral"),
            nullable=False,
        ),
        sa.Column("guild_function", sa.String(length=100)),
        sa.Column("notes", sa.Text()),
        sa.Column("evidence_citation", sa.Text()),
        sa.Column("evidence_source_url", sa.String(length=1000)),
        sa.Column("evidence_grade", sa.String(length=50)),
        sa.Column("applicability_context", sa.Text()),
        sa.Column("jurisdiction", sa.String(length=255)),
        sa.Column(
            "review_state",
            _text_enum("companion_review_state", "draft", "reviewed", "approved", "rejected"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        sa.ForeignKeyConstraint(["species_a_id"], ["agri.species.id"]),
        sa.ForeignKeyConstraint(["species_b_id"], ["agri.species.id"]),
        sa.UniqueConstraint("species_a_id", "species_b_id", name="uq_companion_pair"),
        sa.CheckConstraint(
            "species_a_id <> species_b_id",
            name=op.f("ck_companion_relationships_companion_pair_not_self"),
        ),
        sa.CheckConstraint(
            "review_state <> 'approved' OR (reviewed_at IS NOT NULL AND evidence_citation IS NOT NULL)",
            name=op.f("ck_companion_relationships_approved_companion_has_evidence"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_companion_relationships_species_a_id",
        "companion_relationships",
        ["species_a_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_companion_relationships_species_b_id",
        "companion_relationships",
        ["species_b_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "soil_profiles",
        _uuid_pk(),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _text_enum("soil_source", "ssurgo", "soilgrids"), nullable=False),
        sa.Column("soil_type", sa.String(length=100)),
        sa.Column("texture_class", sa.String(length=50)),
        sa.Column("ph", sa.Float()),
        sa.Column("organic_matter_pct", sa.Float()),
        sa.Column("cec", sa.Float()),
        sa.Column("bulk_density", sa.Float()),
        sa.Column("drainage_class", sa.String(length=50)),
        sa.Column("depth_cm", sa.Float()),
        sa.Column("sand_pct", sa.Float()),
        sa.Column("silt_pct", sa.Float()),
        sa.Column("clay_pct", sa.Float()),
        sa.Column("available_water_capacity", sa.Float()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["agri.locations.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_soil_profiles_location_id", "soil_profiles", ["location_id"], schema=SCHEMA)

    op.create_table(
        "climate_profiles",
        _uuid_pk(),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _text_enum("climate_source", "prism", "noaa", "nasa_power"), nullable=False),
        sa.Column("annual_precip_mm", sa.Float()),
        sa.Column("growing_season_days", sa.Integer()),
        sa.Column("avg_temp_c", sa.Float()),
        sa.Column("min_temp_c", sa.Float()),
        sa.Column("max_temp_c", sa.Float()),
        sa.Column("frost_free_days", sa.Integer()),
        sa.Column("koppen_zone", sa.String(length=10)),
        sa.Column("aridity_index", sa.Float()),
        sa.Column("monthly_precip_json", postgresql.JSONB()),
        sa.Column("monthly_temp_json", postgresql.JSONB()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["agri.locations.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_climate_profiles_location_id", "climate_profiles", ["location_id"], schema=SCHEMA)

    op.create_table(
        "topography_profiles",
        _uuid_pk(),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("elevation_m", sa.Float()),
        sa.Column("slope_pct", sa.Float()),
        sa.Column("aspect_deg", sa.Float()),
        sa.Column("curvature", sa.Float()),
        sa.Column("twi", sa.Float()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["agri.locations.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_topography_profiles_location_id", "topography_profiles", ["location_id"], schema=SCHEMA)

    op.create_table(
        "water_profiles",
        _uuid_pk(),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _text_enum("water_source", "usgs_nwis", "noaa_atlas14"), nullable=False),
        sa.Column("nearest_stream_distance_m", sa.Float()),
        sa.Column("watershed_huc12", sa.String(length=12)),
        sa.Column("annual_runoff_mm", sa.Float()),
        sa.Column("flood_zone", sa.String(length=20)),
        sa.Column("groundwater_depth_m", sa.Float()),
        sa.Column("water_table_seasonal_json", postgresql.JSONB()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["agri.locations.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_water_profiles_location_id", "water_profiles", ["location_id"], schema=SCHEMA)

    op.create_table(
        "land_use_snapshots",
        _uuid_pk(),
        sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", _text_enum("land_use_source", "cdl", "nlcd"), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=100)),
        sa.Column("crop_history_json", postgresql.JSONB()),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["location_id"], ["agri.locations.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_land_use_snapshots_location_id", "land_use_snapshots", ["location_id"], schema=SCHEMA)

    op.create_table(
        "knowledge_chunks",
        _uuid_pk(),
        sa.Column("source_document", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536)),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metadata_json", postgresql.JSONB()),
        _created_at(),
        sa.ForeignKeyConstraint(["strategy_id"], ["agri.strategies.id"]),
        schema=SCHEMA,
    )
    op.create_index("ix_knowledge_chunks_strategy_id", "knowledge_chunks", ["strategy_id"], schema=SCHEMA)
    op.create_index(
        "ix_knowledge_chunks_embedding",
        "knowledge_chunks",
        ["embedding"],
        schema=SCHEMA,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "data_source",
        _uuid_pk(),
        sa.Column("key", sa.String(length=100), nullable=False, unique=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("base_url", sa.String(length=1000)),
        sa.Column("license_name", sa.String(length=255), nullable=False),
        sa.Column("license_url", sa.String(length=1000)),
        sa.Column("citation", sa.Text(), nullable=False),
        sa.Column("refresh_policy", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("retention_days", sa.Integer()),
        sa.Column("allowed_client_exposure", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "review_state",
            _text_enum("source_review_state", "draft", "reviewed", "approved", "rejected", "retired"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("review_due_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(length=255)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "retention_days IS NULL OR retention_days > 0",
            name=op.f("ck_data_source_positive_retention_days"),
        ),
        sa.CheckConstraint(
            "review_state <> 'approved' OR reviewed_at IS NOT NULL",
            name=op.f("ck_data_source_approved_source_has_review"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "source_release",
        _uuid_pk(),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version", sa.String(length=255), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_from", sa.DateTime(timezone=True)),
        sa.Column("observed_to", sa.DateTime(timezone=True)),
        sa.Column("payload_checksum", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger()),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("license_snapshot", sa.Text(), nullable=False),
        sa.Column("query_parameters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("quality_summary", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "validation_state",
            _text_enum("release_validation_state", "pending", "valid", "invalid", "quarantined", "retracted"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("supersedes_release_id", postgresql.UUID(as_uuid=True)),
        sa.Column("retraction_reason", sa.Text()),
        _created_at(),
        sa.ForeignKeyConstraint(["data_source_id"], ["agri.data_source.id"]),
        sa.ForeignKeyConstraint(["supersedes_release_id"], ["agri.source_release.id"]),
        sa.UniqueConstraint("data_source_id", "source_version", "payload_checksum", name="uq_source_release_identity"),
        sa.CheckConstraint(
            "payload_bytes IS NULL OR payload_bytes >= 0",
            name=op.f("ck_source_release_nonnegative_payload_bytes"),
        ),
        sa.CheckConstraint(
            "observed_to IS NULL OR observed_from IS NULL OR observed_to >= observed_from",
            name=op.f("ck_source_release_ordered_observation_window"),
        ),
        sa.CheckConstraint(
            "validation_state <> 'valid' OR validated_at IS NOT NULL",
            name=op.f("ck_source_release_valid_release_has_validation_time"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_source_release_source_available", "source_release", ["data_source_id", "data_available_at"], schema=SCHEMA
    )

    op.create_table(
        "artifact",
        _uuid_pk(),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True)),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("uri", sa.String(length=2000), nullable=False),
        sa.Column("media_type", sa.String(length=255)),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_class", sa.String(length=50), server_default="standard", nullable=False),
        sa.Column("encryption_key_ref", sa.String(length=500)),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary()),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.UniqueConstraint("uri", "checksum_sha256", name="uq_artifact_uri_checksum"),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_artifact_nonnegative_artifact_size")),
        sa.CheckConstraint(
            "content_bytes IS NULL OR octet_length(content_bytes) = size_bytes",
            name=op.f("ck_artifact_inline_artifact_size_matches"),
        ),
        sa.CheckConstraint(
            "storage_class <> 'database_inline' OR content_bytes IS NOT NULL",
            name=op.f("ck_artifact_inline_artifact_has_content"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "release_set",
        _uuid_pk(),
        sa.Column("logical_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("as_of_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "state",
            _text_enum("release_set_state", "draft", "validated", "published", "retired"),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("description", sa.Text()),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.CheckConstraint(
            "manifest_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_release_set_release_set_manifest_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "state NOT IN ('validated', 'published') OR validated_at IS NOT NULL",
            name=op.f("ck_release_set_validated_set_has_timestamp"),
        ),
        sa.CheckConstraint(
            "state <> 'published' OR published_at IS NOT NULL",
            name=op.f("ck_release_set_published_set_has_timestamp"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "release_set_item",
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("source_role", sa.String(length=100), server_default="input", nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        schema=SCHEMA,
    )
    op.execute(
        """
        CREATE FUNCTION agri.enforce_release_set_freeze()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(OLD.id::text, 0));
            IF OLD.state <> 'draft' AND (
                NEW.state IS DISTINCT FROM OLD.state
                OR NEW.logical_key IS DISTINCT FROM OLD.logical_key
                OR NEW.as_of_time IS DISTINCT FROM OLD.as_of_time
                OR NEW.manifest_checksum IS DISTINCT FROM OLD.manifest_checksum
                OR NEW.validated_at IS DISTINCT FROM OLD.validated_at
                OR NEW.published_at IS DISTINCT FROM OLD.published_at
            ) THEN
                RAISE EXCEPTION 'validated release set identity is immutable'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER release_set_identity_freeze
        BEFORE UPDATE ON agri.release_set
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_release_set_freeze()
        """
    )
    op.execute(
        """
        CREATE FUNCTION agri.enforce_release_set_membership_draft()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            candidate_release_set_id uuid;
            candidate_state text;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.release_set_id <> NEW.release_set_id THEN
                RAISE EXCEPTION 'release set membership cannot be reparented'
                    USING ERRCODE = '55000';
            END IF;
            candidate_release_set_id := CASE
                WHEN TG_OP = 'DELETE' THEN OLD.release_set_id
                ELSE NEW.release_set_id
            END;
            SELECT state INTO candidate_state
            FROM agri.release_set
            WHERE id = candidate_release_set_id
            FOR UPDATE;
            IF candidate_state IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'release set membership is immutable after validation'
                    USING ERRCODE = '55000';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER release_set_membership_draft_only
        BEFORE INSERT OR UPDATE OR DELETE ON agri.release_set_item
        FOR EACH ROW EXECUTE FUNCTION agri.enforce_release_set_membership_draft()
        """
    )

    _create_job_tables()


def _create_job_tables() -> None:
    """Create durable execution tables after lineage tables exist."""
    op.create_table(
        "job_definition",
        _uuid_pk(),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("handler", sa.String(length=500), nullable=False),
        sa.Column("queue_name", sa.String(length=100), server_default="default", nullable=False),
        sa.Column("schedule", sa.String(length=255)),
        sa.Column("schedule_timezone", sa.String(length=100), server_default="UTC", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("concurrency_key", sa.String(length=255)),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("lease_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("time_budget_seconds", sa.Integer(), server_default="240", nullable=False),
        sa.Column("retry_policy", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.UniqueConstraint("name", "version", name="uq_job_definition_name_version"),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_job_definition_positive_max_attempts")),
        sa.CheckConstraint("lease_seconds > 0", name=op.f("ck_job_definition_positive_lease_seconds")),
        sa.CheckConstraint(
            "time_budget_seconds > 0",
            name=op.f("ck_job_definition_positive_time_budget_seconds"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "job_run",
        _uuid_pk(),
        sa.Column("job_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True)),
        sa.Column("logical_run_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recipe_version", sa.String(length=100)),
        sa.Column("model_version", sa.String(length=100)),
        sa.Column("target_partitions", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "status",
            _text_enum(
                "job_run_state",
                "queued",
                "running",
                "succeeded",
                "partial",
                "failed",
                "dead_letter",
                "cancelled",
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("requested_by", sa.String(length=255)),
        sa.Column("total_work_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("succeeded_work_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_work_items", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column("last_error_summary", sa.Text()),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["job_definition_id"], ["agri.job_definition.id"]),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.CheckConstraint("total_work_items >= 0", name=op.f("ck_job_run_nonnegative_total_work_items")),
        sa.CheckConstraint(
            "succeeded_work_items >= 0",
            name=op.f("ck_job_run_nonnegative_succeeded_work_items"),
        ),
        sa.CheckConstraint("failed_work_items >= 0", name=op.f("ck_job_run_nonnegative_failed_work_items")),
        sa.CheckConstraint(
            "succeeded_work_items + failed_work_items <= total_work_items",
            name=op.f("ck_job_run_work_item_counts_within_total"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'partial', 'failed', 'dead_letter', 'cancelled') OR completed_at IS NOT NULL",
            name=op.f("ck_job_run_terminal_run_has_completion_time"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_job_run_status_scheduled", "job_run", ["status", "scheduled_for"], schema=SCHEMA)

    _create_work_tables()


def _create_work_tables() -> None:
    """Create resumable work, output, and operational tables."""
    op.create_table(
        "job_dependency",
        _uuid_pk(),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("depends_on_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("required_status", sa.String(length=32), server_default="succeeded", nullable=False),
        sa.Column("satisfied_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["job_run_id"], ["agri.job_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_run_id"], ["agri.job_run.id"]),
        sa.UniqueConstraint("job_run_id", "depends_on_run_id", name="uq_job_dependency_edge"),
        sa.CheckConstraint(
            "job_run_id <> depends_on_run_id",
            name=op.f("ck_job_dependency_dependency_not_self"),
        ),
        sa.CheckConstraint(
            "required_status IN ('succeeded', 'partial')",
            name=op.f("ck_job_dependency_dependency_required_status"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "job_work_item",
        _uuid_pk(),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shard_key", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "status",
            _text_enum(
                "work_item_state",
                "queued",
                "leased",
                "running",
                "retry_wait",
                "deferred",
                "succeeded",
                "dead_letter",
                "cancelled",
            ),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("progress_fraction", sa.Float(), server_default="0", nullable=False),
        sa.Column("checkpoint_sequence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error_class", sa.String(length=255)),
        sa.Column("last_error_summary", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["job_run_id"], ["agri.job_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_run_id", "shard_key", name="uq_job_work_item_run_shard"),
        sa.UniqueConstraint("id", "job_run_id", name="uq_job_work_item_run_identity"),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_job_work_item_nonnegative_attempt_count")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_job_work_item_positive_work_item_max_attempts")),
        sa.CheckConstraint("attempt_count <= max_attempts", name=op.f("ck_job_work_item_attempt_count_within_limit")),
        sa.CheckConstraint("fencing_token >= 0", name=op.f("ck_job_work_item_nonnegative_fencing_token")),
        sa.CheckConstraint("checkpoint_sequence >= 0", name=op.f("ck_job_work_item_nonnegative_checkpoint_sequence")),
        sa.CheckConstraint(
            "progress_fraction >= 0 AND progress_fraction <= 1",
            name=op.f("ck_job_work_item_progress_fraction_range"),
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_job_work_item_complete_lease_pair"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('leased', 'running') OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND fencing_token > 0)",
            name=op.f("ck_job_work_item_active_item_has_fenced_lease"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('retry_wait', 'deferred') OR next_attempt_at IS NOT NULL",
            name=op.f("ck_job_work_item_resumable_item_has_next_attempt"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('succeeded', 'dead_letter', 'cancelled') OR completed_at IS NOT NULL",
            name=op.f("ck_job_work_item_terminal_item_has_completion_time"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_job_work_item_claim",
        "job_work_item",
        ["status", "next_attempt_at", "available_at", "priority"],
        schema=SCHEMA,
    )
    op.create_index("ix_job_work_item_lease_expiry", "job_work_item", ["lease_expires_at"], schema=SCHEMA)

    _create_attempt_and_publication_tables()


def _create_attempt_and_publication_tables() -> None:
    """Create attempt lineage and atomic publication records."""
    op.create_table(
        "job_attempt",
        _uuid_pk(),
        sa.Column("job_work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            _text_enum("attempt_state", "running", "succeeded", "failed", "lost", "deferred", "cancelled"),
            server_default="running",
            nullable=False,
        ),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("failure_class", sa.String(length=255)),
        sa.Column("error_summary", sa.Text()),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["job_work_item_id"], ["agri.job_work_item.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_work_item_id", "attempt_number", name="uq_job_attempt_item_number"),
        sa.UniqueConstraint("job_work_item_id", "fencing_token", name="uq_job_attempt_item_fence"),
        sa.UniqueConstraint("id", "job_work_item_id", "fencing_token", name="uq_job_attempt_checkpoint_fence"),
        sa.CheckConstraint("attempt_number > 0", name=op.f("ck_job_attempt_positive_attempt_number")),
        sa.CheckConstraint("fencing_token > 0", name=op.f("ck_job_attempt_positive_attempt_fencing_token")),
        sa.CheckConstraint(
            "status = 'running' OR finished_at IS NOT NULL",
            name=op.f("ck_job_attempt_terminal_attempt_has_finish_time"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "job_checkpoint",
        _uuid_pk(),
        sa.Column("job_work_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("cursor", postgresql.JSONB(), nullable=False),
        sa.Column("cursor_checksum", sa.String(length=64), nullable=False),
        sa.Column("progress_fraction", sa.Float(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["job_work_item_id"], ["agri.job_work_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["job_attempt_id", "job_work_item_id", "fencing_token"],
            [
                "agri.job_attempt.id",
                "agri.job_attempt.job_work_item_id",
                "agri.job_attempt.fencing_token",
            ],
            name="fk_job_checkpoint_attempt_fence",
        ),
        sa.UniqueConstraint("job_work_item_id", "sequence", name="uq_job_checkpoint_item_sequence"),
        sa.CheckConstraint("sequence > 0", name=op.f("ck_job_checkpoint_positive_checkpoint_sequence")),
        sa.CheckConstraint("fencing_token > 0", name=op.f("ck_job_checkpoint_positive_checkpoint_fencing_token")),
        sa.CheckConstraint(
            "progress_fraction >= 0 AND progress_fraction <= 1",
            name=op.f("ck_job_checkpoint_checkpoint_progress_fraction_range"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "job_output",
        _uuid_pk(),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_work_item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_attempt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("fencing_token", sa.BigInteger()),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("output_key", sa.String(length=500), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column(
            "state",
            _text_enum("output_state", "staged", "validated", "rejected", "published", "superseded"),
            server_default="staged",
            nullable=False,
        ),
        sa.Column("uri", sa.String(length=2000)),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.BigInteger()),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["job_run_id"], ["agri.job_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["agri.artifact.id"]),
        sa.ForeignKeyConstraint(
            ["job_work_item_id", "job_run_id"],
            ["agri.job_work_item.id", "agri.job_work_item.job_run_id"],
            name="fk_job_output_work_item_run",
        ),
        sa.ForeignKeyConstraint(
            ["job_attempt_id", "job_work_item_id", "fencing_token"],
            [
                "agri.job_attempt.id",
                "agri.job_attempt.job_work_item_id",
                "agri.job_attempt.fencing_token",
            ],
            name="fk_job_output_attempt_fence",
        ),
        sa.UniqueConstraint("job_run_id", "output_key", name="uq_job_output_run_key"),
        sa.CheckConstraint("row_count IS NULL OR row_count >= 0", name=op.f("ck_job_output_nonnegative_output_rows")),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name=op.f("ck_job_output_nonnegative_output_size")),
        sa.CheckConstraint(
            "state NOT IN ('validated', 'published') OR validated_at IS NOT NULL",
            name=op.f("ck_job_output_validated_output_has_timestamp"),
        ),
        sa.CheckConstraint(
            "(job_work_item_id IS NULL AND job_attempt_id IS NULL AND fencing_token IS NULL) "
            "OR (job_work_item_id IS NOT NULL AND job_attempt_id IS NOT NULL "
            "AND fencing_token IS NOT NULL AND fencing_token > 0)",
            name=op.f("ck_job_output_work_output_has_attempt_fence"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "publication_pointer",
        _uuid_pk(),
        sa.Column("product", sa.String(length=150), nullable=False),
        sa.Column("scope_key", sa.String(length=500), nullable=False),
        sa.Column("job_output_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_job_output_id", postgresql.UUID(as_uuid=True)),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True)),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_by", sa.String(length=255), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["job_output_id"], ["agri.job_output.id"]),
        sa.ForeignKeyConstraint(["previous_job_output_id"], ["agri.job_output.id"]),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.UniqueConstraint("product", "scope_key", name="uq_publication_pointer_product_scope"),
        sa.CheckConstraint("revision > 0", name=op.f("ck_publication_pointer_positive_publication_revision")),
        sa.CheckConstraint(
            "previous_job_output_id IS NULL OR previous_job_output_id <> job_output_id",
            name=op.f("ck_publication_pointer_publication_previous_differs"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_job_output_artifact_id", "job_output", ["artifact_id"], schema=SCHEMA)

    _create_operational_tables()


def _create_operational_tables() -> None:
    """Create transactional delivery, event, and incident records."""
    op.create_table(
        "job_outbox",
        _uuid_pk(),
        sa.Column("event_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("aggregate_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "status",
            _text_enum("outbox_state", "pending", "publishing", "retry_wait", "delivered", "dead_letter"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="10", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_summary", sa.Text()),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_job_outbox_nonnegative_outbox_attempt_count")),
        sa.CheckConstraint("max_attempts > 0", name=op.f("ck_job_outbox_positive_outbox_max_attempts")),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name=op.f("ck_job_outbox_outbox_attempt_count_within_limit"),
        ),
        sa.CheckConstraint("fencing_token >= 0", name=op.f("ck_job_outbox_nonnegative_outbox_fencing_token")),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_job_outbox_complete_outbox_lease_pair"),
        ),
        sa.CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL",
            name=op.f("ck_job_outbox_delivered_outbox_has_timestamp"),
        ),
        sa.CheckConstraint(
            "status <> 'publishing' OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND fencing_token > 0)",
            name=op.f("ck_job_outbox_publishing_outbox_has_fenced_lease"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_job_outbox_dispatch", "job_outbox", ["status", "next_attempt_at"], schema=SCHEMA)

    op.create_table(
        "job_event",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_work_item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_attempt_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "severity",
            _text_enum("event_severity", "debug", "info", "warning", "error", "critical"),
            nullable=False,
        ),
        sa.Column("event_code", sa.String(length=150), nullable=False),
        sa.Column("environment", sa.String(length=100), nullable=False),
        sa.Column("service", sa.String(length=100), nullable=False),
        sa.Column("trace_id", sa.String(length=255)),
        sa.Column("duration_ms", sa.BigInteger()),
        sa.Column("progress", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["job_run_id"], ["agri.job_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_work_item_id"], ["agri.job_work_item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_attempt_id"], ["agri.job_attempt.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_job_event_nonnegative_event_duration"),
        ),
        schema=SCHEMA,
        postgresql_partition_by="RANGE (occurred_at)",
    )
    op.execute(sa.text("CREATE TABLE agri.job_event_default PARTITION OF agri.job_event DEFAULT"))
    op.create_index("ix_job_event_run_occurred", "job_event", ["job_run_id", "occurred_at"], schema=SCHEMA)
    op.create_index("ix_job_event_severity_occurred", "job_event", ["severity", "occurred_at"], schema=SCHEMA)

    op.create_table(
        "job_incident",
        _uuid_pk(),
        sa.Column("fingerprint", sa.String(length=255), nullable=False, unique=True),
        sa.Column("incident_type", sa.String(length=150), nullable=False),
        sa.Column(
            "severity",
            _text_enum("incident_severity", "debug", "info", "warning", "error", "critical"),
            nullable=False,
        ),
        sa.Column(
            "status",
            _text_enum("incident_state", "open", "acknowledged", "resolved"),
            server_default="open",
            nullable=False,
        ),
        sa.Column("job_run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_work_item_id", postgresql.UUID(as_uuid=True)),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("owner", sa.String(length=255)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.String(length=255)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("detail", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        _updated_at(),
        sa.ForeignKeyConstraint(["job_run_id"], ["agri.job_run.id"]),
        sa.ForeignKeyConstraint(["job_work_item_id"], ["agri.job_work_item.id"]),
        sa.CheckConstraint("occurrence_count > 0", name=op.f("ck_job_incident_positive_incident_occurrence_count")),
        sa.CheckConstraint("last_seen_at >= first_seen_at", name=op.f("ck_job_incident_ordered_incident_seen_window")),
        sa.CheckConstraint(
            "status <> 'acknowledged' OR acknowledged_at IS NOT NULL",
            name=op.f("ck_job_incident_acknowledged_incident_has_timestamp"),
        ),
        sa.CheckConstraint(
            "status <> 'resolved' OR resolved_at IS NOT NULL",
            name=op.f("ck_job_incident_resolved_incident_has_timestamp"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_job_incident_status_severity", "job_incident", ["status", "severity"], schema=SCHEMA)


def downgrade() -> None:
    raise RuntimeError(
        "The agri foundation is a forward-only data boundary; restore a backup into a fresh database instead."
    )
