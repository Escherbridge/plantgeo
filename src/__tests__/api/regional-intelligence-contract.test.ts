import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/server/db", () => ({ db: {} }));
const mocks = vi.hoisted(() => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));

import {
  POST,
  acquireRegionalIntelligenceCapacity,
  regionalResponseSchema,
  releaseRegionalIntelligenceCapacity,
  responseUsesAvailableEvidence,
} from "@/app/api/ai/regional-intelligence/route";

const validResponse = {
  riskSummary: {
    level: "high" as const,
    headline: "Published drought conditions warrant attention.",
    factors: ["D3 drought intersects the selected point."],
    evidenceSources: ["drought" as const],
  },
  historicalEvents: [],
  actionableItems: [
    {
      priority: "short_term" as const,
      action: "Review the verified drought release.",
      rationale: "The selected point intersects its D3 polygon.",
      evidenceSource: "drought" as const,
    },
  ],
  interventionRecommendations: [],
};

describe("regional intelligence evidence contract", () => {
  afterEach(() => {
    releaseRegionalIntelligenceCapacity();
    vi.unstubAllEnvs();
  });

  it("accepts constrained claims backed by an available source", () => {
    const parsed = regionalResponseSchema.parse(validResponse);
    expect(
      responseUsesAvailableEvidence(parsed, {
        drought: new Date().toISOString(),
      })
    ).toBe(true);
  });

  it("rejects claims backed by unavailable evidence", () => {
    const parsed = regionalResponseSchema.parse(validResponse);
    expect(
      responseUsesAvailableEvidence(parsed, { drought: "unavailable" })
    ).toBe(false);
  });

  it("rejects claims backed by stale evidence", () => {
    const parsed = regionalResponseSchema.parse(validResponse);
    expect(
      responseUsesAvailableEvidence(parsed, {
        drought: new Date(
          Date.now() - 15 * 24 * 60 * 60 * 1_000
        ).toISOString(),
      })
    ).toBe(false);
  });

  it("rejects free-form strategies, unbounded scores, and supplier claims", () => {
    const recommendation = {
      strategy: "invented_strategy",
      score: 120,
      whyHere: "Unsupported",
      evidenceSource: "strategyRecommendations",
      suppliersAvailable: true,
    };
    expect(() =>
      regionalResponseSchema.parse({
        ...validResponse,
        interventionRecommendations: [recommendation],
      })
    ).toThrow();
  });

  it("fails closed while the provenance-backed serving contract is inactive", async () => {
    const response = await POST(
      new NextRequest("https://plantgeo.test/api/ai/regional-intelligence", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": String(16 * 1024 + 1),
        },
        body: JSON.stringify({ lat: 40, lon: -105 }),
      })
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toEqual({
      error:
        "Regional intelligence is paused until warehouse-backed evidence and its provenance contract are published",
      retryable: false,
    });
  });

  it("bounds per-replica AI concurrency and permits reuse after release", () => {
    vi.stubEnv("REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA", "1");

    expect(acquireRegionalIntelligenceCapacity()).toBe(true);
    expect(acquireRegionalIntelligenceCapacity()).toBe(false);
    releaseRegionalIntelligenceCapacity();
    expect(acquireRegionalIntelligenceCapacity()).toBe(true);
  });
});
