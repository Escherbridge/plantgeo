"""Add the evaluation-only assignment-time covariate/feature layer.

Revision ID: 20260802_0016
Revises: 20260802_0015
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260802_0016"
down_revision = "20260802_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Body-only rewrite: hoist the per-day ST_Intersects out of the LATERAL into one
    # materialized admissible-polygon set, so a four-year window costs one spatial pass
    # instead of one per day. The outer `CROSS JOIN cell` is retained, so an unknown
    # p_cell_id still returns zero rows rather than a fabricated all-NULL day spine.
    # Preserved: the row set, the day spine, the issue-date/severity/geometry_checksum
    # tie-break order, and both availability gates. Not a refactor guaranteed by
    # construction -- it is guarded by the 0011 contract test, which this revision
    # extends to cover the checksum tie-break, a non-intersecting polygon, and an
    # unknown cell (the three paths the hoist actually touches).
    op.execute(load_object_sql("functions/drought_class_daily_series.sql", or_replace=True))

    op.execute(load_object_sql("functions/covariate_feature_schema.sql"))
    op.execute(load_object_sql("functions/covariate_lookback_days.sql"))
    op.execute(load_object_sql("functions/covariate_declared_gap.sql"))
    op.execute(load_object_sql("functions/covariate_daily_features.sql"))
    op.execute(load_object_sql("functions/covariate_vector_manifest.sql"))
    op.execute(
        r"""
        REVOKE EXECUTE ON FUNCTION agri.covariate_feature_schema(varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.covariate_lookback_days(varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.covariate_declared_gap(varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.covariate_daily_features(
            uuid, timestamptz, timestamptz, timestamptz, varchar
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.covariate_vector_manifest(
            uuid, timestamptz, timestamptz, timestamptz, varchar
        ) FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Evaluation-only covariate read functions carry no reversible state; "
        "restore a verified backup into a fresh database."
    )
