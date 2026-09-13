import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { ChatMessage } from "@/stores/regional-intelligence-store";
import type { RegionalIntelligenceResponse } from "@/lib/regional-intelligence";

/**
 * jsdom implements no scroll behaviour; the panel calls this once per message list change.
 * Stubbed rather than added to the shared test setup, since no other suite needs it.
 */
Element.prototype.scrollIntoView = vi.fn();

/**
 * A 2026-08-14 fabrication audit found this panel rendering three hard-coded chips --
 * "Regenerative Ag (+18% tau)", "Biochar Soil (+15% tau)", "Wildfire Buffer (+12% tau)" --
 * identically on every AI message, regardless of what the model actually recommended. These
 * tests pin the honest replacement: chips built from the message's own `remediation` array, and
 * nothing rendered when there is nothing to show.
 */
const mocks = vi.hoisted(() => ({
  state: {
    isOpen: true,
    selectedLocation: { lat: 43.6, lon: -116.2, precision: "approximate" as const },
    messages: [] as ChatMessage[],
    isLoading: false,
    error: null as string | null,
    errorRetryable: false,
    analysisCancelled: false,
    dataFreshness: {} as Record<string, string>,
    toolActivity: null as string | null,
    activity: [],
    conversationId: null,
    closePanel: vi.fn(),
    cancelAnalysis: vi.fn(),
    setError: vi.fn(),
  },
}));

vi.mock("@/stores/regional-intelligence-store", () => ({
  useRegionalIntelligenceStore: (selector: (state: typeof mocks.state) => unknown) =>
    selector(mocks.state),
}));

vi.mock("@/hooks/useRegionalIntelligence", () => ({
  useRegionalIntelligence: () => ({
    sendFollowUp: vi.fn(),
    retryLastRequest: vi.fn(),
  }),
}));

import RegionalIntelligencePanel, { reportToMarkdown } from "@/components/panels/RegionalIntelligencePanel";

function baseResponse(
  remediation: RegionalIntelligenceResponse["remediation"]
): RegionalIntelligenceResponse {
  return {
    aiGenerated: true,
    riskSummary: {
      level: "moderate",
      headline: "Elevated drought stress with no active fire signal.",
      factors: ["D2 drought classification within the context window."],
      evidenceOrigin: "warehouse",
      evidenceSources: ["drought"],
    },
    observations: [],
    remediation,
    professionalConsultation: "Confirm with a local conservation district.",
    webSources: [],
    dataFreshness: {},
  };
}

function assistantMessage(response: RegionalIntelligenceResponse): ChatMessage {
  return {
    id: "assistant-1",
    role: "assistant",
    content: "",
    parsedResponse: response,
  };
}

