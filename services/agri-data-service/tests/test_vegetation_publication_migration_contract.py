"""Static contracts for the layered durable vegetation publication queue revision."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_REVISION = _ROOT / "alembic" / "versions" / "20260827_0027_vegetation_publication_queue.py"
_TABLE = _ROOT / "db" / "agri" / "tables" / "vegetation_publication_day.sql"
_MANIFEST = _ROOT / "db" / "manifest.sql"


def test_revision_is_layered_idempotently_and_never_deletes_ingestion_data() -> None:
    source = _REVISION.read_text(encoding="utf-8")

    assert 'revision: str = "20260827_0027"' in source
    assert 'down_revision: str | None = "20260825_0000"' in source
    assert "CREATE TABLE IF NOT EXISTS agri.vegetation_publication_day" in source
    assert "CREATE INDEX IF NOT EXISTS ix_vegetation_publication_day_pending" in source
    assert "DELETE FROM agri.forecast_observation" not in source
    assert "TRUNCATE" not in source


def test_declarative_tree_and_manifest_include_the_queue() -> None:
    table = _TABLE.read_text(encoding="utf-8")
    manifest = _MANIFEST.read_text(encoding="utf-8")

    assert "CREATE TABLE agri.vegetation_publication_day" in table
    assert "published_fingerprint character varying(64)" in table
    assert "ix_vegetation_publication_day_pending" in table
    assert "\\i agri/tables/vegetation_publication_day.sql" in manifest
