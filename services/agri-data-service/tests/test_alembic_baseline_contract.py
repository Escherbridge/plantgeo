"""Static contracts for the single current Alembic baseline."""

from __future__ import annotations

import re
from pathlib import Path

from agri_data_service.db.revisions import BASELINE_REVISION

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_BASELINE_SQL = _SERVICE_ROOT / "db" / "agri_baseline.sql"
_REVISION = _SERVICE_ROOT / "alembic" / "versions" / "20260912_0000_greenfield.py"

EXPECTED_TABLES = {
    "climate_profiles",
    "companion_relationships",
    "data_source",
    "expert_label",
    "expert_label_release",
    "expert_label_source",
    "job_attempt",
    "job_checkpoint",
    "job_definition",
    "job_dependency",
    "job_event",
    "job_event_default",
    "job_incident",
    "job_outbox",
    "job_output",
    "job_run",
    "job_work_item",
    "locations",
    "soil_profiles",
    "spatial_cell",
    "species",
    "strategies",
    "topography_profiles",
    "water_profiles",
}

RETIRED_TABLES = {
    "artifact",
    "drought_polygon_snapshot",
    "forecast_model",
    "forecast_run",
    "matview_refresh_state",
    "signal_observation",
    "source_release",
    "vegetation_publication_day",
}


def _baseline_sql() -> str:
    return _BASELINE_SQL.read_text(encoding="utf-8-sig")


def test_only_the_current_greenfield_revision_is_live() -> None:
    versions = sorted(path.name for path in _REVISION.parent.glob("*.py"))
    assert versions == [_REVISION.name]
    source = _REVISION.read_text(encoding="utf-8")
    assert f'revision: str = "{BASELINE_REVISION}"' in source
    assert "down_revision: str | None = None" in source
    assert '"db" / "agri_baseline.sql"' in source


def test_baseline_declares_exactly_the_retained_agri_tables() -> None:
    tables = set(re.findall(r"^CREATE TABLE agri\.([a-z0-9_]+) ", _baseline_sql(), re.MULTILINE))
    assert tables == EXPECTED_TABLES
    assert not tables.intersection(RETIRED_TABLES)


def test_baseline_is_self_contained() -> None:
    source = _baseline_sql()
    assert "CREATE SCHEMA agri;" in source
    assert "transaction_timeout" not in source
    assert not re.search(r" CONSTRAINT [A-Za-z0-9_]+ NOT NULL", source)
    assert "db/manifest.sql" not in _REVISION.read_text(encoding="utf-8")
    assert not (_SERVICE_ROOT / "alembic" / "archive").exists()


def test_baseline_is_forward_only_and_revokes_public_access() -> None:
    source = _REVISION.read_text(encoding="utf-8")
    assert 'raise NotImplementedError("Restore a database backup instead of reversing the baseline.")' in source
    for statement in (
        "REVOKE CREATE ON SCHEMA agri FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM PUBLIC",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri FROM PUBLIC",
        "REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA agri FROM PUBLIC",
    ):
        assert statement in source
