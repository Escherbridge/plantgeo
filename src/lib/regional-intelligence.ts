export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

/**
 * Warehouse layers the agent may be given as observed context. Membership here
 * means "the platform can persist and date-stamp this", not "the agent may only
 * speak about this" — see `src/lib/server/AGENTS.md` §regional-intelligence.
 */
export const REGIONAL_EVIDENCE_SOURCES = [
  "drought",
  "streamflow",
  "weatherObservations",
  "fireDetections",
  "firePerimeters",
  "strategyRecommendations",
  "soilProperties",
  "mtbsPerimeters",
  "carbonPotential",
] as const;

export type RegionalEvidenceSource =
  (typeof REGIONAL_EVIDENCE_SOURCES)[number];

/** Provenance label attached to every claim the agent renders. */
export const EVIDENCE_ORIGINS = [
  "warehouse",
  "web",
  "model_inference",
] as const;

export type EvidenceOrigin = (typeof EVIDENCE_ORIGINS)[number];

/** Maximum accepted age for a published source before it is rendered as stale. */
export const REGIONAL_EVIDENCE_MAX_AGE_MS: Record<
  RegionalEvidenceSource,
  number
> = {
  drought: 14 * 24 * 60 * 60 * 1_000,
  streamflow: 6 * 60 * 60 * 1_000,
  weatherObservations: 6 * 60 * 60 * 1_000,
  fireDetections: 48 * 60 * 60 * 1_000,
  firePerimeters: 14 * 24 * 60 * 60 * 1_000,
  strategyRecommendations: 30 * 24 * 60 * 60 * 1_000,
  soilProperties: 180 * 24 * 60 * 60 * 1_000,
  mtbsPerimeters: 400 * 24 * 60 * 60 * 1_000,
  carbonPotential: 30 * 24 * 60 * 60 * 1_000,
};

export type RegionalEvidenceFreshnessState =
  | "available"
  | "pending"
  | "stale"
  | "unavailable";

export function isRegionalEvidenceSource(
  value: string
): value is RegionalEvidenceSource {
  return (REGIONAL_EVIDENCE_SOURCES as readonly string[]).includes(value);
}

/** Classifies a source timestamp consistently on the server and in the UI. */
export function regionalEvidenceFreshnessState(
  source: RegionalEvidenceSource,
  value: string | undefined,
  now = Date.now()
): RegionalEvidenceFreshnessState {
  if (!value || value === "unavailable") return "unavailable";
  if (value === "published_revision_required") return "pending";

  const observedAt = Date.parse(value);
  if (!Number.isFinite(observedAt) || observedAt > now) {
    return "unavailable";
  }
  return now - observedAt <= REGIONAL_EVIDENCE_MAX_AGE_MS[source]
    ? "available"
    : "stale";
}

export const INTERVENTION_STRATEGIES = [
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
] as const;

export type InterventionStrategy = (typeof INTERVENTION_STRATEGIES)[number];

/** Professions the agent may direct a user toward before they act on advice. */
export const PROFESSIONAL_DISCIPLINES = [
  "agronomist",
  "hydrologist",
  "forester",
  "soil_scientist",
  "wildfire_mitigation_specialist",
  "extension_service",
  "conservation_district",
  "ecologist",
  "land_use_planner",
] as const;

export type ProfessionalDiscipline = (typeof PROFESSIONAL_DISCIPLINES)[number];

/**
 * Shown verbatim wherever agent output is rendered. Both halves are product
 * requirements, not stylistic copy: the output must identify itself as
 * AI-generated and must route the reader to a qualified human.
 */
export const AI_GENERATED_LABEL = "AI-generated";

export const AI_GENERATED_DISCLAIMER =
  "This analysis is AI-generated and may be incomplete or wrong. It is not professional advice. Confirm any remediation plan with a qualified local practitioner before acting on it.";

export interface WebSourceCitation {
  title: string;
  url: string;
}

export interface RemediationRecommendation {
  strategy: InterventionStrategy;
  title: string;
  rationale: string;
  timeframe: "immediate" | "short_term" | "long_term";
  confidence: "low" | "moderate" | "high";
  /** Disciplines to consult before this recommendation is acted on. */
  consultProfessionals: ProfessionalDiscipline[];
  evidenceOrigin: EvidenceOrigin;
  evidenceSource?: RegionalEvidenceSource;
}

export interface RegionalIntelligenceResponse {
  /** Structural marker so no renderer can present this as a validated product. */
  aiGenerated: true;
  riskSummary: {
    level: "low" | "moderate" | "high" | "critical";
    headline: string;
    factors: string[];
    evidenceOrigin: EvidenceOrigin;
    evidenceSources: RegionalEvidenceSource[];
  };
  observations: {
    statement: string;
    evidenceOrigin: EvidenceOrigin;
    evidenceSource?: RegionalEvidenceSource;
  }[];
  remediation: RemediationRecommendation[];
  professionalConsultation: string;
  webSources: WebSourceCitation[];
  dataFreshness: Record<string, string>;
}
