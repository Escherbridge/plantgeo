import { describe, expect, it } from "vitest";
import {
  generateRecommendation,
  RecommendationEvidenceUnavailableError,
} from "@/lib/server/services/agent-engine";

describe("generateRecommendation", () => {
  it("fails closed when no approved model release is published", async () => {
    await expect(generateRecommendation(40, -105)).rejects.toBeInstanceOf(
      RecommendationEvidenceUnavailableError
    );
  });
});
