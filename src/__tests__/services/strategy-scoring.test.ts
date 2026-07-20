import { describe, expect, it } from "vitest";
import {
  getStrategyRecommendationResult,
  getStrategyRecommendations,
  STRATEGY_EVIDENCE_UNAVAILABLE_CODE,
} from "@/lib/server/services/strategy-scoring";

describe("strategy recommendation evidence gate", () => {
  it("reports unavailable without manufacturing coordinate-derived scores", () => {
    expect(getStrategyRecommendationResult(39.7392, -104.9903)).toEqual({
      status: "unavailable",
      code: STRATEGY_EVIDENCE_UNAVAILABLE_CODE,
      reason:
        "Strategy recommendations are unavailable until a validated warehouse evidence release is published.",
      recommendations: [],
      evidenceRevision: null,
      observedAt: null,
    });
  });

  it("keeps the legacy array API fail-closed", async () => {
    await expect(getStrategyRecommendations(39.7392, -104.9903)).rejects.toMatchObject({
      code: STRATEGY_EVIDENCE_UNAVAILABLE_CODE,
    });
  });

  it("rejects invalid coordinates before reporting availability", () => {
    expect(() => getStrategyRecommendationResult(91, 0)).toThrow(RangeError);
    expect(() => getStrategyRecommendationResult(0, 181)).toThrow(RangeError);
  });
});
