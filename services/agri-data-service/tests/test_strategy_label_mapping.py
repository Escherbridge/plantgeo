"""Database-free source-mapping custody tests for strategy labels."""

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from agri_data_service.execution.strategy_label_mapping import (
    load_strategy_label_source_mapping,
    preflight_strategy_label_source_mapping,
)
from agri_data_service.interface.cli import cli

SERVICE_ROOT = Path(__file__).resolve().parents[1]
INCOMPLETE_EXAMPLE = SERVICE_ROOT / "examples" / "strategy-label-source-mapping.incomplete.json"
SHA256_HEX_LENGTH = 64
INCOMPLETE_EXIT_CODE = 2


def _complete_mapping() -> dict[str, Any]:
    return {
        "schema_version": "strategy_label_source_mapping_v1",
        "source": {
            "name": "reviewed-field-trial-outcomes",
            "source_kind": "intervention_outcome_evidence",
            "locator": "local-evidence/field-trial-outcomes.parquet",
            "release_id": "field-trial-2026-07-25",
            "release_checksum_sha256": "a" * 64,
            "lineage_uri": "local-evidence/field-trial-outcomes-lineage.json",
        },
        "outcome_definition": {
            "definition_key_field": "outcome_definition_key",
            "definition_version_field": "outcome_definition_version",
            "metric_name_field": "outcome_metric_name",
            "metric_unit_field": "outcome_metric_unit",
            "benefit_direction_field": "outcome_benefit_direction",
            "smallest_meaningful_effect_field": "smallest_meaningful_effect",
            "baseline_window_field": "baseline_window",
            "outcome_window_field": "outcome_window",
            "aggregation_method_field": "aggregation_method",
            "transform_method_field": "transform_method",
            "eligibility_policy_field": "outcome_eligibility_policy",
        },
        "treatment_control": {
            "arm_field": "assignment_arm",
            "treatment_strategy_id_field": "treatment_strategy_id",
            "treatment_strategy_version_field": "treatment_strategy_version",
            "eligible_control_field": "is_eligible_untreated_control",
            "eligibility_risk_set_field": "eligibility_risk_set",
            "strategy_taxonomy_release_field": "strategy_taxonomy_release",
            "strategy_taxonomy_checksum_field": "strategy_taxonomy_checksum",
        },
        "label_release": {
            "release_set_id_field": "source_release_set_id",
            "as_of_time_field": "source_as_of_time",
            "spatial_block_scheme_field": "spatial_block_scheme",
        },
        "episode": {
            "episode_key_field": "episode_key",
            "subject_id_field": "subject_id",
            "cohort_field": "assignment_cohort",
            "assigned_at_field": "assigned_at",
            "intervention_start_field": "intervention_start",
            "intervention_end_field": "intervention_end",
            "baseline_start_field": "baseline_start",
            "baseline_end_field": "baseline_end",
            "outcome_start_field": "outcome_start",
            "outcome_end_field": "outcome_end",
            "assignment_mechanism_field": "assignment_mechanism",
            "known_assignment_probability_field": "known_assignment_probability",
            "spatial_block_field": "spatial_block",
            "covariates_available_at_field": "covariates_available_at",
            "data_available_at_field": "data_available_at",
        },
        "evidence": {
            "baseline_evidence_id_field": "baseline_evidence_id",
            "baseline_value_field": "baseline_value",
            "baseline_source_release_id_field": "baseline_source_release_id",
            "baseline_evidence_checksum_field": "baseline_evidence_checksum",
            "baseline_observed_from_field": "baseline_observed_from",
            "baseline_observed_to_field": "baseline_observed_to",
            "baseline_available_at_field": "baseline_available_at",
            "outcome_evidence_id_field": "outcome_evidence_id",
            "outcome_value_field": "outcome_value",
            "outcome_source_release_id_field": "outcome_source_release_id",
            "outcome_evidence_checksum_field": "outcome_evidence_checksum",
            "outcome_observed_from_field": "outcome_observed_from",
            "outcome_observed_to_field": "outcome_observed_to",
            "outcome_available_at_field": "outcome_available_at",
        },
        "covariates": [
            {
                "feature_name": "pre_assignment_soil_ph",
                "value_field": "soil_ph",
                "available_at_field": "soil_ph_available_at",
                "evidence_id_field": "soil_ph_evidence_id",
                "source_release_id_field": "soil_ph_source_release_id",
                "evidence_checksum_field": "soil_ph_evidence_checksum",
            }
        ],
    }


