export interface RecommendationResponse {
  availability: "published";
  modelReleaseId: string;
  primarySolution: string;
  rationale: string;
  recommendedPlants: Record<string, unknown>[];
  recommendedTooling: Record<string, unknown>[];
}

export class RecommendationEvidenceUnavailableError extends Error {
  constructor() {
    super("No approved strategy-recommendation model release is published");
    this.name = "RecommendationEvidenceUnavailableError";
  }
}

/** Refuses to manufacture an operational recommendation from coordinates. */
export async function generateRecommendation(
  lat: number,
  lon: number
): Promise<RecommendationResponse> {
  void lat;
  void lon;
  throw new RecommendationEvidenceUnavailableError();
}
