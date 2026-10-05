import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/utils";
import type { ChatMessage } from "@/stores/regional-intelligence-store";
import type { RegionalIntelligenceResponse } from "@/lib/regional-intelligence";
import { remediationReportSchema } from "@/lib/server/services/remediation-report";

/**
 * O2: a SoilGrids model estimate must never wear the "Observed data" badge. Two render paths badge
 * by origin alone (strategy chips, the riskSummary export line), so the interim contract keeps
 * `soilProperties` out of both; the observation path already says "Published estimate".
 */
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

function soilCitingResponse(): RegionalIntelligenceResponse {
  return {
    aiGenerated: true,
    riskSummary: {
      level: "moderate",
      headline: "Acid loam topsoil under moderate drought.",
      factors: ["Topsoil pH 5.8 (SoilGrids v2.0 250 m model estimate, 0-30 cm)."],
      evidenceOrigin: "model_inference",
      evidenceSources: [],
    },
    observations: [{
      statement: "Topsoil pH is 5.8. Soil values are SoilGrids v2.0 250 m model estimates, not measurements.",
      evidenceOrigin: "warehouse",
      evidenceSource: "soilProperties",
    }],
    remediation: [{
      strategy: "cover_cropping", title: "Screen acid-tolerant cover crops",
      rationale: "The SoilGrids model estimate puts topsoil pH near 5.8.", timeframe: "short_term",
      confidence: "low", consultProfessionals: ["soil_scientist"], evidenceOrigin: "model_inference",
    }],
    professionalConsultation: "Confirm with a soil scientist.",
    webSources: [],
    dataFreshness: { soilProperties: "static_release_untimed" },
  };
}

/** The report fields the strict runtime contract validates; the response adds transport fields. */
function reportFields(response: Record<string, unknown>) {
  const { aiGenerated: _aiGenerated, webSources: _webSources, dataFreshness: _dataFreshness, ...report } = response;
  return report;
}

describe("no soil-citing response shows the Observed data badge", () => {
  it("renders the soil observation as a published estimate, and no Observed data anywhere", () => {
    const response = soilCitingResponse();
    expect(remediationReportSchema.safeParse(reportFields({ ...response })).success).toBe(true);
    mocks.state.messages = [{ id: "assistant-1", role: "assistant", content: "", parsedResponse: response }];
    const { container } = renderWithProviders(<RegionalIntelligencePanel />);
    // The finding's provenance line, not a chip: the per-item origin chips were removed 2026-10-04.
    expect(screen.getByText("Published estimate")).toBeTruthy();
    expect(container.textContent).not.toContain("Observed data");
    expect(reportToMarkdown(response)).not.toContain("Observed data");
  });

  it("refuses the two badge-blind paths a soil citation could otherwise reach", () => {
    const response = soilCitingResponse();
    const remediationCitingSoil = { ...response, remediation: [{ ...response.remediation[0], evidenceOrigin: "warehouse", evidenceSource: "soilProperties" }] };
    const riskCitingSoil = { ...response, riskSummary: { ...response.riskSummary, evidenceOrigin: "warehouse", evidenceSources: ["soilProperties"] } };
    expect(remediationReportSchema.safeParse(reportFields({ ...response })).success).toBe(true);
    expect(remediationReportSchema.safeParse(reportFields(remediationCitingSoil)).success).toBe(false);
    expect(remediationReportSchema.safeParse(reportFields(riskCitingSoil)).success).toBe(false);
  });
});
