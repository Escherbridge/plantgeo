"""Migration wiring is isolated from the runtime identity."""

from pathlib import Path


def test_alembic_uses_sync_dsn_and_requires_operator_installed_extensions() -> None:
    service_root = Path(__file__).resolve().parents[1]
    repository_root = service_root.parents[1]
    environment = (service_root / "alembic" / "env.py").read_text(encoding="utf-8")
    foundation = (service_root / "alembic" / "versions" / "20260719_0001_agri_foundation.py").read_text(
        encoding="utf-8"
    )
    extension_gate = (repository_root / "infra" / "local-warehouse" / "enable-extensions.sql").read_text(
        encoding="utf-8"
    )

    assert "settings.database_url_sync" in environment
    assert "settings.database_url)" not in environment
    assert "engine_from_config" in environment
    assert "CREATE EXTENSION" not in foundation
    assert "pg_extension" in foundation
    assert "Agri foundation preflight failed" in foundation
    assert "This migration never creates extensions" in foundation
    # These two assertions read two files whose truths diverged on 2026-08-25, so they no longer
    # share a loop. `foundation` is the APPLIED migration 20260719_0001 -- immutable history, so its
    # preflight still names timescaledb and always will. `extension_gate` is a live dev-environment
    # file, and timescaledb was dropped from production that day (it held one always-empty
    # hypertable and no continuous aggregate), so the gate must stop creating it. Migration
    # 20260825_0026 is what reconciles the two on a fresh build.
    for extension in ("postgis", "timescaledb", "vector", "pgcrypto"):
        assert f"'{extension}'::text" in foundation
    for extension in ("postgis", "vector", "pgcrypto"):
        assert f"CREATE EXTENSION IF NOT EXISTS {extension};" in extension_gate
    assert "CREATE EXTENSION IF NOT EXISTS timescaledb;" not in extension_gate


def test_historical_observation_migration_remains_release_pinned_and_forward_only() -> None:
    service_root = Path(__file__).resolve().parents[1]
    historical = (service_root / "alembic" / "versions" / "20260720_0002_historical_observation_plane.py").read_text(
        encoding="utf-8"
    )

    for object_name in (
        "spatial_cell",
        "cell_source_crosswalk",
        "signal_observation",
        "source_parameter",
        "signal_coverage_audit",
        "source_coverage_audit",
        "drought_polygon_snapshot",
        "v_signal_timeseries_contract",
    ):
        assert object_name in historical
    assert "source_release.validation_state = 'valid'" in historical
    assert "release_set.state IN ('validated', 'published')" in historical
    assert "release_set.as_of_time <= p_as_of_time" in historical
    assert "release_set.validated_at <= p_as_of_time" in historical
    assert "source_release.transform_version" in historical
    assert "ck_signal_coverage_status_matches_counts" in historical
    assert "ck_source_coverage_status_matches_counts" in historical
    assert 'sa.Column("impact_type", sa.String(length=8), server_default="none", nullable=False)' in historical
    assert "raise NotImplementedError" in historical


def test_historical_promotion_migration_binds_resumable_receipts_to_the_target_release_set() -> None:
    service_root = Path(__file__).resolve().parents[1]
    promotion = (service_root / "alembic" / "versions" / "20260720_0003_historical_promotion_receiver.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "20260720_0003"' in promotion
    assert 'down_revision = "20260720_0002"' in promotion
    for table_name in (
        "historical_promotion_bundle",
        "historical_promotion_chunk_receipt",
        "historical_promotion_data_source_receipt",
        "historical_promotion_source_release_receipt",
        "historical_promotion_artifact_receipt",
    ):
        assert table_name in promotion
    assert "source_parameter" in promotion


def test_spatial_support_migration_exposes_resolution_and_acre_compatibility() -> None:
    service_root = Path(__file__).resolve().parents[1]
    support = (service_root / "alembic" / "versions" / "20260720_0004_spatial_support_contract.py").read_text(
        encoding="utf-8"
    )

    assert 'revision = "20260720_0004"' in support
    assert 'down_revision = "20260720_0003"' in support
    for contract_field in (
        "spatial_support_kind",
        "source_parameter",
        "analysis_resolution_m",
        "native_resolution_m",
        "mapping_coverage_fraction",
        "is_acre_scale_compatible",
    ):
        assert contract_field in support
    assert "source_support.crosswalk_count = 1" in support
    assert "raise NotImplementedError" in support
