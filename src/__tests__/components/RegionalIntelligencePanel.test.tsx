import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { ChatMessage } from "@/stores/regional-intelligence-store";
import { AI_GENERATED_DISCLAIMER, type RegionalAnalysisEvidence, type RegionalIntelligenceResponse } from "@/lib/regional-intelligence";
import { buildReportView, consultLine, sourceRowSummary } from "@/lib/regional-evidence-presentation";

/** jsdom implements no scroll behaviour; the panel calls this once per message list change. */
Element.prototype.scrollIntoView = vi.fn();

const mocks = vi.hoisted(() => ({
  state: {
    isOpen: true,
    isVisible: true,
    selectedLocation: { lat: 43.6, lon: -116.2, precision: "approximate" as const },
    messages: [] as ChatMessage[],
    isLoading: false,
    error: null as string | null,
    errorRetryable: false,
    analysisCancelled: false,
    dataFreshness: {} as Record<string, string>,
    analysisEvidence: null as RegionalAnalysisEvidence | null,
    toolActivity: null as string | null,
    activity: [],
    conversationId: null,
    closePanel: vi.fn(),
    cancelAnalysis: vi.fn(),
    setError: vi.fn(),
  },
}));

vi.mock("@/stores/regional-intelligence-store", () => ({
  useRegionalIntelligenceStore: (selector: (state: typeof mocks.state) => unknown) => selector(mocks.state),
}));

vi.mock("@/hooks/useRegionalIntelligence", () => ({
  useRegionalIntelligence: () => ({ sendFollowUp: vi.fn(), retryLastRequest: vi.fn() }),
}));

import RegionalIntelligencePanel, { reportToMarkdown } from "@/components/panels/RegionalIntelligencePanel";

type Remediation = RegionalIntelligenceResponse["remediation"][number];
type Check = RegionalAnalysisEvidence["toolCalls"][number];

function recommendation(overrides: Partial<Remediation> = {}): Remediation {
  return {
    strategy: "cover_cropping", title: "Screen acid-tolerant cover crops",
    rationale: "Topsoil pH near 5.8 favours acid-tolerant species.", timeframe: "short_term",
    confidence: "low", consultProfessionals: ["agronomist"], evidenceOrigin: "model_inference",
    ...overrides,
  };
}

function report(overrides: Partial<RegionalIntelligenceResponse> = {}): RegionalIntelligenceResponse {
  return {
    aiGenerated: true,
    riskSummary: {
      level: "moderate", headline: "Elevated drought stress with no active fire signal.",
      factors: ["D2 drought classification within the context window."],
      evidenceOrigin: "model_inference", evidenceSources: [],
    },
    observations: [],
    remediation: [recommendation()],
    professionalConsultation: "Confirm with a local conservation district.",
    webSources: [],
    dataFreshness: {},
    ...overrides,
  };
}

function evidence(toolCalls: Check[], limitations: string[] = []): RegionalAnalysisEvidence {
  return {
    version: 1,
    stages: [
      { id: "local", label: "Local reads", status: "completed" },
      { id: "temporal", label: "Historical comparison", status: "completed" },
      { id: "additional", label: "Additional evidence", status: "completed" },
    ],
    toolCalls,
    limitations,
  };
}

function showReport(response: RegionalIntelligenceResponse) {
  mocks.state.messages = [{ id: "assistant-1", role: "assistant", content: "", parsedResponse: response }];
  return renderWithProviders(<RegionalIntelligencePanel />);
}

