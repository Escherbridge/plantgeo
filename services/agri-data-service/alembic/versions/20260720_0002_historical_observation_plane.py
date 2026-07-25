"""Add typed, release-pinned historical environmental observations.

Revision ID: 20260720_0002
Revises: 20260719_0001
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260720_0002"
down_revision: str | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agri"


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
    op.add_column(
        "source_release",
        sa.Column("transform_version", sa.String(length=100), server_default="source-native", nullable=False),
        schema=SCHEMA,
    )
    op.drop_constraint("uq_source_release_identity", "source_release", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_source_release_identity",
        "source_release",
        ["data_source_id", "source_version", "payload_checksum", "transform_version"],
        schema=SCHEMA,
    )

    op.create_table(
        "spatial_cell",
        _uuid_pk(),
        sa.Column("cell_key", sa.String(length=180), nullable=False, unique=True),
        sa.Column("grid_name", sa.String(length=100), nullable=False),
        sa.Column("resolution_m", sa.Integer(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("POLYGON", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column(
            "centroid",
            geoalchemy2.Geometry("POINT", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("parent_cell_id", postgresql.UUID(as_uuid=True)),
        sa.Column("coverage_fraction", sa.Float(), server_default="1", nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["parent_cell_id"], ["agri.spatial_cell.id"]),
        sa.CheckConstraint("resolution_m > 0", name=op.f("ck_spatial_cell_positive_resolution")),
        sa.CheckConstraint(
            "coverage_fraction > 0 AND coverage_fraction <= 1",
            name=op.f("ck_spatial_cell_valid_coverage"),
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_spatial_cell_geometry", "spatial_cell", ["geometry"], schema=SCHEMA, postgresql_using="gist")
    op.create_index("ix_spatial_cell_centroid", "spatial_cell", ["centroid"], schema=SCHEMA, postgresql_using="gist")
    op.create_index("ix_spatial_cell_grid_resolution", "spatial_cell", ["grid_name", "resolution_m"], schema=SCHEMA)

    op.create_table(
        "cell_source_crosswalk",
        _uuid_pk(),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("native_feature_key", sa.String(length=255), nullable=False),
        sa.Column(
            "native_geometry",
            geoalchemy2.Geometry("GEOMETRY", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("native_resolution_m", sa.Integer()),
        sa.Column("mapping_method", sa.String(length=100), nullable=False),
        sa.Column("coverage_fraction", sa.Float(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(["cell_id"], ["agri.spatial_cell.id"]),
        sa.UniqueConstraint(
            "source_release_id",
            "native_feature_key",
            "cell_id",
            name="uq_cell_source_crosswalk_release_feature_cell",
        ),
        sa.CheckConstraint(
            "native_resolution_m IS NULL OR native_resolution_m > 0",
            name=op.f("ck_cell_source_crosswalk_positive_native_resolution"),
        ),
        sa.CheckConstraint(
            "coverage_fraction > 0 AND coverage_fraction <= 1",
            name=op.f("ck_cell_source_crosswalk_valid_coverage"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_cell_source_crosswalk_geometry",
        "cell_source_crosswalk",
        ["native_geometry"],
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_cell_source_crosswalk_release_cell",
        "cell_source_crosswalk",
        ["source_release_id", "cell_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "signal_observation",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_name", sa.String(length=150), nullable=False),
        sa.Column("source_parameter", sa.String(length=150), nullable=False),
        sa.Column("support_key", sa.String(length=150), server_default="surface", nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_value", sa.Float()),
        sa.Column("original_unit", sa.String(length=64)),
        sa.Column("normalized_value", sa.Float()),
        sa.Column("normalized_unit", sa.String(length=64)),
        sa.Column("quality_flag", sa.String(length=64), server_default="accepted", nullable=False),
        sa.Column("coverage_fraction", sa.Float(), server_default="1", nullable=False),
        sa.Column("is_observed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(["cell_id"], ["agri.spatial_cell.id"]),
        sa.UniqueConstraint(
            "source_release_id",
            "cell_id",
            "signal_name",
            "source_parameter",
            "support_key",
            "observed_at",
            name="uq_signal_observation_release_cell_signal_time",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name=op.f("ck_signal_observation_ordered_valid_window"),
        ),
        sa.CheckConstraint(
            "coverage_fraction >= 0 AND coverage_fraction <= 1",
            name=op.f("ck_signal_observation_valid_coverage"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_signal_observation_cell_time_signal",
        "signal_observation",
        ["cell_id", "observed_at", "signal_name"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_signal_observation_release_time",
        "signal_observation",
        ["source_release_id", "observed_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "signal_coverage_audit",
        _uuid_pk(),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("signal_name", sa.String(length=150), nullable=False),
        sa.Column("support_key", sa.String(length=150), server_default="surface", nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_observation_count", sa.Integer(), nullable=False),
        sa.Column("received_observation_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.ForeignKeyConstraint(["cell_id"], ["agri.spatial_cell.id"]),
        sa.UniqueConstraint(
            "source_release_id",
            "cell_id",
            "signal_name",
            "support_key",
            "window_start",
            "window_end",
            name="uq_signal_coverage_release_cell_signal_window",
        ),
        sa.CheckConstraint("window_end >= window_start", name=op.f("ck_signal_coverage_ordered_window")),
        sa.CheckConstraint(
            "expected_observation_count >= 0 AND received_observation_count >= 0",
            name=op.f("ck_signal_coverage_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "received_observation_count <= expected_observation_count",
            name=op.f("ck_signal_coverage_received_within_expected"),
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'no_data', 'failed')",
            name=op.f("ck_signal_coverage_known_status"),
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND received_observation_count = expected_observation_count) OR "
            "(status = 'partial' AND received_observation_count > 0 "
            "AND received_observation_count < expected_observation_count) OR "
            "(status = 'no_data' AND received_observation_count = 0) OR "
            "(status = 'failed' AND received_observation_count < expected_observation_count)",
            name=op.f("ck_signal_coverage_status_matches_counts"),
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "source_coverage_audit",
        _uuid_pk(),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_key", sa.String(length=180), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_feature_count", sa.Integer(), nullable=False),
        sa.Column("received_feature_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.UniqueConstraint(
            "source_release_id",
            "scope_key",
            "window_start",
            "window_end",
            name="uq_source_coverage_release_scope_window",
        ),
        sa.CheckConstraint("window_end >= window_start", name=op.f("ck_source_coverage_ordered_window")),
        sa.CheckConstraint(
            "expected_feature_count >= 0 AND received_feature_count >= 0",
            name=op.f("ck_source_coverage_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "received_feature_count <= expected_feature_count",
            name=op.f("ck_source_coverage_received_within_expected"),
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'no_data', 'failed')",
            name=op.f("ck_source_coverage_known_status"),
        ),
        sa.CheckConstraint(
            "(status = 'complete' AND received_feature_count = expected_feature_count) OR "
            "(status = 'partial' AND received_feature_count > 0 "
            "AND received_feature_count < expected_feature_count) OR "
            "(status = 'no_data' AND received_feature_count = 0) OR "
            "(status = 'failed' AND received_feature_count < expected_feature_count)",
            name=op.f("ck_source_coverage_status_matches_counts"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_source_coverage_audit_release_window",
        "source_coverage_audit",
        ["source_release_id", "window_start"],
        schema=SCHEMA,
    )

    op.create_table(
        "drought_polygon_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("severity_class", sa.SmallInteger(), nullable=False),
        sa.Column("impact_type", sa.String(length=8), server_default="none", nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry("MULTIPOLYGON", srid=4326, from_text="ST_GeomFromEWKT", spatial_index=False),
            nullable=False,
        ),
        sa.Column("geometry_checksum", sa.String(length=64), nullable=False),
        sa.Column("data_available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.UniqueConstraint(
            "source_release_id",
            "severity_class",
            "impact_type",
            "geometry_checksum",
            name="uq_drought_polygon_release_identity",
        ),
        sa.CheckConstraint(
            "severity_class >= 0 AND severity_class <= 4",
            name=op.f("ck_drought_polygon_valid_severity"),
        ),
        sa.CheckConstraint(
            "geometry_checksum ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_drought_polygon_geometry_checksum_sha256"),
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_drought_polygon_snapshot_geometry",
        "drought_polygon_snapshot",
        ["geometry"],
        schema=SCHEMA,
        postgresql_using="gist",
    )
    op.create_index(
        "ix_drought_polygon_snapshot_issue_date",
        "drought_polygon_snapshot",
        ["issue_date"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION agri.v_signal_timeseries_contract(
            p_as_of_time timestamptz,
            p_release_set_id uuid
        )
        RETURNS TABLE(
            cell_id uuid,
            signal_name text,
            support_key text,
            observed_at timestamptz,
            valid_from timestamptz,
            valid_to timestamptz,
            original_value double precision,
            original_unit text,
            normalized_value double precision,
            normalized_unit text,
            quality_flag text,
            coverage_fraction double precision,
            is_observed boolean,
            source_release_id uuid,
            data_available_at timestamptz,
            transform_version text
        )
        LANGUAGE sql
        STABLE
        AS $$
            SELECT
                observation.cell_id,
                observation.signal_name,
                observation.support_key,
                observation.observed_at,
                observation.valid_from,
                observation.valid_to,
                observation.original_value,
                observation.original_unit,
                observation.normalized_value,
                observation.normalized_unit,
                observation.quality_flag,
                observation.coverage_fraction,
                observation.is_observed,
                observation.source_release_id,
                observation.data_available_at,
                source_release.transform_version
            FROM agri.signal_observation AS observation
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = observation.source_release_id
            INNER JOIN agri.release_set_item AS member
                ON member.source_release_id = source_release.id
            INNER JOIN agri.release_set AS release_set
                ON release_set.id = member.release_set_id
            WHERE release_set.id = p_release_set_id
              AND release_set.state IN ('validated', 'published')
              AND release_set.as_of_time <= p_as_of_time
              AND release_set.validated_at <= p_as_of_time
              AND source_release.validation_state = 'valid'
              AND source_release.data_available_at <= p_as_of_time
              AND observation.data_available_at <= p_as_of_time
        $$
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Historical observations are append-only; restore a verified backup to roll back.")
