import type { StrategyScore } from "@/lib/strategy-contracts";

export type { StrategyScore } from "@/lib/strategy-contracts";

export const STRATEGY_EVIDENCE_UNAVAILABLE_CODE =
  "VALIDATED_STRATEGY_EVIDENCE_NOT_PUBLISHED" as const;

export interface StrategyRecommendationUnavailable {
  status: "unavailable";
  code: typeof STRATEGY_EVIDENCE_UNAVAILABLE_CODE;
  reason: string;
  recommendations: [];
  evidenceRevision: null;
  observedAt: null;
}

export type StrategyRecommendationResult =
  | {
      status: "available";
      recommendations: StrategyScore[];
      evidenceRevision: string;
      observedAt: string;
    }
  | StrategyRecommendationUnavailable;

export class StrategyEvidenceUnavailableError extends Error {
  readonly code = STRATEGY_EVIDENCE_UNAVAILABLE_CODE;

  constructor(readonly result: StrategyRecommendationUnavailable) {
    super(result.reason);
    this.name = "StrategyEvidenceUnavailableError";
  }
}

export function getStrategyRecommendationResult(
  lat: number,
  lon: number
): StrategyRecommendationResult {
  if (!Number.isFinite(lat) || lat < -90 || lat > 90) {
    throw new RangeError("Latitude must be between -90 and 90");
  }
  if (!Number.isFinite(lon) || lon < -180 || lon > 180) {
    throw new RangeError("Longitude must be between -180 and 180");
  }

  return {
    status: "unavailable",
    code: STRATEGY_EVIDENCE_UNAVAILABLE_CODE,
    reason:
      "Strategy recommendations are unavailable until a validated warehouse evidence release is published.",
    recommendations: [],
    evidenceRevision: null,
    observedAt: null,
  };
}

export async function getStrategyRecommendations(
  lat: number,
  lon: number
): Promise<StrategyScore[]> {
  const result = getStrategyRecommendationResult(lat, lon);
  if (result.status === "available") {
    return result.recommendations;
  }
  throw new StrategyEvidenceUnavailableError(result);
}
