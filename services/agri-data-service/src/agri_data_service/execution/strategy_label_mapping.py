"""Strict, database-free preflight for external strategy-label source mappings."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agri_data_service.execution.contracts import canonical_json_bytes, reject_credential_url

if TYPE_CHECKING:
    from pathlib import Path

_MAX_MAPPING_BYTES = 1_000_000
_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_PLACEHOLDERS = frozenset({"n/a", "na", "not-set", "placeholder", "tbd", "todo", "unknown"})
_BOISE_FORECAST_MARKERS = (
    "boise-actual",
    "boise actual",
    "boise_actual",
    "boise-forecast",
    "boise forecast",
    "boise_forecast",
    "forecast-actual",
    "forecast actual",
    "forecast_actual",
    "hindcast-actual",
    "hindcast actual",
    "hindcast_actual",
)

MappingField = Annotated[str, Field(min_length=1, max_length=500)] | None
SourceName = Annotated[str, Field(min_length=1, max_length=255)] | None
SourceLocator = Annotated[str, Field(min_length=1, max_length=2_000)] | None
SourceReleaseChecksum = Annotated[str, Field(pattern=_SHA256_PATTERN)] | None


class _MappingModel(BaseModel):
    """Forbid undeclared mapping fields and normalize insignificant whitespace."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class StrategyLabelSourceIdentity(_MappingModel):
    """Named, immutable external intervention-evidence release."""

    name: SourceName
    source_kind: Literal["intervention_outcome_evidence"] | None
    locator: SourceLocator
    release_id: MappingField
    release_checksum_sha256: SourceReleaseChecksum
    lineage_uri: SourceLocator

    @field_validator("locator", "lineage_uri")
    @classmethod
    def reject_credential_locators(cls, value: str | None) -> str | None:
        if value is not None:
            reject_credential_url(value)
        return value


class StrategyOutcomeDefinitionMapping(_MappingModel):
    """Direct source fields needed to review one governed outcome definition."""

    definition_key_field: MappingField
    definition_version_field: MappingField
    metric_name_field: MappingField
    metric_unit_field: MappingField
    benefit_direction_field: MappingField
    smallest_meaningful_effect_field: MappingField
    baseline_window_field: MappingField
    outcome_window_field: MappingField
    aggregation_method_field: MappingField
    transform_method_field: MappingField
    eligibility_policy_field: MappingField


class StrategyTreatmentControlMapping(_MappingModel):
    """Direct treatment, control-eligibility, and taxonomy source fields."""

    arm_field: MappingField
    treatment_strategy_id_field: MappingField
    treatment_strategy_version_field: MappingField
    eligible_control_field: MappingField
    eligibility_risk_set_field: MappingField
    strategy_taxonomy_release_field: MappingField
    strategy_taxonomy_checksum_field: MappingField


class StrategyLabelReleaseMapping(_MappingModel):
    """Direct governed cutoff and source-release lineage fields."""

    release_set_id_field: MappingField
    as_of_time_field: MappingField
    spatial_block_scheme_field: MappingField


class StrategyEpisodeMapping(_MappingModel):
    """Direct independent-unit, assignment, window, and spatial source fields."""

    episode_key_field: MappingField
    subject_id_field: MappingField
    cohort_field: MappingField
    assigned_at_field: MappingField
    intervention_start_field: MappingField
    intervention_end_field: MappingField
    baseline_start_field: MappingField
    baseline_end_field: MappingField
    outcome_start_field: MappingField
    outcome_end_field: MappingField
    assignment_mechanism_field: MappingField
    known_assignment_probability_field: MappingField
    spatial_block_field: MappingField
    covariates_available_at_field: MappingField
    data_available_at_field: MappingField


class StrategyEvidenceMapping(_MappingModel):
    """Direct raw baseline/outcome evidence and release-lineage source fields."""

    baseline_evidence_id_field: MappingField
    baseline_value_field: MappingField
    baseline_source_release_id_field: MappingField
    baseline_evidence_checksum_field: MappingField
    baseline_observed_from_field: MappingField
    baseline_observed_to_field: MappingField
    baseline_available_at_field: MappingField
    outcome_evidence_id_field: MappingField
    outcome_value_field: MappingField
    outcome_source_release_id_field: MappingField
    outcome_evidence_checksum_field: MappingField
    outcome_observed_from_field: MappingField
    outcome_observed_to_field: MappingField
    outcome_available_at_field: MappingField


class StrategyCovariateMapping(_MappingModel):
    """One ordered covariate with time-honest value and lineage fields."""

    feature_name: MappingField
    value_field: MappingField
    available_at_field: MappingField
    evidence_id_field: MappingField
    source_release_id_field: MappingField
    evidence_checksum_field: MappingField


