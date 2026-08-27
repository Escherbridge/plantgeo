"""Add the durable per-day vegetation publication queue.

Revision ID: 20260827_0027
Revises: 20260825_0000

The baseline executes the current declarative tree, so every DDL statement is idempotent when the
tree already contains this revision's objects. Existing governed days are enrolled lazily by the
defensive scan and exact repair, without deleting or rewriting any PostgreSQL observation.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_0027"
down_revision: str | None = "20260825_0000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agri.vegetation_publication_day (
    observed_day date PRIMARY KEY,
    source_fingerprint varchar(64) NOT NULL,
    published_fingerprint varchar(64),
    first_enqueued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_enqueued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_attempted_at timestamptz,
    published_at timestamptz,
    attempt_count integer NOT NULL DEFAULT 0,
    last_error text,
    CONSTRAINT ck_vegetation_publication_day_source_fingerprint
        CHECK (source_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_vegetation_publication_day_published_fingerprint
        CHECK (published_fingerprint IS NULL OR published_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_vegetation_publication_day_attempt_count CHECK (attempt_count >= 0)
)
"""

def upgrade() -> None:
    op.execute(_CREATE_SQL)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vegetation_publication_day_pending "
        "ON agri.vegetation_publication_day(last_attempted_at, first_enqueued_at, observed_day) "
        "WHERE published_fingerprint IS DISTINCT FROM source_fingerprint"
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Vegetation publication fingerprints and attempts are operational evidence. Restore a verified backup "
        "and deploy the prior application version instead of deleting them."
    )
