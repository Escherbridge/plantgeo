import { afterEach, describe, expect, it, vi } from "vitest";
import { reportFlowGroundingIssues } from "@/lib/server/services/report-flow-grounding";
import type { WaterGauge } from "@/lib/environmental/water";

vi.mock("@/lib/server/db", () => ({ db: {} }));
const mocks = vi.hoisted(() => ({
  getServerSession: vi.fn(),
  // The SDK client streamRegionalIntelligence builds internally. Mocked at the module boundary
  // rather than passed in, exactly like every other service call in this file, so the request
  // it actually issues -- the `tools` array in particular -- is observable from the test.
  completionStream: vi.fn(),
  loadEvidenceTools: vi.fn().mockResolvedValue(null),
  callEvidenceTool: vi.fn(),
}));
vi.mock('@/lib/server/services/regional-evidence-tools', () => ({
  loadRegionalEvidenceTools: mocks.loadEvidenceTools,
  callRegionalEvidenceTool: mocks.callEvidenceTool,
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
    evidenceOrigin: "model_inference" as const,
    evidenceSources: [],
  },
  observations: [
    {
      statement: "Six fire detections were observed nearby today.",
      evidenceOrigin: "model_inference" as const,
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

const unclassifiedGauge: WaterGauge = {
  siteNo: '123', siteName: 'Nearest gauge', lat: 44.66, lon: -118.83,
  flowCfs: 13.2, percentile: null, condition: 'unknown', trend: null,
  updatedAt: '2026-09-10T06:45:00Z', observedDay: '2026-09-09',
};

describe('streamflow report grounding', () => {
  const explicitDenials = [
    'The gauge value cannot be used to establish low flow',
    'The gauge value does not establish low streamflow',
    "The gauge value doesn't indicate low flow",
    'There is insufficient evidence to establish low flow',
    'There is insufficient evidence to classify this as low flow',
    'There is not enough evidence to determine whether flow is low',
    'There is no evidence to confirm high discharge',
    'We cannot determine whether streamflow is low',
    'Low flow cannot be inferred from the gauge value alone',
    'Low streamflow is not established by this reading',
  ];
  it.each(explicitDenials)('permits an explicit denial without suppressing a later affirmative claim: %s', (denial) => {
    const withFactor = (factor: string) => ({ ...validReport, riskSummary: { ...validReport.riskSummary, factors: [factor] } });
    expect(reportFlowGroundingIssues(withFactor(denial), unclassifiedGauge)).toEqual([]);
    for (const separator of ['. ', '; ', ', but ', ', however ', ', yet ', ', nevertheless ']) {
      expect(reportFlowGroundingIssues(withFactor(`${denial}${separator}low streamflow indicates scarcity`), unclassifiedGauge)).toHaveLength(1);
      expect(reportFlowGroundingIssues(withFactor(`Low streamflow indicates scarcity${separator}${denial}`), unclassifiedGauge)).toHaveLength(1);
    }
  });
  it.each(['We cannot ignore low streamflow.', 'There is no evidence of flooding; low streamflow indicates scarcity.', 'Low flow cannot be ignored.', 'Low flow is not improving.', 'Low flow cannot be inferred to cause fish mortality.', 'Low flow is not established as a cause of fish mortality.'])('does not treat unrelated negation as missing classification evidence: %s', (statement) => {
    expect(reportFlowGroundingIssues({ ...validReport, observations: [{ ...validReport.observations[0], statement }] }, unclassifiedGauge)).toHaveLength(1);
  });
  it('provides actual numeric wording and explicit unknown comparisons only when evidence is missing', async () => {
    const { buildUserMessage } = await import('@/lib/server/services/ai-prompt');
    const payload: RegionalContextPayload = {
      location: { lat: 44.66, lon: -118.83, geohash: '9r' },
      waterScarcity: { droughtClass: 'D2', nearestGauge: unclassifiedGauge },
      strategyRecommendations: null, strategyContext: [], communityProposals: [],
      soilProperties: null, weather: null, fireDetections: null, firePerimeters: null,
      mtbsPerimeters: null, carbonPotential: null,
    };
    const temporal: TemporalContext = { serverCurrentDate: '2026-09-10', viewedLayersUnreported: true, readings: [], viewedDates: [], sourcesServedAsOfLatest: [] };
    const message = buildUserMessage(payload, {}, false, temporal);
    expect(message).toContain('The gauge reports 13.2 cfs.');
    expect(message).toContain('Comparative flow status is unknown.');
    expect(message).toContain('Flow trend is unknown.');
    expect(message).toContain('recommendation titles or rationales');
    payload.waterScarcity = { droughtClass: 'D2', nearestGauge: { ...unclassifiedGauge, flowCfs: 42, percentile: 10, condition: 'below_normal', trend: 'declining' } };
    const supportedMessage = buildUserMessage(payload, {}, false, temporal);
    expect(supportedMessage).toContain('The gauge reports 42 cfs.');
    expect(supportedMessage).not.toContain('Comparative flow status is unknown.');
    expect(supportedMessage).not.toContain('Flow trend is unknown.');
    payload.waterScarcity = { droughtClass: 'D2', nearestGauge: { ...unclassifiedGauge, flowCfs: null } };
    expect(buildUserMessage(payload, {}, false, temporal)).not.toContain('The gauge reports');
  });
  it.each(['Low streamflow (13.2 cfs) indicates water scarcity.', 'High discharge raises concern.', 'Below-normal flow was observed.', 'Streamflow is rising.', 'Stable flow was observed.', 'We cannot determine whether flow is low, but low streamflow indicates scarcity.'])('rejects unsupported comparison: %s', (statement) => {
    const report = { ...validReport, observations: [{ ...validReport.observations[0], statement }] };
    expect(reportFlowGroundingIssues(report, unclassifiedGauge)).toEqual([expect.objectContaining({ path: ['observations', 0, 'statement'] })]);
  });

  it.each(['The gauge reports 13.2 cfs; drought suggests possible water scarcity.', 'Low confidence in the discharge assessment; use low cost monitoring.', 'No evidence of low streamflow is supplied.', 'We cannot infer low flow from 13.2 cfs.', 'Cannot determine whether streamflow is low.', 'Cannot infer that streamflow is low.', 'Flow classification is unknown.'])('preserves numeric evidence and uncertainty: %s', (headline) => {
    expect(reportFlowGroundingIssues({ ...validReport, riskSummary: { ...validReport.riskSummary, headline } }, unclassifiedGauge)).toEqual([]);
  });

  it('requires a matching supplied condition and valid percentile, or a matching trend', () => {
    const report = { ...validReport, riskSummary: { ...validReport.riskSummary, factors: ['Low streamflow was observed.'] } };
    expect(reportFlowGroundingIssues(report, { ...unclassifiedGauge, condition: 'low', percentile: 5 })).toEqual([]);
    for (const gauge of [null, { ...unclassifiedGauge, condition: 'low' as const }, { ...unclassifiedGauge, condition: 'above_normal' as const, percentile: 95 }, { ...unclassifiedGauge, condition: 'low' as const, percentile: NaN }]) {
      expect(reportFlowGroundingIssues(report, gauge)).toHaveLength(1);
    }
    const trendReport = { ...validReport, professionalConsultation: 'Rising streamflow requires assessment.' };
    expect(reportFlowGroundingIssues(trendReport, { ...unclassifiedGauge, trend: 'rising' })).toEqual([]);
    expect(reportFlowGroundingIssues(trendReport, { ...unclassifiedGauge, trend: 'declining' })).toHaveLength(1);
  });

  it('checks recommendation titles and rationale without rejecting drought-only inference', () => {
    const report = { ...validReport, remediation: [{ ...validReport.remediation[0], title: 'Respond to low streamflow', rationale: 'Low streamflow indicates scarcity.' }] };
    expect(reportFlowGroundingIssues(report, unclassifiedGauge).map((issue) => issue.path)).toEqual([['remediation', 0, 'title'], ['remediation', 0, 'rationale']]);
  });
});

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
    expect(prompt).toContain("A small absolute cfs value alone is not evidence of low flow");
    expect(prompt).toContain("Strategy-model evidence is unavailable");
    expect(prompt).toContain("You may still suggest remediation grounded in the supplied environmental evidence");
    expect(prompt).not.toContain("evaluation_only_model");
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
          evidenceSources: { maxItems: 33 },
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
    mocks.loadEvidenceTools.mockReset().mockResolvedValue(null);
    mocks.callEvidenceTool.mockReset();
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
            finish_reason: toolCalls.length ? "tool_calls" : "stop",
            message: {
              role: "assistant",
              content: narration ?? null,
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

  it('offers environmental tools without web and synthesizes only after simultaneous evidence requests return', async () => {
    const webEvidence = await import('@/lib/server/services/web-evidence');
    const provider = vi.spyOn(webEvidence, 'getWebEvidenceProvider').mockReturnValue(null);
    const { streamRegionalIntelligence } = await import('@/lib/server/services/ai-prompt');
    mocks.loadEvidenceTools.mockResolvedValue({
      tools: [{ name: 'surface_value_near_point', description: 'Read governed surface evidence', input_schema: { type: 'object' } }],
      surfaces: ['vegetation'], featureSurfaces: ['vegetation'], valueSurfaces: ['vegetation'],
    });
    mocks.callEvidenceTool.mockResolvedValue(JSON.stringify({ features: [{ served_day: '2024-05-01', properties: { cover: 0.25 } }], day_state: { state: 'published' } }));
    const groundedReport = { ...validReport, observations: [{
      statement: 'The vegetation read at 43.6, -116.2 returned dated evidence for 2024-05-01.',
      evidenceOrigin: 'warehouse', evidenceSource: 'vegetation', evidenceReadIds: ['additional-1'],
    }] };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([
        { id: 'premature', name: 'remediation_report', input: { ...validReport, riskSummary: { ...validReport.riskSummary, headline: 'Premature report' } } },
        { id: 'vegetation', name: 'surface_value_near_point', input: { surface_name: 'vegetation', day: '2024-05-01', longitude: -116.2, latitude: 43.6 } },
      ]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'grounded', name: 'remediation_report', input: groundedReport }]));
    try {
      const events = [];
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) events.push(event);
      expect(mocks.completionStream).toHaveBeenCalledTimes(2);
      expect(mocks.completionStream.mock.calls[0][0].tool_choice).toBe('auto');
      expect(mocks.completionStream.mock.calls[0][0].tools.map((tool: { function: { name: string } }) => tool.function.name)).toContain('surface_value_near_point');
      expect(events.filter((event) => event.type === 'report')).toEqual([{ type: 'report', report: groundedReport }]);
      expect(mocks.completionStream.mock.calls[1][0].messages).toEqual(expect.arrayContaining([
        expect.objectContaining({ role: 'tool', tool_call_id: 'vegetation', content: expect.stringContaining('2024-05-01') }),
        expect.objectContaining({ role: 'tool', tool_call_id: 'vegetation', content: expect.stringContaining('additional-1') }),
        expect.objectContaining({ role: 'tool', tool_call_id: 'premature', content: expect.stringContaining('Report deferred') }),
      ]));
      const audit = events.filter((event) => event.type === 'evidence').at(-1);
      expect(audit?.evidence.toolCalls).toEqual(expect.arrayContaining([expect.objectContaining({ stage: 'additional', source: 'vegetation', selectedDate: '2024-05-01', status: 'observed' })]));
      expect(JSON.stringify(mocks.completionStream.mock.calls[0][0].messages)).toContain('initial regional snapshot');
    } finally { provider.mockRestore(); }
  });

  it('bounds extra evidence calls and supplies missing history anchors while recording the skipped request', async () => {
    const { streamRegionalIntelligence } = await import('@/lib/server/services/ai-prompt');
    mocks.loadEvidenceTools.mockResolvedValue({ tools: [{ name: 'drought_history_at_point', description: 'Dated history', input_schema: { type: 'object' } }], surfaces: [], featureSurfaces: [], valueSurfaces: [] });
    mocks.callEvidenceTool.mockResolvedValue(JSON.stringify({ weekly_severity: [{ valid_date: '2024-01-01', severity_class: null }] }));
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream(Array.from({ length: 7 }, (_, index) => ({ id: `history-${index}`, name: 'drought_history_at_point', input: { longitude: -116.2, latitude: 43.6 } }))))
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'final', name: 'remediation_report', input: validReport }]));
    const events = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, { ...minimalTemporalContext(), viewedDates: ['2024-01-02'] }, [])) events.push(event);
    const audit = events.filter((event) => event.type === 'evidence').at(-1);
    const additional = audit?.evidence.toolCalls.filter((call) => call.stage === 'additional') ?? [];
    expect(additional.filter((call) => call.status === 'observed')).toHaveLength(6);
    expect(additional.filter((call) => call.status === 'not_queried')).toHaveLength(1);
    expect(mocks.callEvidenceTool.mock.calls.every((call) => call[1].as_of_day === '2024-01-02')).toBe(true);
  });

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
      if (event.type !== 'evidence') events.push(event);
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
      if (event.type !== 'evidence') events.push(event as { type: string; report?: unknown });
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
    for await (const event of streamRegionalIntelligence(payload, {}, false, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    const sent = JSON.stringify(mocks.completionStream.mock.calls[0]?.[0]);
    expect(sent).toContain("SoilGrids model predictions; not a local soil sample");
    expect(sent).toContain("g/kg");
    expect(sent).toContain("61.9");
    expect(sent).toContain("5.12");
    expect(sent).toContain("divide g/kg by 10");
    expect(events.some((event) => event.type === "report")).toBe(true);
  });

  it.each([false, true])("logs safe provider diagnostics for a 400 on the initial or correction round (%s)", async (afterCorrection) => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const secret = "sk-or-hidden-provider-key";
    const privateQuestion = "Private ranch analysis question";
    const failure = Object.assign(new Error("400 Provider returned error"), { status: 400, code: 400, error: { metadata: { provider_name: "Google AI Studio", raw: JSON.stringify({ error: { code: 400, status: "INVALID_ARGUMENT", message: `Missing thought_signature; echoed ${privateQuestion} ${secret}` } }) } } });
    if (afterCorrection) mocks.completionStream.mockImplementationOnce(() => fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: { invalid: true } }]));
    mocks.completionStream.mockImplementationOnce(() => ({
      [Symbol.asyncIterator]: () => ({ next: async () => { throw failure; } }),
    }));
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [], privateQuestion)) void event;
    };
    try {
      await expect(collect()).rejects.toBe(failure);
      expect(mocks.completionStream).toHaveBeenCalledTimes(afterCorrection ? 2 : 1);
      expect(log).toHaveBeenCalledWith("[AI] provider request failed", expect.objectContaining({ round: afterCorrection ? 2 : 1, isFinalRound: afterCorrection, toolChoiceMode: "forced_report", correctingReport: afterCorrection, status: 400, provider: "Google AI Studio", reasons: ["thought_signature"], messageCount: afterCorrection ? 4 : 2 }));
      const diagnostic = log.mock.calls[0]?.[1] as { requestByteCount: number };
      expect(diagnostic.requestByteCount).toBeGreaterThan(100);
      expect(JSON.stringify(log.mock.calls)).not.toContain(secret);
      expect(JSON.stringify(log.mock.calls)).not.toContain(privateQuestion);
    } finally {
      log.mockRestore();
    }
  });

  it.each([false, true])('uses only one correction for unsupported flow claims (repeated=%s)', async (repeated) => {
    const { streamRegionalIntelligence } = await import('@/lib/server/services/ai-prompt');
    const unsupported = { ...validReport, riskSummary: { ...validReport.riskSummary, factors: ['Low streamflow (13.2 cfs) indicates water scarcity.'] } };
    const corrected = { ...validReport, riskSummary: { ...validReport.riskSummary, factors: ['The gauge reports 13.2 cfs. Drought suggests possible water scarcity.'] } };
    const payload = minimalPayload();
    payload.waterScarcity = { droughtClass: 'D2', nearestGauge: unclassifiedGauge };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'bad', name: 'remediation_report', input: unsupported }], 'Rejected narration'))
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'correction', name: 'remediation_report', input: repeated ? unsupported : corrected }]));
    const events: Array<{ type: string }> = [];
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(payload, {}, false, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    };
    if (repeated) await expect(collect()).rejects.toThrow('bounded correction attempt');
    else await collect();
    expect(events).toEqual(repeated ? [] : [{ type: 'report', report: corrected }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(mocks.completionStream.mock.calls[1]?.[0])).toContain('Unsupported streamflow classification or trend');
  });

  it.each([false, true])('targets overlong consultation text without relaxing its bound (repeated=%s)', async (repeated) => {
    const { streamRegionalIntelligence, buildSystemPrompt, REPORT_TOOL } = await import('@/lib/server/services/ai-prompt');
    const overlong = { ...validReport, professionalConsultation: 'x'.repeat(601) };
    const corrected = { ...validReport, professionalConsultation: 'Consult a local wildfire mitigation specialist before acting.' };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'long', name: 'remediation_report', input: overlong }], 'Rejected narration'))
      .mockReturnValueOnce(fakeCompletionStream([{ id: 'short', name: 'remediation_report', input: repeated ? overlong : corrected }]));
    const events: Array<{ type: string }> = [];
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    };
    if (repeated) await expect(collect()).rejects.toThrow('bounded correction attempt');
    else await collect();
    expect(events).toEqual(repeated ? [] : [{ type: 'report', report: corrected }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
    expect(JSON.stringify(mocks.completionStream.mock.calls[1]?.[0])).toContain('replace the long text with ONE short sentence');
    expect(buildSystemPrompt(false)).toContain('one short sentence naming only the relevant professional disciplines');
    expect(JSON.stringify(REPORT_TOOL.input_schema)).toContain('One short sentence naming the relevant professional disciplines');
    expect(remediationReportSchema.safeParse(overlong).success).toBe(false);
  });

  it("corrects too many observations before emitting a report or its narration", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([
        { id: "invalid", name: "remediation_report", input: tooMany },
        { id: "unused", name: "search_web", input: { query: "unnecessary" } },
      ], "Unvalidated answer"))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "corrected", name: "remediation_report", input: validReport }], "Validated answer"));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    expect(events).toEqual([{ type: "text", text: "Validated answer" }, { type: "report", report: validReport }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
    const correctiveRequest = mocks.completionStream.mock.calls[1][0];
    expect(correctiveRequest.tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
    expect(mocks.completionStream.mock.calls[0][0].tool_choice).toEqual(correctiveRequest.tool_choice);
    expect(correctiveRequest.tools).toEqual(mocks.completionStream.mock.calls[0][0].tools);
    expect(correctiveRequest.messages).toEqual(expect.arrayContaining([
      expect.objectContaining({ role: "tool", tool_call_id: "invalid", content: expect.stringMatching(/observations:.*12/) }),
      expect.objectContaining({ role: "tool", tool_call_id: "unused", content: expect.stringContaining("not executed") }),
    ]));
  });

  it("allows one correction when the first invalid report arrives on the final normal round", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: tooMany }]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "corrected", name: "remediation_report", input: validReport }]));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    expect(events).toEqual([{ type: "report", report: validReport }]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(5);
    expect(mocks.completionStream.mock.calls[3][0].tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
    expect(mocks.completionStream.mock.calls[4][0].tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
  });

  it("retains forced report selection for other configured models", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "another/provider-model");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: {} }]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "valid", name: "remediation_report", input: validReport }]));
    const events = [];
    for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    expect(events).toEqual([{ type: "report", report: validReport }]);
    expect(mocks.completionStream.mock.calls[1][0].tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
    expect(mocks.completionStream.mock.calls[0][0].tools[0].function.parameters).toHaveProperty("properties.observations.maxItems", 12);
  });

  it("forces a compatible report on the first Gemini call without search", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const webEvidence = await import("@/lib/server/services/web-evidence");
    const provider = vi.spyOn(webEvidence, "getWebEvidenceProvider").mockReturnValue(null);
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    mocks.completionStream.mockReturnValue(fakeCompletionStream([{ id: "first", name: "remediation_report", input: validReport }]));
    try {
      const events = [];
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
      expect(events).toEqual([{ type: "report", report: validReport }]);
      expect(mocks.completionStream).toHaveBeenCalledTimes(1);
      const request = mocks.completionStream.mock.calls[0][0];
      expect(request.tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
      expect(request.tools).toHaveLength(2);
      expect(request.tools[0].function.parameters).toEqual(request.tools[1].function.parameters);
      expect(request.tools[0].function.parameters).not.toHaveProperty("properties.observations.maxItems");
      expect(request.tools[0].function.parameters).toHaveProperty("properties.observations.description", "Maximum item count: 12.");
    } finally { provider.mockRestore(); }
  });

  it("keeps search-enabled rounds automatic until the final report round", async () => {
    const webEvidence = await import("@/lib/server/services/web-evidence");
    const provider = vi.spyOn(webEvidence, "getWebEvidenceProvider").mockReturnValue({ name: "test", search: vi.fn() });
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([]))
      .mockReturnValueOnce(fakeCompletionStream([{ id: "final", name: "remediation_report", input: validReport }]));
    try {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) void event;
      expect(mocks.completionStream.mock.calls.slice(0, 3).map((call) => call[0].tool_choice)).toEqual(["auto", "auto", "auto"]);
      expect(mocks.completionStream.mock.calls[3][0].tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
    } finally { provider.mockRestore(); }
  });

  it("rejects a text-only forced correction without emitting it or adding retries", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    mocks.completionStream
      .mockReturnValueOnce(fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: {} }]))
      .mockReturnValueOnce(fakeCompletionStream([], "Unstructured fallback"));
    const events: Array<{ type: string }> = [];
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    };
    await expect(collect()).rejects.toThrow("The correction attempt did not return a report.");
    expect(events).toEqual([]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
    expect(mocks.completionStream.mock.calls[1][0].tool_choice).toEqual({ type: "function", function: { name: "remediation_report" } });
  });

  it("stops after one failed correction and never truncates a report into validity", async () => {
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const tooMany = { ...validReport, observations: Array.from({ length: 13 }, () => validReport.observations[0]) };
    mocks.completionStream.mockImplementation(() => fakeCompletionStream([{ id: "invalid", name: "remediation_report", input: tooMany }], "Not yet valid"));
    const events: Array<{ type: string; report?: unknown; text?: string }> = [];
    const collect = async () => {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
    };
    await expect(collect()).rejects.toThrow("bounded correction attempt");
    expect(events).toEqual([]);
    expect(mocks.completionStream).toHaveBeenCalledTimes(2);
  });

  it("diagnoses exhausted rounds without accepting narration or logging its content", async () => {
    vi.stubEnv("OPENROUTER_MODEL", "google/gemini-2.5-flash-lite");
    const { streamRegionalIntelligence } = await import("@/lib/server/services/ai-prompt");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const privateNarration = "Private model output without a report";
    mocks.completionStream.mockImplementation(() => fakeCompletionStream([], privateNarration));
    const events: Array<{ type: string }> = [];
    try {
      for await (const event of streamRegionalIntelligence(minimalPayload(), {}, true, minimalTemporalContext(), [])) if (event.type !== 'evidence') events.push(event);
      expect(events).toEqual([]);
      expect(mocks.completionStream).toHaveBeenCalledTimes(4);
      expect(warn).toHaveBeenCalledWith("[AI] incomplete report response", expect.objectContaining({ round: 4, reason: "report_missing", finishReason: "stop", contentKind: "text", reportToolCount: 0, toolChoiceMode: "forced_report" }));
      expect(warn).toHaveBeenCalledWith("[AI] report attempts exhausted", expect.objectContaining({ reportCorrections: 0, maxToolRounds: 4 }));
      expect(JSON.stringify(warn.mock.calls)).not.toContain(privateNarration);
    } finally {
      warn.mockRestore();
    }
  });
});
