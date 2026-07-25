"""Metadata contracts for resolution-aware intervention evidence."""

# ruff: noqa: PLR2004

from geoalchemy2 import Geometry
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB

from agri_data_service import models as model_registry
from agri_data_service.db.base import Base


def _check_sql(table_name: str) -> set[str]:
    table = Base.metadata.tables[f"agri.{table_name}"]
    return {str(constraint.sqltext) for constraint in table.constraints if isinstance(constraint, CheckConstraint)}


def test_geospatial_evidence_tables_are_registered_and_resolution_aware() -> None:
    expected = {
        "analysis_subject",
        "normalized_source_feature",
        "intervention_analysis_run",
        "intervention_evidence_input",
        "intervention_evidence_lineage",
    }
    assert expected <= {table.name for table in Base.metadata.tables.values()}
    assert model_registry.AnalysisSubject.__table__.name == "analysis_subject"
    assert model_registry.NormalizedSourceFeature.__table__.name == "normalized_source_feature"
    assert model_registry.InterventionAnalysisRun.__table__.name == "intervention_analysis_run"
    assert model_registry.InterventionEvidenceInput.__table__.name == "intervention_evidence_input"
    assert model_registry.InterventionEvidenceLineage.__table__.name == "intervention_evidence_lineage"

    resolution_contract = {
        "spatial_support_kind",
        "native_resolution_m",
        "native_scale",
        "maximum_inference_scale",
        "confidence",
        "confidence_basis",
        "method_key",
        "method_version",
        "is_life_safety_validated",
    }
    for table_name in ("analysis_subject", "normalized_source_feature", "intervention_evidence_input"):
        table = Base.metadata.tables[f"agri.{table_name}"]
        assert resolution_contract <= set(table.c.keys())
        assert table.c.is_life_safety_validated.nullable is False
        assert table.c.confidence.nullable is True
        assert table.c.confidence_basis.nullable is False
        assert any("is_life_safety_validated = false" in check for check in _check_sql(table_name))


def test_all_evidence_geometries_are_wgs84_and_gist_indexed() -> None:
    geometry_columns = {
        "analysis_subject": "geometry",
        "normalized_source_feature": "geometry",
        "intervention_evidence_input": "evidence_geometry",
    }
    for table_name, column_name in geometry_columns.items():
        table = Base.metadata.tables[f"agri.{table_name}"]
        geometry_type = table.c[column_name].type
        assert isinstance(geometry_type, Geometry)
        assert geometry_type.srid == 4326
        assert any("ST_IsValid" in check and "ST_IsEmpty" in check for check in _check_sql(table_name))
        assert any(
            column_name in index.columns and index.dialect_options["postgresql"]["using"] == "gist"
            for index in table.indexes
        )


def test_subjects_and_features_retain_exact_source_release_and_artifact_lineage() -> None:
    feature = Base.metadata.tables["agri.normalized_source_feature"]
    subject = Base.metadata.tables["agri.analysis_subject"]

    assert {"source_release_id", "artifact_id", "feature_checksum", "geometry_checksum"} <= set(feature.c.keys())
    assert {"source_release_id", "artifact_id", "source_feature_id", "geometry_checksum"} <= set(subject.c.keys())
    assert {"agri.source_release.id", "agri.artifact.id"} <= {
        foreign_key.target_fullname for foreign_key in feature.foreign_keys
    }
    subject_targets = {foreign_key.target_fullname for foreign_key in subject.foreign_keys}
    assert {
        "agri.source_release.id",
        "agri.artifact.id",
        "agri.normalized_source_feature.id",
        "agri.normalized_source_feature.source_release_id",
        "agri.normalized_source_feature.artifact_id",
    } <= subject_targets


def test_evidence_kinds_are_typed_and_use_relational_lineage() -> None:
    evidence = Base.metadata.tables["agri.intervention_evidence_input"]
    lineage = Base.metadata.tables["agri.intervention_evidence_lineage"]
    checks = _check_sql("intervention_evidence_input")

    assert any(
        all(kind in check for kind in ("observed_fact", "model_derived_feature", "known_gap")) for check in checks
    )
    assert any("num_nonnulls(numeric_value, text_value, boolean_value) = 1" in check for check in checks)
    assert any("evidence_kind = 'known_gap'" in check and "gap_detail IS NOT NULL" in check for check in checks)
    assert not any(isinstance(column.type, JSONB) for column in evidence.c)
    assert {
        "release_set_id",
        "analysis_subject_id",
        "intervention_analysis_run_id",
        "evidence_checksum",
    } <= set(evidence.c.keys())
    assert {
        "evidence_input_id",
        "source_release_id",
        "source_feature_id",
        "source_record_table",
        "source_record_key",
        "source_record_checksum",
        "lineage_role",
    } <= set(lineage.c.keys())

    lineage_targets = {foreign_key.target_fullname for foreign_key in lineage.foreign_keys}
    assert {
        "agri.intervention_evidence_input.id",
        "agri.source_release.id",
        "agri.normalized_source_feature.id",
        "agri.normalized_source_feature.source_release_id",
    } <= lineage_targets


def test_model_derived_evidence_uses_a_plane_specific_validated_run_receipt() -> None:
    analysis_run = Base.metadata.tables["agri.intervention_analysis_run"]
    evidence = Base.metadata.tables["agri.intervention_evidence_input"]

    assert {
        "release_set_id",
        "run_key",
        "method_key",
        "method_version",
        "analysis_plan_checksum",
        "analysis_code_checksum",
        "output_checksum",
        "validation_state",
        "validated_at",
        "finalized_at",
        "row_count",
        "is_life_safety_prediction",
    } <= set(analysis_run.c.keys())
    assert any("is_life_safety_prediction = false" in check for check in _check_sql("intervention_analysis_run"))
    assert "agri.release_set.id" in {foreign_key.target_fullname for foreign_key in analysis_run.foreign_keys}
    assert "agri.intervention_analysis_run.id" in {foreign_key.target_fullname for foreign_key in evidence.foreign_keys}
    assert "agri.job_run.id" not in {foreign_key.target_fullname for foreign_key in evidence.foreign_keys}
