import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
const mocks = vi.hoisted(() => ({ getServerSession: vi.fn() }));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));

import {
  acquireRegionalIntelligenceCapacity,
  releaseRegionalIntelligenceCapacity,
  remediationReportSchema,
} from "@/app/api/ai/regional-intelligence/route";

const validReport = {
  riskSummary: {
    level: "high" as const,
    headline: "Active fire detections sit within 25 km of this point.",
    factors: ["Six FIRMS detections in the last 24 hours."],
    evidenceOrigin: "warehouse" as const,
    evidenceSources: ["fireDetections" as const],
  },
  observations: [
    {
      statement: "Six fire detections were observed nearby today.",
      evidenceOrigin: "warehouse" as const,
      evidenceSource: "fireDetections" as const,
    },
  ],
  remediation: [
    {
      strategy: "fuel_reduction" as const,
      title: "Reduce surface fuel loading around structures",
      rationale: "Detections upwind raise short-term ignition exposure.",
      timeframe: "immediate" as const,
      confidence: "moderate" as const,
      consultProfessionals: ["wildfire_mitigation_specialist" as const],
      evidenceOrigin: "model_inference" as const,
    },
  ],
  professionalConsultation:
    "Confirm defensible-space spacing with a local wildfire mitigation specialist before clearing.",
};

describe("remediation report contract", () => {
  afterEach(() => {
    releaseRegionalIntelligenceCapacity();
    vi.unstubAllEnvs();
  });

  it("accepts a well-formed AI-generated report", () => {
    const parsed = remediationReportSchema.parse(validReport);
    expect(parsed.remediation[0].strategy).toBe("fuel_reduction");
  });

  it("requires a professional-consultation statement", () => {
    const { professionalConsultation: _omitted, ...withoutConsultation } =
      validReport;
    expect(() => remediationReportSchema.parse(withoutConsultation)).toThrow();
  });

  it("rejects an unknown evidence origin", () => {
    expect(() =>
      remediationReportSchema.parse({
        ...validReport,
        riskSummary: { ...validReport.riskSummary, evidenceOrigin: "vibes" },
      })
    ).toThrow();
  });

  it("rejects a free-form remediation strategy", () => {
    expect(() =>
      remediationReportSchema.parse({
        ...validReport,
        remediation: [
          { ...validReport.remediation[0], strategy: "invented_strategy" },
        ],
      })
    ).toThrow();
  });

  it("rejects unknown fields smuggled into a remediation item", () => {
    expect(() =>
      remediationReportSchema.parse({
        ...validReport,
        remediation: [
          { ...validReport.remediation[0], suppliersAvailable: true },
        ],
      })
    ).toThrow();
  });

  it("bounds per-replica AI concurrency and permits reuse after release", () => {
    vi.stubEnv("REGIONAL_INTELLIGENCE_MAX_CONCURRENT_PER_REPLICA", "1");

    expect(acquireRegionalIntelligenceCapacity()).toBe(true);
    expect(acquireRegionalIntelligenceCapacity()).toBe(false);
    releaseRegionalIntelligenceCapacity();
    expect(acquireRegionalIntelligenceCapacity()).toBe(true);
  });
});
