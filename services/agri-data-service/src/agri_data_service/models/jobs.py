"""PostgreSQL-backed durable execution ledger."""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, TimestampMixin, UUIDMixin


def _enum_type(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class JobRunState(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class WorkItemState(enum.StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    DEFERRED = "deferred"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class AttemptState(enum.StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"


class OutputState(enum.StrEnum):
    STAGED = "staged"
    VALIDATED = "validated"
    REJECTED = "rejected"
    PUBLISHED = "published"
    SUPERSEDED = "superseded"


class OutboxState(enum.StrEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    RETRY_WAIT = "retry_wait"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"


class EventSeverity(enum.StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class IncidentState(enum.StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class JobDefinition(Base, UUIDMixin, TimestampMixin):
    """Versioned executable job policy."""

    __tablename__ = "job_definition"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    handler: Mapped[str] = mapped_column(String(500), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(100), nullable=False, server_default="default")
    schedule: Mapped[str | None] = mapped_column(String(255))
    schedule_timezone: Mapped[str] = mapped_column(String(100), nullable=False, server_default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    concurrency_key: Mapped[str | None] = mapped_column(String(255))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    lease_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="300")
    time_budget_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="240")
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_job_definition_name_version"),
        CheckConstraint("max_attempts > 0", name="positive_max_attempts"),
        CheckConstraint("lease_seconds > 0", name="positive_lease_seconds"),
        CheckConstraint("time_budget_seconds > 0", name="positive_time_budget_seconds"),
    )


class JobRun(Base, UUIDMixin, TimestampMixin):
    """Logical scheduled run with pinned inputs."""

    __tablename__ = "job_run"

    job_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_definition.id"), nullable=False
    )
    release_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.release_set.id"))
    logical_run_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recipe_version: Mapped[str | None] = mapped_column(String(100))
    model_version: Mapped[str | None] = mapped_column(String(100))
    target_partitions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[JobRunState] = mapped_column(
        _enum_type(JobRunState, "job_run_state"), nullable=False, server_default="queued"
    )
    requested_by: Mapped[str | None] = mapped_column(String(255))
    total_work_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    succeeded_work_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed_work_items: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    last_error_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("total_work_items >= 0", name="nonnegative_total_work_items"),
        CheckConstraint("succeeded_work_items >= 0", name="nonnegative_succeeded_work_items"),
        CheckConstraint("failed_work_items >= 0", name="nonnegative_failed_work_items"),
        CheckConstraint(
            "succeeded_work_items + failed_work_items <= total_work_items",
            name="work_item_counts_within_total",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'partial', 'failed', 'dead_letter', 'cancelled') OR completed_at IS NOT NULL",
            name="terminal_run_has_completion_time",
        ),
        Index("ix_job_run_status_scheduled", "status", "scheduled_for"),
    )


class JobDependency(Base, UUIDMixin):
    """Directed dependency between logical runs."""

    __tablename__ = "job_dependency"

    job_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_run.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_run.id"), nullable=False
    )
    required_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="succeeded")
    satisfied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("job_run_id", "depends_on_run_id", name="uq_job_dependency_edge"),
        CheckConstraint("job_run_id <> depends_on_run_id", name="dependency_not_self"),
        CheckConstraint("required_status IN ('succeeded', 'partial')", name="dependency_required_status"),
    )


