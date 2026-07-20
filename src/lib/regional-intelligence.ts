export interface ConversationTurn {
  role: "user" | "assistant";
  content: string;
}

export const REGIONAL_EVIDENCE_SOURCES = [
  "drought",
  "streamflow",
  "strategyRecommendations",
  "soilProperties",
  "mtbsPerimeters",
  "carbonPotential",
] as const;

export type RegionalEvidenceSource =
  (typeof REGIONAL_EVIDENCE_SOURCES)[number];

/** Maximum accepted age for a published source before it is rendered as stale. */
export const REGIONAL_EVIDENCE_MAX_AGE_MS: Record<
  RegionalEvidenceSource,
  number
> = {
  drought: 14 * 24 * 60 * 60 * 1_000,
  streamflow: 6 * 60 * 60 * 1_000,
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
] as const;

export type InterventionStrategy = (typeof INTERVENTION_STRATEGIES)[number];

export interface RegionalIntelligenceResponse {
  riskSummary: {
    level: "low" | "moderate" | "high" | "critical";
    headline: string;
    factors: string[];
    evidenceSources: RegionalEvidenceSource[];
  };
  historicalEvents: {
    date: string;
    type: "wildfire";
    description: string;
    severity: "low" | "moderate" | "high" | "critical";
    evidenceSource: "mtbsPerimeters";
  }[];
  actionableItems: {
    priority: "immediate" | "short_term" | "long_term";
    action: string;
    rationale: string;
    strategy?: InterventionStrategy;
    evidenceSource: RegionalEvidenceSource;
  }[];
  interventionRecommendations: {
    strategy: InterventionStrategy;
    score: number;
    whyHere: string;
    evidenceSource: "strategyRecommendations" | "carbonPotential";
  }[];
  dataFreshness: Record<string, string>;
}