function openSources(name: RegExp = /^Sources \(/) {
  fireEvent.click(screen.getByRole("button", { name }));
  return within(screen.getByRole("list", { name: "Source lanes" }));
}

function laneTexts(): string[] {
  // Direct children only: an expanded row nests its own detail list.
  return [...screen.getByRole("list", { name: "Source lanes" }).children].map((item) => item.textContent ?? "");
}

afterEach(() => {
  mocks.state.messages = [];
  mocks.state.dataFreshness = {};
  mocks.state.analysisEvidence = null;
  mocks.state.isLoading = false;
  vi.restoreAllMocks();
});

describe("regional analysis panel", () => {
  it("shows risk, findings as value · day, recommendations and one consult line, with every item still reachable", () => {
    const calls: Check[] = [
      { id: "moisture", stage: "local", tool: "surface_value_near_point", source: "soil-field-moisture", selectedDate: "2026-09-10", resolvedDay: "2026-09-04", dayOffset: -6, status: "observed" },
      { id: "precip", stage: "local", tool: "surface_value_near_point", source: "climate-field-precipitation", selectedDate: "2026-09-10", resolvedDay: "2026-09-10", cellDistanceKm: 23.6, status: "observed" },
    ];
    showReport(report({
      observations: [
        { statement: "Surface soil moisture is 0.12 m³/m³.", evidenceOrigin: "warehouse", evidenceSource: "soil-field-moisture", evidenceReadIds: ["moisture"] },
        { statement: "Precipitation was 3 mm.", evidenceOrigin: "warehouse", evidenceSource: "climate-field-precipitation", evidenceReadIds: ["precip"] },
        { statement: "Third finding.", evidenceOrigin: "model_inference" },
        { statement: "Fourth finding.", evidenceOrigin: "model_inference" },
      ],
      remediation: [
        recommendation(),
        recommendation({ title: "Second", consultProfessionals: ["soil_scientist"] }),
        recommendation({ title: "Third", timeframe: "immediate", consultProfessionals: [] }),
        recommendation({ title: "Fourth", timeframe: "long_term", consultProfessionals: [] }),
      ],
      analysisEvidence: evidence(calls),
    }));

    expect(screen.getByRole("heading", { name: "Risk · Moderate" })).toBeTruthy();
    expect(screen.getByText("Elevated drought stress with no active fire signal.")).toBeTruthy();
    expect(screen.getByText("Published estimate · 2026-09-04 · nearest day, 6 d earlier")).toBeTruthy();
    expect(screen.getByText("Published estimate · 2026-09-10 · nearest cell, 23.6 km")).toBeTruthy();
    expect(screen.queryByText("Fourth finding.")).toBeNull();
    expect(screen.getByText("Third · Now")).toBeTruthy();
    expect(screen.queryByText("Fourth · Multi-year")).toBeNull();
    expect(screen.getByText("Confirm with an agronomist or soil scientist before acting.")).toBeTruthy();

    const showMore = screen.getAllByRole("button", { name: "Show 1 more" });
    expect(showMore).toHaveLength(2);
    showMore.forEach((button) => fireEvent.click(button));
    expect(screen.getByText("Fourth finding.")).toBeTruthy();
    expect(screen.getByText("Fourth · Multi-year")).toBeTruthy();

    // Per-item provenance chips and the strategy chip row are gone; the verbatim disclaimer is the footer.
    expect(screen.queryByText(/AI inference/)).toBeNull();
    expect(screen.queryByLabelText("Suggested strategy chips")).toBeNull();
    expect(screen.getAllByText(AI_GENERATED_DISCLAIMER)).toHaveLength(1);
    expect(document.body.textContent).not.toContain("published estimates, not measurements");
    expect(document.body.textContent).not.toMatch(/\+\d+%|tau/i);
  });

  it("offers copy and both exports from one menu per report", () => {
    showReport(report());
    expect(screen.queryByRole("button", { name: /Copy text|Share text|Export JSON/ })).toBeNull();
    const menuButton = screen.getByRole("button", { name: "Export" });
    expect(menuButton.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(menuButton);
    expect(menuButton.getAttribute("aria-expanded")).toBe("true");
    for (const name of ["Copy as text", "Download Markdown", "Download JSON"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
  });

  it("merges local and history passes per lane and repeated literature lookups into one ×N row", () => {
    showReport(report({
      analysisEvidence: evidence([
        { id: "l1", stage: "local", tool: "surface_value_near_point", source: "soil-field-moisture", selectedDate: "2026-09-10", resolvedDay: "2026-09-10", status: "observed" },
        { id: "h1", stage: "temporal", tool: "surface_values_near_point", source: "soil-field-moisture", rangeStart: "2026-08-10", rangeEnd: "2026-10-10", timeScale: "month", status: "observed" },
        { id: "l2", stage: "local", tool: "surface_value_near_point", source: "climate-field-precipitation", selectedDate: "2026-09-10", resolvedDay: "2026-09-10", status: "observed" },
        { id: "h2", stage: "temporal", tool: "surface_values_near_point", source: "climate-field-precipitation", selectedDate: "2026-08-10", resolvedDay: "2026-08-10", status: "observed" },
        { id: "k1", stage: "additional", tool: "search_environmental_strategies", source: "strategy-knowledge", status: "answered" },
        { id: "k2", stage: "additional", tool: "search_strategy_research_findings", source: "strategy-knowledge", status: "answered" },
        { id: "k3", stage: "additional", tool: "get_environmental_strategies", status: "answered_no_records" },
        { id: "skip", stage: "additional", tool: "surface_value_near_point", source: "vegetation", status: "not_queried" },
      ]),
    }));
    openSources();
    expect(laneTexts()).toEqual([
      "Soil field moisture · Found · 2026-09-10",
      "Climate field precipitation · Found · 2026-09-10",
      "Strategy literature ×3 · Found",
    ]);
  });

  it.each([
    ["exact read", { status: "observed", resolvedDay: "2026-09-10" }, "Found · 2026-09-10"],
    ["nearest day", { status: "observed", resolvedDay: "2026-09-04", dayOffset: -6 }, "Nearest day · 2026-09-04 (6 d earlier)"],
    ["nearest cell", { status: "observed", resolvedDay: "2026-09-10", cellDistanceKm: 23.6 }, "Nearest cell · 2026-09-10 (23.6 km away)"],
    ["static layer", { status: "observed", staticLayer: true }, "Static layer"],
    ["unpublished day", { status: "unavailable", reason: "requested_day_not_published" }, "Not published"],
    ["failed read", { status: "error", reason: "timeout" }, "Error"],
  ] as const)("labels a %s with its single status", (_name, fields, expected) => {
    showReport(report({
      analysisEvidence: evidence([{ id: "one", stage: "local", tool: "surface_value_near_point", source: "soil-survey", selectedDate: "2026-09-10", ...fields } as Check]),
    }));
    openSources();
    expect(laneTexts()).toEqual([`Soil survey · ${expected}`]);
  });

  it("keeps raw scope inside the row expand, with zoom and coordinates rounded", () => {
    showReport(report({
      analysisEvidence: evidence([{
        id: "z", stage: "local", tool: "surface_value_near_point", source: "watersheds", selectedDate: "2026-09-10",
        resolvedDay: "2026-09-10", zoom: 14.904274419894014, location: { lat: 43.612345678, lon: -116.212345678 }, status: "observed",
      }]),
    }));
    const lanes = openSources();
    const scope = "Local reads: Requested 2026-09-10 · Zoom 14.9 · 43.6123°, -116.2123°";
    expect(screen.queryByText(scope)).toBeNull();
    const row = lanes.getByRole("button", { name: /Watersheds · Found/ });
    fireEvent.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(scope)).toBeTruthy();
    expect(document.body.textContent).not.toContain("14.904274419894014");
  });

  it("states a partial report once and lists one gap line per lane inside Sources", () => {
    // The server's real lane-line format (`regionalLaneLimitation`): "<source>: text", no read id.
    const limitationsWall = Array.from({ length: 12 }, () => "soil-field-moisture: availability only, not a measured condition.");
    showReport(report({
      analysisEvidence: evidence([
        { id: "l1", stage: "local", tool: "surface_value_near_point", source: "soil-field-moisture", resolvedDay: "2026-09-10", status: "observed" },
        { id: "h1", stage: "temporal", tool: "surface_values_near_point", source: "soil-field-moisture", status: "observed" },
        { id: "d1", stage: "local", tool: "surface_value_near_point", source: "drought-areas", status: "unavailable", reason: "requested_day_not_published" },
      ], [...limitationsWall, "A nearby region is not an evaluated intervention outcome.", "A nearby region is not an evaluated intervention outcome."]),
    }));
    expect(screen.getByRole("note").textContent).toBe("Some sources unavailable — see Sources.");
    openSources();
    expect(screen.getByText("Soil field moisture: availability only, not a measured condition.")).toBeTruthy();
    expect(screen.getByText("Drought areas: requested day not published")).toBeTruthy();
    expect(screen.getAllByText("A nearby region is not an evaluated intervention outcome.")).toHaveLength(1);
  });

  it("shows no partial note when every queried source answered", () => {
    showReport(report({
      analysisEvidence: evidence([{ id: "l1", stage: "local", tool: "surface_value_near_point", source: "watersheds", resolvedDay: "2026-09-10", status: "observed" }]),
    }));
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("contains a malformed saved report to its own boundary and keeps the conversation usable", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.state.messages = [
      { id: "bad", role: "assistant", content: "", parsedResponse: { aiGenerated: true } as unknown as RegionalIntelligenceResponse },
      { id: "odd", role: "assistant", content: "", parsedResponse: report({ riskSummary: { ...report().riskSummary, level: "severe" as never }, dataFreshness: null as never }) },
    ];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByRole("alert").textContent).toMatch(/This report could not be displayed/);
    expect(screen.getByRole("heading", { name: "Risk · Unknown" })).toBeTruthy();
    expect(screen.getByLabelText("Ask a follow-up question about this location")).toBeTruthy();
  });

  it("exports exactly the rows, findings and consult line the screen shows", () => {
    const response = report({
      observations: [{ statement: "Surface soil moisture is 0.12 m³/m³.", evidenceOrigin: "warehouse", evidenceSource: "soil-field-moisture", evidenceReadIds: ["m"] }],
      analysisEvidence: evidence([
        { id: "m", stage: "local", tool: "surface_value_near_point", source: "soil-field-moisture", resolvedDay: "2026-09-04", dayOffset: -6, status: "observed" },
        { id: "m2", stage: "temporal", tool: "surface_values_near_point", source: "soil-field-moisture", status: "observed" },
        { id: "k1", stage: "additional", tool: "search_environmental_strategies", source: "strategy-knowledge", status: "answered" },
        { id: "k2", stage: "additional", tool: "search_environmental_strategies", source: "strategy-knowledge", status: "answered" },
        { id: "s", stage: "local", tool: "surface_features_near_point", source: "soil-survey", staticLayer: true, status: "observed" },
      ]),
      dataFreshness: { soilProperties: "static_release_untimed" },
    });
    showReport(response);
    openSources();
    const view = buildReportView(response);
    const markdown = reportToMarkdown(response);
    const exportedRows = view.sources.rows.map(sourceRowSummary);
    expect(laneTexts()).toEqual(exportedRows);
    for (const row of exportedRows) expect(markdown).toContain(`\n- ${row}\n`);
    expect(markdown).toContain(`- ${response.observations[0].statement} — ${view.findings[0].meta}`);
    expect(screen.getByText(view.findings[0].meta as string)).toBeTruthy();
    expect(markdown).toContain(view.consult as string);
    // The legally load-bearing disclaimer travels verbatim, exactly once, as the export's footer.
    expect(markdown.split(AI_GENERATED_DISCLAIMER)).toHaveLength(2);
    expect(markdown.trimEnd().endsWith(`_${AI_GENERATED_DISCLAIMER}_`)).toBe(true);
  });

  it("files literature citations under the literature row, https links only, escaped in Markdown", () => {
    const response = report({
      remediation: [recommendation({
        evidenceOrigin: "literature", evidenceSource: "strategy-knowledge",
        literatureRecordIds: ["sk-1", "sk-2"],
        literatureCitations: [
          { recordId: "sk-1", kind: "finding", title: "Effect of [no-till] on *evaporation* (field study)", magnitude: "*−65%*", direction: "mixed", conditions: "Trials in `plot_1` (irrigated)", sourceUrl: "https://example.org/study_(2019)" },
          { recordId: "sk-2", kind: "strategy", title: "Reduced tillage / no-till", sourceUrl: "http://example.org/insecure" },
        ],
      })],
    });
    showReport(response);
    const lanes = openSources();
    fireEvent.click(lanes.getByRole("button", { name: /Strategy literature · Found/ }));
    expect(screen.getByText(/Effect of \[no-till\] on \*evaporation\* \(field study\) · Reported \*−65%\* \(direction: mixed\)/)).toBeTruthy();
    const links = lanes.getAllByRole("link");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.org/study_(2019)");

    const markdown = reportToMarkdown(response);
    expect(markdown).toContain("Effect of \\[no-till\\] on \\*evaporation\\* \\(field study\\) · Reported \\*−65%\\* \\(direction: mixed\\) · Conditions: Trials in \\`plot_1\\` \\(irrigated\\) · [Source](https://example.org/study_%282019%29)");
    expect(markdown).not.toContain("http://example.org/insecure");
  });

  it.each([
    ["undated soil release", { soilProperties: "static_release_untimed" }, ["Soil properties · Static layer"]],
    ["captured perimeter snapshot", { firePerimeters: "snapshot_captured_2026-09-01" }, ["Fire perimeters · Found · 2026-09-01"]],
    ["old perimeter snapshot", { firePerimeters: "snapshot_captured_2026-08-01" }, ["Fire perimeters · Found · 2026-08-01 (stale)"]],
    ["MTBS publication", { mtbsPerimeters: "publication_available_2026-09-09" }, ["MTBS perimeters · Found · 2026-09-09"]],
    ["drought publisher day", { drought: "2026-09-01T00:00:00Z" }, ["Drought · Found · 2026-09-01"]],
    ["deferred placeholders", { strategyRecommendations: "unavailable", carbonPotential: "published_revision_required", streamflow: "unavailable" }, ["Streamflow · Not published"]],
  ] as const)("folds the initial-context %s into Sources", (_name, freshness, expected) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    try {
      showReport(report({ dataFreshness: { ...freshness } }));
      openSources();
      expect(laneTexts()).toEqual(expected);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the streaming state with the live Sources disclosure, then nothing stale once idle", () => {
    mocks.state.isLoading = true;
    mocks.state.messages = [{ id: "s", role: "assistant", content: "", isStreaming: true }];
    mocks.state.analysisEvidence = evidence([{ id: "l1", stage: "local", tool: "surface_value_near_point", source: "watersheds", resolvedDay: "2026-09-10", status: "observed" }]);
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText("Reviewing this location…")).toBeTruthy();
    openSources(/^Sources so far \(1\)/);
    expect(laneTexts()).toEqual(["Watersheds · Found · 2026-09-10"]);
  });

  it("distinguishes the empty panel from a completed turn with no analysis", () => {
    const { unmount } = renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText("Ask about this location to get AI-generated remediation suggestions.")).toBeTruthy();
    unmount();
    mocks.state.messages = [{ id: "failed", role: "assistant", content: "", isStreaming: false }];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText("No analysis was completed.")).toBeTruthy();
    expect(screen.queryByText("Reviewing this location…")).toBeNull();
  });
});

describe("consultLine", () => {
  it.each([
    [["agronomist"], "Confirm with an agronomist before acting."],
    [["agronomist", "soil_scientist"], "Confirm with an agronomist or soil scientist before acting."],
    [["hydrologist", "ecologist", "extension_service"], "Confirm with a hydrologist, ecologist, or extension service before acting."],
    [["soil_scientist", "soil_scientist"], "Confirm with a soil scientist before acting."],
    [[], null],
  ])("%j -> %s", (disciplines, expected) => {
    expect(consultLine(disciplines)).toBe(expected);
  });
});