class JobWorkItem(Base, UUIDMixin, TimestampMixin):
    """Bounded resumable shard claimed with a fenced lease."""

    __tablename__ = "job_work_item"

    job_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_run.id", ondelete="CASCADE"), nullable=False
    )
    shard_key: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[WorkItemState] = mapped_column(
        _enum_type(WorkItemState, "work_item_state"), nullable=False, server_default="queued"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress_fraction: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    checkpoint_sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_class: Mapped[str | None] = mapped_column(String(255))
    last_error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("job_run_id", "shard_key", name="uq_job_work_item_run_shard"),
        UniqueConstraint("id", "job_run_id", name="uq_job_work_item_run_identity"),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempt_count"),
        CheckConstraint("max_attempts > 0", name="positive_work_item_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="attempt_count_within_limit"),
        CheckConstraint("fencing_token >= 0", name="nonnegative_fencing_token"),
        CheckConstraint("checkpoint_sequence >= 0", name="nonnegative_checkpoint_sequence"),
        CheckConstraint("progress_fraction >= 0 AND progress_fraction <= 1", name="progress_fraction_range"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="complete_lease_pair",
        ),
        CheckConstraint(
            "status NOT IN ('leased', 'running') "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND fencing_token > 0)",
            name="active_item_has_fenced_lease",
        ),
        CheckConstraint(
            "status NOT IN ('retry_wait', 'deferred') OR next_attempt_at IS NOT NULL",
            name="resumable_item_has_next_attempt",
        ),
        CheckConstraint(
            "status NOT IN ('succeeded', 'dead_letter', 'cancelled') OR completed_at IS NOT NULL",
            name="terminal_item_has_completion_time",
        ),
        Index("ix_job_work_item_claim", "status", "next_attempt_at", "available_at", "priority"),
        Index("ix_job_work_item_lease_expiry", "lease_expires_at"),
    )


class JobAttempt(Base, UUIDMixin):
    """Immutable history for one fenced worker attempt."""

    __tablename__ = "job_attempt"

    job_work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_work_item.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[AttemptState] = mapped_column(
        _enum_type(AttemptState, "attempt_state"), nullable=False, server_default="running"
    )
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_class: Mapped[str | None] = mapped_column(String(255))
    error_summary: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        UniqueConstraint("job_work_item_id", "attempt_number", name="uq_job_attempt_item_number"),
        UniqueConstraint("job_work_item_id", "fencing_token", name="uq_job_attempt_item_fence"),
        UniqueConstraint(
            "id",
            "job_work_item_id",
            "fencing_token",
            name="uq_job_attempt_checkpoint_fence",
        ),
        CheckConstraint("attempt_number > 0", name="positive_attempt_number"),
        CheckConstraint("fencing_token > 0", name="positive_attempt_fencing_token"),
        CheckConstraint("status = 'running' OR finished_at IS NOT NULL", name="terminal_attempt_has_finish_time"),
    )


class JobCheckpoint(Base, UUIDMixin):
    """Append-only resumable cursor written under a fencing token."""

    __tablename__ = "job_checkpoint"

    job_work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_work_item.id", ondelete="CASCADE"), nullable=False
    )
    job_attempt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cursor_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    progress_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("job_work_item_id", "sequence", name="uq_job_checkpoint_item_sequence"),
        ForeignKeyConstraint(
            ["job_attempt_id", "job_work_item_id", "fencing_token"],
            [
                "agri.job_attempt.id",
                "agri.job_attempt.job_work_item_id",
                "agri.job_attempt.fencing_token",
            ],
            name="fk_job_checkpoint_attempt_fence",
        ),
        CheckConstraint("sequence > 0", name="positive_checkpoint_sequence"),
        CheckConstraint("fencing_token > 0", name="positive_checkpoint_fencing_token"),
        CheckConstraint(
            "progress_fraction >= 0 AND progress_fraction <= 1",
            name="checkpoint_progress_fraction_range",
        ),
    )


class JobOutput(Base, UUIDMixin):
    """Immutable staged or published work output."""

    __tablename__ = "job_output"

    job_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_run.id", ondelete="CASCADE"), nullable=False
    )
    job_work_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    job_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.artifact.id"), index=True
    )
    output_key: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[OutputState] = mapped_column(
        _enum_type(OutputState, "output_state"), nullable=False, server_default="staged"
    )
    uri: Mapped[str | None] = mapped_column(String(2000))
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("job_run_id", "output_key", name="uq_job_output_run_key"),
        ForeignKeyConstraint(
            ["job_work_item_id", "job_run_id"],
            ["agri.job_work_item.id", "agri.job_work_item.job_run_id"],
            name="fk_job_output_work_item_run",
        ),
        ForeignKeyConstraint(
            ["job_attempt_id", "job_work_item_id", "fencing_token"],
            [
                "agri.job_attempt.id",
                "agri.job_attempt.job_work_item_id",
                "agri.job_attempt.fencing_token",
            ],
            name="fk_job_output_attempt_fence",
        ),
        CheckConstraint("row_count IS NULL OR row_count >= 0", name="nonnegative_output_rows"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="nonnegative_output_size"),
        CheckConstraint(
            "state NOT IN ('validated', 'published') OR validated_at IS NOT NULL",
            name="validated_output_has_timestamp",
        ),
        CheckConstraint(
            "(job_work_item_id IS NULL AND job_attempt_id IS NULL AND fencing_token IS NULL) "
            "OR (job_work_item_id IS NOT NULL AND job_attempt_id IS NOT NULL "
            "AND fencing_token IS NOT NULL AND fencing_token > 0)",
            name="work_output_has_attempt_fence",
        ),
    )


