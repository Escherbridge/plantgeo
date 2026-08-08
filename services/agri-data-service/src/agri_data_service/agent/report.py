"""Structured remediation report: the agent graph's only user-visible output."""
# ruff: noqa: N815 -- Field names are the frontend wire contract, mirrored verbatim from
# ai-prompt.ts's REPORT_TOOL so the Next.js renderer can switch endpoints unchanged.
# See agent/AGENTS.md, "Report vocabulary".

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

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

# Rendered verbatim wherever agent output appears; copied from regional-intelligence.ts.
AI_GENERATED_DISCLAIMER: Final = (
    "This analysis is AI-generated and may be incomplete or wrong. It is not professional "
    "advice. Confirm any remediation plan with a qualified local practitioner before acting on it."
)


class RiskSummary(BaseModel):
    """Headline risk judgement and the provenance it rests on."""

    model_config = ConfigDict(extra="forbid")

    level: RiskLevel
    headline: str
    factors: list[str]
    evidenceOrigin: EvidenceOrigin
    evidenceSources: list[RegionalEvidenceSource]


class Observation(BaseModel):
    """One statement about what the supplied data actually shows."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    evidenceOrigin: EvidenceOrigin
    evidenceSource: RegionalEvidenceSource | None = None


class RemediationRecommendation(BaseModel):
    """One recommended intervention, with the disciplines to consult before acting."""

    model_config = ConfigDict(extra="forbid")

    strategy: InterventionStrategy
    title: str
    rationale: str
    timeframe: Timeframe
    confidence: ConfidenceLevel
    consultProfessionals: list[ProfessionalDiscipline]
    evidenceOrigin: EvidenceOrigin
    evidenceSource: RegionalEvidenceSource | None = None


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
