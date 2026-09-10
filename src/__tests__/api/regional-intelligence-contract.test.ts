import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/server/db", () => ({ db: {} }));
const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  // The SDK client streamRegionalIntelligence builds internally. Mocked at the module boundary
  // rather than passed in, exactly like every other service call in this file, so the request
  // it actually issues -- the `tools` array in particular -- is observable from the test.
  completionStream: vi.fn(),
}));
vi.mock("@/lib/server/auth", () => ({
  getServerSession: mocks.getServerSession,
}));
vi.mock("openai", () => ({
  default: class MockOpenAI {
    chat = { completions: { stream: mocks.completionStream } };
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

  it("distinguishes single readings, perimeter records and unmeasured fuels in its evidence rules", async () => {
    const { buildSystemPrompt } = await import("@/lib/server/services/ai-prompt");
    const prompt = buildSystemPrompt(false);
    expect(prompt).toContain("A single streamflow reading establishes a flow at its own observation time, not a trend");
    expect(prompt).toContain("Attribute named-day streamflow to observedDay");
    expect(prompt).toContain("never replace observedDay with the date obtained by converting updatedAt");
    expect(prompt).toContain("firePerimeters contains perimeter records, not active satellite detections");
    expect(prompt).toContain("Missing vegetation/fuels evidence cannot establish abundant, dry or available fuel");
  });

  it("advertises the same report limits enforced by validation", async () => {
    const { REPORT_TOOL, GENERATE_REMEDIATION_REPORT_TOOL } = await import("@/lib/server/services/ai-prompt");
    expect(REPORT_TOOL.input_schema).toMatchObject({
      additionalProperties: false,
      properties: {
        riskSummary: { properties: {
          headline: { minLength: 1, maxLength: 300 },
          factors: { maxItems: 8, items: { minLength: 1, maxLength: 240 } },
          evidenceSources: { maxItems: 9 },
        } },
        observations: { maxItems: 12, items: { properties: { statement: { minLength: 1, maxLength: 500 } } } },
        remediation: { maxItems: 8, items: { properties: {
          title: { maxLength: 160 }, rationale: { maxLength: 900 }, consultProfessionals: { maxItems: 5 },
        } } },
        professionalConsultation: { minLength: 1, maxLength: 600 },
      },
    });
    expect(GENERATE_REMEDIATION_REPORT_TOOL.input_schema).toBe(REPORT_TOOL.input_schema);
    expect(remediationReportSchema.safeParse({ ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) }).success).toBe(false);
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
    mocks.completionStream.mockReset();
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

  /**
   * A `ChatCompletionStream`-shaped stand-in: no text deltas, `finalChatCompletion()` resolves
   * immediately. Tool arguments are SERIALISED here on purpose -- the completions dialect delivers
   * them as a JSON string, and a fixture that handed back a live object would pass against a
   * service that never parses one.
   */
  function fakeCompletionStream(
    toolCalls: Array<{ id: string; name: string; input: unknown }>,
    narration?: string
  ) {
    return {
      [Symbol.asyncIterator]: () =>
        (async function* () {
          if (narration) yield { choices: [{ delta: { content: narration } }] };
        })(),
      finalChatCompletion: async () => ({
        choices: [
          {
            message: {
              role: "assistant",
              content: null,
              refusal: null,
              tool_calls: toolCalls.map((call) => ({
                id: call.id,
                type: "function",
                function: {
                  name: call.name,
                  arguments: JSON.stringify(call.input),
                },
              })),
            },
          },
        ],
      }),
    };
  }

  it("includes generate_remediation_report in the tools array actually sent to the provider", async () => {
    const { streamRegionalIntelligence, REPORT_TOOL, GENERATE_REMEDIATION_REPORT_TOOL } =
      await import("@/lib/server/services/ai-prompt");

    let capturedTools:
      | Array<{ function: { name: string } }>
      | undefined;
    mocks.completionStream.mockImplementation(
      (request: { tools: Array<{ function: { name: string } }> }) => {
        capturedTools = request.tools;
        return fakeCompletionStream([
          { id: "call_1", name: REPORT_TOOL.name, input: validReport },
        ]);
      }
    );

    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
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

    expect(capturedTools?.map((tool) => tool.function.name)).toEqual(
      expect.arrayContaining([REPORT_TOOL.name, GENERATE_REMEDIATION_REPORT_TOOL.name])
    );
  });

  it("dispatches a generate_remediation_report tool_use as the report, in a single round", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");

    mocks.completionStream.mockImplementation(() =>
      fakeCompletionStream([
        { id: "call_2", name: "generate_remediation_report", input: validReport },
      ])
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
    expect(mocks.completionStream).toHaveBeenCalledTimes(1);
  });

  it("sends nitrogen and carbon to the model with g/kg units and soil prediction provenance", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    mocks.completionStream.mockImplementation(() => fakeCompletionStream([{ id: "soil_report", name: "remediation_report", input: validReport }]));
    const payload = minimalPayload();
    payload.soilProperties = { ph: 6.6, organicCarbon: 61.9, nitrogen: 5.12, bulkDensity: 1.25, cec: 14, ocd: 3.2 };
    const events = [];
    for await (const event of streamRegionalIntelligence(payload, {}, false, minimalTemporalContext(), [])) events.push(event);
    const sent = JSON.stringify(mocks.completionStream.mock.calls[0]?.[0]);
    expect(sent).toContain("SoilGrids model predictions; not a local soil sample");
    expect(sent).toContain("g/kg");
    expect(sent).toContain("61.9");
    expect(sent).toContain("5.12");
    expect(sent).toContain("divide g/kg by 10");
    expect(events.some((event) => event.type === "report")).toBe(true);
  });

  it("corrects too many observations before emitting a report or its narration", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([
        { id: "invalid", name: "remediation_report", input: tooMany },
        { id: "unused", name: "search_web", input: { query: "unnecessary" } },
      ], "Unvalidated answer"))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "corrected", name: "remediation_report", input: validReport }], "Validated answer"));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) events.push(event);
    expect(events).toEqual([{ type: "text", text: "Validated answer" }, { type: "report", report: validReport }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
    const correctiveRequest = mocks.completionStream.mock.calls[1][0];
    expect(correctiveRequest.tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
    expect(correctiveRequest.messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: "tool", tool_call_id: "invalid", content: expect.stringMatching(/observations:.*12/) }),
      expect.objectContaining({ role: "tool", tool_call_id: "unused", content: expect.stringContaining("not executed") }),
    ]));
  });

  it("allows one correction when the first invalid report arrives on the final normal round", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: tooMany }]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "corrected", name: "remediation_report", input: validReport }]));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) events.push(event);
    expect(events).toEqual([{ type: "report", report: validReport }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(5);
  });

  it("stops after one failed correction and never truncates a report into validity", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream.mockImplementation(() => fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: tooMany }], "Not yet valid"));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) events.push(event);
    };
    await expect(collect()).rejects.toThrow("bounded correction attempt");
    expect(events).toEqual([]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
  });
});