class PublicationPointer(Base, UUIDMixin, TimestampMixin):
    """Atomic pointer to the current validated output."""

    __tablename__ = "publication_pointer"

    product: Mapped[str] = mapped_column(String(150), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(500), nullable=False)
    job_output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id"), nullable=False
    )
    previous_job_output_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id")
    )
    release_set_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.release_set.id"))
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    published_by: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        UniqueConstraint("product", "scope_key", name="uq_publication_pointer_product_scope"),
        CheckConstraint("revision > 0", name="positive_publication_revision"),
        CheckConstraint(
            "previous_job_output_id IS NULL OR previous_job_output_id <> job_output_id",
            name="publication_previous_differs",
        ),
    )


class JobOutbox(Base, UUIDMixin, TimestampMixin):
    """Transactional delivery record for dispatch and publication events."""

    __tablename__ = "job_outbox"

    event_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxState] = mapped_column(
        _enum_type(OutboxState, "outbox_state"), nullable=False, server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_summary: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="nonnegative_outbox_attempt_count"),
        CheckConstraint("max_attempts > 0", name="positive_outbox_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="outbox_attempt_count_within_limit"),
        CheckConstraint("fencing_token >= 0", name="nonnegative_outbox_fencing_token"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="complete_outbox_lease_pair",
        ),
        CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL",
            name="delivered_outbox_has_timestamp",
        ),
        CheckConstraint(
            "status <> 'publishing' "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL AND fencing_token > 0)",
            name="publishing_outbox_has_fenced_lease",
        ),
        Index("ix_job_outbox_dispatch", "status", "next_attempt_at"),
    )


class JobEvent(Base):
    """Redacted structured operational event retained for 30 days."""

    __tablename__ = "job_event"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False, server_default=func.now()
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_run.id", ondelete="CASCADE")
    )
    job_work_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_work_item.id", ondelete="CASCADE")
    )
    job_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_attempt.id", ondelete="CASCADE")
    )
    severity: Mapped[EventSeverity] = mapped_column(_enum_type(EventSeverity, "event_severity"), nullable=False)
    event_code: Mapped[str] = mapped_column(String(150), nullable=False)
    environment: Mapped[str] = mapped_column(String(100), nullable=False)
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(255))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="nonnegative_event_duration"),
        Index("ix_job_event_run_occurred", "job_run_id", "occurred_at"),
        Index("ix_job_event_severity_occurred", "severity", "occurred_at"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


class JobIncident(Base, UUIDMixin, TimestampMixin):
    """Deduplicated operational alert lifecycle."""

    __tablename__ = "job_incident"

    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    incident_type: Mapped[str] = mapped_column(String(150), nullable=False)
    severity: Mapped[EventSeverity] = mapped_column(_enum_type(EventSeverity, "incident_severity"), nullable=False)
    status: Mapped[IncidentState] = mapped_column(
        _enum_type(IncidentState, "incident_state"), nullable=False, server_default="open"
    )
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_run.id"))
    job_work_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_work_item.id"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner: Mapped[str | None] = mapped_column(String(255))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint("occurrence_count > 0", name="positive_incident_occurrence_count"),
        CheckConstraint("last_seen_at >= first_seen_at", name="ordered_incident_seen_window"),
        CheckConstraint(
            "status <> 'acknowledged' OR acknowledged_at IS NOT NULL",
            name="acknowledged_incident_has_timestamp",
        ),
        CheckConstraint(
            "status <> 'resolved' OR resolved_at IS NOT NULL",
            name="resolved_incident_has_timestamp",
        ),
        Index("ix_job_incident_status_severity", "status", "severity"),
    )
