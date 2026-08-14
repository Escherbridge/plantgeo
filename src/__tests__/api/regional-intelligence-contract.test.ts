import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  // The SDK client streamRegionalIntelligence builds internally. Mocked at the module boundary
  // rather than passed in, exactly like every other service call in this file, so the request
  // it actually issues -- the `tools` array in particular -- is observable from the test.
  anthropicStream: vi.fn(),
}));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));
vi.mock("@anthropic-ai/sdk", () => ({
  default: class MockAnthropic {
    messages = { stream: mocks.anthropicStream };
  },
}));

import {
  acquireRegionalIntelligenceCapacity,
  releaseRegionalIntelligenceCapacity,
  remediationReportSchema,
} from "@/app/api/ai/regional-intelligence/route";
import type {
  RegionalContextPayload,
  TemporalContext,
} from "@/lib/server/services/regional-context";

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

  it("validates generate_remediation_report tool definition", async () => {
    const { GENERATE_REMEDIATION_REPORT_TOOL } = await import(
      "@/lib/server/services/ai-prompt"
    );
    expect(GENERATE_REMEDIATION_REPORT_TOOL.name).toBe("generate_remediation_report");
    expect(GENERATE_REMEDIATION_REPORT_TOOL.input_schema).toBeDefined();
  });
});

/**
 * Prior to 2026-08-14, GENERATE_REMEDIATION_REPORT_TOOL was defined and exported (the previous
 * test confirms that much still holds) but never included in the `tools` array actually sent to
 * Anthropic, and the report-dispatch `find()` only matched REPORT_TOOL's name. A model that took
 * the system prompt's own "call remediation_report or generate_remediation_report" instruction at
 * its word could never actually reach it: the tool was not offered, and if it somehow were called
 * anyway the response would never be recognized as the report. These tests exercise the real
 * `streamRegionalIntelligence` agent loop against a mocked Anthropic client and would have failed
 * against the pre-fix code on both counts.
 */
describe("generate_remediation_report tool wiring", () => {
  afterEach(() => {
    mocks.anthropicStream.mockReset();
    vi.unstubAllEnvs();
  });

  function minimalPayload(): RegionalContextPayload {
    return {
      location: { lat: 43.6, lon: -116.2, geohash: "43.60_-116.20" },
      strategyRecommendations: null,
      strategyContext: [],
      communityProposals: [],
      soilProperties: null,
      waterScarcity: null,
      weather: null,
      fireDetections: null,
      firePerimeters: null,
      mtbsPerimeters: null,
      carbonPotential: null,
    };
  }

  function minimalTemporalContext(): TemporalContext {
    return {
      serverCurrentDate: "2026-08-14",
      viewedLayersUnreported: true,
      readings: [],
      viewedDates: [],
      sourcesServedAsOfLatest: [],
    };
  }

  /** A `MessageStream`-shaped stand-in: no text deltas, `finalMessage()` resolves immediately. */
  function fakeAnthropicStream(finalMessage: {
    stop_reason: string;
    content: Array<{ type: string; id: string; name: string; input: unknown }>;
  }) {
    return {
      [Symbol.asyncIterator]: () =>
        (async function* () {
          /* no text deltas for this fixture */
        })(),
      finalMessage: async () => finalMessage,
    };
  }

  it("includes generate_remediation_report in the tools array actually sent to Anthropic", async () => {
    const { streamRegionalIntelligence, REPORT_TOOL, GENERATE_REMEDIATION_REPORT_TOOL } =
      await import("@/lib/server/services/ai-prompt");

    let capturedTools: Array<{ name: string }> | undefined;
    mocks.anthropicStream.mockImplementation(
      (request: { tools: Array<{ name: string }> }) => {
        capturedTools = request.tools;
        return fakeAnthropicStream({
          stop_reason: "tool_use",
          content: [
            { type: "tool_use", id: "toolu_1", name: REPORT_TOOL.name, input: validReport },
          ],
        });
      }
    );

    const events = [];
    for await (const event of streamRegionalIntelligence(
      minimalPayload(),
      { drought: "unavailable" },
      true,
      minimalTemporalContext(),
      [],
      "What should I do here?"
    )) {
      events.push(event);
    }

    expect(capturedTools?.map((tool) => tool.name)).toEqual(
      expect.arrayContaining([REPORT_TOOL.name, GENERATE_REMEDIATION_REPORT_TOOL.name])
    );
  });

  it("dispatches a generate_remediation_report tool_use as the report, in a single round", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");

    mocks.anthropicStream.mockImplementation(() =>
      fakeAnthropicStream({
        stop_reason: "tool_use",
        content: [
          {
            type: "tool_use",
            id: "toolu_2",
            name: "generate_remediation_report",
            input: validReport,
          },
        ],
      })
    );

    const events: { type: string; report?: unknown }[] = [];
    for await (const event of streamRegionalIntelligence(
      minimalPayload(),
      { drought: "unavailable" },
      true,
      minimalTemporalContext(),
      [],
      "What should I do here?"
    )) {
      events.push(event as { type: string; report?: unknown });
    }

    const reportEvent = events.find((event) => event.type === "report");
    expect(reportEvent?.report).toEqual(validReport);
    // Would have kept nudging for the full MAX_TOOL_ROUNDS before the dispatch fix, since a
    // generate_remediation_report tool_use was never recognized as the report.
    expect(mocks.anthropicStream).toHaveBeenCalledTimes(1);
  });
});
