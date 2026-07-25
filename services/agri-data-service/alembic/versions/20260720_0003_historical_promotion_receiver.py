"""Add resumable typed historical-promotion receipts.

Revision ID: 20260720_0003
Revises: 20260720_0002
Create Date: 2026-07-20
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260720_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None

SCHEMA = "agri"


def upgrade() -> None:
    op.add_column(
        "signal_coverage_audit",
        sa.Column("source_parameter", sa.String(length=150), server_default="legacy-unspecified", nullable=False),
        schema=SCHEMA,
    )
    op.execute(
        """
        UPDATE agri.signal_coverage_audit
        SET source_parameter = COALESCE(NULLIF(details ->> 'source_parameter', ''), 'legacy-unspecified')
        """
    )
    op.alter_column("signal_coverage_audit", "source_parameter", server_default=None, schema=SCHEMA)
    op.drop_constraint(
        "uq_signal_coverage_release_cell_signal_window",
        "signal_coverage_audit",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_signal_coverage_release_cell_signal_parameter_window",
        "signal_coverage_audit",
        [
            "source_release_id",
            "cell_id",
            "signal_name",
            "source_parameter",
            "support_key",
            "window_start",
            "window_end",
        ],
        schema=SCHEMA,
    )

    op.create_table(
        "historical_promotion_bundle",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manifest_checksum", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("release_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="receiving", nullable=False),
        sa.Column("expected_chunk_count", sa.Integer(), nullable=False),
        sa.Column("expected_record_count", sa.Integer(), nullable=False),
        sa.Column("received_by", sa.String(length=255), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "manifest_checksum ~ '^[0-9a-f]{64}$'",
            name="historical_promotion_manifest_checksum_sha256",
        ),
        sa.CheckConstraint("state IN ('receiving', 'finalized')", name="historical_promotion_known_state"),
        sa.CheckConstraint("expected_chunk_count > 0", name="historical_promotion_positive_chunk_count"),
        sa.CheckConstraint("expected_record_count > 0", name="historical_promotion_positive_record_count"),
        sa.ForeignKeyConstraint(["release_set_id"], ["agri.release_set.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_checksum"),
        sa.UniqueConstraint("release_set_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "historical_promotion_chunk_receipt",
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("sequence > 0", name="historical_chunk_positive_sequence"),
        sa.CheckConstraint("payload_sha256 ~ '^[0-9a-f]{64}$'", name="historical_chunk_checksum_sha256"),
        sa.CheckConstraint("payload_bytes > 0", name="historical_chunk_positive_bytes"),
        sa.CheckConstraint("record_count > 0", name="historical_chunk_positive_record_count"),
        sa.ForeignKeyConstraint(["bundle_id"], ["agri.historical_promotion_bundle.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bundle_id", "sequence"),
        schema=SCHEMA,
    )
    op.create_table(
        "historical_promotion_artifact_receipt",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uri", sa.String(length=2000), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("checksum_sha256 ~ '^[0-9a-f]{64}$'", name="historical_artifact_checksum_sha256"),
        sa.CheckConstraint("size_bytes > 0", name="historical_artifact_positive_bytes"),
        sa.ForeignKeyConstraint(["artifact_id"], ["agri.artifact.id"]),
        sa.ForeignKeyConstraint(["bundle_id"], ["agri.historical_promotion_bundle.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", "source_release_id", name="uq_historical_artifact_receipt_bundle_release"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_historical_artifact_receipt_bundle_pending",
        "historical_promotion_artifact_receipt",
        ["bundle_id", "uploaded_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "historical_promotion_data_source_receipt",
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["agri.historical_promotion_bundle.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("bundle_id", "source_key"),
        schema=SCHEMA,
    )
    op.create_table(
        "historical_promotion_source_release_receipt",
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["agri.historical_promotion_bundle.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_release_id"], ["agri.source_release.id"]),
        sa.PrimaryKeyConstraint("bundle_id", "source_release_id"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("historical_promotion_source_release_receipt", schema=SCHEMA)
    op.drop_table("historical_promotion_data_source_receipt", schema=SCHEMA)
    op.drop_index(
        "ix_historical_artifact_receipt_bundle_pending",
        "historical_promotion_artifact_receipt",
        schema=SCHEMA,
    )
    op.drop_table("historical_promotion_artifact_receipt", schema=SCHEMA)
    op.drop_table("historical_promotion_chunk_receipt", schema=SCHEMA)
    op.drop_table("historical_promotion_bundle", schema=SCHEMA)
    op.drop_constraint(
        "uq_signal_coverage_release_cell_signal_parameter_window",
        "signal_coverage_audit",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_signal_coverage_release_cell_signal_window",
        "signal_coverage_audit",
        ["source_release_id", "cell_id", "signal_name", "support_key", "window_start", "window_end"],
        schema=SCHEMA,
    )
    op.drop_column("signal_coverage_audit", "source_parameter", schema=SCHEMA)