def test_complete_mapping_has_stable_checksum_without_database_access(tmp_path: Path) -> None:
    compact_path = tmp_path / "compact.json"
    pretty_path = tmp_path / "pretty.json"
    mapping = _complete_mapping()
    compact_path.write_text(json.dumps(mapping, separators=(",", ":")), encoding="utf-8")
    pretty_path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")

    compact = preflight_strategy_label_source_mapping(compact_path)
    pretty = preflight_strategy_label_source_mapping(pretty_path)

    assert compact.ready is True
    assert compact.missing_requirements == ()
    assert compact.mapping_checksum == pretty.mapping_checksum
    assert compact.mapping_checksum is not None
    assert len(compact.mapping_checksum) == SHA256_HEX_LENGTH


def test_incomplete_example_fails_readiness_without_checksum() -> None:
    result = preflight_strategy_label_source_mapping(INCOMPLETE_EXAMPLE)

    assert result.ready is False
    assert result.mapping_checksum is None
    assert "source.name" in result.missing_requirements
    assert "outcome_definition.definition_key_field" in result.missing_requirements
    assert "treatment_control.eligible_control_field" in result.missing_requirements
    assert "label_release.as_of_time_field" in result.missing_requirements
    assert "evidence.outcome_source_release_id_field" in result.missing_requirements
    assert "covariates[]" in result.missing_requirements


def test_manifest_rejects_forecast_actuals_and_undeclared_fields(tmp_path: Path) -> None:
    mapping = _complete_mapping()
    source = mapping["source"]
    assert isinstance(source, dict)
    source["name"] = "Boise forecast actuals"
    path = tmp_path / "boise.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")

    with pytest.raises(ValueError, match="forecast-error labels"):
        load_strategy_label_source_mapping(path)

    mapping = _complete_mapping()
    source = mapping["source"]
    assert isinstance(source, dict)
    source["source_kind"] = "forecast_actual"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(ValueError, match="intervention_outcome_evidence"):
        load_strategy_label_source_mapping(path)

    mapping = _complete_mapping()
    episode = mapping["episode"]
    assert isinstance(episode, dict)
    episode["fabricated_episode_field"] = "do_not_accept"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_strategy_label_source_mapping(path)


def test_preflight_cli_reports_readiness_and_uses_nonzero_for_template(tmp_path: Path) -> None:
    complete_path = tmp_path / "complete.json"
    complete_path.write_text(json.dumps(_complete_mapping()), encoding="utf-8")
    runner = CliRunner()

    ready = runner.invoke(
        cli,
        ["ml", "strategy-label-map-preflight", "--mapping-manifest", str(complete_path)],
    )
    incomplete = runner.invoke(
        cli,
        ["ml", "strategy-label-map-preflight", "--mapping-manifest", str(INCOMPLETE_EXAMPLE)],
    )

    assert ready.exit_code == 0
    ready_payload = json.loads(ready.output)
    assert ready_payload["ready"] is True
    assert len(ready_payload["mapping_checksum"]) == SHA256_HEX_LENGTH
    assert incomplete.exit_code == INCOMPLETE_EXIT_CODE
    incomplete_payload = json.loads(incomplete.output)
    assert incomplete_payload["ready"] is False
    assert incomplete_payload["mapping_checksum"] is None
