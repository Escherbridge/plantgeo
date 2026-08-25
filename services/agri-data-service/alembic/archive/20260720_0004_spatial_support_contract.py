"""Expose immutable source spatial support for historical serving.

Revision ID: 20260720_0004
Revises: 20260720_0003
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "20260720_0004"
down_revision = "20260720_0003"
branch_labels = None
depends_on = None

SCHEMA = "agri"


def upgrade() -> None:
    op.add_column(
        "cell_source_crosswalk",
        sa.Column("spatial_support_kind", sa.String(length=32), server_default="unknown", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_cell_source_crosswalk_known_spatial_support_kind",
        "cell_source_crosswalk",
        "spatial_support_kind IN ('native_grid_cell', 'native_polygon', 'point_sample', 'area_aggregate', 'unknown')",
        schema=SCHEMA,
    )
    op.alter_column("cell_source_crosswalk", "spatial_support_kind", server_default=None, schema=SCHEMA)

    op.execute("DROP FUNCTION agri.v_signal_timeseries_contract(timestamptz, uuid)")
    op.execute(
        """
        CREATE FUNCTION agri.v_signal_timeseries_contract(
            p_as_of_time timestamptz,
            p_release_set_id uuid
        )
        RETURNS TABLE(
            cell_id uuid,
            signal_name text,
            source_parameter text,
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
            transform_version text,
            grid_name text,
            analysis_resolution_m integer,
            spatial_support_kind text,
            native_resolution_m integer,
            mapping_method text,
            mapping_coverage_fraction double precision,
            is_acre_scale_compatible boolean
        )
        LANGUAGE sql
        STABLE
        AS $$
            SELECT
                observation.cell_id,
                observation.signal_name,
                observation.source_parameter,
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
                source_release.transform_version,
                spatial_cell.grid_name,
                spatial_cell.resolution_m,
                source_support.spatial_support_kind,
                source_support.native_resolution_m,
                source_support.mapping_method,
                source_support.coverage_fraction,
                source_support.spatial_support_kind = 'native_grid_cell'
                    AND source_support.native_resolution_m <= 64
                    AND source_support.coverage_fraction = 1
                    AND observation.coverage_fraction = 1 AS is_acre_scale_compatible
            FROM agri.signal_observation AS observation
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = observation.source_release_id
            INNER JOIN agri.spatial_cell AS spatial_cell
                ON spatial_cell.id = observation.cell_id
            INNER JOIN LATERAL (
                SELECT
                    count(*)::integer AS crosswalk_count,
                    min(crosswalk.spatial_support_kind) AS spatial_support_kind,
                    min(crosswalk.native_resolution_m) AS native_resolution_m,
                    min(crosswalk.mapping_method) AS mapping_method,
                    min(crosswalk.coverage_fraction) AS coverage_fraction
                FROM agri.cell_source_crosswalk AS crosswalk
                WHERE crosswalk.source_release_id = observation.source_release_id
                  AND crosswalk.cell_id = observation.cell_id
            ) AS source_support
                ON source_support.crosswalk_count = 1
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
