"""Prevent hindcast uncertainty calibration from crossing its simulated cutoff.

Revision ID: 20260722_0007
Revises: 20260722_0006
"""

from collections.abc import Sequence

from alembic import op

revision = "20260722_0007"
down_revision = "20260722_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
        ADD CONSTRAINT ck_forecast_hindcast_run_calibration_horizon
        CHECK (
            uncertainty_calibration_cutoff_time + step_interval * horizon_steps
                <= simulated_cutoff_time
        );
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Hindcast leakage guards are forward-only; restore a verified backup into a fresh database."
    )
