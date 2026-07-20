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
    for extension in ("postgis", "timescaledb", "vector", "pgcrypto"):
        assert f"'{extension}'::text" in foundation
        assert f"CREATE EXTENSION IF NOT EXISTS {extension};" in extension_gate