class StrategyLabelSourceMappingManifest(_MappingModel):
    """Versioned mapping only; it never contains or creates label episodes."""

    schema_version: Literal["strategy_label_source_mapping_v1"]
    source: StrategyLabelSourceIdentity
    outcome_definition: StrategyOutcomeDefinitionMapping
    treatment_control: StrategyTreatmentControlMapping
    label_release: StrategyLabelReleaseMapping
    episode: StrategyEpisodeMapping
    evidence: StrategyEvidenceMapping
    covariates: tuple[StrategyCovariateMapping, ...]

    @model_validator(mode="after")
    def reject_forecast_actual_sources(self) -> StrategyLabelSourceMappingManifest:
        identity = " ".join(
            value.lower()
            for value in (
                self.source.name,
                self.source.locator,
                self.source.release_id,
                self.source.lineage_uri,
            )
            if value is not None
        )
        if any(marker in identity for marker in _BOISE_FORECAST_MARKERS) or (
            "boise" in identity and "actual" in identity
        ):
            raise ValueError("Boise forecast actuals are forecast-error labels, not intervention-effect outcomes")
        feature_names = [
            covariate.feature_name
            for covariate in self.covariates
            if covariate.feature_name is not None and not _is_placeholder(covariate.feature_name)
        ]
        if len(feature_names) != len(set(feature_names)):
            raise ValueError("covariate feature_name values must be unique")
        return self

    @property
    def missing_requirements(self) -> tuple[str, ...]:
        """List every unset or placeholder mapping path."""
        missing = _missing_paths(self.model_dump(mode="json"))
        if not self.covariates:
            missing.append("covariates[]")
        return tuple(sorted(set(missing)))

    @property
    def mapping_checksum(self) -> str:
        """Hash only a complete, canonical, intervention-evidence mapping."""
        missing = self.missing_requirements
        if missing:
            raise ValueError(f"mapping is incomplete: {', '.join(missing)}")
        return hashlib.sha256(canonical_json_bytes(self.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True)
class StrategyLabelMappingPreflight:
    """Machine-readable readiness evidence without database side effects."""

    schema_version: Literal["strategy_label_mapping_preflight_v1"]
    source_name: str | None
    ready: bool
    missing_requirements: tuple[str, ...]
    mapping_checksum: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "mapping_checksum": self.mapping_checksum,
                "missing_requirements": list(self.missing_requirements),
                "ready": self.ready,
                "schema_version": self.schema_version,
                "source_name": self.source_name,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def load_strategy_label_source_mapping(path: Path) -> StrategyLabelSourceMappingManifest:
    """Load one bounded strict JSON mapping without opening a database."""
    payload = path.read_bytes()
    if len(payload) > _MAX_MAPPING_BYTES:
        raise ValueError(f"mapping manifest exceeds {_MAX_MAPPING_BYTES} bytes")
    try:
        raw = json.loads(
            payload,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("mapping manifest must be strict UTF-8 JSON without duplicate keys") from exc
    if not isinstance(raw, dict):
        raise ValueError("mapping manifest root must be an object")
    try:
        return StrategyLabelSourceMappingManifest.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"invalid strategy-label source mapping: {exc}") from exc


def preflight_strategy_label_source_mapping(path: Path) -> StrategyLabelMappingPreflight:
    """Report completeness and emit a checksum only when the mapping is ready."""
    manifest = load_strategy_label_source_mapping(path)
    missing = manifest.missing_requirements
    return StrategyLabelMappingPreflight(
        schema_version="strategy_label_mapping_preflight_v1",
        source_name=manifest.source.name,
        ready=not missing,
        missing_requirements=missing,
        mapping_checksum=None if missing else manifest.mapping_checksum,
    )


def _missing_paths(value: Any, prefix: str = "") -> list[str]:
    if value is None or (isinstance(value, str) and _is_placeholder(value)):
        return [prefix]
    if isinstance(value, dict):
        missing: list[str] = []
        for key, nested in value.items():
            path = f"{prefix}.{key}" if prefix else key
            missing.extend(_missing_paths(nested, path))
        return missing
    if isinstance(value, list | tuple):
        missing = []
        for index, nested in enumerate(value):
            missing.extend(_missing_paths(nested, f"{prefix}[{index}]"))
        return missing
    return []


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        normalized in _PLACEHOLDERS
        or normalized.startswith("replace-with")
        or (normalized.startswith("<") and normalized.endswith(">"))
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, nested in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = nested
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value!r} is not permitted")
