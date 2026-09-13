"""Structured remediation report: the agent graph's only user-visible output."""
# ruff: noqa: N815 -- Field names are the frontend wire contract, mirrored verbatim from
# ai-prompt.ts's REPORT_TOOL so the Next.js renderer can switch endpoints unchanged.
# See agent/AGENTS.md, "Report vocabulary".

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Vocabularies mirrored from src/lib/regional-intelligence.ts. That module is the single
# definition; these are its Python projection and must not drift. See agent/AGENTS.md.
EvidenceOrigin = Literal["warehouse", "web", "model_inference"]

RegionalEvidenceSource = Literal[
    "drought",
    "streamflow",
    "weatherObservations",
    "fireDetections",
    "firePerimeters",
    "strategyRecommendations",
    "soilProperties",
    "mtbsPerimeters",
    "carbonPotential",
]

RegionalToolEvidenceSource = Literal[
    "burn-severity",
    "evacuation-zones",
    "fire-detections",
    "fire-perimeters",
    "interventions",
    "sensors",
    "soil-survey",
    "vegetation",
    "watersheds",
    "water-gauges",
    "weather-observations",
    "climate-field-air-temperature",
    "climate-field-dew-point",
    "climate-field-precipitation",
    "climate-field-relative-humidity",
    "climate-field-shortwave-radiation",
    "climate-field-soil-wetness-profile",
    "climate-field-soil-wetness-root-zone",
    "climate-field-soil-wetness-surface",
    "climate-field-wind-speed",
    "drought-areas",
    "soil-field-moisture",
    "soil-field-temperature",
    "soil-field-vpd",
]
RegionalClaimEvidenceSource = RegionalEvidenceSource | RegionalToolEvidenceSource

InterventionStrategy = Literal[
    "keyline",
    "silvopasture",
    "reforestation",
    "biochar",
    "water_harvesting",
    "cover_cropping",
    "fuel_reduction",
    "riparian_buffer",
    "erosion_control",
    "managed_grazing",
    "other",
]

ProfessionalDiscipline = Literal[
    "agronomist",
    "hydrologist",
    "forester",
    "soil_scientist",
    "wildfire_mitigation_specialist",
    "extension_service",
    "conservation_district",
    "ecologist",
    "land_use_planner",
]

RiskLevel = Literal["low", "moderate", "high", "critical"]
Timeframe = Literal["immediate", "short_term", "long_term"]
ConfidenceLevel = Literal["low", "moderate", "high"]
EvidenceReadId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

# Rendered verbatim wherever agent output appears; copied from regional-intelligence.ts.
AI_GENERATED_DISCLAIMER: Final = (
    "This analysis is AI-generated and may be incomplete or wrong. It is not professional "
    "advice. Confirm any remediation plan with a qualified local practitioner before acting on it."
)


class EvidenceClaim(BaseModel):
    """Shared evidence origin and optional executed-read references."""

    model_config = ConfigDict(extra="forbid")

    evidenceOrigin: EvidenceOrigin
    evidenceReadIds: list[EvidenceReadId] = Field(
        default_factory=list, max_length=8, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def read_ids_require_warehouse_origin(self) -> EvidenceClaim:
        """Keep server read references off web and model-inference claims."""
        if "evidenceReadIds" in self.model_fields_set and self.evidenceOrigin != "warehouse":
            raise ValueError("evidenceReadIds are allowed only on warehouse-origin claims")
        return self


class RiskSummary(EvidenceClaim):
    """Headline risk judgement and the provenance it rests on."""

    level: RiskLevel
    headline: str
    factors: list[str]
    evidenceSources: list[RegionalClaimEvidenceSource] = Field(max_length=33)


class Observation(EvidenceClaim):
    """One statement about what the supplied data actually shows."""

    statement: str
    evidenceSource: RegionalClaimEvidenceSource | None = None


class RemediationRecommendation(EvidenceClaim):
    """One recommended intervention, with the disciplines to consult before acting."""

    strategy: InterventionStrategy
    title: str
    rationale: str
    timeframe: Timeframe
    confidence: ConfidenceLevel
    consultProfessionals: list[ProfessionalDiscipline]
    evidenceSource: RegionalClaimEvidenceSource | None = None


class RemediationReport(BaseModel):
    """The full briefing, shaped exactly like ai-prompt.ts's remediation_report tool input."""

    model_config = ConfigDict(extra="forbid")

    riskSummary: RiskSummary
    observations: list[Observation]
    remediation: list[RemediationRecommendation]
    professionalConsultation: str


class WebSourceCitation(BaseModel):
    """One web page the agent actually consulted, mirroring the TypeScript interface."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str


class ConversationTurn(BaseModel):
    """One replayed turn of prior conversation, mirroring the TypeScript interface."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


def report_json_schema() -> dict[str, Any]:
    """Return the report's JSON Schema for output_config.format / documentation."""
    return RemediationReport.model_json_schema()
