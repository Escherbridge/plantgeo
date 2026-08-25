"""Migration source contracts for the geospatial evidence foundation."""

# ruff: noqa: PLR2004

from pathlib import Path


def test_resolution_aware_evidence_migration_is_additive_pinned_and_forward_only() -> None:
    service_root = Path(__file__).resolve().parents[1]
    migration = (
        service_root / "alembic" / "archive" / "20260723_0009_resolution_aware_intervention_evidence.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "20260723_0009"' in migration
    assert 'down_revision = "20260722_0008"' in migration
    for table_name in (
        "normalized_source_feature",
        "analysis_subject",
        "intervention_analysis_run",
        "intervention_evidence_input",
        "intervention_evidence_lineage",
    ):
        assert f'"{table_name}"' in migration
    assert "raise NotImplementedError" in migration
    assert "strategy_selection" not in migration
    assert "recommendation" not in migration


def test_migration_enforces_wgs84_resolution_and_non_life_safety_contracts() -> None:
    service_root = Path(__file__).resolve().parents[1]
    migration = (
        service_root / "alembic" / "archive" / "20260723_0009_resolution_aware_intervention_evidence.py"
    ).read_text(encoding="utf-8")

    assert migration.count('Geometry("GEOMETRY", srid=4326') == 3
    assert migration.count('postgresql_using="gist"') == 3
    for field in (
        "spatial_support_kind",
        "native_resolution_m",
        "native_scale",
        "maximum_inference_scale",
        "confidence",
        "confidence_basis",
        "method_key",
        "method_version",
        "is_life_safety_validated",
    ):
        assert migration.count(f'"{field}"') >= 3
    assert migration.count("is_life_safety_validated = false") == 3
    assert migration.count("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)") == 3
    assert migration.count("ST_IsValid(") == 3
    assert "model_grid_cell" in migration


def test_migration_keeps_evidence_categories_and_lineage_relational() -> None:
    service_root = Path(__file__).resolve().parents[1]
    migration = (
        service_root / "alembic" / "archive" / "20260723_0009_resolution_aware_intervention_evidence.py"
    ).read_text(encoding="utf-8")

    for evidence_kind in ("observed_fact", "model_derived_feature", "known_gap"):
        assert evidence_kind in migration
    assert "num_nonnulls(numeric_value, text_value, boolean_value)" in migration
    assert "fk_intervention_evidence_lineage_feature_release" in migration
    assert "trg_intervention_evidence_requires_lineage" in migration
    assert "DEFERRABLE INITIALLY DEFERRED" in migration
    assert "trg_intervention_lineage_release_membership" in migration
    assert "subject_membership.source_release_id = subject.source_release_id" in migration
    assert "release_set.state IN ('validated', 'published')" in migration
    assert "source_release.validation_state = 'valid'" in migration
    assert migration.count("SECURITY DEFINER") == 3
    assert migration.count("SET search_path = pg_catalog, agri") == 3
    assert "FOR SHARE OF subject_release" in migration
    assert "reject_geospatial_evidence_mutation" in migration
    assert "protect_intervention_evidence_parents" in migration
    for trigger_name in (
        "trg_source_release_intervention_parent",
        "trg_artifact_intervention_parent",
        "trg_release_set_intervention_parent",
        "trg_release_set_item_intervention_parent",
    ):
        assert trigger_name in migration
    assert "inline_artifact_checksum_matches" in migration
    assert "public.digest(content_bytes, 'sha256')" in migration
    assert "intervention_analysis_run" in migration
    assert "analysis_plan_checksum" in migration
    assert "analysis_code_checksum" in migration
    assert "output_checksum" in migration
    assert "analysis_run.validation_state = 'validated'" in migration
    assert "agri.job_run" not in migration
    assert "agri.job_output" not in migration
