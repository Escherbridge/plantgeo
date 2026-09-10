import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { readMapFocus } from "@/lib/map/focus-params";
import type { RegionalIntelligenceResponse } from "@/lib/regional-intelligence";
import { readSavedReport } from "@/app/dashboard/conversations/saved-report";

const mocks = vi.hoisted(() => ({ limit: vi.fn(), orderBy: vi.fn() }));
vi.mock("@/lib/server/auth", () => ({ getServerSession: async () => ({ user: { id: "owner" } }) }));
vi.mock("@/lib/server/db", () => ({ db: {
  select: () => ({ from: () => ({ where: () => ({ limit: mocks.limit, orderBy: mocks.orderBy }) }) }),
} }));

import ConversationDetailPage from "@/app/dashboard/conversations/[id]/page";

const report: RegionalIntelligenceResponse = {
  aiGenerated: true,
  riskSummary: { level: "moderate", headline: "Review the riverbank before planting.", factors: ["Sparse cover"], evidenceOrigin: "model_inference", evidenceSources: [] },
  observations: [{ statement: "Published soil carbon estimate is 61.9 g/kg.", evidenceOrigin: "warehouse", evidenceSource: "soilProperties" }],
  remediation: [{ strategy: "riparian_buffer", title: "Discuss a riparian buffer", rationale: "Confirm appropriate native species with a local practitioner.", timeframe: "long_term", confidence: "low", consultProfessionals: ["ecologist"], evidenceOrigin: "model_inference" }],
  professionalConsultation: "Consult a local ecologist before planting.",
  webSources: [{ title: "SoilGrids documentation", url: "https://docs.isric.org/" }],
  dataFreshness: { soilProperties: "static_release_untimed" },
};

async function renderTranscript(messages: unknown[]) {
  mocks.limit.mockResolvedValue([{ id: "saved", title: "Snake River", lat: 45.94, lon: -116.78 }]);
  mocks.orderBy.mockResolvedValue(messages);
  return render(await ConversationDetailPage({ params: Promise.resolve({ id: "saved" }) }));
}

it("renders saved reports with the live report presentation and safe source links", async () => {
  const { container } = await renderTranscript([{ id: "answer", role: "assistant", content: "Original narration", structuredResponse: report, createdAt: "2026-09-10T12:00:00Z" }]);
  expect(screen.getByText(report.riskSummary.headline)).toBeTruthy();
  expect(screen.getByText("Saved conversation. Reports show the analysis as recorded.")).toBeTruthy();
  expect(screen.getByText(report.observations[0].statement)).toBeTruthy();
  expect(screen.getByText("Published estimate · soilProperties")).toBeTruthy();
  expect(screen.getByText(report.remediation[0].title)).toBeTruthy();
  expect(screen.getByText(report.professionalConsultation)).toBeTruthy();
  expect(screen.getByText("AI-generated.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Export Markdown" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "SoilGrids documentation" }).getAttribute("href")).toBe("https://docs.isric.org/");
  expect(container.querySelector("pre")).toBeNull();
});

it("preserves ordinary user text without interpreting attached structured data", async () => {
  const { container } = await renderTranscript([{ id: "question", role: "user", content: '<img src=x onerror="alert(1)">\nWhat does this mean?', structuredResponse: report, createdAt: "2026-09-10T12:00:00Z" }]);
  expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  expect(container.querySelector("img")).toBeNull();
  expect(screen.queryByText(report.riskSummary.headline)).toBeNull();
  expect(screen.queryByText("Original saved data")).toBeNull();
});

it("keeps malformed legacy data escaped and collapsed alongside its original narration", async () => {
  const legacy = { summary: "<script>alert(1)</script>", unknown: true };
  const { container } = await renderTranscript([{ id: "old", role: "assistant", content: "Original older analysis", structuredResponse: legacy, createdAt: "2026-09-10T12:00:00Z" }]);
  expect(screen.getByText("Original older analysis")).toBeTruthy();
  expect(screen.getByText("Original saved data")).toBeTruthy();
  expect(container.querySelector("details")?.open).toBe(false);
  expect(container.querySelector("pre")?.textContent).toContain("<script>alert(1)</script>");
  expect(container.querySelector("script")).toBeNull();
});

it("does not pass unsafe citations or invalid report shapes to the canonical renderer", () => {
  expect(readSavedReport("assistant", report)).toEqual(report);
  expect(readSavedReport("user", report)).toBeNull();
  expect(readSavedReport("assistant", { ...report, webSources: [{ title: "Unsafe", url: "javascript:alert(1)" }] })).toBeNull();
  expect(readSavedReport("assistant", { ...report, webSources: [{ title: "Broken", url: "not a URL" }] })).toBeNull();
  expect(readSavedReport("assistant", { ...report, aiGenerated: false })).toBeNull();
  expect(readSavedReport("assistant", { ...report, observations: [{}] })).toBeNull();
  expect(readSavedReport("assistant", null)).toBeNull();
});

it("opens a saved analysis at the saved point using the camera reader's URL contract", async () => {
  mocks.limit.mockResolvedValue([{ id: "saved", title: "Snake River", lat: 45.94, lon: -116.78 }]);
  mocks.orderBy.mockResolvedValue([]);
  render(await ConversationDetailPage({ params: Promise.resolve({ id: "saved" }) }));
  const href = screen.getByRole("link", { name: "Open on Map" }).getAttribute("href");
  const url = new URL(href!, "https://plantgeo.example");
  expect(readMapFocus(url.searchParams)).toEqual({ latitude: 45.94, longitude: -116.78, zoom: 13 });
  expect(url.searchParams.has("ai")).toBe(false);
});
