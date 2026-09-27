"""Structured remediation report: the agent graph's only user-visible output."""
# ruff: noqa: N815 -- Field names are the frontend wire contract, mirrored verbatim from
# ai-prompt.ts's REPORT_TOOL so the Next.js renderer can switch endpoints unchanged.
# See agent/AGENTS.md, "Report vocabulary".

from __future__ import annotations

from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Vocabularies mirrored from src/lib/regional-intelligence.ts. That module is the single
# definition; these are its Python projection and must not drift. See agent/AGENTS.md.
EvidenceOrigin = Literal["warehouse", "web", "literature", "model_inference"]

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
    "soil-phh2o",
    "soil-soc",
    "soil-nitrogen",
    "soil-bdod",
    "soil-cec",
    "soil-ocd",
    "botanical-occurrences",
    "botanical-richness",
    "botanical-collection-effort",
    "gbif-occurrences",
    "demand-heatmap",
    "strategy-recommendations",
    "land-context",
    "land-context-boundaries",
    "crop-cover",
    "fire-risk",
    "weather-forecast",
    "groundwater",
    "strategy-knowledge",
]
RegionalClaimEvidenceSource = RegionalEvidenceSource | RegionalToolEvidenceSource

#: The one origin/source pair a strategy-knowledge (literature) claim carries; see agent/AGENTS.md.
LITERATURE_EVIDENCE_ORIGIN: Final = "literature"
STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE: Final = "strategy-knowledge"
MODEL_INFERENCE_EVIDENCE_ORIGIN: Final = "model_inference"

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
        """Keep server read references off web, literature and model-inference claims."""
        if "evidenceReadIds" in self.model_fields_set and self.evidenceOrigin != "warehouse":
            raise ValueError("evidenceReadIds are allowed only on warehouse-origin claims")
        return self


def _require_literature_pairing(origin: str, source: str | None) -> None:
    """Literature cites exactly strategy-knowledge, and only literature may cite strategy-knowledge."""
    if origin == LITERATURE_EVIDENCE_ORIGIN and source != STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE:
        raise ValueError('evidenceOrigin "literature" requires evidenceSource "strategy-knowledge"')
    if source == STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE and origin != LITERATURE_EVIDENCE_ORIGIN:
        raise ValueError('evidenceSource "strategy-knowledge" is allowed only with evidenceOrigin "literature"')


def _pair_literature_provenance(data: Any) -> Any:
    """Fill a missing "strategy-knowledge" source on literature claims and strip it from
    web/model_inference claims, ahead of the strict after-validator. Never changes an origin or
    touches read IDs; a genuinely conflicting source (e.g. literature + "soil-phh2o") is left
    alone for `_require_literature_pairing` to reject. Mirrors `pairLiteratureProvenance` in
    remediation-report.ts. See agent/AGENTS.md, "Literature/strategy-knowledge normalisation".
    """
    if not isinstance(data, dict):
        return data
    origin = data.get("evidenceOrigin")
    source = data.get("evidenceSource")
    if origin == LITERATURE_EVIDENCE_ORIGIN and source in (None, ""):
        return {**data, "evidenceSource": STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE}
    if origin in (MODEL_INFERENCE_EVIDENCE_ORIGIN, "web") and source == STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE:
        paired = dict(data)
        paired.pop("evidenceSource", None)
        return paired
    return data


class RiskSummary(EvidenceClaim):
    """Headline risk judgement and the provenance it rests on."""

    level: RiskLevel
    headline: str
    factors: list[str]
    evidenceSources: list[RegionalClaimEvidenceSource] = Field(max_length=64)

    @model_validator(mode="after")
    def risk_is_never_literature(self) -> RiskSummary:
        """Keep literature out of the risk judgement, matching remediation-report.ts."""
        if (
            self.evidenceOrigin == LITERATURE_EVIDENCE_ORIGIN
            or STRATEGY_KNOWLEDGE_EVIDENCE_SOURCE in self.evidenceSources
        ):
            raise ValueError('riskSummary cannot be literature-origin or cite "strategy-knowledge"')
        return self


class Observation(EvidenceClaim):
    """One statement about what the supplied data actually shows."""

    statement: str
    evidenceSource: RegionalClaimEvidenceSource | None = None

    @model_validator(mode="before")
    @classmethod
    def pair_literature_provenance(cls, data: Any) -> Any:
        """Normalise the literature/strategy-knowledge pairing before field validation."""
        return _pair_literature_provenance(data)

    @model_validator(mode="after")
    def literature_pairs_with_strategy_knowledge(self) -> Observation:
        """Enforce the literature origin/source pairing."""
        _require_literature_pairing(self.evidenceOrigin, self.evidenceSource)
        return self


class RemediationRecommendation(EvidenceClaim):
    """One recommended intervention, with the disciplines to consult before acting."""

    strategy: InterventionStrategy
    title: str
    rationale: str
    timeframe: Timeframe
    confidence: ConfidenceLevel
    consultProfessionals: list[ProfessionalDiscipline]
    evidenceSource: RegionalClaimEvidenceSource | None = None

    @model_validator(mode="before")
    @classmethod
    def pair_literature_provenance(cls, data: Any) -> Any:
        """Normalise the literature/strategy-knowledge pairing before field validation."""
        return _pair_literature_provenance(data)

    @model_validator(mode="after")
    def literature_pairs_with_strategy_knowledge(self) -> RemediationRecommendation:
        """Enforce the literature origin/source pairing."""
        _require_literature_pairing(self.evidenceOrigin, self.evidenceSource)
        return self


class RemediationReport(BaseModel):
    """The full briefing, shaped exactly like ai-prompt.ts's remediation_report tool input."""

    model_config = ConfigDict(extra="forbid")

    riskSummary: RiskSummary
    observations: list[Observation]
    remediation: list[RemediationRecommendation]
    professionalConsultation: str


_SINGLE_SOURCE_SECTIONS: Final = ("observations", "remediation")


def downgrade_literature_claims(report: RemediationReport) -> tuple[RemediationReport, tuple[str, ...]]:
    """Relabel every literature claim model_inference without its source; return the paths changed.

    Used when no strategy-knowledge call answered this run. See agent/AGENTS.md, "A literature claim
    needs a literature answer from this run".
    """
    payload = report.model_dump()
    downgraded: list[str] = []
    for section in _SINGLE_SOURCE_SECTIONS:
        for index, claim in enumerate(payload[section]):
            if claim["evidenceOrigin"] == LITERATURE_EVIDENCE_ORIGIN:
                claim["evidenceOrigin"] = MODEL_INFERENCE_EVIDENCE_ORIGIN
                claim["evidenceSource"] = None
                downgraded.append(f"{section}[{index}]")
    if not downgraded:
        return report, ()
    return RemediationReport.model_validate(payload), tuple(downgraded)


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
