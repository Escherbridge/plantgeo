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

import RegionalIntelligencePanel from "@/components/panels/RegionalIntelligencePanel";

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
    fireEvent.click(screen.getByRole("button", { name: "Data sources (2)" }));
    expect(screen.getByText("Static release (undated)")).toBeTruthy();
    expect(screen.getByText("Snapshot captured 2026-09-01")).toBeTruthy();
    expect(screen.queryByText("Not observed")).toBeNull();
  });

  it("keeps an old perimeter snapshot stale while displaying its actual capture day", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-10T12:00:00Z"));
    mocks.state.dataFreshness = { firePerimeters: "snapshot_captured_2026-08-01" };
    renderWithProviders(<RegionalIntelligencePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Data sources (1)" }));
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
      fireEvent.click(screen.getByRole("button", { name: "Data sources (1)" }));
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
