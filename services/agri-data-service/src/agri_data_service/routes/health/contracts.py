"""Extensions and migration revision required by the readiness probe."""

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS

# Keep this equal to the sole Alembic head; readiness requires exact equality.
EXPECTED_ALEMBIC_REVISION = "20260912_0000"

__all__ = ["EXPECTED_ALEMBIC_REVISION", "REQUIRED_EXTENSIONS"]
