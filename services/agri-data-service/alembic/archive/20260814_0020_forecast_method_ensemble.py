"""Widen forecast_method and model_kind CHECK constraints to support NWP ensemble forecasts.

Revision ID: 20260814_0020
Revises: 20260808_0019
"""

from collections.abc import Sequence

from alembic import op

revision = "20260814_0020"
down_revision = "20260808_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Widen ForecastModel model_kind constraint to accept 'ensemble'
    op.drop_constraint("kind", "forecast_model", schema="agri", type_="check")
    op.create_check_constraint(
        "kind",
        "forecast_model",
        "model_kind IN ('sql_linear', 'ml', 'ensemble')",
        schema="agri",
    )

    # Widen ForecastRun forecast_method constraint to accept 'ensemble'
    op.drop_constraint("method", "forecast_run", schema="agri", type_="check")
    op.create_check_constraint(
        "method",
        "forecast_run",
        "forecast_method IN ('sql_linear', 'ml', 'ensemble')",
        schema="agri",
    )

    # Update model_binding constraint to permit ensemble forecast runs without a training run
    op.drop_constraint("model_binding", "forecast_run", schema="agri", type_="check")
    op.create_check_constraint(
        "model_binding",
        "forecast_run",
        "(forecast_method = 'sql_linear' AND training_run_id IS NULL) OR "
        "(forecast_method = 'ml' AND training_run_id IS NOT NULL) OR "
        "(forecast_method = 'ensemble' AND training_run_id IS NULL)",
        schema="agri",
    )


def downgrade() -> None:
    op.drop_constraint("model_binding", "forecast_run", schema="agri", type_="check")
    op.create_check_constraint(
        "model_binding",
        "forecast_run",
        "(forecast_method = 'sql_linear' AND training_run_id IS NULL) OR "
        "(forecast_method = 'ml' AND training_run_id IS NOT NULL)",
        schema="agri",
    )

    op.drop_constraint("method", "forecast_run", schema="agri", type_="check")
    op.create_check_constraint(
        "method",
        "forecast_run",
        "forecast_method IN ('sql_linear', 'ml')",
        schema="agri",
    )

    op.drop_constraint("kind", "forecast_model", schema="agri", type_="check")
    op.create_check_constraint(
        "kind",
        "forecast_model",
        "model_kind IN ('sql_linear', 'ml')",
        schema="agri",
    )
