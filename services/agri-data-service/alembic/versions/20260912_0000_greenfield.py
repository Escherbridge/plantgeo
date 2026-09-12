"""Create the current relational control and lookup schema.

Revision ID: 20260912_0000
Revises: none
"""

from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS
from alembic import op

revision: str = "20260912_0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASELINE_PATH = Path(__file__).resolve().parents[2] / "db" / "agri_baseline.sql"
_REQUIRED = ", ".join(f"'{name}'" for name in REQUIRED_EXTENSIONS)


def upgrade() -> None:
    connection = op.get_bind()
    missing = connection.execute(
        sa.text(
            "SELECT array_agg(required.name ORDER BY required.name) "
            "FROM unnest(ARRAY[" + _REQUIRED + "]) AS required(name) "
            "LEFT JOIN pg_extension installed ON installed.extname = required.name "
            "WHERE installed.extname IS NULL"
        )
    ).scalar_one()
    if missing:
        raise RuntimeError(f"missing required PostgreSQL extensions: {', '.join(missing)}")
    op.execute(sa.text(_BASELINE_PATH.read_text(encoding="utf-8-sig")))
    op.execute("REVOKE CREATE ON SCHEMA agri FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM PUBLIC")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA agri FROM PUBLIC")


def downgrade() -> None:
    raise NotImplementedError("Restore a database backup instead of reversing the baseline.")