describe("RegionalIntelligencePanel strategy chips", () => {
  it('shows the dated read scope beside each cited claim and keeps the associations in Markdown', () => {
    const response = baseResponse([{
      strategy: 'water_harvesting', title: 'Assess water harvesting feasibility',
      rationale: 'Compare site runoff and soil constraints before designing an installation.',
      timeframe: 'short_term', confidence: 'low', consultProfessionals: ['hydrologist'],
      evidenceOrigin: 'warehouse', evidenceSource: 'watersheds', evidenceReadIds: ['additional-read'],
    }]);
    response.riskSummary.evidenceSources = ['climate-field-precipitation'];
    response.riskSummary.evidenceReadIds = ['history-read'];
    response.observations = [{
      statement: 'The comparison location has a published soil-moisture estimate.',
      evidenceOrigin: 'warehouse', evidenceSource: 'soil-field-moisture', evidenceReadIds: ['regional-read'],
    }];
    response.analysisEvidence = {
      version: 1,
      stages: [
        { id: 'temporal', label: 'Historical comparison', status: 'completed' },
        { id: 'regional', label: 'Regional comparison', status: 'completed' },
        { id: 'additional', label: 'Additional evidence', status: 'completed' },
      ],
      toolCalls: [
        { id: 'history-read', stage: 'temporal', tool: 'surface_value_near_point', source: 'climate-field-precipitation', selectedDate: '2025-09-10', observedDates: ['2025-09-10'], location: { lat: 43.6, lon: -116.2 }, status: 'observed' },
        { id: 'regional-read', stage: 'regional', tool: 'surface_value_near_point', source: 'soil-field-moisture', selectedDate: '2026-09-10', servedDates: ['2026-09-09'], location: { lat: 43.6, lon: -117.7 }, status: 'observed' },
        { id: 'additional-read', stage: 'additional', tool: 'surface_value_near_point', source: 'watersheds', selectedDate: '2026-09-10', validDates: ['2026-08-30'], servedDates: ['2026-09-01'], location: { lat: 43.6, lon: -116.2 }, status: 'observed' },
      ],
      limitations: [],
    };
    const riskScope = 'Cited evidence: Historical comparison · climate field precipitation · Requested 2025-09-10 · Observed days: 2025-09-10 · 43.6°, -116.2°';
    const observationScope = 'Cited evidence: Regional comparison · soil field moisture · Requested 2026-09-10 · Served days: 2026-09-09 · 43.6°, -117.7°';
    const recommendationScope = 'Cited evidence: Additional evidence · watersheds · Requested 2026-09-10 · Valid dates: 2026-08-30 · Served days: 2026-09-01 · 43.6°, -116.2°';
    mocks.state.messages = [assistantMessage(response)];
    renderWithProviders(<RegionalIntelligencePanel />);
    const risk = within(screen.getByText(response.riskSummary.headline).parentElement as HTMLElement);
    const observation = within(screen.getByText(response.observations[0].statement).closest('li') as HTMLElement);
    const recommendation = within(screen.getByText(response.remediation[0].title).closest('article') as HTMLElement);
    expect(risk.getByText(riskScope)).toBeTruthy();
    expect(observation.getByText(observationScope)).toBeTruthy();
    expect(recommendation.getByText(recommendationScope)).toBeTruthy();
    const markdown = reportToMarkdown(response);
    expect(markdown.split('## What the data shows')[0]).toContain(riskScope);
    expect(markdown.split('## What the data shows')[1].split('## Suggested remediation')[0]).toContain(observationScope);
    expect(markdown.split('## Suggested remediation')[1].split('## Professional consultation')[0]).toContain(recommendationScope);
    expect(markdown).not.toContain('history-read');
  });

  it('does not display unresolved read IDs as evidence in historical reports or Markdown', () => {
    const response = baseResponse([]);
    response.riskSummary.evidenceReadIds = ['missing-audit-read'];
    mocks.state.messages = [assistantMessage(response)];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.queryByLabelText('Cited evidence')).toBeNull();
    expect(document.body.textContent).not.toContain('missing-audit-read');
    expect(reportToMarkdown(response)).not.toContain('missing-audit-read');
  });

  it('renders actual source checks with dates and locations and retains additional reads in exports', () => {
    const response = baseResponse([]);
    response.analysisEvidence = {
      version: 1,
      stages: [{ id: 'history', label: 'Historical comparison', status: 'partial' }],
      toolCalls: [
        { id: 'one', stage: 'history', tool: 'surface_values_near_point', source: 'soil-field-moisture', selectedDate: '2025-09-10', location: { lat: 43.6, lon: -116.2 }, status: 'unavailable', reason: 'Requested day has not been published.' },
        { id: 'two', stage: 'history', tool: 'surface_values_near_point', source: 'climate-field-precipitation', status: 'not_queried' },
        { id: 'three', stage: 'additional', tool: 'surface_features_near_point', source: 'vegetation', selectedDate: '2026-09-10', validDates: ['2026-08-30'], observedDates: ['2026-08-31'], servedDates: ['2026-09-01'], location: { lat: 43.9, lon: -116.5 }, status: 'observed', summary: 'Nearby vegetation records returned; this is a comparison location.' },
      ],
      limitations: ['A nearby region is not an evaluated intervention outcome.'],
    };
    mocks.state.messages = [assistantMessage(response)];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText('Evidence checks (2 queried)')).toBeTruthy();
    expect(screen.getByText('soil field moisture · Unavailable')).toBeTruthy();
    expect(screen.getByText('climate field precipitation · Not queried')).toBeTruthy();
    expect(screen.getByText('vegetation · Evidence returned')).toBeTruthy();
    expect(screen.getByText('Requested 2025-09-10 · 43.6°, -116.2°')).toBeTruthy();
    expect(screen.getByText(response.analysisEvidence.limitations[0])).toBeTruthy();
    const markdown = reportToMarkdown(response);
    expect(markdown).toContain('Requested 2025-09-10 · 43.6°, -116.2°');
    expect(markdown).toContain('Requested 2026-09-10 · Valid dates: 2026-08-30 · Observed days: 2026-08-31 · Served days: 2026-09-01');
    expect(screen.getByText('Requested 2026-09-10 · Valid dates: 2026-08-30 · Observed days: 2026-08-31 · Served days: 2026-09-01 · 43.9°, -116.5°')).toBeTruthy();
    expect(markdown).toContain('vegetation: Evidence returned');
    expect(markdown).toContain('Requested day has not been published.');
    expect(markdown).toContain('A nearby region is not an evaluated intervention outcome.');
  });

  it("counts supported sources without advertising deferred model placeholders", () => {
    mocks.state.dataFreshness = {
      drought: "unavailable", streamflow: "unavailable", weatherObservations: "unavailable",
      fireDetections: "unavailable", firePerimeters: "unavailable", soilProperties: "unavailable",
      mtbsPerimeters: "unavailable", strategyRecommendations: "unavailable",
      carbonPotential: "published_revision_required",
    };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Initial context sources (7)" }));
    expect(screen.getAllByText("No dated evidence in initial context")).toHaveLength(7);
    expect(screen.queryByText("strategyRecommendations")).toBeNull();
    expect(screen.queryByText("carbonPotential")).toBeNull();
  });

  it("omits a footer containing only deferred model placeholders", () => {
    mocks.state.dataFreshness = { strategyRecommendations: "published_revision_required", carbonPotential: "unavailable" };
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.queryByRole("button", { name: /Initial context sources/ })).toBeNull();
  });

  it("retains dated evidence in historical reports even for a now-deferred source", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    mocks.state.dataFreshness = { strategyRecommendations: "2026-08-01T12:00:00Z", carbonPotential: "2026-08-01T12:00:00Z" };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Initial context sources (2)" }));
    expect(screen.getByText("strategyRecommendations")).toBeTruthy();
    expect(screen.getByText("carbonPotential")).toBeTruthy();
    expect(screen.queryByText("No dated evidence in initial context")).toBeNull();
  });

  it("labels SoilGrids evidence as a published estimate without relabelling measured sources", () => {
    const response = baseResponse([]);
    response.observations = [
      { statement: "SoilGrids estimates surface clay at 26%.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" },
      { statement: "Streamflow was 14,100 cfs.", evidenceOrigin: "warehouse", evidenceSource: "streamflow" },
    ];
    mocks.state.messages = [assistantMessage(response)];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText("Published estimate · soilProperties")).toBeTruthy();
    expect(screen.getByText("Observed data · streamflow")).toBeTruthy();
  });

  it("labels an observation instant with its viewer timezone rather than an ambiguous calendar date", () => {
    mocks.state.dataFreshness = { streamflow: "2026-09-10T06:45:00Z" };
    const formatter = vi.spyOn(Date.prototype, "toLocaleString");
    try {
      renderWithProviders(<RegionalIntelligencePanel />);
      fireEvent.click(screen.getByRole("button", { name: "Initial context sources (1)" }));
      expect(formatter).toHaveBeenCalledWith(undefined, { timeZoneName: "short" });
    } finally {
      formatter.mockRestore();
    }
  });
  afterEach(() => {
    mocks.state.messages = [];
    mocks.state.dataFreshness = {};
    vi.useRealTimers();
  });

  it("does not describe an empty completed request as still reviewing", () => {
    mocks.state.messages = [{ id: "failed", role: "assistant", content: "", isStreaming: false }];
    renderWithProviders(<RegionalIntelligencePanel />);
    expect(screen.getByText("No analysis was completed.")).toBeTruthy();
    expect(screen.queryByText("Reviewing this location…")).toBeNull();
  });

  it("labels undated soil and captured perimeter releases without calling them unobserved", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    mocks.state.dataFreshness = {
      soilProperties: "static_release_untimed",
      firePerimeters: "snapshot_captured_2026-09-01",
    };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Initial context sources (2)" }));
    expect(screen.getByText("Static release (undated)")).toBeTruthy();
    expect(screen.getByText("Snapshot captured 2026-09-01")).toBeTruthy();
    expect(screen.queryByText("No dated evidence in initial context")).toBeNull();
  });

  it("labels MTBS publication availability separately from snapshot capture or ignition", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-11T12:00:00Z"));
    mocks.state.dataFreshness = { mtbsPerimeters: "publication_available_2026-09-11" };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Initial context sources (1)" }));
    expect(screen.getByText("Publication available 2026-09-11")).toBeTruthy();
    expect(screen.queryByText("Snapshot captured 2026-09-11")).toBeNull();
    expect(screen.queryByText("No dated evidence in initial context")).toBeNull();
  });

  it("keeps an old perimeter snapshot stale while displaying its actual capture day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    mocks.state.dataFreshness = { firePerimeters: "snapshot_captured_2026-08-01" };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Initial context sources (1)" }));
    expect(screen.getByText("Stale (Snapshot captured 2026-08-01)")).toBeTruthy();
  });

  it("labels the drought release by its publisher day rather than the previous local evening", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    const dateFormat = vi.spyOn(Date.prototype, "toLocaleDateString").mockImplementation(
      function (this: Date, _locales, options) {
        return new Intl.DateTimeFormat("en-US", { timeZone: "America/Denver", ...options }).format(this);
      }
    );
    try {
      mocks.state.dataFreshness = { drought: "2026-09-01T00:00:00Z" };
      renderWithProviders(<RegionalIntelligencePanel />);
      fireEvent.click(screen.getByRole("button", { name: "Initial context sources (1)" }));
      expect(screen.getByText("Release Sep 1, 2026")).toBeTruthy();
    } finally {
      dateFormat.mockRestore();
    }
  });

  it("renders a chip per recommended strategy, named from the model's own remediation items", () => {
    mocks.state.messages = [
      assistantMessage(
        baseResponse([
          {
            strategy: "riparian_buffer",
            title: "Restore streambank vegetation",
            rationale: "Bank cover reduces erosion under drought stress.",
            timeframe: "short_term",
            confidence: "moderate",
            consultProfessionals: ["hydrologist"],
            evidenceOrigin: "model_inference",
          },
          {
            strategy: "managed_grazing",
            title: "Rotate grazing away from riparian corridor",
            rationale: "Reduces compaction while vegetation recovers.",
            timeframe: "immediate",
            confidence: "high",
            consultProfessionals: ["agronomist"],
            evidenceOrigin: "warehouse",
            evidenceSource: "drought",
          },
        ])
      ),
    ];

    renderWithProviders(<RegionalIntelligencePanel />);

    const chips = within(screen.getByLabelText("Suggested strategy chips"));
    expect(chips.getByText(/riparian buffer/i)).toBeTruthy();
    expect(chips.getByText(/managed grazing/i)).toBeTruthy();
    // No fabricated causal language anywhere on the page.
    expect(screen.queryByText(/tau/i)).toBeNull();
    expect(document.body.textContent).not.toMatch(/\+\d+%/);
  });

  it("renders no strategy chips when the response recommends nothing", () => {
    mocks.state.messages = [assistantMessage(baseResponse([]))];

    renderWithProviders(<RegionalIntelligencePanel />);

    expect(
      screen.getByText(/did not find enough here to suggest a remediation/i)
    ).toBeTruthy();
    expect(screen.queryByLabelText("Suggested strategy chips")).toBeNull();
    expect(screen.queryByText(/tau/i)).toBeNull();
    expect(document.body.textContent).not.toMatch(/\+\d+%/);
  });

  it("still offers both a JSON and a Markdown export once a report has rendered", () => {
    mocks.state.messages = [
      assistantMessage(
        baseResponse([
          {
            strategy: "biochar",
            title: "Apply biochar to depleted plots",
            rationale: "Organic carbon deficit observed in supplied soil context.",
            timeframe: "long_term",
            confidence: "low",
            consultProfessionals: ["soil_scientist"],
            evidenceOrigin: "model_inference",
          },
        ])
      ),
    ];

    renderWithProviders(<RegionalIntelligencePanel />);

    expect(screen.getByRole("button", { name: /export json/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /export markdown/i })).toBeTruthy();
  });
});
