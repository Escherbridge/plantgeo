"""Add evaluation-only iteration-outcome and drought-class daily read functions.

Revision ID: 20260725_0011
Revises: 20260723_0010
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260725_0011"
down_revision = "20260723_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(load_object_sql("functions/forecast_iteration_evaluation.sql"))
    op.execute(load_object_sql("functions/drought_class_daily_series.sql"))
    op.execute(
        r"""
        REVOKE EXECUTE ON FUNCTION agri.forecast_iteration_evaluation(
            uuid, timestamptz
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.drought_class_daily_series(
            uuid, timestamptz, timestamptz, timestamptz
        ) FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Evaluation-only read functions carry no reversible state; restore a verified backup into a fresh database."
    )
