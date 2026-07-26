"""ORM mappings for the SQL-first forecasting framework."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from agri_data_service.db.base import Base, UUIDMixin


class ForecastSeries(Base, UUIDMixin):
    """Stable identity for one source, entity, metric, and support contract."""

    __tablename__ = "forecast_series"

    series_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    source_variant_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input_adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.data_source.id"), nullable=False
    )
    signal_name: Mapped[str | None] = mapped_column(String(150))
    source_parameter: Mapped[str | None] = mapped_column(String(150))
    support_key: Mapped[str | None] = mapped_column(String(150))
    source_transform_version: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(150), nullable=False)
    metric_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    spatial_cell_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.spatial_cell.id"))
    representation_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    spatial_support_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_spatial_resolution_m: Mapped[int | None] = mapped_column(Integer)
    output_spatial_resolution_m: Mapped[int | None] = mapped_column(Integer)
    source_temporal_support: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    output_temporal_support: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    aggregation_method: Mapped[str | None] = mapped_column(String(100))
    allow_ml_daily_aggregate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "representation_kind IN ('raw_native', 'resampled', 'aggregate')",
            name="representation_kind",
        ),
        CheckConstraint(
            "input_adapter IN ('signal_observation', 'forecast_observation')",
            name="input_adapter",
        ),
        CheckConstraint(
            "input_adapter <> 'signal_observation' OR "
            "(signal_name IS NOT NULL AND source_parameter IS NOT NULL "
            "AND support_key IS NOT NULL AND spatial_cell_id IS NOT NULL)",
            name="signal_adapter_identity",
        ),
        CheckConstraint(
            "spatial_support_kind IN "
            "('native_grid_cell', 'native_polygon', 'point_sample', 'area_aggregate', 'unknown')",
            name="spatial_support_kind",
        ),
        CheckConstraint(
            "(source_spatial_resolution_m IS NULL OR source_spatial_resolution_m > 0) AND "
            "(output_spatial_resolution_m IS NULL OR output_spatial_resolution_m > 0)",
            name="positive_resolutions",
        ),
        CheckConstraint(
            "source_temporal_support > INTERVAL '0' AND output_temporal_support > INTERVAL '0'",
            name="positive_temporal_support",
        ),
        CheckConstraint(
            "(representation_kind = 'raw_native' AND aggregation_method IS NULL) OR "
            "(representation_kind IN ('resampled', 'aggregate') AND aggregation_method IS NOT NULL)",
            name="aggregate_method",
        ),
        Index("ix_forecast_series_entity_metric", "entity_type", "entity_key", "metric_name"),
        Index(
            "ix_forecast_series_spatial_cell",
            "spatial_cell_id",
            postgresql_where=text("spatial_cell_id IS NOT NULL"),
        ),
        Index(
            "uq_forecast_series_exact_source_identity",
            "data_source_id",
            "source_transform_version",
            "input_adapter",
            "signal_name",
            "source_parameter",
            "support_key",
            "spatial_cell_id",
            "entity_type",
            "entity_key",
            "metric_name",
            "metric_unit",
            "representation_kind",
            "spatial_support_kind",
            "source_temporal_support",
            "output_temporal_support",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


class ForecastEntityState(Base, UUIDMixin):
    """Immutable version of entity state applicable to a forecast series."""

    __tablename__ = "forecast_entity_state"

    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    state_key: Mapped[str] = mapped_column(String(255), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    state_geometry = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False))
    state_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("series_id", "state_key", "state_checksum", name="uq_forecast_entity_state_identity"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ordered_window"),
        CheckConstraint("state_checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        Index("ix_forecast_entity_state_series_window", "series_id", "valid_from", "valid_to"),
        Index(
            "ix_forecast_entity_state_geometry",
            "state_geometry",
            postgresql_using="gist",
            postgresql_where=text("state_geometry IS NOT NULL"),
        ),
    )


class ForecastObservation(Base):
    """Append-only generic observation with source-release and state lineage."""

    __tablename__ = "forecast_observation"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    entity_state_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_entity_state.id")
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    quality_flag: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'accepted'"))
    source_event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    observation_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "source_release_id", "series_id", "source_event_key", name="uq_forecast_observation_source_event"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ordered_window",
        ),
        CheckConstraint("observation_checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        CheckConstraint(
            "metric_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="finite_value",
        ),
        Index("ix_forecast_observation_series_time", "series_id", "observed_at"),
        Index("ix_forecast_observation_release_available", "source_release_id", "data_available_at"),
    )


class ForecastFeatureSnapshot(Base, UUIDMixin):
    """Checksum-bound feature matrix pinned to one local job and release set."""

    __tablename__ = "forecast_feature_snapshot"

    snapshot_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    job_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_run.id"), nullable=False)
    job_output_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_output.id"))
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_recipe_version: Mapped[str] = mapped_column(String(100), nullable=False)
    feature_code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    training_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'draft'"))
    validation_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "input_release_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_code_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_checksum ~ '^[0-9a-f]{64}$'",
            name="checksums",
        ),
        CheckConstraint("training_window_end >= training_window_start", name="ordered_window"),
        CheckConstraint("row_count > 0", name="positive_rows"),
        CheckConstraint("status IN ('draft', 'validated', 'rejected')", name="status"),
        CheckConstraint(
            "status <> 'validated' OR validated_at IS NOT NULL",
            name="validated_at",
        ),
    )


class ForecastModel(Base, UUIDMixin):
    """Versioned SQL baseline or gated locally trained model identity."""

    __tablename__ = "forecast_model"

    model_key: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    model_purpose: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'metric_forecast'"))
    algorithm: Mapped[str] = mapped_column(String(150), nullable=False)
    model_code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.artifact.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("model_key", "model_version", name="uq_forecast_model_version"),
        CheckConstraint("model_kind IN ('sql_linear', 'ml')", name="kind"),
        CheckConstraint("model_kind <> 'ml' OR artifact_id IS NOT NULL", name="ml_artifact"),
        CheckConstraint("model_purpose IN ('metric_forecast', 'strategy_selection')", name="purpose"),
        CheckConstraint("model_code_checksum ~ '^[0-9a-f]{64}$'", name="code_checksum_sha256"),
    )


class ForecastTrainingRun(Base, UUIDMixin):
    """Local-only training receipt that remains gated until validated."""

    __tablename__ = "forecast_training_run"

    training_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_model.id"), nullable=False
    )
    job_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_run.id"), nullable=False)
    job_output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id"), nullable=False
    )
    feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_feature_snapshot.id"), nullable=False
    )
    strategy_label_release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.strategy_label_release.id")
    )
    strategy_label_checksum: Mapped[str | None] = mapped_column(String(64))
    execution_mode: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'local'"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'gated'"))
    input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    training_code_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_checksum: Mapped[str | None] = mapped_column(String(64))
    validation_checksum: Mapped[str | None] = mapped_column(String(64))
    validation_metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("execution_mode = 'local'", name="execution_mode"),
        CheckConstraint("status IN ('gated', 'running', 'validated', 'rejected')", name="status"),
        CheckConstraint(
            "input_release_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_checksum ~ '^[0-9a-f]{64}$' "
            "AND training_code_checksum ~ '^[0-9a-f]{64}$'",
            name="input_checksums",
        ),
        CheckConstraint(
            "status <> 'validated' OR (completed_at IS NOT NULL AND validated_at IS NOT NULL "
            "AND model_checksum ~ '^[0-9a-f]{64}$' AND validation_checksum ~ '^[0-9a-f]{64}$')",
            name="validated_evidence",
        ),
        CheckConstraint(
            "(strategy_label_release_id IS NULL AND strategy_label_checksum IS NULL) "
            "OR (strategy_label_release_id IS NOT NULL "
            "AND strategy_label_checksum ~ '^[0-9a-f]{64}$')",
            name="strategy_label_binding",
        ),
    )


class ForecastQualityPolicy(Base, UUIDMixin):
    """Versioned thresholds required before a forecast run can be validated."""

    __tablename__ = "forecast_quality_policy"

    policy_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    min_training_points: Mapped[int] = mapped_column(Integer, nullable=False)
    min_backtest_points: Mapped[int] = mapped_column(Integer, nullable=False)
    min_coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    max_mae: Mapped[float | None] = mapped_column(Float)
    max_rmse: Mapped[float | None] = mapped_column(Float)
    max_mape: Mapped[float | None] = mapped_column(Float)
    min_skill_score: Mapped[float | None] = mapped_column(Float)
    required_quantiles: Mapped[list[float]] = mapped_column(
        ARRAY(Float), nullable=False, server_default=text("ARRAY[0.1, 0.5, 0.9]::float8[]")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "min_training_points >= 3 AND min_backtest_points > 0",
            name="positive_counts",
        ),
        CheckConstraint(
            "min_coverage_fraction > 0 AND min_coverage_fraction <= 1",
            name="coverage",
        ),
        CheckConstraint(
            "(max_mae IS NULL OR max_mae >= 0) AND (max_rmse IS NULL OR max_rmse >= 0) "
            "AND (max_mape IS NULL OR max_mape >= 0) "
            "AND (min_skill_score IS NULL OR min_skill_score <= 1)",
            name="nonnegative_limits",
        ),
        CheckConstraint(
            "agri.forecast_quantiles_valid(required_quantiles) AND 0.5 = ANY(required_quantiles)",
            name="quantiles",
        ),
    )


class ForecastRun(Base, UUIDMixin):
    """One checksum-bound local forecast computation."""

    __tablename__ = "forecast_run"

    run_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    job_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_run.id"), nullable=False)
    feature_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_feature_snapshot.id"), nullable=False
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_model.id"), nullable=False
    )
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_training_run.id")
    )
    quality_policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_quality_policy.id"), nullable=False
    )
    forecast_method: Mapped[str] = mapped_column(String(24), nullable=False)
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    step_interval: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staged'"))
    backtest_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("forecast_method IN ('sql_linear', 'ml')", name="method"),
        CheckConstraint(
            "(forecast_method = 'sql_linear' AND training_run_id IS NULL) OR "
            "(forecast_method = 'ml' AND training_run_id IS NOT NULL)",
            name="model_binding",
        ),
        CheckConstraint("valid_to > valid_from", name="ordered_window"),
        CheckConstraint("horizon_steps > 0 AND step_interval > INTERVAL '0'", name="positive_horizon"),
        CheckConstraint(
            "input_release_checksum ~ '^[0-9a-f]{64}$' "
            "AND feature_checksum ~ '^[0-9a-f]{64}$' "
            "AND model_checksum ~ '^[0-9a-f]{64}$' "
            "AND parameter_checksum ~ '^[0-9a-f]{64}$'",
            name="checksums",
        ),
        CheckConstraint("status IN ('staged', 'validated', 'rejected')", name="status"),
        CheckConstraint(
            "status <> 'validated' OR (backtest_passed AND validated_at IS NOT NULL)",
            name="validated_evidence",
        ),
        Index("ix_forecast_run_issue_time", text("issue_time DESC")),
    )


class ForecastBacktestMetric(Base, UUIDMixin):
    """Stored holdout metrics evaluated against a forecast quality policy."""

    __tablename__ = "forecast_backtest_metric"

    forecast_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_run.id"), nullable=False
    )
    job_output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id"), nullable=False
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    training_point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    backtest_point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mae: Mapped[float] = mapped_column(Float, nullable=False)
    rmse: Mapped[float] = mapped_column(Float, nullable=False)
    naive_rmse: Mapped[float | None] = mapped_column(Float)
    skill_score: Mapped[float | None] = mapped_column(Float)
    bias: Mapped[float] = mapped_column(Float, nullable=False)
    mape: Mapped[float | None] = mapped_column(Float)
    coverage_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    metrics_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("forecast_run_id", "series_id", "cutoff_time", name="uq_forecast_backtest_run_series_cutoff"),
        CheckConstraint("training_point_count >= 3 AND backtest_point_count > 0", name="counts"),
        CheckConstraint(
            "mae >= 0 AND rmse >= 0 AND (naive_rmse IS NULL OR naive_rmse >= 0) "
            "AND (skill_score IS NULL OR skill_score <= 1) AND (mape IS NULL OR mape >= 0) "
            "AND mae::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND rmse::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND bias::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="nonnegative_metrics",
        ),
        CheckConstraint("coverage_fraction >= 0 AND coverage_fraction <= 1", name="coverage"),
        CheckConstraint("metrics_checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
    )


class ForecastHindcastRun(Base, UUIDMixin):
    """Immutable retrospective SQL forecast receipt with an explicit simulated cutoff."""

    __tablename__ = "forecast_hindcast_run"

    hindcast_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    forecast_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_run.id"), nullable=False
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    simulated_cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uncertainty_calibration_cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    step_interval: Mapped[timedelta] = mapped_column(Interval(), nullable=False)
    minimum_training_points: Mapped[int] = mapped_column(Integer, nullable=False)
    training_point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_value_count: Mapped[int] = mapped_column(Integer, nullable=False)
    availability_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'retrospective_pinned_release'")
    )
    input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    model_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_digest_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'hindcast_v2'")
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staging'"))
    quality_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    mae: Mapped[float | None] = mapped_column(Float)
    rmse: Mapped[float | None] = mapped_column(Float)
    naive_rmse: Mapped[float | None] = mapped_column(Float)
    skill_score: Mapped[float | None] = mapped_column(Float)
    bias: Mapped[float | None] = mapped_column(Float)
    mape: Mapped[float | None] = mapped_column(Float)
    coverage_fraction: Mapped[float | None] = mapped_column(Float)
    interval_coverage_fraction: Mapped[float | None] = mapped_column(Float)
    receipt_checksum: Mapped[str | None] = mapped_column(String(64))
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "forecast_run_id",
            "series_id",
            "simulated_cutoff_time",
            "input_release_checksum",
            "model_checksum",
            "parameter_checksum",
            name="uq_forecast_hindcast_run_identity",
        ),
        CheckConstraint(
            "uncertainty_calibration_cutoff_time < simulated_cutoff_time",
            name="calibration_cutoff",
        ),
        CheckConstraint(
            "uncertainty_calibration_cutoff_time + step_interval * horizon_steps <= simulated_cutoff_time",
            name="calibration_horizon",
        ),
        CheckConstraint(
            "horizon_steps > 0 AND expected_value_count = horizon_steps AND step_interval > INTERVAL '0'",
            name="horizon",
        ),
        CheckConstraint(
            "minimum_training_points >= 3 AND training_point_count >= minimum_training_points",
            name="training_count",
        ),
        CheckConstraint(
            "availability_mode IN ('as_recorded', 'retrospective_pinned_release')",
            name="availability",
        ),
        CheckConstraint(
            "input_release_checksum ~ '^[0-9a-f]{64}$' "
            "AND model_checksum ~ '^[0-9a-f]{64}$' "
            "AND parameter_checksum ~ '^[0-9a-f]{64}$'",
            name="checksums",
        ),
        CheckConstraint(
            "receipt_digest_version IN ('hindcast_v1', 'hindcast_v2')",
            name="receipt_digest_version",
        ),
        CheckConstraint("status IN ('staging', 'finalized')", name="status"),
        CheckConstraint(
            "status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' "
            "AND recorded_at IS NOT NULL AND finalized_at IS NOT NULL "
            "AND mae >= 0 AND rmse >= 0 AND naive_rmse >= 0 "
            "AND (skill_score IS NULL OR skill_score <= 1) "
            "AND (mape IS NULL OR mape >= 0) "
            "AND coverage_fraction BETWEEN 0 AND 1 "
            "AND interval_coverage_fraction BETWEEN 0 AND 1)",
            name="finalized_evidence",
        ),
        Index("ix_forecast_hindcast_run_series_cutoff", "series_id", "simulated_cutoff_time"),
        Index("ix_forecast_hindcast_run_parent", "forecast_run_id"),
    )


class ForecastHindcastValue(Base):
    """Cutoff-verified prediction, actual, residual, and empirical interval evidence."""

    __tablename__ = "forecast_hindcast_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    hindcast_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_hindcast_run.id"), nullable=False
    )
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_step: Mapped[int] = mapped_column(Integer, nullable=False)
    point_value: Mapped[float] = mapped_column(Float, nullable=False)
    p10_value: Mapped[float] = mapped_column(Float, nullable=False)
    p50_value: Mapped[float] = mapped_column(Float, nullable=False)
    p90_value: Mapped[float] = mapped_column(Float, nullable=False)
    naive_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), nullable=False
    )
    actual_observation_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    residual_value: Mapped[float] = mapped_column(Float, Computed("actual_value - point_value", persisted=True))
    absolute_error: Mapped[float] = mapped_column(Float, Computed("abs(actual_value - point_value)", persisted=True))
    squared_error: Mapped[float] = mapped_column(
        Float,
        Computed("(actual_value - point_value) * (actual_value - point_value)", persisted=True),
    )
    interval_covered: Mapped[bool] = mapped_column(
        Boolean,
        Computed("actual_value >= p10_value AND actual_value <= p90_value", persisted=True),
    )
    value_checksum: Mapped[str] = mapped_column(
        String(64),
        Computed(
            "agri.forecast_hindcast_value_checksum(valid_time, horizon_step, point_value, "
            "p10_value, p50_value, p90_value, naive_value, actual_value, "
            "actual_source_release_id, actual_observation_checksum)",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("hindcast_run_id", "valid_time", name="uq_forecast_hindcast_value_time"),
        UniqueConstraint("hindcast_run_id", "horizon_step", name="uq_forecast_hindcast_value_horizon"),
        CheckConstraint("horizon_step > 0", name="horizon"),
        CheckConstraint("p10_value <= p50_value AND p50_value <= p90_value", name="bands"),
        CheckConstraint("actual_observation_checksum ~ '^[0-9a-f]{64}$'", name="actual_checksum"),
        CheckConstraint(
            "point_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND p10_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND p50_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND p90_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND naive_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND actual_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="finite",
        ),
        Index("ix_forecast_hindcast_value_run_time", "hindcast_run_id", "valid_time"),
    )


class ForecastInputRecordedAt(Base):
    """Server-written knowledge boundary for generic forecast inputs."""

    __tablename__ = "forecast_input_recorded_at"

    input_kind: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_key: Mapped[str] = mapped_column(Text, primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForecastIteration(Base, UUIDMixin):
    """Immutable evaluation-only bootstrap iteration over the generic time-series contract."""

    __tablename__ = "forecast_iteration"

    iteration_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'evaluation_only'"))
    availability_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    method: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'daily_increment_bootstrap_v1'")
    )
    as_of_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cutoff_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    history_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    simulation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1000"))
    simulation_seed: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    grain: Mapped[timedelta] = mapped_column(Interval(), nullable=False, server_default=text("INTERVAL '1 day'"))
    gap_policy: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'strict'"))
    lower_bound: Mapped[float | None] = mapped_column(Float)
    upper_bound: Mapped[float | None] = mapped_column(Float)
    input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    input_license_snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    contract_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    contract_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    history_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parameter_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    training_day_count: Mapped[int] = mapped_column(Integer, nullable=False)
    increment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_value_count: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staging'"))
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("method = 'daily_increment_bootstrap_v1'", name="method"),
        CheckConstraint("purpose = 'evaluation_only'", name="purpose"),
        CheckConstraint(
            "availability_mode IN ('as_of_pinned_release', 'retrospective_pinned_release')",
            name="availability",
        ),
        CheckConstraint("history_start <= cutoff_time AND cutoff_time <= as_of_time", name="ordered_history"),
        CheckConstraint(
            "horizon_days BETWEEN 1 AND 366 AND expected_value_count = horizon_days AND grain = INTERVAL '1 day'",
            name="horizon",
        ),
        CheckConstraint("simulation_count BETWEEN 100 AND 10000", name="simulations"),
        CheckConstraint("gap_policy IN ('strict', 'locf')", name="gap_policy"),
        CheckConstraint(
            "(lower_bound IS NULL OR lower_bound::text NOT IN ('NaN', 'Infinity', '-Infinity')) "
            "AND (upper_bound IS NULL OR upper_bound::text NOT IN ('NaN', 'Infinity', '-Infinity')) "
            "AND (lower_bound IS NULL OR upper_bound IS NULL OR lower_bound <= upper_bound)",
            name="bounds",
        ),
        CheckConstraint("training_day_count >= 3 AND increment_count >= 2", name="counts"),
        CheckConstraint(
            "input_release_checksum ~ '^[0-9a-f]{64}$' "
            "AND contract_checksum ~ '^[0-9a-f]{64}$' "
            "AND history_checksum ~ '^[0-9a-f]{64}$' "
            "AND parameter_checksum ~ '^[0-9a-f]{64}$'",
            name="checksums",
        ),
        CheckConstraint(
            "jsonb_typeof(input_license_snapshots) = 'array' "
            "AND jsonb_array_length(input_license_snapshots) > 0 "
            "AND jsonb_typeof(contract_snapshot) = 'object'",
            name="snapshots",
        ),
        CheckConstraint("status IN ('staging', 'finalized')", name="status"),
        CheckConstraint(
            "status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND recorded_at IS NOT NULL)",
            name="finalized_evidence",
        ),
        Index("ix_forecast_iteration_series_cutoff", "series_id", text("cutoff_time DESC")),
    )


class ForecastIterationValue(Base):
    """One checksummed low, median, and high value from an evaluation iteration."""

    __tablename__ = "forecast_iteration_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    iteration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_iteration.id"), nullable=False
    )
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_step: Mapped[int] = mapped_column(Integer, nullable=False)
    low_value: Mapped[float] = mapped_column(Float, nullable=False)
    median_value: Mapped[float] = mapped_column(Float, nullable=False)
    high_value: Mapped[float] = mapped_column(Float, nullable=False)
    increment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parameter_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    value_checksum: Mapped[str] = mapped_column(
        String(64),
        Computed(
            "agri.forecast_iteration_value_checksum("
            "valid_time, horizon_step, low_value, median_value, high_value, "
            "increment_count, parameter_checksum)",
            persisted=True,
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("iteration_id", "valid_time", name="uq_forecast_iteration_value_time"),
        UniqueConstraint("iteration_id", "horizon_step", name="uq_forecast_iteration_value_horizon"),
        CheckConstraint("horizon_step > 0", name="horizon"),
        CheckConstraint("low_value <= median_value AND median_value <= high_value", name="ordered"),
        CheckConstraint("increment_count >= 2", name="increment_count"),
        CheckConstraint("parameter_checksum ~ '^[0-9a-f]{64}$'", name="parameter_checksum"),
        CheckConstraint(
            "low_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND median_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND high_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="finite",
        ),
        Index("ix_forecast_iteration_value_iteration_time", "iteration_id", "valid_time"),
    )


class ForecastIterationActual(Base, UUIDMixin):
    """Separately arriving observed realization for one immutable iteration value."""

    __tablename__ = "forecast_iteration_actual"

    iteration_value_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("agri.forecast_iteration_value.id"), nullable=False, unique=True
    )
    actual_release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    actual_input_release_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    actual_value: Mapped[float] = mapped_column(Float, nullable=False)
    source_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    data_available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actual_digest_version: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'forecast_iteration_actual_v2'")
    )
    actual_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("source_sample_count > 0", name="samples"),
        CheckConstraint(
            "actual_digest_version = 'forecast_iteration_actual_v2'",
            name="digest_version",
        ),
        CheckConstraint(
            "actual_input_release_checksum ~ '^[0-9a-f]{64}$' AND actual_checksum ~ '^[0-9a-f]{64}$'",
            name="checksum",
        ),
        CheckConstraint(
            "actual_value::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="finite",
        ),
        CheckConstraint("recorded_at >= data_available_at", name="availability"),
    )


class ForecastIterationActualInput(Base):
    """Exact governed source rows contributing to one daily actual realization."""

    __tablename__ = "forecast_iteration_actual_input"

    actual_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_iteration_actual.id"), primary_key=True
    )
    source_release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.source_release.id"), primary_key=True
    )
    observation_checksum: Mapped[str] = mapped_column(String(64), primary_key=True)
    license_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "observation_checksum ~ '^[0-9a-f]{64}$' AND btrim(license_snapshot) <> ''",
            name="checksum",
        ),
    )


class ForecastReceipt(Base, UUIDMixin):
    """Immutable digest and extent declaration for staged forecast values."""

    __tablename__ = "forecast_receipt"

    forecast_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_run.id"), nullable=False
    )
    job_output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id"), nullable=False
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_series.id"), nullable=False
    )
    entity_state_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_entity_state.id")
    )
    issue_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_value_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quantile_levels: Mapped[list[float]] = mapped_column(ARRAY(Float), nullable=False)
    receipt_checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'staging'"))
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "forecast_run_id",
            "series_id",
            "issue_time",
            "valid_from",
            "valid_to",
            name="uq_forecast_receipt_run_series_window",
        ),
        CheckConstraint("valid_to > valid_from", name="ordered_window"),
        CheckConstraint("expected_value_count > 0", name="expected_values"),
        CheckConstraint(
            "agri.forecast_quantiles_valid(quantile_levels) AND 0.5 = ANY(quantile_levels)",
            name="quantiles",
        ),
        CheckConstraint("status IN ('staging', 'finalized')", name="status"),
        CheckConstraint(
            "status <> 'finalized' OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)",
            name="finalized_evidence",
        ),
    )


class ForecastValue(Base):
    """Append-only point and uncertainty values owned by one forecast receipt."""

    __tablename__ = "forecast_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    forecast_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_receipt.id"), nullable=False
    )
    valid_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    horizon_step: Mapped[int] = mapped_column(Integer, nullable=False)
    point_value: Mapped[float] = mapped_column(Float, nullable=False)
    p10_value: Mapped[float | None] = mapped_column(Float)
    p50_value: Mapped[float | None] = mapped_column(Float)
    p90_value: Mapped[float | None] = mapped_column(Float)
    quantile_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("forecast_receipt_id", "valid_time", name="uq_forecast_value_receipt_time"),
        UniqueConstraint("forecast_receipt_id", "horizon_step", name="uq_forecast_value_receipt_horizon"),
        CheckConstraint("horizon_step > 0", name="positive_horizon"),
        CheckConstraint(
            "(p10_value IS NULL OR p50_value IS NULL OR p10_value <= p50_value) AND "
            "(p50_value IS NULL OR p90_value IS NULL OR p50_value <= p90_value)",
            name="ordered_bands",
        ),
        CheckConstraint(
            "point_value::text NOT IN ('NaN', 'Infinity', '-Infinity') "
            "AND (p10_value IS NULL OR p10_value::text NOT IN ('NaN', 'Infinity', '-Infinity')) "
            "AND (p50_value IS NULL OR p50_value::text NOT IN ('NaN', 'Infinity', '-Infinity')) "
            "AND (p90_value IS NULL OR p90_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="finite_point",
        ),
        Index("ix_forecast_value_receipt_time", "forecast_receipt_id", "valid_time"),
    )


class ForecastPublication(Base, UUIDMixin):
    """Atomic serving manifest composed only from finalized receipts."""

    __tablename__ = "forecast_publication"

    publication_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    job_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agri.job_run.id"), nullable=False)
    job_output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.job_output.id"), nullable=False, unique=True
    )
    release_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.release_set.id"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'draft'"))
    manifest_checksum: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("state IN ('draft', 'published', 'retired')", name="state"),
        CheckConstraint(
            "state = 'draft' OR (manifest_checksum ~ '^[0-9a-f]{64}$' AND published_at IS NOT NULL)",
            name="published_evidence",
        ),
        Index(
            "uq_forecast_publication_live_scope",
            "scope_key",
            unique=True,
            postgresql_where=text("state = 'published'"),
        ),
    )


class ForecastPublicationItem(Base):
    """Immutable membership of one finalized receipt in a publication."""

    __tablename__ = "forecast_publication_item"

    publication_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_publication.id"), primary_key=True
    )
    forecast_receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agri.forecast_receipt.id"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
